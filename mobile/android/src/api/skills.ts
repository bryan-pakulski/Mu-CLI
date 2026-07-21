import { api } from './client';

export interface Skill {
  name: string;
  description: string;
  trigger: string;
  source: string;
  body: string;
  enabled: boolean;
}

export const skillsApi = {
  list: () => api.get<{ skills: Skill[] }>('/api/skills'),
  get: (name: string) => api.get<Skill>(`/api/skills/${encodeURIComponent(name)}`),
  enable: (name: string) => api.post<{ ok: boolean; skills: Skill[] }>(`/api/skills/${encodeURIComponent(name)}/enable`),
  disable: (name: string) => api.post<{ ok: boolean; skills: Skill[] }>(`/api/skills/${encodeURIComponent(name)}/disable`),
  reload: () => api.post<{ ok: boolean; skills: Skill[] }>('/api/skills/reload'),
  save: (skill: { name: string; description: string; trigger: string; body: string; scope: string }) =>
    api.post<{ ok: boolean; skills: Skill[] }>('/api/skills/save', skill),
  delete: (name: string) => api.delete<{ ok: boolean; skills: Skill[] }>(`/api/skills/${encodeURIComponent(name)}`),
};