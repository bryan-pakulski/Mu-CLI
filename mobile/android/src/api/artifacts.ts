import { api, baseUrl } from './client';

export interface ArtifactDescriptor {
  artifact_id: string;
  name: string;
  size: number;
  mime_type: string;
  created_at: number;
  download_url: string;
  kind?: 'file' | 'visualization';
  display?: 'download' | 'inline';
  title?: string;
  height?: number;
  view_url?: string;
}

export const artifactsApi = {
  list: (sessionName: string) =>
    api.get<{ artifacts: ArtifactDescriptor[] }>(
      `/api/sessions/${encodeURIComponent(sessionName)}/artifacts`,
      { query: { _ts: Date.now() } },
    ),
  downloadUrl: (sessionName: string, artifactId: string) =>
    `${baseUrl()}/api/sessions/${encodeURIComponent(sessionName)}/artifacts/${encodeURIComponent(artifactId)}/download`,
  viewUrl: (sessionName: string, artifactId: string) =>
    `${baseUrl()}/api/sessions/${encodeURIComponent(sessionName)}/artifacts/${encodeURIComponent(artifactId)}/view`,
  remove: (sessionName: string, artifactId: string) =>
    api.delete<{ ok: boolean; artifact_id: string }>(
      `/api/sessions/${encodeURIComponent(sessionName)}/artifacts/${encodeURIComponent(artifactId)}`,
    ),
};
