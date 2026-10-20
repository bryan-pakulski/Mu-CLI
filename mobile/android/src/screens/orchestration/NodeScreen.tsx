// A node (web nodes.js renderDetail + renderMaintenance): its state in a sentence, what it's running
// now, the server's node actions, Maintenance — open runs here with their decisions inline, then
// update checks, upgrades, reboots and service control as reviewed runs, each with a dry run —
// the machine, labels and groups, the node-local policy (edited from here only when the node
// allows remote edits: validate → send, operator only), the agent and recent executions.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchAction, type OrchMaintenance, type OrchMaintenanceAction, type OrchNodeDetail } from '../../api/orchestration';
import { orchState } from '../../theme/status';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, BoardSection, Button, CheckRow, ErrorState, Field, KV, Loading, Meter, PageHeader, Row, Rows, Segmented, Sheet, Text, TextField, useToast, type StatePart } from '../../ui';
import { OrchActionBar, ago, busyRunning, cap, errorText, meterTone, nodeStateKey, plural, useOrchActions } from './parts';
import { useOrchData } from './useOrchData';

export const POLICY_ROWS: Array<[string, string]> = [
  ['max_effect', 'Highest effect'], ['max_privilege', 'Highest privilege'], ['allowed_run_as', 'Run as'],
  ['allowed_runtimes', 'Runtimes'], ['allowed_tools', 'Allowed tools'], ['denied_tools', 'Denied tools'],
  ['accept_categories', 'Categories'], ['quiet_hours', 'Quiet hours'], ['require_check_mode_first', 'Check mode first'],
  ['deny_all', 'Deny everything'], ['sandbox', 'Sandbox'],
];
export const listText = (v: unknown) => (Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v ?? ''));
const MAINT_DONE: Record<string, string> = { check_updates: 'Update check', security_updates: 'Security updates', full_upgrade: 'Upgrade', reboot: 'Reboot', service: 'Service action' };
const SERVICE_ACTIONS = ['restart', 'reload', 'start', 'stop'] as const;

export function nodeState(n: OrchNodeDetail): StatePart[] {
  const key = nodeStateKey(n);
  return [
    { text: cap(key === 'active' ? (n.style?.label || n.status) : key === 'offline' || key === 'connecting' ? key : (n.style?.label || key)),
      tone: n.status === 'pending' ? 'attention' : key === 'offline' || n.status === 'revoked' ? 'danger' : key === 'connecting' ? 'quiet' : '' },
    busyRunning(n) ? { text: 'working' } : null,
    n.canary ? { text: 'canary' } : null,
    n.kind ? { text: n.kind } : null,
    { text: Object.keys(n.labels || {}).length ? Object.entries(n.labels).map(([k, v]) => `${k}=${v}`).join(', ') : 'no labels' },
    n.last_seen ? { text: `seen ${ago(n.last_seen)}` } : null,
  ].filter(Boolean) as StatePart[];
}

export function OrchNodeScreen({ route, navigation }: { route: { params: { nodeId: string } }; navigation: any }) {
  const { colors } = useTheme();
  const toast = useToast();
  const nodeId = route.params.nodeId;
  const { data: n, error, refreshing, refresh, reload } = useOrchData(() => orchestrationApi.node(nodeId),
    c => (c.scope === 'node' && c.id === nodeId) || c.scope === 'run', [nodeId]);
  const maint = useOrchData(() => orchestrationApi.maintenance(nodeId), c => (c.scope === 'node' && c.id === nodeId) || c.scope === 'run' || c.scope === 'tool', [nodeId]);
  const { run, send, element, busy } = useOrchActions();
  const [labels, setLabels] = useState(false);
  const [policy, setPolicy] = useState(false);
  const back = { label: 'Orchestration', onPress: () => navigation.goBack() };
  const done = () => { void reload(); void maint.reload(); };
  const openRun = (runId: string) => navigation.navigate('OrchRun', { runId });

  if (!n) {
    return (
      <View style={{ flex: 1 }}>
        <PageHeader title="Node" back={back} />
        {error ? <ErrorState message={error} onRetry={() => { void reload(); }} /> : <View style={styles.pad}><Loading label="Loading node…" /></View>}
      </View>
    );
  }
  const s = n.summary || ({} as OrchNodeDetail['summary']);
  const rp = n.remote_policy || { allowed: false };
  const pend = rp.pending;
  const policyKeys = POLICY_ROWS.filter(([k]) => k in (n.policy || {}));

  const updateAgent = async () => {
    try {
      const res = await orchestrationApi.proposeAgentUpdate([nodeId]);
      const r = res.data.run as { id: string; state: string };
      toast(r.state === 'proposed' ? 'Update job proposed: approve it under Maintenance' : 'Update job started', 'ok');
      done();
    } catch (err) { toast(errorText(err), 'danger'); }
  };
  const stopTestNode: OrchAction = { id: 'stopTestNode', label: 'Stop test node', enabled: true, reason: '', primary: false, danger: true, tone: 'danger',
    confirm: 'The container is removed and the node is revoked.' } as unknown as OrchAction;

  return (
    <View style={{ flex: 1 }} testID="orch-node">
      <PageHeader testID="orch-node-head" title={n.name || n.id} back={back} state={nodeState(n)} />
      <ScrollView contentContainerStyle={styles.pad} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { refresh(); void maint.reload(); }} tintColor={colors.ink3} />}>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        {n.sandbox === 'none' ? <Banner tone="caution" glyph="◆" title="No sandbox">This node runs tools directly on the host (its local policy sets sandbox: none). Keep it to test or low-risk machines.</Banner> : null}
        {(n.policy_errors || []).length ? <Banner tone="danger" glyph="✗" title="The node’s local policy has errors">{`It refuses work until they’re fixed: ${n.policy_errors.join('; ')}`}</Banner> : null}
        {n.busy ? (
          <Rows testID="orch-node-busy">
            <Row pinned status={n.busy.state === 'running' ? 'active' : 'pending'} title={n.busy.title} when={n.busy.since ? ago(n.busy.since) : undefined}
              sub={[n.busy.state === 'running' ? `running here${n.busy.step_name && n.busy.step_name !== 'main' ? `: ${n.busy.step_name}` : ''}` : 'queued for the node’s next poll',
                n.busy.tool, n.busy.check_mode ? 'dry run' : '', n.busy.count + n.busy.queued > 1 ? `${n.busy.count + n.busy.queued - 1} more here` : ''].filter(Boolean).join(' · ')}
              onPress={() => openRun(n.busy!.run_id)} />
          </Rows>
        ) : null}
        <OrchActionBar testID="orch-node-actions" actions={n.actions} run={run}
          ctx={{ args: { node_id: n.id }, done, dialogs: { labels: () => setLabels(true) } }} />
        {n.kind === 'container' && n.status !== 'revoked' ? (
          <Button size="md" variant="tonal" tone="danger" label="Stop test node" disabled={busy} testID="orch-node-stop-test"
            onPress={() => { void run(stopTestNode, { args: { node_id: n.id }, done }); }} />
        ) : null}

        <Maintenance node={n} m={maint.data} error={maint.error} busy={busy} run={run} send={send} openRun={openRun} done={done} onEditPolicy={rp.allowed ? () => setPolicy(true) : undefined} />

        <BoardSection title="Machine" testID="orch-node-machine">
          <View style={styles.kv}>
            <Text variant="caption" tone="ink3">{n.facts_at ? `facts collected ${ago(n.facts_at)}` : 'facts not collected yet'}</Text>
            {s.max_disk_used_pct != null ? <Gauge label="Fullest disk" pct={s.max_disk_used_pct} /> : null}
            {s.memory_used_pct != null ? <Gauge label={`Memory${s.memory_gb != null ? ` of ${s.memory_gb} GB` : ''}`} pct={s.memory_used_pct} /> : null}
            {s.load != null ? <KV label={`Load (1 min)${s.cpus ? ` on ${plural(s.cpus, 'CPU')}` : ''}`}>{String(s.load)}</KV> : null}
            {s.uptime_days != null ? <KV label="Uptime">{`${s.uptime_days} d`}</KV> : null}
            {s.hostname ? <KV label="Hostname">{s.hostname}</KV> : null}
            {s.os ? <KV label="OS">{s.os}</KV> : null}
            {s.kernel ? <KV label="Kernel">{s.kernel}</KV> : null}
            {s.arch ? <KV label="Architecture">{s.arch}</KV> : null}
            {s.systemd != null ? <KV label="systemd">{s.systemd ? `yes${s.services != null ? ` · ${s.services} services` : ''}` : 'no'}</KV> : null}
            {n.address ? <KV label="Address"><Text variant="monoSm" selectable>{n.address}</Text></KV> : null}
          </View>
        </BoardSection>

        <BoardSection title="Labels and groups" testID="orch-node-labels">
          <View style={styles.kv}>
            <Text variant="body">{Object.keys(n.labels || {}).length ? Object.entries(n.labels).map(([k, v]) => `${k}=${v}`).join('  ') : 'No labels. Runs target nodes by label; set some from the actions.'}</Text>
            <Text variant="caption" tone="ink3">{(n.groups || []).length ? `In ${n.groups.join(', ')}` : 'In no group (groups are label selectors, set on the computer).'}</Text>
          </View>
        </BoardSection>

        <BoardSection title="Node-local policy" testID="orch-node-policy"
          right={rp.allowed ? <Button size="md" variant="ghost" label="Edit policy" onPress={() => setPolicy(true)} testID="orch-node-edit-policy" /> : <Text variant="caption" tone="ink3">Edited on the node</Text>}>
          <View style={styles.kv}>
            {pend?.state === 'pending' ? (
              <Banner tone="attention" glyph="◆" testID="orch-node-policy-pending" title={`An edit by ${pend.by || 'the operator'} is waiting for ${n.name} to check in (sent ${ago(pend.at)}).`}
                action={{ label: 'Cancel it', testID: 'orch-node-policy-cancel', onPress: () => { void send('cancelNodePolicy', { node_id: n.id }, 'Cancel policy edit').then(r => { if (r) done(); }); } }} />
            ) : null}
            {pend?.state === 'refused' ? <Text variant="caption" tone="danger">✗ {n.name} refused the last edit: {pend.reason || 'no reason given'}</Text> : null}
            {pend?.state === 'expired' ? <Text variant="caption" tone="attention">◆ The last edit expired before the node checked in.</Text> : null}
            {!rp.allowed && rp.reason ? <Text variant="caption" tone="ink3">{rp.reason}</Text> : null}
            {policyKeys.length ? policyKeys.map(([k, label]) => <KV key={k} label={label}>{listText((n.policy as Record<string, unknown>)[k])}</KV>)
              : <Text variant="caption" tone="ink3">The node hasn’t reported a policy yet.</Text>}
          </View>
        </BoardSection>

        <BoardSection title="Agent" testID="orch-node-agent">
          <View style={styles.kv}>
            <Text variant="body" tone={n.agent?.outdated ? 'attention' : 'ink'}>{n.agent_version ? `mucli-node ${n.agent_version}` : 'Version not reported yet'}{n.agent?.outdated ? ` · master has ${n.agent.current}` : n.agent_version ? ' · up to date' : ''}</Text>
            {n.agent?.outdated ? (n.agent.updatable
              ? <Button size="md" variant="tonal" tone="attention" label="Update this node" onPress={() => { void updateAgent(); }} testID="orch-node-update-agent" />
              : <Text variant="caption" tone="ink3">{n.agent.reason}</Text>) : null}
          </View>
        </BoardSection>

        <BoardSection title="Recent executions" testID="orch-node-execs" empty="Nothing has run here yet.">
          {(n.recent_executions || []).map(x => (
            <Row key={x.id} testID={`orch-node-exec-${x.id}`} status={orchState('orch_exec_state', x.state, x.style).status} title={x.tool}
              sub={[cap(x.style?.label || x.state || ''), x.step_name, x.exit_code == null ? '' : `exit ${x.exit_code}`, x.check_mode ? 'check mode' : '', x.rollback_of ? 'rollback' : '', x.reason].filter(Boolean).join(' · ')}
              when={ago(x.created_at)} onPress={() => openRun(x.run_id)} />
          ))}
        </BoardSection>
        <View style={styles.kv}>
          <KV label="Node id"><Text variant="monoSm" selectable>{n.id}</Text></KV>
          {n.created_at ? <KV label="Enrolled">{ago(n.created_at)}</KV> : null}
        </View>
      </ScrollView>
      <LabelsSheet visible={labels} node={n} onClose={() => setLabels(false)} onSaved={() => { setLabels(false); done(); }} />
      <PolicySheet visible={policy} node={n} onClose={() => setPolicy(false)} onSent={() => { setPolicy(false); done(); }} />
      {element}
    </View>
  );
}

function Gauge({ label, pct }: { label: string; pct: number }) {
  const v = Math.max(0, Math.min(100, Math.round(pct)));
  const tone = meterTone(v);
  return (
    <View style={styles.gauge} accessible accessibilityLabel={`${label} ${v}% used`}>
      <View style={styles.gaugeHead}><Text variant="caption" tone="ink2">{label}</Text><Text variant="label" tone={tone || 'ink'}>{v}%</Text></View>
      <Meter value={v} max={100} tone={tone || 'accent'} />
    </View>
  );
}

/* ------------------------------------------------------------ maintenance */

function Maintenance({ node, m, error, busy, run, send, openRun, done, onEditPolicy }: {
  node: OrchNodeDetail; m: OrchMaintenance | null; error: string; busy: boolean;
  run: ReturnType<typeof useOrchActions>['run']; send: ReturnType<typeof useOrchActions>['send'];
  openRun: (id: string) => void; done: () => void; onEditPolicy?: () => void;
}) {
  const toast = useToast();
  const [unit, setUnit] = useState('');
  const [svcAction, setSvcAction] = useState<typeof SERVICE_ACTIONS[number]>('restart');
  const [removals, setRemovals] = useState(false);
  const [onlyIf, setOnlyIf] = useState(false);
  if (error) return <BoardSection title="Maintenance" empty={error} testID="orch-maint" />;
  if (!m) return <BoardSection title="Maintenance" testID="orch-maint"><Loading label="Checking what this node allows…" /></BoardSection>;

  const status: string[] = [];
  const r = m.reboot || { required: null };
  if (r.required === null) status.push('Reboot status needs agent 1.2.0 or newer');
  else if (r.required) status.push(`Needs a reboot${r.reasons?.length ? `: ${r.reasons.join('; ')}` : ''}`);
  else status.push('No reboot needed');
  if (m.uptime_days != null) status.push(`up ${m.uptime_days} days`);
  const pend = m.pending_updates;
  status.push(pend ? (pend.count ? `${plural(pend.count, 'pending update')}, checked ${ago(pend.at)}` : `no pending updates, checked ${ago(pend.at)}`) : 'updates not checked yet');
  const installed = (m.actions || []).every(a => a.installed);

  const paramsFor = (key: string): Record<string, unknown> => (key === 'service' ? { unit: unit.trim(), action: svcAction }
    : key === 'full_upgrade' ? { allow_removals: removals } : key === 'reboot' ? { only_if_required: onlyIf } : {});
  const start = async (a: OrchMaintenanceAction, check: boolean) => {
    const params = paramsFor(a.key);
    if (a.key === 'service' && !params.unit) { toast('Choose a service first.', 'danger'); return; }
    const action = { id: 'nodeMaintenance', label: check ? 'Dry run' : (a.key === 'reboot' ? 'Propose reboot' : a.label), enabled: true, reason: '', primary: false,
      danger: a.key === 'reboot' && !check, tone: a.danger ? 'danger' : '',
      confirm: a.key === 'reboot' && !check ? `${node.name} reboots 30 seconds after the run is approved and is unreachable for a minute or two.${onlyIf ? ' It only reboots if it still needs one.' : ''}` : null } as unknown as OrchAction;
    await run(action, { args: { node_id: node.id, action: a.key, params, check_mode: check }, done: res => {
      const waiting = (res.data?.run as { state?: string } | undefined)?.state === 'proposed';
      toast(waiting ? `${check ? 'Dry run' : (MAINT_DONE[a.key] || 'Run')} proposed: approve it below` : (check ? 'Dry run started' : 'Started'), 'ok');
      done();
    } });
  };

  return (
    <View testID="orch-maint" style={styles.maint}>
      <Text variant="label" accessibilityRole="header" tone="ink2">Maintenance</Text>
      <Text variant="caption" tone="ink3">Updates, reboots and services, as reviewed runs.</Text>
      <Text variant="caption" tone={r.required ? 'attention' : 'ink2'} testID="orch-maint-status">{status.join(' · ')}</Text>
      {(m.open_runs || []).length ? (
        <BoardSection title="Open runs here" count={m.open_runs.length} testID="orch-maint-open">
          {m.open_runs.map(o => (
            <Row key={o.id} testID={`orch-open-run-${o.id}`} pinned status={orchState('orch_run_state', o.state, o.style).status} title={o.title} when={ago(o.created_at)}
              sub={[cap(o.style?.label || o.state), o.state === 'proposed' ? (o.decision?.summary || '') : `here: ${String(o.here).replace(/_/g, ' ')}`,
                o.progress && o.nodes > 1 ? o.progress : '', o.check_mode ? 'dry run' : '', o.paused_reason].filter(Boolean).join(' · ')}
              onPress={() => openRun(o.id)}
              actions={<OrchActionBar testID={`orch-open-run-${o.id}-actions`} actions={o.actions} only={['approveRun', 'rejectRun', 'continueRun', 'cancelRun']} run={run} ctx={{ args: { run_id: o.id }, done }} />} />
          ))}
        </BoardSection>
      ) : null}
      {!installed ? (
        <Banner tone="attention" glyph="◆" testID="orch-maint-install" title="The built-in maintenance tools aren’t installed yet."
          action={{ label: 'Install built-in tools', testID: 'orch-maint-install-go', onPress: () => { void send('installStarterPack', {}, 'Install built-in tools').then(res => { if (res) done(); }); } }}>
          They’re signed and published like any tool: reboot, security updates, full upgrade and service control, each with a dry run.
        </Banner>
      ) : null}
      <Rows>
        {(m.actions || []).map(a => {
          const blocked = !a.available;
          return (
            <Row key={a.key} testID={`orch-maint-${a.key}`} title={a.label} sub={blocked ? a.reason : a.help} status={blocked ? 'skipped' : undefined}
              actions={blocked ? (onEditPolicy && /max_effect|allowed_tools|denied_tools|accept_categories/.test(a.reason)
                ? <Button size="md" variant="ghost" label="Edit policy" onPress={onEditPolicy} /> : undefined) : (
                <View style={styles.maintDo}>
                  {a.key === 'service' ? (
                    <>
                      <TextField value={unit} onChangeText={setUnit} placeholder="unit, e.g. nginx" accessibilityLabel="Service" autoCapitalize="none" autoCorrect={false} maxLength={128} testID="orch-maint-unit" />
                      {m.services?.length ? <Text variant="caption" tone="ink3" numberOfLines={2}>Known: {m.services.slice(0, 12).join(', ')}</Text> : null}
                      <Segmented<typeof SERVICE_ACTIONS[number]> accessibilityLabel="Service action" value={svcAction} onChange={setSvcAction}
                        options={SERVICE_ACTIONS.map(x => ({ value: x, label: x }))} />
                    </>
                  ) : null}
                  {a.key === 'full_upgrade' ? <CheckRow label="Allow removals" checked={removals} onChange={setRemovals} /> : null}
                  {a.key === 'reboot' ? <CheckRow label="Only if needed" checked={onlyIf} onChange={setOnlyIf} testID="orch-maint-onlyif" /> : null}
                  <View style={styles.buttons}>
                    <Button size="md" variant={a.danger ? 'tonal' : 'secondary'} tone={a.danger ? 'danger' : undefined} disabled={busy}
                      label={a.key === 'check_updates' ? 'Check now' : a.label} onPress={() => { void start(a, false); }} testID={`orch-maint-${a.key}-run`} />
                    {a.check_available ? <Button size="md" variant="ghost" label="Dry run" disabled={busy} onPress={() => { void start(a, true); }} testID={`orch-maint-${a.key}-dry`} /> : null}
                  </View>
                </View>
              )} />
          );
        })}
      </Rows>
      {(m.recent || []).length ? (
        <BoardSection title="Recent maintenance" count={m.recent.length} testID="orch-maint-recent">
          {m.recent.map(x => <Row key={x.id} status={orchState('orch_exec_state', x.state, x.style).status} title={x.tool}
            sub={[cap(x.style?.label || x.state), x.check_mode ? 'dry run' : ''].filter(Boolean).join(' · ')} when={ago(x.finished_at || x.created_at)} onPress={() => openRun(x.run_id)} />)}
        </BoardSection>
      ) : null}
    </View>
  );
}

/* ------------------------------------------------------------ labels */

const formatLabels = (labels: Record<string, string>) => Object.entries(labels || {}).map(([k, v]) => `${k}=${v}`).join(', ');
/** "env=prod, role=web" → {env: 'prod', role: 'web'} (web ui.parseLabels). */
export function parseLabels(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  String(text || '').split(',').map(s => s.trim()).filter(Boolean).forEach(pair => {
    const i = pair.indexOf('=');
    if (i > 0) out[pair.slice(0, i).trim()] = pair.slice(i + 1).trim();
    else out[pair] = '';
  });
  return out;
}

function LabelsSheet({ visible, node, onClose, onSaved }: { visible: boolean; node: OrchNodeDetail; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [text, setText] = useState(formatLabels(node.labels));
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (visible) setText(formatLabels(node.labels)); }, [visible, node.labels]);
  const save = async () => {
    setBusy(true);
    try {
      await orchestrationApi.command('setNodeLabels', { node_id: node.id, labels: parseLabels(text) });
      toast('Labels saved', 'ok');
      onSaved();
    } catch (err) { toast(errorText(err), 'danger'); } finally { setBusy(false); }
  };
  return (
    <Sheet visible={visible} onClose={onClose} title={`Labels · ${node.name}`} testID="orch-labels"
      footer={<><Button variant="secondary" label="Cancel" onPress={onClose} /><Button variant="primary" label="Save labels" loading={busy} onPress={() => { void save(); }} testID="orch-labels-save" /></>}>
      <Field label="Labels" hint="Comma-separated key=value pairs. Runs target nodes by label.">
        <TextField value={text} onChangeText={setText} mono autoCapitalize="none" autoCorrect={false} testID="orch-labels-input" />
      </Field>
    </Sheet>
  );
}

/* ------------------------------------------------------------ policy (operator only) */

type PolicyCheck = { ok: boolean; errors: string[]; changes: Array<{ key: string; from: unknown; to: unknown }>; warnings: string[] };

/**
 * Edit policy.yaml on the master (web nodes.policyDialog): validateNodePolicy checks every edit
 * (errors, what changes, warnings); setNodePolicy sends it, signed, on the node's next poll. Never
 * offered to the AI; the node checks it again before installing.
 */
function PolicySheet({ visible, node, onClose, onSent }: { visible: boolean; node: OrchNodeDetail; onClose: () => void; onSent: () => void }) {
  const toast = useToast();
  const original = String(node.remote_policy?.text || '');
  const [text, setText] = useState(original);
  const [check, setCheck] = useState<PolicyCheck | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  useEffect(() => { if (visible) { setText(original); setCheck(null); setError(''); } }, [visible, original]);
  useEffect(() => {
    if (!visible) return undefined;
    const mine = ++seq.current;
    const timer = setTimeout(() => {
      orchestrationApi.command('validateNodePolicy', { node_id: node.id, text })
        .then(res => { if (mine === seq.current) { setCheck(res.data as unknown as PolicyCheck); setError(''); } })
        .catch(err => { if (mine === seq.current) { setCheck(null); setError(errorText(err)); } });
    }, 250);
    return () => clearTimeout(timer);
  }, [visible, text, node.id]);
  const same = text.trim() === original.trim();
  const fmt = (v: unknown) => (Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : v == null ? 'unset' : String(v));
  const ready = !!check?.ok && !same && !busy;
  const sendIt = async () => {
    setBusy(true);
    try {
      await orchestrationApi.command('setNodePolicy', { node_id: node.id, text });
      toast(`Policy sent; ${node.name} installs it on its next check-in`, 'ok');
      onSent();
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const changes = useMemo(() => check?.changes || [], [check]);
  return (
    <Sheet visible={visible} onClose={onClose} title={`Node policy · ${node.name}`} full testID="orch-policy"
      footer={<><Button variant="secondary" label="Cancel" onPress={onClose} /><Button variant="primary" label={`Send to ${node.name}`} disabled={!ready} loading={busy} onPress={() => { void sendIt(); }} testID="orch-policy-send" /></>}>
      <View style={styles.form}>
        <Field label={`policy.yaml on ${node.name}`} hint="The node checks it again before installing and keeps the previous file as policy.yaml.prev. Runs are still decided by the access policy.">
          <TextField value={text} onChangeText={setText} multiline mono autoCapitalize="none" autoCorrect={false} testID="orch-policy-text" />
        </Field>
        <View accessibilityLiveRegion="polite" testID="orch-policy-check" style={styles.kv}>
          {error ? <Text variant="caption" tone="danger">✗ {error}</Text> : null}
          {check && !check.ok ? check.errors.map((e, i) => <Text key={i} variant="caption" tone="danger">✗ {e}</Text>) : null}
          {check?.ok && same ? <Text variant="caption" tone="ink3">No changes yet.</Text> : null}
          {check?.ok && !same ? (changes.length ? changes.map(c => <KV key={c.key} label={c.key}>{`${fmt(c.from)} → ${fmt(c.to)}`}</KV>) : <Text variant="caption" tone="ink3">Formatting or comments only.</Text>) : null}
          {check?.ok && !same ? check.warnings.map((w, i) => <Text key={i} variant="caption" tone="attention">◆ {w}</Text>) : null}
        </View>
      </View>
    </Sheet>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  kv: { gap: spacing.xs },
  maint: { gap: spacing.sm, marginBottom: spacing.lg },
  maintDo: { gap: spacing.sm },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  gauge: { gap: spacing.xs },
  gaugeHead: { flexDirection: 'row', justifyContent: 'space-between' },
  form: { gap: spacing.md },
});
