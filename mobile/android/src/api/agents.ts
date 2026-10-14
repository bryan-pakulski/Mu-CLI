// Agent harnesses (documentation/plans/agent_harnesses.md): each agent stage of a job runs on
// mucli, Claude Code or Codex. Types for GET /api/jobs/agents plus the pure helpers the New job
// sheet, the job header and Settings share. Secret values are never sent to the client.

import { api } from './client';

export type AgentName = 'mucli' | 'claude' | 'codex';
export type AgentStage = 'plan' | 'implement' | 'review';
export const AGENT_STAGES: AgentStage[] = ['plan', 'implement', 'review'];
export const AGENT_NAMES: AgentName[] = ['mucli', 'claude', 'codex'];
export const AGENT_LABELS: Record<AgentName, string> = { mucli: 'mucli', claude: 'Claude Code', codex: 'Codex' };
export const SECRET_KEYS = ['CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY'] as const;
export type SecretKey = typeof SECRET_KEYS[number];

export interface StageAgentChoice { agent: AgentName; model?: string; effort?: string }
export type StageAgents = Partial<Record<AgentStage, StageAgentChoice>>;

/** VM `stage_agents` on a job summary. */
export interface ResolvedStageAgent { agent: AgentName | string; model: string; effort: string; source: string; label: string }

export interface HarnessStatus {
  agent: string; label: string; ready: boolean; problems: string[]; warnings: string[];
  binary?: string; version?: string; binary_kind?: string; credential?: string; credential_source?: string;
  expires_at?: number | null; default_models?: Record<AgentStage, string>; models?: Record<AgentStage, string>;
}

export interface AgentsStatus {
  mucli: HarnessStatus; claude: HarnessStatus; codex: HarnessStatus;
  defaults: Record<AgentStage, AgentName>;
  /** Where each secret is set: '' (unset), 'environment' or 'secrets.env' — never the value. */
  secrets: Record<string, string>;
  setup: Record<string, string[]>;
}

export const agentsApi = {
  status: () => api.get<AgentsStatus>('/api/jobs/agents', { timeoutMs: 20_000 }),
  /** Store a harness secret on the host (an empty value clears it). */
  saveSecret: (key: SecretKey, value: string) => api.put<AgentsStatus>('/api/jobs/agents/secret', { key, value }),
};

/** One per-stage override: the agent and its model (for mucli also the provider → execution.stages). */
export interface StageAgentDraft { agent: AgentName; model: string; provider?: string; touched?: boolean }
export type StageAgentDrafts = Record<AgentStage, StageAgentDraft>;

export function draftsFromDefaults(defaults?: Partial<Record<AgentStage, string>> | null): StageAgentDrafts {
  const out = {} as StageAgentDrafts;
  for (const stage of AGENT_STAGES) {
    const agent = (defaults?.[stage] || 'mucli') as AgentName;
    out[stage] = { agent: AGENT_NAMES.includes(agent) ? agent : 'mucli', model: '' };
  }
  return out;
}

/** `execution.stage_agents` for the payload, or null when nothing was changed from the defaults. */
export function stageAgentsPayload(drafts: StageAgentDrafts, defaults?: Partial<Record<AgentStage, string>> | null): StageAgents | null {
  const base = draftsFromDefaults(defaults);
  const changed = AGENT_STAGES.some(s => drafts[s].touched || drafts[s].agent !== base[s].agent || drafts[s].model.trim());
  if (!changed) return null;
  const out: StageAgents = {};
  for (const stage of AGENT_STAGES) {
    const { agent, model } = drafts[stage];
    out[stage] = { agent, ...(model.trim() && agent !== 'mucli' ? { model: model.trim() } : {}) };
  }
  return out;
}

/** `execution.stages` for mucli stages with a model override (blank stages use Settings, then the session). */
export function stageModelsPayload(drafts: StageAgentDrafts): Partial<Record<AgentStage, { provider?: string; model: string }>> {
  const out: Partial<Record<AgentStage, { provider?: string; model: string }>> = {};
  for (const stage of AGENT_STAGES) {
    const { agent, model, provider } = drafts[stage];
    if (agent !== 'mucli' || !model.trim()) continue;
    out[stage] = { ...(provider?.trim() ? { provider: provider.trim() } : {}), model: model.trim() };
  }
  return out;
}

/** First problem of a not-ready harness (shown beside its choice), '' when ready. */
export function agentProblem(status: AgentsStatus | null | undefined, agent: AgentName): string {
  if (!status || agent === 'mucli') return '';
  const item = status[agent];
  if (!item || item.ready) return '';
  return item.problems?.[0] || `${AGENT_LABELS[agent]} is not ready`;
}

export function defaultModel(status: AgentsStatus | null | undefined, agent: AgentName, stage: AgentStage): string {
  if (agent === 'mucli') return '';
  return status?.[agent]?.models?.[stage] || status?.[agent]?.default_models?.[stage] || '';
}

export const CODEX_IMPLEMENT_WARNING =
  "Codex can't relay approvals: unless this job auto-approves writes, each Codex command goes through Codex's automatic " +
  'approval review, which may refuse to run project code such as tests.';

/** Job header text: `plan Claude Code claude-opus-5-5 · implement mucli · …` (null when all stages run on mucli). */
export function stageAgentsLabel(agents?: Partial<Record<string, ResolvedStageAgent>> | null): string | null {
  if (!agents) return null;
  const entries = AGENT_STAGES.map(s => [s, agents[s]] as const).filter(([, a]) => a);
  if (!entries.some(([, a]) => a && a.agent !== 'mucli')) return null;
  return entries.map(([s, a]) => `${s} ${a!.label || a!.agent}${a!.agent !== 'mucli' && a!.model ? ` ${a!.model}` : ''}`).join(' · ');
}
