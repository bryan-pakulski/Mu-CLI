// Node groups on the phone (web: the Nodes dashboard's left-hand groups and the Groups tab;
// mu/orchestration: a group is a name, an optional label selector and explicit members — a node is
// in it when it is a member or the selector matches its labels).
//
//   GroupPicker      the dashboard's scope: All nodes · each group (with its health) · Ungrouped · + New group
//   useGroupMove     long-press / ⋯ on a node: Move to group… (a move when viewing a group), Remove from group
//   GroupSheet       create or edit a group: name (rename), description, selector (checked live), members
//   GroupRunSheet    Run on this group: pick a published tool or runbook, then the run sheet aimed at the group
//   GroupsView       the Groups tab: every group with its health, opened in GroupSheet; + New group
//
// Drag and drop doesn't suit a phone; the action sheet does the same job. Every change is a command
// (putGroup, addToGroup, removeFromGroup, renameGroup, deleteGroup), operator-only on the server.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import {
  orchestrationApi, type OrchGroupSummary, type OrchGroupsResponse, type OrchHealthCounts, type OrchRunbookBrief,
  type OrchScope, type OrchToolBrief,
} from '../../api/orchestration';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import {
  ActionSheet, Banner, Button, CheckRow, Field, Loading, PrimaryPill, Row, Rows, Sheet, Text, TextField,
  useServerActions, useToast, type MenuItem,
} from '../../ui';
import { RunSheet } from './forms';
import { errorText, plural } from './parts';
import { useOrchData } from './useOrchData';

/* ------------------------------------------------------------ words */

/** "1 critical · 2 warning" (zeros left out); "all healthy" when nothing is wrong. */
export function healthWords(h: OrchHealthCounts | null | undefined, nodes?: number): string {
  if (!h) return '';
  const parts = [h.critical ? `${h.critical} critical` : '', h.warning ? `${h.warning} warning` : '', h.pending ? `${h.pending} awaiting acceptance` : ''].filter(Boolean);
  if (parts.length) return parts.join(' · ');
  return nodes === 0 ? 'no nodes' : 'all healthy';
}

/** The worst level in a group's counts, as a status sign. */
export function healthSign(h: OrchHealthCounts | null | undefined): string {
  if (!h) return 'pending';
  if (h.critical) return 'failed';
  if (h.warning) return 'unverified';
  if (h.pending) return 'waiting';
  return 'done';
}

/** A scope's label: All nodes, Ungrouped or the group's name. */
export const scopeLabel = (scope: OrchScope) => (scope == null ? 'All nodes' : scope === '-' ? 'Ungrouped' : scope);

/* ------------------------------------------------------------ the scope picker */

export function GroupPicker({ scope, onScope, groups, fleet, ungrouped, onNew }: {
  scope: OrchScope; onScope: (s: OrchScope) => void; groups: OrchGroupSummary[];
  fleet: { nodes: number; health: OrchHealthCounts } | null; ungrouped: { nodes: number; health: OrchHealthCounts } | null; onNew: () => void;
}) {
  const chip = (key: string, value: OrchScope, label: string, count: number | undefined, h?: OrchHealthCounts) => {
    const on = scope === value;
    const flag = h?.critical ? ' ✗' : h?.warning ? ' ?' : '';
    return <Button key={key} size="md" variant={on ? 'tonal' : 'ghost'} selected={on} label={`${label}${count != null ? ` ${count}` : ''}${flag}`}
      accessibilityHint={h ? healthWords(h, count) : undefined} onPress={() => onScope(value)} testID={`orch-scope-${key}`} />;
  };
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips} accessibilityLabel="Groups" testID="orch-scopes">
      {chip('all', null, 'All nodes', fleet?.nodes, fleet?.health)}
      {groups.map(g => chip(`g-${g.name}`, g.name, g.name, g.nodes, g.health))}
      {ungrouped && ungrouped.nodes ? chip('ungrouped', '-', 'Ungrouped', ungrouped.nodes, ungrouped.health) : null}
      <Button size="md" variant="ghost" icon="plus" label="New group" onPress={onNew} testID="orch-scope-new" />
    </ScrollView>
  );
}

/* ------------------------------------------------------------ move a node */

/**
 * The node action sheet: Move to group… (from the group being viewed, so it's a move) and Remove
 * from the group. `open(node)`; render `element` once.
 */
export function useGroupMove({ scope, groups, onChanged, onNewGroup }: {
  scope: OrchScope; groups: OrchGroupSummary[]; onChanged: () => void; onNewGroup: (nodeId: string) => void;
}) {
  const toast = useToast();
  type Picked = { id: string; name: string; groups: string[] };
  // Two sheets, each with its own node: the kit's ActionSheet closes before it runs an item, so
  // the chooser can't read the menu's (already cleared) node.
  const [menuFor, setMenuFor] = useState<Picked | null>(null);
  const [chooseFor, setChooseFor] = useState<Picked | null>(null);
  const viewing = scope && scope !== '-' ? groups.find(g => g.name === scope) || null : null;

  const move = useCallback(async (n: Picked, target: string) => {
    const from = viewing && viewing.members.includes(n.id) ? viewing.name : undefined;
    try {
      await orchestrationApi.addToGroup(target, [n.id], from);
      toast(from ? `${n.name} moved to ${target}` : `${n.name} added to ${target}`, 'ok');
      onChanged();
    } catch (err) { toast(errorText(err), 'danger'); }
  }, [viewing, toast, onChanged]);

  const remove = useCallback(async (n: Picked) => {
    if (!viewing) return;
    try {
      const res = await orchestrationApi.removeFromGroup(viewing.name, [n.id]);
      const still = ((res.data.still_matched_by_selector as string[] | undefined) || []).includes(n.id);
      toast(still ? `${n.name} stays in ${viewing.name}: its labels match the group’s selector` : `${n.name} removed from ${viewing.name}`, still ? 'attention' : 'ok');
      onChanged();
    } catch (err) { toast(errorText(err), 'danger'); }
  }, [viewing, toast, onChanged]);

  const menu: MenuItem[] = menuFor ? [
    { key: 'move', label: viewing ? 'Move to another group…' : 'Add to group…', icon: 'folder', onPress: () => setChooseFor(menuFor) },
    ...(viewing ? [{ key: 'remove', label: `Remove from ${viewing.name}`, icon: 'x' as const, tone: 'danger' as const, onPress: () => { void remove(menuFor); } }] : []),
  ] : [];
  const targets: MenuItem[] = chooseFor ? [
    ...groups.filter(g => !(chooseFor.groups || []).includes(g.name)).map(g => ({
      key: `to-${g.name}`, label: g.name, icon: 'folder' as const, onPress: () => { void move(chooseFor, g.name); },
    })),
    { key: 'new', label: 'New group…', icon: 'plus', onPress: () => onNewGroup(chooseFor.id) },
  ] : [];

  const element = (
    <>
      <ActionSheet visible={!!menuFor} onClose={() => setMenuFor(null)} title={menuFor?.name} items={menu} />
      <ActionSheet visible={!!chooseFor} onClose={() => setChooseFor(null)} title={viewing ? `Move ${chooseFor?.name} to` : `Add ${chooseFor?.name} to`} items={targets} />
    </>
  );
  const open = useCallback((n: Picked) => { setChooseFor(null); setMenuFor(n); }, []);
  return { open, element };
}

/* ------------------------------------------------------------ create / edit */

type GroupForm = { name: string; description: string; selector: string; members: string[] };

/**
 * Create (`group` null) or edit a group: rename, description, label selector (checked as you type;
 * empty = members only) and the explicit members. Nodes the selector matches are in the group
 * whatever the checklist says, so they show as matched and can't be unticked.
 */
export function GroupSheet({ visible, group, initialMembers = [], onClose, onSaved }: {
  visible: boolean; group: OrchGroupSummary | null; initialMembers?: string[]; onClose: () => void; onSaved: (name: string | null) => void;
}) {
  const toast = useToast();
  const { ask, element } = useServerActions();
  const [form, setForm] = useState<GroupForm>({ name: '', description: '', selector: '', members: [] });
  const [nodes, setNodes] = useState<OrchGroupsResponse['nodes']>([]);
  const [matched, setMatched] = useState<string[]>([]);
  const [selectorError, setSelectorError] = useState('');
  const [q, setQ] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!visible) return;
    setForm(group ? { name: group.name, description: group.description, selector: group.selector, members: [...group.members] }
      : { name: '', description: '', selector: '', members: [...initialMembers] });
    setError(''); setQ('');
    orchestrationApi.groups().then(r => setNodes(r.nodes)).catch(() => setNodes([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, group]);

  // The selector, checked live: the nodes it reaches now, or why it isn't valid.
  useEffect(() => {
    if (!visible) return undefined;
    const text = form.selector.trim();
    // A changed selector drops the old verdict at once (Save isn't blocked by a stale error;
    // the server validates on save anyway), then the debounced check answers.
    setSelectorError('');
    if (!text) { setMatched([]); return undefined; }
    let alive = true;
    const t = setTimeout(() => {
      orchestrationApi.previewSelector(text).then(res => {
        if (!alive) return;
        const d = res.data as { ok?: boolean; error?: string; nodes?: Array<{ id: string }> };
        setSelectorError(d.ok === false ? d.error || 'This selector isn’t valid.' : '');
        setMatched(d.ok === false ? [] : (d.nodes || []).map(n => n.id));
      }).catch(err => { if (alive) { setSelectorError(errorText(err)); setMatched([]); } });
    }, 350);
    return () => { alive = false; clearTimeout(t); };
  }, [form.selector, visible]);

  // A message about the form goes once the form changes.
  useEffect(() => { setError(''); }, [form]);

  const needle = q.trim().toLowerCase();
  const shown = nodes.filter(n => !needle || [n.name, n.id, Object.entries(n.labels || {}).map(([k, v]) => `${k}=${v}`).join(' ')].join(' ').toLowerCase().includes(needle));
  const inGroup = new Set([...form.members, ...matched]);
  const toggle = (id: string, on: boolean) => setForm(f => ({ ...f, members: on ? [...f.members, id] : f.members.filter(m => m !== id) }));

  const save = async () => {
    setError('');
    const name = form.name.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name)) { setError('Group names use letters, digits, “-”, “_” and “.”.'); return; }
    if (selectorError) { setError(selectorError); return; }
    setBusy(true);
    try {
      let current = group?.name || null;
      if (group && name !== group.name) { await orchestrationApi.renameGroup(group.name, name); current = name; }
      await orchestrationApi.putGroup({ name, selector: form.selector.trim(), description: form.description.trim(), members: form.members });
      toast(group ? `${name} saved` : `${name} created`, 'ok');
      onSaved(current || name);
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };

  const remove = async () => {
    if (!group) return;
    const ok = await ask({ id: 'deleteGroup', label: 'Delete group', danger: true,
      confirm: `Delete ${group.name}? Its nodes stay enrolled; runs can no longer target the group.` });
    if (!ok) return;
    try {
      await orchestrationApi.deleteGroup(group.name);
      toast(`${group.name} deleted`, 'ok');
      onSaved(null);
    } catch (err) { toast(errorText(err), 'danger'); }
  };

  return (
    <Sheet visible={visible} onClose={onClose} title={group ? group.name : 'New group'} full testID="orch-group-sheet"
      footer={<>
        {group ? <Button variant="ghost" tone="danger" label="Delete" onPress={() => { void remove(); }} testID="orch-group-delete" /> : null}
        <Button variant="secondary" label="Cancel" onPress={onClose} />
        <Button variant="primary" label={group ? 'Save' : 'Create group'} loading={busy} onPress={() => { void save(); }} testID="orch-group-save" />
      </>}>
      <View style={styles.form}>
        <Field label="Name" hint="Letters, digits, “-”, “_” and “.”">
          <TextField value={form.name} onChangeText={name => setForm(f => ({ ...f, name }))} autoCapitalize="none" autoCorrect={false} testID="orch-group-name" />
        </Field>
        <Field label="Description" optional>
          <TextField value={form.description} onChangeText={description => setForm(f => ({ ...f, description }))} testID="orch-group-description" />
        </Field>
        <Field label="Label selector" optional error={selectorError || undefined}
          hint={form.selector.trim() ? `${plural(matched.length, 'node')} match now` : 'Empty: only the members you pick below. With a selector, matching nodes join automatically.'}>
          <TextField value={form.selector} onChangeText={selector => setForm(f => ({ ...f, selector }))} mono autoCapitalize="none" autoCorrect={false}
            placeholder="role=web,env!=prod" invalid={!!selectorError} testID="orch-group-selector" />
        </Field>
        <Text variant="label" accessibilityRole="header">Members {inGroup.size ? `· ${inGroup.size}` : ''}</Text>
        <TextField value={q} onChangeText={setQ} placeholder="Search nodes" accessibilityLabel="Search nodes" autoCapitalize="none" autoCorrect={false} testID="orch-group-search" />
        <View testID="orch-group-members">
          {shown.length ? shown.map(n => matched.includes(n.id) ? (
            <View key={n.id} style={styles.matched}>
              <CheckRow label={`${n.name} · matched by the selector`} checked disabled onChange={() => undefined} testID={`orch-group-member-${n.id}`} />
            </View>
          ) : (
            <CheckRow key={n.id} label={n.name} checked={form.members.includes(n.id)} onChange={on => toggle(n.id, on)} testID={`orch-group-member-${n.id}`} />
          )) : <Text variant="caption" tone="ink3">{nodes.length ? 'No nodes match.' : 'No nodes enrolled yet.'}</Text>}
        </View>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} testID="orch-group-error" /> : null}
      </View>
      {element}
    </Sheet>
  );
}

/* ------------------------------------------------------------ run on a group */

/** Pick a published tool or runbook, then the run sheet aimed at the group ({groups: [name]}). */
export function GroupRunSheet({ group, visible, onClose, onProposed }: {
  group: string; visible: boolean; onClose: () => void; onProposed: (runId: string, message: string) => void;
}) {
  const [catalog, setCatalog] = useState<{ tools: OrchToolBrief[]; runbooks: OrchRunbookBrief[] } | null>(null);
  const [pick, setPick] = useState<{ kind: 'tool' | 'runbook'; ref: string; inputs: unknown } | null>(null);
  const [q, setQ] = useState('');
  useEffect(() => {
    if (!visible) { setPick(null); return; }
    orchestrationApi.tools().then(setCatalog).catch(() => setCatalog({ tools: [], runbooks: [] }));
  }, [visible]);
  const needle = q.trim().toLowerCase();
  const published = useMemo(() => [
    ...(catalog?.tools || []).filter(t => t.status === 'published').map(t => ({ kind: 'tool' as const, ref: t.ref, title: t.title || t.ref, sub: t.summary || '' })),
    ...(catalog?.runbooks || []).filter(r => r.status === 'published').map(r => ({ kind: 'runbook' as const, ref: r.ref, title: r.title || r.ref, sub: 'runbook' })),
  ].filter(x => !needle || `${x.ref} ${x.title} ${x.sub}`.toLowerCase().includes(needle)), [catalog, needle]);

  const choose = async (kind: 'tool' | 'runbook', ref: string) => {
    const [id, v] = ref.split('@');
    try {
      const detail = kind === 'tool' ? await orchestrationApi.tool(id, v) : await orchestrationApi.runbook(id, v);
      const inputs = kind === 'tool' ? (detail as { manifest?: { inputs?: unknown } }).manifest?.inputs : (detail as { inputs?: unknown }).inputs;
      setPick({ kind, ref, inputs });
    } catch { setPick({ kind, ref, inputs: null }); }
  };

  if (pick) {
    return <RunSheet kind={pick.kind} target={pick.ref} inputs={pick.inputs as never} groups={[group]} visible={visible}
      onClose={onClose} onProposed={onProposed} />;
  }
  return (
    <Sheet visible={visible} onClose={onClose} title={`Run on ${group}`} full testID="orch-group-run">
      <View style={styles.form}>
        <Text variant="body" tone="ink2">Choose what to run on every node in {group}. You see the plan before anything is proposed.</Text>
        <TextField value={q} onChangeText={setQ} placeholder="Search tools and runbooks" accessibilityLabel="Search tools and runbooks" autoCapitalize="none" autoCorrect={false} testID="orch-group-run-search" />
        {!catalog ? <Loading label="Loading the catalog…" /> : (
          <Rows empty="No published tools or runbooks yet.">
            {published.map(x => <Row key={`${x.kind}-${x.ref}`} testID={`orch-group-run-${x.ref}`} title={x.title} sub={[x.ref, x.sub].filter(Boolean).join(' · ')}
              onPress={() => { void choose(x.kind, x.ref); }} />)}
          </Rows>
        )}
      </View>
    </Sheet>
  );
}

/* ------------------------------------------------------------ the Groups tab */

export function GroupsView() {
  const { colors } = useTheme();
  const { data, error, refreshing, refresh, reload } = useOrchData(() => orchestrationApi.groups(), ['node']);
  const [editing, setEditing] = useState<OrchGroupSummary | null>(null);
  const [creating, setCreating] = useState(false);
  const groups = data?.groups || [];
  return (
    <View style={{ flex: 1 }} testID="orch-groups">
      <ScrollView contentContainerStyle={styles.pad} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        {!data && !error ? <Loading label="Loading groups…" /> : null}
        {data ? (
          <>
            <Text variant="caption" tone="ink3">A group is a set of nodes runs can target. Pick members, or give it a label selector so matching nodes join on their own.</Text>
            <Rows label="Groups" empty="No groups yet. Create one, then add nodes from here or from Nodes (long-press a node).">
              {groups.map(g => (
                <Row key={g.name} testID={`orch-group-${g.name}`} status={healthSign(g.health)} title={g.name}
                  sub={[plural(g.nodes, 'node'), healthWords(g.health, g.nodes), g.selector ? `selector ${g.selector}` : 'members only', g.description].filter(Boolean).join(' · ')}
                  onPress={() => setEditing(g)} />
              ))}
            </Rows>
            {data.ungrouped.nodes ? <Text variant="caption" tone="ink3" testID="orch-groups-ungrouped">{plural(data.ungrouped.nodes, 'node')} in no group.</Text> : null}
          </>
        ) : null}
      </ScrollView>
      <PrimaryPill label="New group" onPress={() => setCreating(true)} testID="orch-groups-new" />
      <GroupSheet visible={creating || !!editing} group={editing} onClose={() => { setCreating(false); setEditing(null); }}
        onSaved={() => { setCreating(false); setEditing(null); void reload(); }} />
    </View>
  );
}

const styles = StyleSheet.create({
  chips: { gap: spacing.xs },
  form: { gap: spacing.sm },
  pad: { padding: spacing.base, paddingBottom: spacing['3xl'], gap: spacing.sm },
  matched: { opacity: 0.8 },
});
