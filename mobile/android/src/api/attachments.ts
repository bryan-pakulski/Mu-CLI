
import { ApiError, DEFAULT_REQUEST_TIMEOUT_MS, api, baseUrl } from './client';

export interface AttachmentDescriptor {
  attachment_id: string;
  name: string;
  size: number;
  mime_type: string;
  sha256?: string;
  created_at: number;
  download_url: string;
  deduplicated?: boolean;
}

export interface PickedDocument {
  uri: string;
  name: string;
  mimeType?: string | null;
  size?: number | null;
}

export const attachmentsApi = {
  list: (sessionName: string, signal?: AbortSignal) =>
    api.get<{ attachments: AttachmentDescriptor[] }>(
      `/api/sessions/${encodeURIComponent(sessionName)}/attachments`,
      { signal, query: { session_name: undefined } },
    ),

  remove: (sessionName: string, attachmentId: string) =>
    api.delete<{ ok: boolean; attachment_id: string }>(
      `/api/sessions/${encodeURIComponent(sessionName)}/attachments/${encodeURIComponent(attachmentId)}`,
      { query: { session_name: undefined } },
    ),

  downloadUrl: (sessionName: string, attachmentId: string) =>
    `${baseUrl()}/api/sessions/${encodeURIComponent(sessionName)}/attachments/${encodeURIComponent(attachmentId)}/download`,

  upload: async (sessionName: string, document: PickedDocument): Promise<AttachmentDescriptor> => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Math.max(DEFAULT_REQUEST_TIMEOUT_MS, 60_000));
    const form = new FormData();
    form.append('file', {
      uri: document.uri,
      name: document.name || 'attachment',
      type: document.mimeType || 'application/octet-stream',
    } as unknown as Blob);
    try {
      const response = await fetch(
        `${baseUrl()}/api/sessions/${encodeURIComponent(sessionName)}/attachments`,
        { method: 'POST', body: form, signal: controller.signal },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new ApiError(response.status, String(body.detail || `Upload failed (${response.status})`), body);
      }
      return body.attachment as AttachmentDescriptor;
    } catch (error) {
      if (controller.signal.aborted) throw new ApiError(0, 'Attachment upload timed out');
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  },
};
