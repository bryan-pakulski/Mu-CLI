import { baseUrl } from './client';

export const audioApi = {
  tts: async (text: string, voice?: string, sessionName?: string): Promise<Blob> => {
    const res = await fetch(`${baseUrl()}/api/audio/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice, session_name: sessionName }),
    });
    if (!res.ok) throw new Error(`TTS failed: ${res.status}`);
    return res.blob();
  },
  stt: async (audioUri: string, sessionName?: string): Promise<{ text: string; language: string }> => {
    const formData = new FormData();
    formData.append('audio', {
      uri: audioUri,
      type: 'audio/webm',
      name: 'audio.webm',
    } as unknown as Blob);
    const res = await fetch(`${baseUrl()}/api/audio/stt`, {
      method: 'POST',
      headers: { 'Content-Type': 'multipart/form-data' },
      body: formData,
    });
    if (!res.ok) throw new Error(`STT failed: ${res.status}`);
    return res.json();
  },
};