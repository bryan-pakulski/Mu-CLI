// Orchestration settings on the phone (web settings.js), to read: the node endpoint, the access
// policy (ordered rules, first match wins), signing keys, secret names (never values), node agents,
// rollout defaults, schedules and groups. Changing them stays on the computer, where the web's
// validate → save flows run; nothing here mutates policy or secrets.

import React from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchAccessRule } from '../../api/orchestration';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, BoardSection, KV, Loading, Row, Text } from '../../ui';
import { plural } from './parts';
import { useOrchData } from './useOrchData';

const DECISION_SIGN: Record<string, string> = { allow: 'done', confirm: 'waiting', typed: 'waiting', deny: 'failed' };

/** A rule's decision in words: allow, confirm, typed confirmation or deny. */
export function decisionWord(d: OrchAccessRule['decision']): string {
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object' && (d as Record<string, unknown>).confirm === 'typed') return 'typed';
  return JSON.stringify(d);
}
const matchWords = (m: Record<string, unknown>) => Object.entries(m || {}).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`).join(' · ') || 'everything';

export function SettingsView() {
  const { colors } = useTheme();
  const { data: s, error, refreshing, refresh } = useOrchData(() => orchestrationApi.settings(), () => false);
  return (
    <ScrollView contentContainerStyle={styles.pad} testID="orch-settings"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
      <Text variant="caption" tone="ink3">To change these, open Orchestration › Settings on the computer running μcli.</Text>
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      {!s && !error ? <Loading label="Loading settings…" /> : null}
      {s ? (
        <>
          <BoardSection title="Network" testID="orch-set-network">
            <View style={styles.kv}>
              <KV label="Address">{s.network.bind ? `${s.network.bind}:${s.network.port}` : 'Not set'}</KV>
              <KV label="Endpoint">{s.endpoint.running ? 'Listening' : s.endpoint.error ? `Not running: ${s.endpoint.error}` : 'Not configured'}</KV>
              {s.endpoint.url ? <KV label="URL">{s.endpoint.url}</KV> : null}
              {s.endpoint.fingerprint ? <KV label="TLS fingerprint"><Text variant="monoSm" selectable>{s.endpoint.fingerprint}</Text></KV> : null}
            </View>
          </BoardSection>
          <BoardSection title="Access policy" count={s.access_policy.rules.length} testID="orch-set-access">
            {s.access_policy.rules.map((r, i) => {
              const d = decisionWord(r.decision);
              return <Row key={`${i}-${r.name}`} status={DECISION_SIGN[d] || 'pending'} title={`${i + 1}. ${r.name}`} sub={`${d} · when ${matchWords(r.match)}`} />;
            })}
          </BoardSection>
          <Text variant="caption" tone="ink3">{s.access_policy.default ? 'The default policy.' : `Version ${s.access_policy.version}.`} First match wins; each node is judged separately and the strictest result wins.</Text>
          <BoardSection title="Keys" testID="orch-set-keys">
            <View style={styles.kv}>
              {Object.entries(s.keys || {}).map(([name, k]) => <KV key={name} label={name === 'signing' ? 'Signing key' : name === 'test' ? 'Test key' : name}><Text variant="monoSm" selectable>{k.key_id}</Text></KV>)}
              {s.trusted_keys.length ? <KV label="Trusted">{plural(s.trusted_keys.length, 'key')}</KV> : null}
            </View>
          </BoardSection>
          <BoardSection title="Secrets" testID="orch-set-secrets" empty={s.secrets.locked ? 'The secret store is locked.' : 'No secrets yet.'}>
            {s.secrets.names.map(n => <Row key={n} title={n} sub="value hidden" />)}
          </BoardSection>
          <BoardSection title="Node agents" testID="orch-set-agents">
            <View style={styles.kv}>
              <KV label="Master serves">{s.agent_updates.current}</KV>
              <KV label="Automatic updates">{s.agent_updates.auto ? 'on' : 'off'}</KV>
            </View>
          </BoardSection>
          <BoardSection title="Rollout defaults" testID="orch-set-defaults">
            <View style={styles.kv}>
              {Object.entries(s.defaults.strategy || {}).map(([k, v]) => <KV key={k} label={k.replace(/_/g, ' ')}>{String(v)}</KV>)}
            </View>
          </BoardSection>
          <BoardSection title="Schedules" count={s.schedules.length} testID="orch-set-schedules" empty="No schedules.">
            {s.schedules.map((x, i) => <Row key={String(x.id || i)} status={x.enabled === false ? 'skipped' : 'pending'} title={String(x.name || x.id || 'Schedule')}
              sub={[x.target, x.cron || (x.interval_s ? `every ${x.interval_s}s` : ''), x.enabled === false ? 'off' : ''].filter(Boolean).join(' · ')} />)}
          </BoardSection>
          <BoardSection title="Groups" count={s.groups.length} testID="orch-set-groups" empty="No groups.">
            {s.groups.map((g, i) => <Row key={String(g.name || i)} title={String(g.name)} sub={String(g.selector || '')} />)}
          </BoardSection>
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.sm },
  kv: { gap: spacing.xs },
});
