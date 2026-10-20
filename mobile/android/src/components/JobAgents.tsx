// Agent harness UI: the New job sheet's per-stage agent picker and the Settings status card.

import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import {
  AGENT_LABELS, AGENT_NAMES, AGENT_STAGES, CODEX_IMPLEMENT_WARNING, agentProblem, agentsApi, defaultModel,
  type AgentName, type AgentStage, type AgentsStatus, type StageAgentDrafts,
} from '../api/agents';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, spacing } from '../theme/tokens';
import { Banner, Button, Field, KV, Loading, Panel, Segmented, Text, TextField, useToast } from '../ui';
import { STATUS } from '../theme/status';

export function useAgentsStatus(active: boolean) {
  const [status, setStatus] = useState<AgentsStatus | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const reload = useCallback(() => {
    setLoading(true);
    agentsApi.status().then(s => { setStatus(s); setError(''); })
      .catch(err => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { if (active) reload(); }, [active, reload]);
  return { status, setStatus, error, loading, reload };
}

const STAGE_LABEL: Record<AgentStage, string> = { plan: 'Plan', implement: 'Implement', review: 'Review' };

export type StageModelDefaults = Partial<Record<AgentStage, { provider?: string; model?: string }>>;

/** New job sheet: the one per-stage override picker — agent (defaults from Settings → Agent harnesses)
 * and its model. For mucli the model is a provider + model; blank uses Settings → Stage models, then
 * the session's model. */
export function StageAgentsField({ status, value, onChange, stageDefaults }: {
  status: AgentsStatus | null; value: StageAgentDrafts; onChange: (v: StageAgentDrafts) => void; stageDefaults?: StageModelDefaults;
}) {
  const { colors } = useTheme();
  const set = (stage: AgentStage, patch: Partial<{ agent: AgentName; model: string; provider: string }>) =>
    onChange({ ...value, [stage]: { ...value[stage], ...patch, touched: true } });
  return (
    <View style={styles.col} testID="stage-agents-field">
      <Text variant="sm" style={styles.strong}>Agent per stage</Text>
      {AGENT_STAGES.map(stage => {
        const draft = value[stage];
        const problem = agentProblem(status, draft.agent);
        const fallback = stageDefaults?.[stage];
        return (
          <Field key={stage} label={STAGE_LABEL[stage]} error={problem || undefined} testID={`stage-agent-${stage}`}>
            <Segmented<AgentName> accessibilityLabel={`${STAGE_LABEL[stage]} agent`} value={draft.agent}
              options={AGENT_NAMES.map(a => ({ value: a, label: AGENT_LABELS[a] }))} onChange={agent => set(stage, { agent, model: '' })} />
            {draft.agent !== 'mucli' ? (
              <TextField mono value={draft.model} onChangeText={model => set(stage, { model })}
                placeholder={defaultModel(status, draft.agent, stage) || 'default model'} accessibilityLabel={`${STAGE_LABEL[stage]} model`}
                autoCapitalize="none" autoCorrect={false} testID={`stage-agent-model-${stage}`} />
            ) : (
              <View style={styles.row}>
                <View style={styles.provider}>
                  <TextField mono value={draft.provider || ''} onChangeText={provider => set(stage, { provider })}
                    placeholder={fallback?.provider || 'provider'} accessibilityLabel={`${STAGE_LABEL[stage]} provider`}
                    autoCapitalize="none" autoCorrect={false} testID={`stage-provider-${stage}`} />
                </View>
                <View style={styles.grow}>
                  <TextField mono value={draft.model} onChangeText={model => set(stage, { model })}
                    placeholder={fallback?.model || 'session model'} accessibilityLabel={`${STAGE_LABEL[stage]} model`}
                    autoCapitalize="none" autoCorrect={false} testID={`stage-agent-model-${stage}`} />
                </View>
              </View>
            )}
          </Field>
        );
      })}
      {value.implement.agent === 'codex' ? <Banner tone="warn" glyph="◆" testID="codex-implement-warning" title={CODEX_IMPLEMENT_WARNING} /> : null}
      <Text variant="xs" tone="ink3">A blank model uses Settings → Stage models, then the session’s model. Claude Code and Codex run in the same sandbox with one copied credential.</Text>
    </View>
  );
}

/** Settings: Claude Code / Codex install + credential status and the setup-token slot. */
export function AgentsSettings({ active }: { active: boolean }) {
  const { colors } = useTheme();
  const toast = useToast();
  const { status, setStatus, error, loading } = useAgentsStatus(active);
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async (value: string) => {
    setBusy(true);
    try {
      setStatus(await agentsApi.saveSecret('CLAUDE_CODE_OAUTH_TOKEN', value));
      setToken('');
      toast(value ? 'Setup token saved' : 'Setup token cleared', 'ok');
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'danger');
    } finally { setBusy(false); }
  };
  const tokenWhere = status?.secrets?.CLAUDE_CODE_OAUTH_TOKEN || '';
  return (
    <Panel title="Agent harnesses" testID="agents-settings">
      {loading && !status ? <Loading label="Checking Claude Code and Codex…" /> : null}
      {error ? <Text variant="xs" tone="ink3">Unavailable: {error}</Text> : null}
      {status ? (
        <>
          <KV label="Defaults"><Text variant="xs">{AGENT_STAGES.map(s => `${s} ${AGENT_LABELS[status.defaults?.[s] as AgentName] || status.defaults?.[s] || 'mucli'}`).join(' · ')}</Text></KV>
          {(['claude', 'codex'] as const).map(agent => {
            const item = status[agent];
            if (!item) return null;
            return (
              <View key={agent} style={styles.col} testID={`agent-status-${agent}`}>
                <Text variant="sm" tone={item.ready ? 'ok' : 'attention'} style={styles.strong}>
                  {item.ready ? STATUS.done.icon : STATUS.waiting.icon} {item.label || AGENT_LABELS[agent]} · {item.ready ? 'ready' : 'not ready'}
                </Text>
                {item.binary ? <Text variant="xs" style={[styles.mono, { color: colors.ink3 }]} numberOfLines={2}>{item.version || 'unknown version'} · {item.binary}</Text> : null}
                {item.credential ? <Text variant="xs" tone="ink3">credential: {item.credential}{item.credential_source ? ` (${item.credential_source})` : ''}</Text> : null}
                {(item.problems || []).map((p, i) => <Text key={`p${i}`} variant="xs" tone="danger">✗ {p}</Text>)}
                {(item.warnings || []).map((w, i) => <Text key={`w${i}`} variant="xs" tone="attention">◆ {w}</Text>)}
                {!item.ready ? (status.setup?.[agent] || []).map((step, i) => <Text key={`s${i}`} variant="xs" selectable>→ {step}</Text>) : null}
              </View>
            );
          })}
          <Field label="Claude setup token" hint={tokenWhere ? `Set (${tokenWhere}). From \`claude setup-token\`; stored 0600 on the host.` : 'From `claude setup-token`; stored 0600 on the host, never shown again.'} optional>
            <TextField mono secureTextEntry value={token} onChangeText={setToken} placeholder={tokenWhere ? '••••••••' : 'sk-ant-oat…'}
              autoCapitalize="none" autoCorrect={false} accessibilityLabel="Claude setup token" testID="claude-token-input" />
          </Field>
          <View style={styles.rowEnd}>
            {tokenWhere === 'secrets.env' ? <Button small label="Clear" disabled={busy} onPress={() => void save('')} testID="claude-token-clear" /> : null}
            <Button small primary label="Save token" loading={busy} disabled={busy || !token.trim()} onPress={() => void save(token.trim())} testID="claude-token-save" />
          </View>
        </>
      ) : null}
    </Panel>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  strong: { fontWeight: fontWeights.semibold },
  flexStrong: { flex: 1, fontWeight: fontWeights.semibold },
  mono: { fontFamily: fonts.mono },
  monoShrink: { flexShrink: 1, fontFamily: fonts.mono },
  sectionGap: { fontWeight: fontWeights.semibold, marginTop: spacing.sm },
  intro: { marginBottom: spacing.sm },
  col: { gap: spacing.xs },
  row: { flexDirection: 'row', gap: spacing.sm },
  provider: { width: 110 },
  grow: { flex: 1 },
  rowEnd: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm },
});
