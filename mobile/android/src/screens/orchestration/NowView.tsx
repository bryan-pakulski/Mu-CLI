// Now (web view.js renderOverview / nowRows): everything that needs you, in the order you must act —
// runs to approve, paused runs, unreachable executions, nodes to accept, reviews, offline nodes,
// reboots, an unconfigured endpoint — each with its decisive action. Then the fleet, dispatch and
// the catalog, recent runs and the audit trail. Approving goes through the server's action, so
// typed confirmation and policy apply exactly as on the run page.

import React from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchAction, type OrchAuditEntry, type OrchOverview } from '../../api/orchestration';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, Board, BoardSection, Button, Loading, Row, Text } from '../../ui';
import { NodeRow, OrchActionBar, RunRow, ago, byAI, plural, presence, useOrchActions } from './parts';
import { useOrchData } from './useOrchData';

const AUDIT_WORDS: Record<string, string> = {
  'run.approve': 'approved', 'run.reject': 'rejected', 'run.propose': 'proposed', 'run.start': 'started', 'run.cancel': 'cancelled',
  'run.continue': 'continued', 'run.rollback': 'rolled back', 'node.accept': 'accepted', 'node.enrol': 'enrolled', 'node.quarantine': 'quarantined',
  'tool.publish': 'published', 'tool.submit': 'submitted', 'dispatch.pause': 'paused dispatch', 'dispatch.resume': 'resumed dispatch',
};
const actorWord = (a: string) => (a === 'operator' ? 'You' : byAI(a) ? 'The AI' : /^node:/.test(a || '') ? a.slice(5) : (a ? a[0].toUpperCase() + a.slice(1) : 'System'));
export const auditWords = (e: OrchAuditEntry) => `${actorWord(e.actor)} ${AUDIT_WORDS[e.action] || String(e.action || '').replace(/[._]/g, ' ')}`;

/** The kill switch as an action: pausing is confirmed, resuming isn't (web setDispatch). */
export function dispatchAction(paused: boolean): OrchAction {
  return paused
    ? { id: 'resumeDispatch', label: 'Resume dispatch', enabled: true, reason: '', primary: true, danger: false, tone: 'ok', confirm: null, input: null } as unknown as OrchAction
    : { id: 'pauseDispatch', label: 'Pause all dispatch', enabled: true, reason: '', primary: false, danger: true, tone: 'danger',
        confirm: 'No new work is sent to any node until you resume. Executions already running on nodes keep going; cancel their runs to stop them.', input: null } as unknown as OrchAction;
}

export function rebootAction(names: string[]): OrchAction {
  return { id: 'nodeMaintenance', label: 'Propose reboots', enabled: true, reason: '', primary: false, danger: true, tone: 'danger',
    confirm: `${names.join(', ')} reboot one at a time, each only if it still needs one. The run waits for your approval as usual.` } as unknown as OrchAction;
}

export type NowProps = {
  overview: OrchOverview | null;
  onChanged: () => void;
  openRun: (id: string) => void;
  openNode: (id: string) => void;
  openTool: (id: string, version: number | string) => void;
  openRunbook: (id: string, version: number | string) => void;
  openTab: (tab: string) => void;
};

export function NowView({ overview: o, onChanged, openRun, openNode, openTool, openRunbook, openTab }: NowProps) {
  const { colors } = useTheme();
  const { data, error, refreshing, refresh, reload } = useOrchData(async () => {
    const [a, nodes, audit] = await Promise.all([
      orchestrationApi.approvals(),
      orchestrationApi.nodes().catch(() => ({ nodes: [] })),
      orchestrationApi.audit(10).catch(() => ({ entries: [] as OrchAuditEntry[] })),
    ]);
    return { a, nodes: nodes.nodes || [], audit: audit.entries || [] };
  }, ['node', 'tool', 'run']);
  const { run, element, busy } = useOrchActions();
  const done = () => { void reload(); onChanged(); };

  if (!o || !data) {
    return error ? <View style={styles.pad}><Banner tone="danger" glyph="✗" role="alert" title={error} /></View> : <View style={styles.pad}><Loading label="Loading orchestration…" /></View>;
  }
  const a = data.a;
  const paused = !!o.attention?.dispatch_paused;
  const kill = dispatchAction(paused);
  const reboot = o.reboot || { count: 0, nodes: [] };
  const firstRun = !o.nodes.total && !o.recent_runs.length;
  const fleet = data.nodes.slice().sort((x, y) => {
    const rank = (n: typeof x) => (n.status === 'pending' ? 0 : n.status === 'active' && presence(n) === 'offline' ? 1 : 2);
    const load = (n: typeof x) => Math.max(n.summary?.max_disk_used_pct ?? 0, n.summary?.memory_used_pct ?? 0);
    return rank(x) - rank(y) || load(y) - load(x);
  });
  const entries = data.audit.slice().sort((x, y) => (y.seq || 0) - (x.seq || 0)).slice(0, 10);

  const now: React.ReactNode[] = [];
  a.runs.forEach(r => {
    const deny = r.decision?.level === 'deny';
    now.push(<Row key={`run-${r.id}`} testID={`orch-now-run-${r.id}`} pinned status={deny ? 'failed' : 'waiting'} title={`${deny ? 'Denied' : 'Approve'} · ${r.title}`}
      sub={[r.blast_radius || plural(r.nodes || 0, 'node'), r.check_mode ? 'check mode' : '', r.effect_style?.label, r.decision?.summary || '', byAI(r.proposed_by) ? `proposed by the AI (${r.proposed_by.slice(3)})` : ''].filter(Boolean).join(' · ')}
      when={ago(r.created_at)} onPress={() => openRun(r.id)}
      actions={<OrchActionBar testID={`orch-now-run-${r.id}-actions`} actions={r.actions} only={['approveRun', 'rejectRun']} run={run} ctx={{ args: { run_id: r.id }, done }} />} />);
  });
  a.paused.forEach(r => {
    now.push(<Row key={`paused-${r.id}`} testID={`orch-now-paused-${r.id}`} pinned status="waiting" title={`Paused · ${r.title}`}
      sub={r.paused_reason || 'Waiting for you to continue, roll back or cancel.'} when={ago(r.created_at)} onPress={() => openRun(r.id)}
      actions={r.actions ? <OrchActionBar actions={r.actions} only={['continueRun', 'rollbackRun', 'cancelRun']} run={run} ctx={{ args: { run_id: r.id }, done }} />
        : <Button size="md" variant="ghost" label="Decide" navigates onPress={() => openRun(r.id)} />} />);
  });
  (o.unreachable || []).forEach(x => {
    now.push(<Row key={`unreach-${x.id}`} pinned status="unverified" title={`Unreachable · ${x.tool} on ${x.node_id}`}
      sub="The node dropped mid-run; this execution needs a decision." onPress={() => openRun(x.run_id)} />);
  });
  a.nodes.forEach(n => {
    now.push(<Row key={`node-${n.id}`} testID={`orch-now-node-${n.id}`} pinned status="waiting" title={`Accept · ${n.name || n.id}`}
      sub={[n.address, Object.keys(n.labels || {}).length ? Object.entries(n.labels).map(([k, v]) => `${k}=${v}`).join(', ') : 'no labels', n.summary?.os].filter(Boolean).join(' · ')}
      when={ago(n.created_at)} onPress={() => openNode(n.id)}
      actions={<OrchActionBar testID={`orch-now-node-${n.id}-actions`} actions={n.actions} only={['acceptNode', 'revokeNode']} run={run} ctx={{ args: { node_id: n.id }, done }} />} />);
  });
  a.reviews.forEach(t => {
    now.push(<Row key={`review-${t.ref}`} testID={`orch-now-review-${t.ref}`} pinned status="waiting" title={`Review · ${t.title}`}
      sub={[t.ref, t.effect_style?.label].filter(Boolean).join(' · ')} onPress={() => openTool(String(t.tool_id || t.ref.split('@')[0]), t.version)} />);
  });
  a.runbook_reviews.forEach(r => {
    now.push(<Row key={`rbreview-${r.ref}`} pinned status="waiting" title={`Review · ${r.title}`}
      sub={[r.ref, plural((r.steps || []).length, 'step'), r.effect_style?.label, (r.problems || []).join('; ')].filter(Boolean).join(' · ')}
      onPress={() => openRunbook(String(r.runbook_id || r.ref.split('@')[0]), r.version)}
      actions={r.actions ? <OrchActionBar actions={r.actions} only={['publishRunbook', 'requestRunbookChanges']} run={run} ctx={{ args: { ref: r.ref }, done }} /> : undefined} />);
  });
  if (o.attention?.offline_nodes) {
    now.push(<Row key="offline" status="failed" title={`${plural(o.attention.offline_nodes, 'node is', 'nodes are')} offline`} sub="Not seen for over 90 seconds." onPress={() => openTab('nodes')} />);
  }
  if (reboot.count) {
    const names = reboot.nodes.map(x => x.name);
    now.push(<Row key="reboot" testID="orch-now-reboot" pinned status="waiting" title={`${plural(reboot.count, 'node needs', 'nodes need')} a reboot`}
      sub={names.slice(0, 6).join(', ') + (reboot.count > 6 ? ` and ${reboot.count - 6} more` : '') + (reboot.nodes[0]?.reasons?.[0] ? ` · ${reboot.nodes[0].reasons[0]}` : '')}
      onPress={() => openTab('nodes')}
      actions={<Button size="md" variant="secondary" label="Reboot one at a time…" testID="orch-now-reboot-go" disabled={busy}
        onPress={() => { void run(rebootAction(names), { args: { node_ids: reboot.nodes.map(x => x.node_id), action: 'reboot', params: { only_if_required: true } }, done: res => {
          const id = (res.data?.run as { id?: string } | undefined)?.id; done(); if (id) openRun(id);
        } }); }} />} />);
  }
  if (!(o.network || { bind: 'x' }).bind) {
    now.push(<Row key="network" testID="orch-now-network" pinned status="waiting" title="Choose a network for nodes"
      sub="The node endpoint isn’t configured yet, so nodes can’t enrol. Set it on the computer: Orchestration › Settings › Network." onPress={() => openTab('settings')} />);
  }

  const c = o.catalog || { published: 0, drafts: 0, in_review: 0 };
  return (
    <ScrollView contentContainerStyle={styles.pad} testID="orch-now"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { refresh(); onChanged(); }} tintColor={colors.ink3} />}>
      {paused ? (
        <Banner tone="danger" glyph="✗" role="alert" testID="orch-dispatch-paused" title="Dispatch is paused: no new work is sent to any node."
          action={{ label: kill.label, onPress: () => { void run(kill, { args: {}, done }); }, testID: 'orch-kill-switch' }} />
      ) : null}
      {firstRun ? (
        <Banner tone="info" glyph="●" title="Set up your first node">Run reviewed tools across your machines. Enrol a node from Orchestration › Nodes on the computer; nothing runs without a plan you can read first.</Banner>
      ) : null}
      <Board>
        {now.length ? <BoardSection title="Needs you" count={now.length} testID="orch-now-needs">{now}</BoardSection> : null}
        {!firstRun ? (
          <>
            <BoardSection title="Fleet" testID="orch-now-fleet" empty="No nodes enrolled yet."
              right={<Button size="md" variant="ghost" label="All nodes" onPress={() => openTab('nodes')} />}>
              {fleet.slice(0, 8).map(n => <NodeRow key={n.id} node={n} onPress={() => openNode(n.id)} />)}
            </BoardSection>
            <View style={styles.dispatch} testID="orch-now-dispatch">
              <Text variant="label" accessibilityRole="header" tone="ink2">Dispatch</Text>
              <Text variant="title3" tone={paused ? 'danger' : 'ink'}>{paused ? 'Paused' : 'Dispatching'}</Text>
              <Text variant="caption" tone="ink3">{paused ? 'No new work goes to any node until you resume.' : 'Approved runs go out as soon as their batch is due.'}</Text>
              {!paused ? <Button size="md" variant="tonal" tone="danger" label={kill.label} icon="pause" testID="orch-kill-switch" disabled={busy} onPress={() => { void run(kill, { args: {}, done }); }} /> : null}
              <Text variant="caption" tone="ink2" testID="orch-now-catalog">
                {[plural(c.published || 0, 'published tool'), c.in_review ? `${c.in_review} in review` : '', c.drafts ? plural(c.drafts, 'draft') : ''].filter(Boolean).join(' · ')}
              </Text>
            </View>
            <BoardSection title="Recent runs" testID="orch-recent" empty="No runs yet. Start one from a published tool."
              right={<Button size="md" variant="ghost" label="All runs" onPress={() => openTab('runs')} />}>
              {o.recent_runs.slice(0, 8).map(r => <RunRow key={r.id} run={r} onPress={() => openRun(r.id)} />)}
            </BoardSection>
            <BoardSection title="Activity" testID="orch-activity" empty="Nothing recorded yet.">
              {entries.map(e => <Row key={e.seq} title={auditWords(e)} sub={e.subject} when={ago(e.at)}
                onPress={/^r_/.test(e.subject || '') ? () => openRun(e.subject) : /^n_/.test(e.subject || '') ? () => openNode(e.subject) : undefined} />)}
            </BoardSection>
          </>
        ) : null}
      </Board>
      {element}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.base },
  dispatch: { gap: spacing.xs, marginBottom: spacing.lg },
});
