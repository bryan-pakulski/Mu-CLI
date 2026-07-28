import { api, baseUrl } from './client';

export interface ArtifactDescriptor {
  artifact_id: string;
  name: string;
  size: number;
  mime_type: string;
  created_at: number;
  download_url: string;
}

export const artifactsApi = {
  list: (sessionName: string) =>
    api.get<{ artifacts: ArtifactDescriptor[] }>(
      `/api/sessions/${encodeURIComponent(sessionName)}/artifacts`,
    ),
  downloadUrl: (sessionName: string, artifactId: string) =>
    `${baseUrl()}/api/sessions/${encodeURIComponent(sessionName)}/artifacts/${encodeURIComponent(artifactId)}/download`,
  remove: (sessionName: string, artifactId: string) =>
    api.delete<{ ok: boolean; artifact_id: string }>(
      `/api/sessions/${encodeURIComponent(sessionName)}/artifacts/${encodeURIComponent(artifactId)}`,
    ),
};
