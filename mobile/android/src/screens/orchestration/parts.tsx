// Shared orchestration pieces for the phone (documentation/plans/mobile_parity.md §6 Orchestration):
// the server-action runner (web ui.runAction), node and run rows (web nodes.nodeRow, runs.runRow)
// and the words they use. Every button is a server ActionVM (mu/orchestration/actions.py): the phone
// never decides what is allowed; disabled actions say why, typed confirmation is the server's.

import React, { useCallback, useRef, useState } from 'react';
import { orchestrationApi, type OrchAction, type OrchBusy, type OrchCommandResult, type OrchNode, type OrchRunBrief } from '../../api/orchestration';
import { ApiError } from '../../api/client';
import { orchState } from '../../theme/status';
import { ActionBar, Row, useServerActions, useToast, type ServerAction } from '../../ui';

/* ------------------------------------------------------------ words */

export const plural = (n: number, word: string, many?: string) => `${n} ${n === 1 ? word : (many || `${word}s`)}`;
export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
export const byAI = (who: string | null | undefined) => /^ai:/.test(String(who || ''));

/** "5m ago" from epoch seconds (web ui.ago). */
export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return '';
  const s = Math.max(0, Math.round(now - Number(ts)));
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** A node's presence (vm.node_presence); older payloads fall back to online/offline. */
export const presence = (n: Pick<OrchNode, 'presence' | 'online'>) => n.presence || (n.online ? 'online' : 'offline');
/** The node's state key: an active node that isn't online shows its presence (connecting / offline). */
export const nodeStateKey = (n: Pick<OrchNode, 'presence' | 'online' | 'status'>) => (n.status === 'active' && presence(n) !== 'online' ? presence(n) : n.status);
export const busyRunning = (n: Pick<OrchNode, 'busy' | 'status'>) => n.busy?.state === 'running' && n.status === 'active';

/** "Running Restart web +1" / "Queued: …" (web nodes.busyWord). */
export function busyWords(b: OrchBusy | null | undefined): string {
  if (!b) return '';
  const more = b.count + b.queued > 1 ? ` +${b.count + b.queued - 1}` : '';
  return b.state === 'running' ? `Running ${b.title}${more}` : `Queued: ${b.title}${more}`;
}

export const meterTone = (v: number) => (v >= 92 ? 'danger' : v >= 80 ? 'attention' : '');

/** One node's sub line: state word, what it's running, meters, reboot, agent drift, sandbox, policy errors. */
export function nodeSub(n: OrchNode): string {
  const key = nodeStateKey(n);
  const offline = key === 'offline';
  const parts = [
    key === 'active' ? '' : cap(offline ? 'offline' : key === 'connecting' ? 'connecting' : (n.style?.label || n.status)),
    busyWords(n.busy),
    n.summary?.max_disk_used_pct != null ? `disk ${Math.round(n.summary.max_disk_used_pct)}%` : '',
    n.summary?.memory_used_pct != null ? `mem ${Math.round(n.summary.memory_used_pct)}%` : '',
    n.summary?.reboot_required ? 'needs reboot' : '',
    n.agent?.outdated ? `agent ${n.agent_version} → ${n.agent.current}` : '',
    n.sandbox === 'none' ? 'no sandbox' : '',
    (n.policy_errors || []).length ? plural(n.policy_errors.length, 'policy error') : '',
  ];
  return parts.filter(Boolean).join(' · ');
}

/** The sign for a node row: offline fails, busy is active, else its status style. */
export function nodeSign(n: OrchNode): string {
  if (nodeStateKey(n) === 'offline') return 'failed';
  if (nodeStateKey(n) === 'connecting') return 'pending';
  if (busyRunning(n)) return 'active';
  return orchState('orch_node_status', n.status, n.style).status;
}

/** Run progress in words (web runs.progressWords). */
export function progressWords(r: OrchRunBrief): string {
  const n = r.summary?.nodes;
  if (!n) return plural(r.nodes || 0, 'node');
  const total = r.summary?.total_nodes || r.nodes || 0;
  return [`${n.done || 0} of ${total} done`, n.failed ? `${n.failed} failed` : '', n.active ? `${n.active} running` : ''].filter(Boolean).join(' · ');
}

export function runSub(r: OrchRunBrief): string {
  const st = orchState('orch_run_state', r.state, r.style).status;
  const stateWord = ['waiting', 'active', 'unverified'].includes(st) || r.state === 'paused' ? cap(r.style?.label || r.state || '') : '';
  return [stateWord, progressWords(r), r.kind === 'test' || r.kind === 'runbook_test' ? 'test' : '', r.check_mode ? 'check mode' : '',
    r.effect_style?.label || '', byAI(r.proposed_by) ? 'by the AI' : '', r.state === 'paused' && r.paused_reason ? r.paused_reason : '']
    .filter(Boolean).join(' · ');
}

export function RunRow({ run, onPress }: { run: OrchRunBrief; onPress: () => void }) {
  return <Row testID={`orch-run-row-${run.id}`} status={orchState('orch_run_state', run.state, run.style).status} title={run.title || run.id}
    sub={runSub(run)} when={ago(run.created_at)} onPress={onPress} accessibilityLabel={`${run.title}, ${run.style?.label || run.state}`} />;
}

export function NodeRow({ node, onPress, actions }: { node: OrchNode; onPress: () => void; actions?: React.ReactNode }) {
  const tags = [node.canary ? 'canary' : '', node.kind === 'container' ? 'test node' : ''].filter(Boolean).join(' · ');
  return <Row testID={`orch-node-${node.id}`} status={nodeSign(node)} title={`${node.name || node.id}${tags ? `  ·  ${tags}` : ''}`}
    sub={nodeSub(node) || undefined} when={node.last_seen ? ago(node.last_seen) : undefined} onPress={onPress} actions={actions}
    pinned={node.status === 'pending'} accessibilityLabel={`${node.name}, ${nodeStateKey(node)}${busyRunning(node) ? `, ${busyWords(node.busy)}` : ''}`} />;
}

/* ------------------------------------------------------------ actions */

export type OrchActionContext = {
  /** Args every command of this subject carries ({run_id}, {node_id}, {ref}). */
  args: Record<string, unknown>;
  /** After success (refetch, navigate). */
  done?: (result: OrchCommandResult, action: OrchAction) => void | Promise<void>;
  /** Actions the server marks with a `dialog` (labels, test, run, edit) open these. */
  dialogs?: Record<string, (action: OrchAction) => void>;
  /** Replace an action's flow on this screen (submit for review asks for a headline first). */
  overrides?: Record<string, (action: OrchAction) => void>;
};

/** Message of a failed command (the server's detail). */
export const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * Runs server actions like the web's runAction: disabled → its reason; a dialog → the screen's
 * dialog; input / confirm / typed confirmation → the kit dialog; then POST /api/orchestration/cmd.
 * Render `element` once.
 */
export function useOrchActions() {
  const toast = useToast();
  const { ask, element } = useServerActions();
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  const send = useCallback(async (type: string, args: Record<string, unknown>, label: string): Promise<OrchCommandResult | null> => {
    if (busyRef.current) return null;
    busyRef.current = true; setBusy(true);
    try {
      const result = await orchestrationApi.command(type, args);
      toast(`${label}: done`, 'ok');
      return result;
    } catch (err) {
      toast(`${label} failed: ${errorText(err)}`, 'danger');
      return null;
    } finally { busyRef.current = false; setBusy(false); }
  }, [toast]);

  const run = useCallback(async (action: OrchAction, ctx: OrchActionContext) => {
    if (action.enabled === false) { toast(`${action.label}: ${action.reason || 'not available now'}`, 'attention'); return null; }
    if (ctx.overrides?.[action.id]) { ctx.overrides[action.id](action); return null; }
    if (action.dialog) {
      const open = ctx.dialogs?.[action.dialog];
      if (open) open(action);
      else toast(`${action.label} isn’t available on this screen.`, 'attention');
      return null;
    }
    const extra = await ask(action as unknown as ServerAction);
    if (!extra) return null;
    const result = await send(action.id, { ...ctx.args, ...extra }, action.label);
    if (result) await ctx.done?.(result, action);
    return result;
  }, [ask, send, toast]);

  return { run, send, busy, element };
}

/** The server's actions for one subject as a bar (primary first, the rest under More). */
export function OrchActionBar({ actions, ctx, run, only, exclude, testID }: {
  actions: OrchAction[] | null | undefined; ctx: OrchActionContext; run: ReturnType<typeof useOrchActions>['run'];
  only?: string[]; exclude?: string[]; testID?: string;
}) {
  return <ActionBar testID={testID} actions={(actions || []) as unknown as ServerAction[]} only={only} exclude={exclude}
    onAction={a => { void run(a as unknown as OrchAction, ctx); }} />;
}

/** A 409 save conflict (someone else changed the draft): the server sends the current draft in `data`. */
export const isConflict = (err: unknown) => err instanceof ApiError && err.status === 409
  && !!(err.body && typeof err.body === 'object' && (err.body as { data?: { conflict?: unknown } }).data?.conflict);
