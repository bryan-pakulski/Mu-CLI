"""Pin the /file → image_input vision pipeline end-to-end.

The bug this regression-tests: `Session.add_file` used to call
`provider.upload_file()` and stage a `type="file"` part for every file
type. For OpenAI/Ollama, `upload_file` is a no-op stub that returns a
local-path FileReference, which providers then render as a plain
"[File: <name>]" text stub. Vision models never saw the bytes.

After the fix, image MIMEs are detected at staging time and routed to
`type="image_input"` with raw bytes (base64-encoded for JSON-safe
storage), so per-provider conversion can emit them as inline images.
"""

import base64

from core.session import Session, SessionManager
from providers.base import ImageData, LLMProvider, MessagePart, ProviderResponse


PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(
            text="ok", parts=[], input_tokens=0, output_tokens=0, total_tokens=0
        )

    def upload_file(self, file_path, mime_type):
        # If add_file routes images here we've regressed — make it loud.
        raise AssertionError(
            f"upload_file should not be invoked for images (got {mime_type})"
        )


def _make_session():
    sm = SessionManager()
    return Session(DummyProvider("dummy"), False, "system instruction", sm)


def test_add_file_routes_png_to_image_input(tmp_path):
    """An image staged via /file becomes a JSON-safe image_input dict
    with base64-encoded bytes — not a `type="file"` ref."""
    png_path = tmp_path / "screenshot.png"
    png_path.write_bytes(PNG_1x1)

    session = _make_session()
    session.add_file(str(png_path))

    assert len(session.staged_files) == 1
    staged = session.staged_files[0]
    assert staged["type"] == "image_input"
    assert staged["image"]["mime_type"] == "image/png"
    assert staged["image"]["source"] == str(png_path)
    # Round-trip the bytes through base64.
    decoded = base64.b64decode(staged["image"]["data_b64"])
    assert decoded == PNG_1x1


def test_add_file_non_image_still_uses_file_ref(tmp_path, monkeypatch):
    """Non-image MIMEs must keep going through the legacy upload_file path."""
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("hello")

    session = _make_session()
    # Swap the asserting upload_file with a benign one for this test only.
    from providers.base import FileReference

    monkeypatch.setattr(
        session.provider,
        "upload_file",
        lambda p, m: FileReference(uri=p, mime_type=m, display_name=p),
    )

    session.add_file(str(txt_path))
    assert len(session.staged_files) == 1
    assert session.staged_files[0]["type"] == "file"


def test_image_input_dict_rehydrates_into_messagepart(tmp_path):
    """The staged dict must round-trip through `_build_messages_from_history`
    back into a real `MessagePart(image_input, ImageData(raw bytes))`."""
    session = _make_session()
    b64 = base64.b64encode(PNG_1x1).decode("ascii")
    new_user = {
        "role": "user",
        "parts": [
            {
                "type": "image_input",
                "image": {
                    "data_b64": b64,
                    "mime_type": "image/png",
                    "source": "/tmp/x.png",
                },
            },
            {"type": "text", "text": "what's in this image?"},
        ],
    }

    messages = session._build_messages_from_history([], new_user)
    assert len(messages) == 1
    parts = messages[0].parts
    assert [p.type for p in parts] == ["image_input", "text"]
    img_part = parts[0]
    assert isinstance(img_part.image, ImageData)
    assert img_part.image.data == PNG_1x1
    assert img_part.image.mime_type == "image/png"
    assert img_part.image.source == "/tmp/x.png"


def test_image_input_summarized_for_history_degrade():
    """`_summarize_message_parts` must produce a human-readable line for
    image_input so degraded history doesn't say 'no serializable content'."""
    session = _make_session()
    msg = {
        "role": "user",
        "parts": [
            {
                "type": "image_input",
                "image": {
                    "data_b64": "",
                    "mime_type": "image/jpeg",
                    "source": "/tmp/photo.jpg",
                },
            }
        ],
    }
    summary = session._summarize_message_parts(msg)
    assert "image:/tmp/photo.jpg" in summary


def test_send_message_attaches_staged_image(tmp_path, monkeypatch):
    """End-to-end: /file <png> then send_message — the user turn's parts
    must include the image_input part as the first entry."""
    captured = {}

    class CapturingProvider(DummyProvider):
        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            captured["messages"] = messages
            return ProviderResponse(
                text="seen", parts=[], input_tokens=0, output_tokens=0, total_tokens=0
            )

    sm = SessionManager()
    session = Session(
        CapturingProvider("dummy"), False, "system instruction", sm
    )
    png_path = tmp_path / "diag.png"
    png_path.write_bytes(PNG_1x1)
    session.add_file(str(png_path))

    # send_message is non-agentic by default — it calls provider.generate once.
    session.send_message("look at this please")

    msgs = captured.get("messages") or []
    # The last (user) message holds the staged file.
    user_msg = msgs[-1]
    assert user_msg.role == "user"
    types = [p.type for p in user_msg.parts]
    assert types[0] == "image_input", f"expected image_input first, got {types}"
    img_part = user_msg.parts[0]
    assert isinstance(img_part.image, ImageData)
    assert img_part.image.data == PNG_1x1
