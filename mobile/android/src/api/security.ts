import { api } from './client';
import { ModeWorkspaceContract } from './modeWorkspace';

export interface SecurityFinding {
  finding_id: string;
  title: string;
  summary: string;
  vulnerability_class: string;
  severity: string;
  affected_paths: string[];
  exploit_path: string;
  references: string[];
  status: string;
  has_proof: boolean;
  proof_verified: boolean;
  proof_command: string;
  proof_description: string;
  proof_verified_at: number | null;
  has_remediation: boolean;
  remediation_verified: boolean;
  remediation_description: string;
  remediation_verified_at: number | null;
  patch_diff: string;
}

export interface SecurityReportInfo {
  scan_id: string;
  title: string;
  summary: string;
  status: string;
  directory: string;
  metadata_path: string;
  findings_total: number;
}

export interface SecuritySummary {
  scan_id: string;
  title: string;
  status: string;
  directory: string;
  metadata_path: string;
  findings_total: number;
  by_status: Record<string, number>;
  by_severity: Record<string, number>;
  approved: Array<{ finding_id: string; title: string; severity: string }>;
}

export interface SecurityState {
  active: boolean;
  report: SecurityReportInfo | null;
  findings: SecurityFinding[];
  summary: SecuritySummary | null;
  workspace: ModeWorkspaceContract;
}

export const securityApi = {
  getState: () => api.get<SecurityState>('/api/security/state'),
  approveFinding: (findingId: string) => api.post<{ ok: boolean; finding_id: string; status: string }>(`/api/security/findings/${encodeURIComponent(findingId)}/approve`),
  refuteFinding: (findingId: string, reason: string) => api.post<{ ok: boolean; finding_id: string; status: string }>(`/api/security/findings/${encodeURIComponent(findingId)}/refute`, { reason }),
};
