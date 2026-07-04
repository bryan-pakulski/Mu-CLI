"""Audio endpoints: TTS synthesis (Kokoro) and STT transcription (Whisper).

Models are lazy-loaded on first request so the GUI starts fast and
does not crash when the optional dependencies are not installed.
"""

from __future__ import annotations

import io
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy-loaded model singletons (module-level cache)

_tts_pipeline = None  # KPipeline instance
_tts_voice_default = "af_heart"
_tts_sample_rate = 24000  # Kokoro outputs at 24 kHz


def _get_tts_pipeline():
    """Lazy-load Kokoro KPipeline. Raises HTTPException on failure."""
    global _tts_pipeline
    if _tts_pipeline is not None:
        return _tts_pipeline
    try:
        from kokoro import KPipeline
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="kokoro is not installed. Install with: pip install 'kokoro>=0.9.2'",
        )
    try:
        _tts_pipeline = KPipeline(lang_code="a")  # 'a' = American English
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialise Kokoro TTS pipeline: {exc}",
        )
    return _tts_pipeline


# ---------------------------------------------------------------------------
# TTS endpoint

@router.post("/tts")
async def text_to_speech(request: Request, payload: Dict[str, Any]):
    """Synthesize speech from text using Kokoro TTS.

    Request body:
        {"text": "Hello world", "voice": "af_heart"}

    Returns audio/wav streaming response.
    """
    session = request.app.state.session_by_name(
        (payload.get("session_name") or "").strip() or None
    )

    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    voice = str(payload.get("voice") or _tts_voice_default).strip()

    # Check if TTS is enabled in session variables
    if session is not None:
        enabled = session.variables.get("tts_enabled", False)
        if not enabled:
            raise HTTPException(
                status_code=403,
                detail="TTS is disabled. Enable with /set tts_enabled true",
            )

    pipeline = _get_tts_pipeline()

    import asyncio

    def _synth():
        """Run synthesis in a thread — Kokoro is CPU/GPU bound."""
        import numpy as np
        import wave

        chunks = []
        try:
            for _gs, _ps, audio in pipeline(text, voice=voice):
                if audio is not None:
                    chunks.append(np.asarray(audio, dtype=np.float32))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"TTS synthesis failed: {exc}",
            )

        if not chunks:
            raise HTTPException(
                status_code=500,
                detail="TTS produced no audio output.",
            )

        combined = np.concatenate(chunks)
        # Write WAV using stdlib wave module (no soundfile dependency).
        # Convert float32 [-1.0, 1.0] → int16 PCM.
        int16_data = np.clip(combined, -1.0, 1.0)
        int16_data = (int16_data * 32767).astype(np.int16)
        pcm_bytes = int16_data.tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(_tts_sample_rate)
            wav.writeframes(pcm_bytes)
        buf.seek(0)
        return buf

    buf = await asyncio.to_thread(_synth)

    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={"Content-Disposition": "inline"},
    )


# ---------------------------------------------------------------------------
# Lazy-loaded STT model singleton (module-level cache)

_stt_model = None  # WhisperModel instance
_stt_model_size_default = "base"  # "tiny" | "base" | "small" | "medium" | "large-v3"


def _get_stt_model(model_size: str = ""):
    """Lazy-load faster-whisper WhisperModel. Raises HTTPException on failure."""
    global _stt_model, _stt_model_size_default
    requested = (model_size or "").strip() or _stt_model_size_default
    # Re-instantiate if a different model size was requested.
    if _stt_model is not None and getattr(_stt_model, "_size", "") == requested:
        return _stt_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="faster-whisper is not installed. Install with: pip install faster-whisper",
        )
    try:
        _stt_model = WhisperModel(requested, device="cpu", compute_type="int8")
        _stt_model._size = requested  # type: ignore[attr-defined]
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialise Whisper STT model ('{requested}'): {exc}",
        )
    return _stt_model


# ---------------------------------------------------------------------------
# STT endpoint

@router.post("/stt")
async def speech_to_text(request: Request):
    """Transcribe an audio blob using faster-whisper STT.

    Accepts a multipart upload with a single ``audio`` file field.
    Returns JSON: ``{"text": "...", "language": "en"}``.
    """
    session = None
    try:
        session = request.app.state.session_by_name(None)
    except AttributeError:
        pass  # standalone test / no session loaded

    # Check if STT is enabled in session variables
    if session is not None:
        enabled = session.variables.get("stt_enabled", False)
        if not enabled:
            raise HTTPException(
                status_code=403,
                detail="STT is disabled. Enable with /set stt_enabled true",
            )

    form = await request.form()
    audio_file = form.get("audio")
    if audio_file is None or not hasattr(audio_file, "read"):
        raise HTTPException(
            status_code=400,
            detail="audio file is required (multipart field 'audio')",
        )

    # Read the uploaded audio bytes into a temp file (faster-whisper needs a
    # file path or file-like object; the UploadFile is fine as a file-like).
    audio_bytes = await audio_file.read()
    if not audio_bytes:
        raise HTTPException(
            status_code=400,
            detail="audio file is empty",
        )

    # Determine model size from session variables
    model_size = ""
    if session is not None:
        model_size = str(session.variables.get("stt_model", "") or "").strip()

    model = _get_stt_model(model_size)

    import asyncio
    import tempfile
    import os

    def _transcribe():
        """Run transcription in a thread — Whisper is CPU bound."""
        # Write to a temp file because faster-whisper accepts a path.
        suffix = ".wav"
        content_type = getattr(audio_file, "content_type", "") or ""
        if "webm" in content_type:
            suffix = ".webm"
        elif "ogg" in content_type:
            suffix = ".ogg"
        elif "mp3" in content_type:
            suffix = ".mp3"
        elif "m4a" in content_type:
            suffix = ".m4a"

        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
            segments, info = model.transcribe(tmp_path, beam_size=5)
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())
            return " ".join(text_parts), info.language
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    try:
        text, language = await asyncio.to_thread(_transcribe)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"STT transcription failed: {exc}",
        )

    return {"text": text, "language": language}