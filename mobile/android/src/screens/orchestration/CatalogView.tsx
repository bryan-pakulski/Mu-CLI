// Catalog (web catalog.js renderList): tools by category — published, drafts and reviews, or every
// version — searchable; then runbooks.

import React, { useMemo, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet } from 'react-native';
import { orchestrationApi, type OrchToolBrief } from '../../api/orchestration';
import { orchState } from '../../theme/status';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, BoardSection, Loading, Row, Segmented, TextField } from '../../ui';
import { cap, plural } from './parts';
import { useOrchData } from './useOrchData';

export const OPEN_STATUSES = ['draft', 'testing', 'in_review'];
type Show = 'published' | 'open' | 'all';

/** Tools grouped by id, the head version per filter, grouped by category (web catalog.renderList). */
export function catalogGroups(tools: OrchToolBrief[], show: Show, q: string) {
  const byTool = new Map<string, OrchToolBrief[]>();
  tools.forEach(v => { if (!byTool.has(v.tool_id)) byTool.set(v.tool_id, []); byTool.get(v.tool_id)!.push(v); });
  byTool.forEach(list => list.sort((a, b) => a.version - b.version));
  const needle = q.trim().toLowerCase();
  const rows = [...byTool.values()].map(list => {
    const visible = list.filter(v => show === 'all' || (show === 'open' ? OPEN_STATUSES.includes(v.status) : ['published', 'deprecated'].includes(v.status)));
    return { visible, head: visible[visible.length - 1] || list[list.length - 1] };
  }).filter(t => t.visible.length && (!needle || [t.head.tool_id, t.head.title, t.head.summary, t.head.category].join(' ').toLowerCase().includes(needle)));
  const cats = new Map<string, typeof rows>();
  rows.forEach(t => { const c = t.head.category || 'uncategorised'; if (!cats.has(c)) cats.set(c, []); cats.get(c)!.push(t); });
  return [...cats.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

export function CatalogView({ openTool, openRunbook }: { openTool: (id: string, v: number) => void; openRunbook: (id: string, v: number) => void }) {
  const { colors } = useTheme();
  const [show, setShow] = useState<Show>('published');
  const [q, setQ] = useState('');
  const { data, error, refreshing, refresh } = useOrchData(() => orchestrationApi.tools(), ['tool']);
  const groups = useMemo(() => catalogGroups(data?.tools || [], show, q), [data, show, q]);
  return (
    <ScrollView contentContainerStyle={styles.pad} testID="orch-catalog" keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
      <Segmented<Show> accessibilityLabel="Show" value={show} onChange={setShow} options={[
        { value: 'published', label: 'Published', testID: 'orch-catalog-published' },
        { value: 'open', label: 'Drafts and reviews', testID: 'orch-catalog-open' },
        { value: 'all', label: 'All', testID: 'orch-catalog-all' },
      ]} />
      <TextField value={q} onChangeText={setQ} placeholder="Search tools" accessibilityLabel="Search tools" autoCapitalize="none" autoCorrect={false} testID="orch-catalog-search" />
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      {!data && !error ? <Loading label="Loading the catalog…" /> : null}
      {data ? (
        <>
          {groups.length ? groups.map(([cat, list]) => (
            <BoardSection key={cat} title={cap(cat)} testID={`orch-catalog-cat-${cat}`}>
              {list.map(t => (
                <Row key={t.head.tool_id} testID={`orch-tool-${t.head.tool_id}`} status={orchState('orch_tool_status', t.head.status, t.head.style).status} title={t.head.title}
                  sub={[t.head.tool_id, t.head.effect_style?.label, t.visible.map(v => `v${v.version}${v.status === 'published' ? '' : ` ${v.style?.label || v.status}`}`).join(', '), t.head.summary].filter(Boolean).join(' · ')}
                  onPress={() => openTool(t.head.tool_id, t.head.version)} />
              ))}
            </BoardSection>
          )) : <BoardSection title="Tools" empty={show === 'open' ? 'No open drafts. Start one in the Studio.' : 'No tools match. Try another filter, or install the starter pack on the computer.'} />}
          <BoardSection title="Runbooks" testID="orch-catalog-runbooks" empty="No runbooks yet. Write one in the Studio.">
            {data.runbooks.map(r => (
              <Row key={r.ref} testID={`orch-runbook-${r.ref}`} status={orchState('orch_tool_status', r.status, r.style).status} title={r.title}
                sub={[`${r.runbook_id} v${r.version}`, ['published', 'deprecated'].includes(r.status) ? '' : (r.style?.label || r.status), plural((r.steps || []).length, 'step'),
                  r.effect_style?.label, (r.problems || []).length ? plural(r.problems.length, 'problem') : ''].filter(Boolean).join(' · ')}
                onPress={() => openRunbook(r.runbook_id, r.version)} />
            ))}
          </BoardSection>
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.sm },
});
