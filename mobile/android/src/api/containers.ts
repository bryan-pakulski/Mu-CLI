import { api } from './client';
import type {
  ContainerDevice,
  ContainerHardwareCapabilities,
  ContainerMount,
} from './sessions'; // MUCLI_CONTAINER_HARDWARE_V1

export interface ManagedContainer {
  name: string;
  image: string;
  status: string;
  template_name?: string | null;
  attached_sessions: string[];
  mounts: ContainerMount[];
  gpu_request?: string;
  devices?: ContainerDevice[];
  egress_allow: string[];
  egress_deny: string[];
  network_name?: string;
}

export interface ContainerGpuStats {
  requested: boolean;
  scope: 'assigned_device_total';
  utilization_percent?: number | null;
  memory_used_bytes: number;
  memory_total_bytes: number;
  temperature_c?: number | null;
  power_watts?: number | null;
  devices: Array<{
    index?: string;
    uuid?: string;
    name?: string;
    utilization_percent?: number;
    memory_used_bytes?: number;
    memory_total_bytes?: number;
    temperature_c?: number;
    power_watts?: number;
  }>;
}

export interface ContainerStats {
  name: string;
  status: string;
  sampled_at: number;
  cpu_percent: number;
  memory_used_bytes: number;
  memory_limit_bytes: number;
  memory_percent: number;
  network_rx_bytes: number;
  network_tx_bytes: number;
  network_rx_bytes_per_second: number;
  network_tx_bytes_per_second: number;
  block_read_bytes: number;
  block_write_bytes: number;
  pids: number;
  storage_writable_bytes: number;
  storage_rootfs_bytes: number;
  restart_count: number;
  uptime_seconds?: number | null;
  gpu: ContainerGpuStats;
  attached_device_count: number;
  error?: string | null;
}

export interface ContainerStatsResponse {
  sampled_at: number;
  poll_after_ms: number;
  containers: Record<string, ContainerStats>;
}

// MUCLI_CONTAINER_MONITOR_V1
export interface ContainerTemplateSummary {
  name: string;
  image: string;
  source_container: string;
  description?: string;
  egress_allow?: string[];
  egress_deny?: string[];
}

export interface ContainerListing {
  containers: ManagedContainer[];
  templates: ContainerTemplateSummary[];
}

export interface ContainerConfiguration {
  name: string;
  container_name: string;
  dockerfile?: string | null;
  template_name?: string | null;
  mounts: ContainerMount[];
  gpu_request?: string;
  devices?: ContainerDevice[];
  egress_allow: string[];
  egress_deny: string[];
  hardware?: ContainerHardwareCapabilities;
}

export interface ContainerEnvironmentPayload extends Record<string, unknown> {
  name: string;
  dockerfile?: string | null;
  template_name?: string | null;
  mounts?: ContainerMount[];
  gpu_request?: string;
  devices?: ContainerDevice[];
  egress_allow?: string[] | null;
  egress_deny?: string[] | null;
  start?: boolean;
}

export interface ContainerJob {
  job_id: string;
  state: 'queued' | 'running' | 'ready' | 'error';
  stage: string;
  message: string;
  detail?: string;
  logs?: Array<{ seq: number; stream: string; text: string }>;
}

export const containersApi = {
  list: () => api.get<ContainerListing>('/api/containers'),
  stats: () => api.get<ContainerStatsResponse>('/api/containers/stats'),
  configuration: (name: string) =>
    api.get<ContainerConfiguration>(`/api/containers/${encodeURIComponent(name)}/configuration`),
  create: (payload: ContainerEnvironmentPayload) =>
    api.post<{ ok: boolean; job_id: string }>('/api/containers', payload),
  update: (name: string, payload: ContainerEnvironmentPayload) =>
    api.put<{ ok: boolean; job_id: string }>(`/api/containers/${encodeURIComponent(name)}`, payload),
  job: (jobId: string, after: number = 0) =>
    api.get<ContainerJob>(`/api/containers/jobs/${encodeURIComponent(jobId)}`, { query: { after } }),
  action: (name: string, action: 'start' | 'stop' | 'restart') =>
    api.post<Record<string, unknown>>(`/api/containers/${encodeURIComponent(name)}/actions/${action}`),
  remove: (name: string) =>
    api.delete<void>(`/api/containers/${encodeURIComponent(name)}`, { query: { force: true } }),
  snapshot: (name: string, templateName: string, description: string = '') =>
    api.post<Record<string, unknown>>(`/api/containers/${encodeURIComponent(name)}/snapshot`, {
      template_name: templateName,
      description,
    }),
  removeTemplate: (name: string) =>
    api.delete<void>(`/api/container-templates/${encodeURIComponent(name)}`),
};
