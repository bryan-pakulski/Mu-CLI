// Job page tabs — Session, Plan, Diff, Review and Checks — ported from the
// planos web Task page (web/src/pages/task/*) and the mucli web jobs mode.

import React, { useMemo, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Markdown from 'react-native-markdown-display';

import type { CheckSuite, Finding, JobAction, JobVM } from '../api/jobCore';
import { JobPlanEditor, PlanCriteriaCards } from './JobPlanEditor';
import { planMarkdownStyles, planProse } from './planMarkdown';
import {
  Banner, CheckRow, CountBadge, Empty, Meter, Panel, Pill, PlanosButton, Pre, ResultBar, Segmented, Sparkline,
  StatusIcon, StatusPill, TextField,
} from './planos';
import { Text } from './Text';
import { actionLabel } from './JobActions';
import {
  FILE_STATUS_LABEL, countOf, listOf, parsePatch, runCounts, runtimeStatus, sessionItems, suiteCounts,
  type DiffFile, type SessionItem,
} from './jobViews';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, radii, spacing, tint, toneColor, touchTarget } from '../theme/tokens';
import {
  COVERAGE, SEVERITY, SEVERITY_ORDER, duration, stateStatus, stripAnsi, verdictStyle, when,
} from '../theme/status';

type Invoke = (idOrAction: string | JobAction, extra?: Record<string, unknown>) => void;

/* ================================================================ Session */

export function SessionTab({ job, invoke, busy }: { job: JobVM; invoke: Invoke; busy: boolean }) {
  const { colors } = useTheme();
  const [stage, setStage] = useState('');
  const [text, setText] = useState('');
  const data = job.session;
  const items = useMemo(() => sessionItems(data, stage), [data, stage]);
  const live = stateStatus(job.state) === 'active' && !!data?.live;
  const status = live ? runtimeStatus(data?.events) : '';
  const msg = job.actions.find(a => a.id === 'message');
  return (
    <View style={styles.col}>
      <View style={styles.wrap}>
        {job.state === 'needs_input'
          ? <Text variant="xs" style={{ color: colors.warn }}>◆ waiting for you</Text>
          : live ? <Text variant="xs" style={{ color: colors.running }}>● live session · the agent is working</Text>
            : <Text variant="xs" style={{ color: colors.muted }}>{stage ? `transcript of the ${stage} stage` : 'recent stage activity'}</Text>}
        <View style={{ flex: 1 }} />
        <Text variant="xs" style={{ color: colors.muted }}>{items.length} items</Text>
      </View>
      <Segmented
        accessibilityLabel="Session source"
        value={stage}
        onChange={setStage}
        options={[{ value: '', label: 'Live' }, { value: 'plan', label: 'Plan' }, { value: 'implement', label: 'Implement' }, { value: 'review', label: 'Review' }]}
      />
      {status ? <Text variant="xs" style={[styles.mono, { color: colors.muted }]} numberOfLines={2}>› {status}</Text> : null}
      <View accessibilityRole="list" accessibilityLabel="Agent session" style={styles.transcript} testID="job-transcript">
        {items.length ? items.map((it, i) => <TranscriptItem key={i} item={it} />) : <Empty>No session output yet.</Empty>}
      </View>
      {msg?.enabled ? (
        <View style={styles.composer}>
          <View style={{ flex: 1 }}>
            <TextField accessibilityLabel="Message the agent" placeholder="Message the agent…" value={text} onChangeText={setText} multiline style={{ minHeight: 44 }} />
          </View>
          <PlanosButton primary label="Send" disabled={busy || !text.trim()} onPress={() => { invoke('message', { text: text.trim() }); setText(''); }} testID="job-send-message" />
        </View>
      ) : <Text variant="xs" style={{ color: colors.muted }}>Messaging unavailable{msg?.reason ? `: ${msg.reason}` : ''}.</Text>}
    </View>
  );
}

/** planos transcript: assistant = raised + border with a squared top-left corner;
 * user = accent tint, right-aligned; tool calls fold to one mono line. */
export function TranscriptItem({ item }: { item: SessionItem }) {
  const { colors } = useTheme();
  const md = useMemo(() => planMarkdownStyles(colors), [colors]);
  const [open, setOpen] = useState(false);
  switch (item.kind) {
    case 'assistant':
      return (
        <View style={[styles.assistant, { backgroundColor: colors.raised, borderColor: colors.border }]}>
          <Markdown style={md}>{item.text || ' '}</Markdown>
        </View>
      );
    case 'user':
      return (
        <View accessibilityLabel={`You: ${item.text}`} style={[styles.user, { backgroundColor: colors.accentSubtle, borderColor: tint(colors.accent, 0.4) }]}>
          <Text variant="sm" selectable>{item.text}</Text>
        </View>
      );
    case 'tool':
    case 'tool_result': {
      const multi = !!item.text && item.text !== item.summary;
      return (
        <View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`${item.kind === 'tool' ? 'Tool call' : 'Tool result'}: ${item.summary}`}
            accessibilityState={{ expanded: open, disabled: !multi }}
            disabled={!multi}
            onPress={() => setOpen(v => !v)}
            style={({ pressed }) => [styles.fold, { backgroundColor: pressed ? colors.hover : 'transparent' }]}
          >
            <Text variant="xs" style={{ color: colors.muted, width: 12 }}>{multi ? (open ? '▾' : '▸') : ''}</Text>
            <Text variant="xs" style={[styles.mono, { color: item.kind === 'tool' ? colors.text : colors.muted, flex: 1 }]} numberOfLines={1}>
              {item.kind === 'tool' ? '› ' : '↳ '}{item.summary}
            </Text>
          </Pressable>
          {open && multi ? <Pre>{item.text}</Pre> : null}
        </View>
      );
    }
    case 'workbench': return <Pill tone="accent" glyph="⬢">{item.text}</Pill>;
    case 'error': return <Text variant="xs" style={{ color: colors.danger }}>✗ {item.text}</Text>;
    default: return <Text variant="xs" style={{ color: colors.muted }}>{item.text}</Text>;
  }
}

/* ================================================================ Plan */

export type PlanTabProps = {
  job: JobVM;
  editing: boolean;
  draft: string;
  dirty: boolean;
  historyVersion?: number;
  busy: boolean;
  onEdit: () => void;
  onChangeDraft: (text: string) => void;
  onSave: () => void;
  onCancel: () => void;
  onVersion: (version: number | undefined) => void;
};

export function PlanTab({ job, editing, draft, dirty, historyVersion, busy, onEdit, onChangeDraft, onSave, onCancel, onVersion }: PlanTabProps) {
  const { colors } = useTheme();
  const md = useMemo(() => planMarkdownStyles(colors), [colors]);
  const plan = job.plan;
  const editAction = job.actions.find(a => a.id === 'savePlanDraft');
  const latest = historyVersion === undefined;
  if (plan?.error) return <Banner tone="danger" glyph="✗" role="alert" title={plan.error} />;
  const markdown = historyVersion === undefined ? (plan?.markdown || '') : (plan?.markdown || '');
  return (
    <View style={styles.col}>
      <Text variant="xs" style={[styles.num, { color: colors.muted }]}>
        Version {plan?.version || 0} · approved v{plan?.approved_version || 0}{dirty ? ' · Unsaved edits' : ''}
      </Text>
      {(plan?.versions || []).length > 1 ? (
        <View style={styles.wrap} accessibilityLabel="Plan version">
          <PlanosButton small selected={latest} disabled={dirty} label="Latest" onPress={() => onVersion(undefined)} />
          {(plan?.versions || []).map(v => (
            <PlanosButton key={v} small selected={historyVersion === v} disabled={dirty} onPress={() => onVersion(v)}
              label={`v${v}${v === plan?.approved_version ? ' · approved' : ''}`} />
          ))}
        </View>
      ) : null}
      {plan?.draft ? <Text variant="xs" style={{ color: colors.warn }}>◆ unsaved draft — edit the plan to continue or save it</Text>
        : job.state === 'plan_review' ? <Text variant="xs" style={{ color: colors.warn }}>◆ waiting for your approval</Text> : null}
      {editing ? (
        <>
          <JobPlanEditor value={draft} onChange={onChangeDraft} />
          <View style={styles.wrap}>
            <PlanosButton primary label={editAction ? editAction.label : 'Save draft'} disabled={busy || !dirty} onPress={onSave} testID="plan-save" />
            <PlanosButton label="Done" onPress={onCancel} testID="plan-cancel" />
          </View>
        </>
      ) : (
        <>
          {editAction?.enabled && latest ? (
            <View style={styles.wrap}><PlanosButton label={actionLabel(editAction)} onPress={onEdit} testID="plan-edit" /></View>
          ) : null}
          {plan?.diff_from_previous ? <Collapsible title="Changes from the previous version"><Pre>{plan.diff_from_previous}</Pre></Collapsible> : null}
          {(plan?.errors || []).length ? <Banner tone="warn" glyph="◆" title={(plan?.errors || []).join('\n')} /> : null}
          <Panel>
            <Markdown style={md}>{planProse(markdown || plan?.draft || '') || '_Planning has not finished._'}</Markdown>
          </Panel>
          <PlanCriteriaCards markdown={markdown || plan?.draft || ''} />
        </>
      )}
    </View>
  );
}

function Collapsible({ title, children, initiallyOpen = false }: { title: string; children: React.ReactNode; initiallyOpen?: boolean }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <View>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(v => !v)} style={styles.disclosure}>
        <Text variant="xs" style={{ color: colors.muted }}>{open ? '▾' : '▸'} {title}</Text>
      </Pressable>
      {open ? children : null}
    </View>
  );
}

/* ================================================================ Diff */

export type DiffTabProps = {
  job: JobVM;
  viewed: Set<string>;
  onViewed: (path: string, viewed: boolean) => void;
  onFix: (findingId: string) => void;
  /** File to reveal (Jump to diff). */
  focus?: { file: string; line: number | null; seq: number } | null;
  onFileLayout?: (path: string, y: number) => void;
};

const MAX_LINES = 400;

export function DiffTab({ job, viewed, onViewed, onFix, focus, onFileLayout }: DiffTabProps) {
  const { colors } = useTheme();
  // While a job is active the server sends the current worktree diff (diff.live);
  // finished jobs show the checkpointed diff.patch.
  const live = job.diff?.live || null;
  const patch = live ? live.patch : job.diff?.patch;
  const files = useMemo(() => parsePatch(patch), [patch]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const findings = (job.review?.findings || []).filter(f => (f.disposition || 'open') === 'open');
  const fix = job.actions.find(a => a.id === 'reprompt');
  const markViewed = job.actions.find(a => a.id === 'markViewed');
  const liveHead = live ? (
    <View style={styles.wrap} testID="diff-live">
      <Pill tone="running" glyph="●" accessibilityLabel="live worktree diff">Live</Pill>
      <Text variant="xs" style={{ color: colors.muted }}>worktree vs base {live.base_sha.slice(0, 10)} · updated {when(live.generated_at)}</Text>
      {live.untracked_total || live.untracked.length ? <Text variant="xs" style={{ color: colors.muted }} testID="diff-live-untracked">{live.untracked_total ?? live.untracked.length} new file{(live.untracked_total ?? live.untracked.length) === 1 ? '' : 's'}</Text> : null}
      {live.truncated ? <Text variant="xs" style={{ color: colors.warn }} testID="diff-live-truncated">truncated · showing the first {Math.round(live.patch.length / 1000)} KB</Text> : null}
    </View>
  ) : null;
  if (!files.length) return <View style={styles.col}>{liveHead}<Empty>{live ? 'No changes in the worktree yet.' : 'No diff yet. The implementer’s changes appear here.'}</Empty></View>;
  const totalA = files.reduce((n, f) => n + f.added, 0);
  const totalR = files.reduce((n, f) => n + f.removed, 0);
  const isCollapsed = (f: DiffFile) => (focus?.file === f.path ? false : collapsed[f.path] ?? (!live && viewed.has(f.path)));
  return (
    <View style={styles.col}>
      {liveHead}
      <View style={styles.wrap}>
        <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{files.length} file{files.length === 1 ? '' : 's'}</Text>
        <Text variant="xs" style={{ color: colors.ok }}>+{totalA}</Text>
        <Text variant="xs" style={{ color: colors.danger }}>−{totalR}</Text>
        <View style={{ flex: 1 }} />
        {live ? null : <Text variant="xs" style={{ color: colors.muted }} testID="diff-viewed-count">{files.filter(f => viewed.has(f.path)).length}/{files.length} viewed</Text>}
      </View>
      <Panel flush accessibilityLabel="Changed files">
        {files.map(f => (
          <View key={f.path} style={[styles.treeRow, { borderBottomColor: colors.border }]}>
            <FileStatus status={f.status} />
            <Text variant="xs" style={[styles.mono, { flex: 1, color: viewed.has(f.path) ? colors.muted : colors.text }]} numberOfLines={1}>{f.path}</Text>
            <ChangeBar added={f.added} removed={f.removed} />
          </View>
        ))}
      </Panel>
      {files.map(f => {
        const closed = isCollapsed(f);
        const at = new Map<number, Finding[]>();
        findings.filter(x => x.file === f.path).forEach(x => { const k = Number(x.line_start || 0); at.set(k, [...(at.get(k) || []), x]); });
        const fileFindings = findings.filter(x => x.file === f.path);
        const shown = f.lines.slice(0, MAX_LINES);
        return (
          <View key={f.path} testID={`diff-file-${f.path}`} onLayout={e => onFileLayout?.(f.path, e.nativeEvent.layout.y)}
            style={[styles.diffFile, { borderColor: focus?.file === f.path ? colors.accent : colors.border, backgroundColor: colors.surface }]}>
            <View style={[styles.diffHead, { borderBottomColor: closed ? 'transparent' : colors.border }]}>
              <Pressable accessibilityRole="button" accessibilityState={{ expanded: !closed }} accessibilityLabel={`${f.path}, ${FILE_STATUS_LABEL[f.status]}`}
                onPress={() => setCollapsed(c => ({ ...c, [f.path]: !closed }))} style={[styles.row, { flex: 1, minHeight: touchTarget }]}>
                <Text variant="xs" style={{ color: colors.muted }}>{closed ? '▸' : '▾'}</Text>
                <FileStatus status={f.status} />
                <Text variant="xs" style={[styles.mono, { flex: 1 }]} numberOfLines={2}>{f.path}</Text>
              </Pressable>
              <Text variant="xs" style={{ color: colors.ok }}>+{f.added}</Text>
              <Text variant="xs" style={{ color: colors.danger }}>−{f.removed}</Text>
              {fileFindings.length ? <Text variant="xs" style={{ color: colors.warn }} accessibilityLabel={`${fileFindings.length} review findings`}>⚑ {fileFindings.length}</Text> : null}
              {live ? null : <CheckRow label="Viewed" accessibilityLabel={`Viewed ${f.path}`} checked={viewed.has(f.path)} disabled={markViewed ? !markViewed.enabled : false}
                onChange={v => { setCollapsed(c => { const n = { ...c }; delete n[f.path]; return n; }); onViewed(f.path, v); }} testID={`viewed-${f.path}`} />}
            </View>
            {closed ? null : f.binary ? <Empty>Binary file.</Empty> : (
              <View>
                {shown.map((l, i) => {
                  if (l.kind === 'hunk') return <Text key={i} variant="xs" style={[styles.mono, styles.hunk, { color: colors.running, backgroundColor: tint(colors.running, 0.08) }]}>{l.text}</Text>;
                  if (l.kind === 'note') return <Text key={i} variant="xs" style={[styles.mono, styles.codeLine, { color: colors.muted }]}>{l.text}</Text>;
                  const c = l.kind === 'add' ? colors.ok : l.kind === 'del' ? colors.danger : null;
                  const here = l.kind !== 'del' && l.newNo != null ? at.get(l.newNo) : undefined;
                  return (
                    <React.Fragment key={i}>
                      <View style={[styles.codeRow, c ? { backgroundColor: tint(c, 0.1) } : null]}>
                        <Text variant="xs" style={[styles.mono, styles.lineNo, { color: colors.muted }]}>{l.kind === 'del' ? l.oldNo : l.newNo}</Text>
                        <Text variant="xs" accessibilityLabel={l.kind === 'add' ? 'added' : l.kind === 'del' ? 'removed' : undefined} style={[styles.mono, styles.sign, { color: c || colors.muted }]}>{l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : ' '}</Text>
                        <Text variant="xs" selectable style={[styles.mono, styles.code, { color: colors.text }]}>{l.text}</Text>
                      </View>
                      {here?.map(x => <InlineFinding key={x.id} finding={x} fixEnabled={!!fix?.enabled} fixReason={fix?.reason} onFix={() => onFix(x.id)} />)}
                    </React.Fragment>
                  );
                })}
                {f.lines.length > MAX_LINES ? <Text variant="xs" style={{ color: colors.muted, padding: spacing.sm }}>{f.lines.length - MAX_LINES} more lines not shown on mobile.</Text> : null}
                {(at.get(0) || []).map(x => <InlineFinding key={x.id} finding={x} fixEnabled={!!fix?.enabled} fixReason={fix?.reason} onFix={() => onFix(x.id)} />)}
              </View>
            )}
          </View>
        );
      })}
    </View>
  );
}

function FileStatus({ status }: { status: DiffFile['status'] }) {
  const { colors } = useTheme();
  const c = status === 'A' ? colors.ok : status === 'D' ? colors.danger : status === 'R' ? colors.running : colors.warn;
  return (
    <View accessible accessibilityLabel={FILE_STATUS_LABEL[status]} style={[styles.fileStatus, { borderColor: tint(c, 0.5), backgroundColor: tint(c, 0.14) }]}>
      <Text variant="xs" style={[styles.mono, { color: c, fontWeight: fontWeights.bold }]}>{status}</Text>
    </View>
  );
}

function ChangeBar({ added, removed }: { added: number; removed: number }) {
  const { colors } = useTheme();
  const total = added + removed, blocks = 5;
  const a = total ? Math.round((added / total) * blocks) : 0;
  const r = total ? Math.min(blocks - a, Math.max(removed ? 1 : 0, blocks - a)) : 0;
  return (
    <View accessible accessibilityLabel={`${added} added, ${removed} removed`} style={styles.cbar}>
      {Array.from({ length: blocks }).map((_, i) => (
        <View key={i} style={[styles.cblock, { backgroundColor: i < a ? colors.ok : i < a + r ? colors.danger : colors.hover }]} />
      ))}
    </View>
  );
}

function InlineFinding({ finding, fixEnabled, fixReason, onFix }: { finding: Finding; fixEnabled: boolean; fixReason?: string; onFix: () => void }) {
  const { colors } = useTheme();
  const tone = SEVERITY[finding.severity] || 'muted';
  return (
    <View accessibilityRole="summary" accessibilityLabel={`Review finding: ${finding.severity}: ${finding.title}`}
      style={[styles.inlineFinding, { borderLeftColor: toneColor(colors, tone), backgroundColor: colors.raised, borderColor: colors.border }]}>
      <View style={styles.wrap}>
        <Text variant="xs" style={{ color: toneColor(colors, tone) }}>⚑</Text>
        <Pill tone={tone}>{finding.severity}</Pill>
        <Text variant="xs" style={{ flex: 1, fontWeight: fontWeights.semibold }}>{finding.title}</Text>
      </View>
      {finding.detail ? <Text variant="xs">{finding.detail}</Text> : null}
      {finding.suggestion ? <Text variant="xs" style={{ color: colors.muted }}>Suggestion: {finding.suggestion}</Text> : null}
      <View style={styles.row}>
        <PlanosButton small label="Fix this" disabled={!fixEnabled} accessibilityHint={fixEnabled ? 'Run a targeted implement pass that fixes only this finding' : fixReason} onPress={onFix} testID={`fix-this-${finding.id}`} />
      </View>
    </View>
  );
}

/* ================================================================ Review */

export type ReviewTabProps = {
  job: JobVM;
  selected: string[];
  onToggle: (id: string) => void;
  onFixSelected: () => void;
  onFixOne: (id: string) => void;
  onDisposition: (id: string, disposition: string) => void;
  onJump: (finding: Finding) => void;
  busy: boolean;
};

export function ReviewTab({ job, selected, onToggle, onFixSelected, onFixOne, onDisposition, onJump, busy }: ReviewTabProps) {
  const { colors } = useTheme();
  const md = useMemo(() => planMarkdownStyles(colors), [colors]);
  const report = job.review;
  const files = useMemo(() => new Set(parsePatch(job.diff?.patch).map(f => f.path)), [job.diff?.patch]);
  if (!report) return <Empty>Review report pending. The reviewer reports here after checks pass.</Empty>;
  const fix = job.actions.find(a => a.id === 'reprompt');
  const mark = job.actions.find(a => a.id === 'markFindings');
  const findings = report.findings || [];
  const [vTone, vLabel] = verdictStyle(report.verdict);
  const readiness = Number(report.merge_readiness || 0);
  return (
    <View style={styles.col}>
      <View style={styles.wrap}>
        <Pill tone={vTone} testID="review-verdict">{vLabel}</Pill>
        <Text variant="xs" style={{ color: colors.muted }}>merge readiness</Text>
        <View style={{ flex: 1, minWidth: 80 }}><Meter value={readiness} max={100} tone={readiness >= 80 ? 'ok' : readiness >= 50 ? 'warn' : 'danger'} accessibilityLabel={`Merge readiness ${readiness} of 100`} /></View>
        <Text variant="sm" style={[styles.num, { fontWeight: fontWeights.bold }]}>{readiness}/100</Text>
      </View>
      {Object.keys(report.counts || {}).length ? <Text variant="xs" style={{ color: colors.muted }}>{Object.entries(report.counts).map(([s, c]) => `${c} ${s}`).join(' · ')}</Text> : null}
      {findings.length ? (
        <View style={styles.col}>
          <Text variant="xs" style={{ color: colors.muted }}>Pick findings to fix in one targeted implement run (checks and review follow); the plan stays as it is.</Text>
          <View style={styles.row}>
            <PlanosButton primary label={`Fix ${selected.length || ''} selected`} disabled={busy || !fix?.enabled || !selected.length}
              accessibilityHint={fix?.enabled ? 'Fix the selected findings' : fix?.reason || 'not available'} onPress={onFixSelected} testID="review-fix-selected" />
          </View>
        </View>
      ) : null}
      <Panel title="Summary">
        <Markdown style={md}>{report.summary || '_No summary._'}</Markdown>
      </Panel>
      <Panel title="Plan adherence">
        {(report.plan_coverage || []).length ? report.plan_coverage.map(c => (
          <View key={c.criterion_id} style={styles.coverage}>
            <StatusIcon status={COVERAGE[c.status] || 'pending'} label={c.status} />
            <Text variant="xs" style={[styles.mono, { fontWeight: fontWeights.semibold }]}>{c.criterion_id}</Text>
            <Text variant="xs" style={{ flex: 1 }}>{c.evidence || c.status}</Text>
          </View>
        )) : <Empty>No acceptance criteria were assessed.</Empty>}
      </Panel>
      {(report.out_of_scope_changes || []).length ? (
        <Banner tone="warn" glyph="◆" title="Out-of-scope changes">
          {listOf(report.out_of_scope_changes).map((x, i) => <Text key={i} variant="xs">• {x}</Text>)}
        </Banner>
      ) : null}
      {findings.length ? null : <Empty status="done">No findings.</Empty>}
      {SEVERITY_ORDER.map(sev => {
        const list = findings.filter(f => f.severity === sev);
        if (!list.length) return null;
        const tone = SEVERITY[sev];
        return (
          <View key={sev} style={styles.col} testID={`review-severity-${sev}`}>
            <View style={styles.row}>
              <Text variant="sm" accessibilityRole="header" style={{ color: toneColor(colors, tone), fontWeight: fontWeights.semibold, textTransform: 'capitalize' }}>{sev}</Text>
              <CountBadge tone={tone}>{list.length}</CountBadge>
            </View>
            {list.map(f => {
              const closed = (f.disposition || 'open') !== 'open';
              const picked = selected.includes(f.id);
              return (
                <View key={f.id} testID={`finding-${f.id}`} style={[styles.findingCard, { borderColor: colors.border, borderLeftColor: toneColor(colors, tone), backgroundColor: colors.surface, opacity: closed ? 0.7 : 1 }]}>
                  <View style={styles.wrap}>
                    <CheckRow label="" accessibilityLabel={`select “${f.title}” for fixing`} checked={picked} disabled={closed} onChange={() => onToggle(f.id)} />
                    <Pill tone={tone}>{f.severity}</Pill>
                    <Text variant="sm" style={{ flex: 1, fontWeight: fontWeights.semibold }}>{f.title}</Text>
                  </View>
                  <Text variant="xs" style={[styles.mono, { color: colors.muted }]}>{f.file || 'no file'}{f.line_start ? `:${f.line_start}` : ''}{f.category ? ` · ${f.category}` : ''}{f.confidence != null ? ` · confidence ${f.confidence}` : ''}</Text>
                  {f.detail ? <Markdown style={md}>{f.detail}</Markdown> : null}
                  {f.suggestion ? <View><Text variant="xs" style={{ color: colors.muted }}>Suggestion</Text><Markdown style={md}>{f.suggestion}</Markdown></View> : null}
                  <View style={styles.wrap}>
                    {f.file && files.has(f.file)
                      ? <PlanosButton small label="◎ Jump to diff" onPress={() => onJump(f)} testID={`jump-${f.id}`} />
                      : <Text variant="xs" style={{ color: colors.muted }}>not in diff</Text>}
                    <PlanosButton small label="Fix" disabled={busy || !fix?.enabled || closed} accessibilityHint={fix?.enabled ? 'Fix only this finding' : fix?.reason} onPress={() => onFixOne(f.id)} testID={`fix-${f.id}`} />
                  </View>
                  <Segmented
                    accessibilityLabel={`Disposition of ${f.title}`}
                    value={(f.disposition || 'open') as string}
                    onChange={d => { if (mark?.enabled && d !== (f.disposition || 'open')) onDisposition(f.id, d); }}
                    options={['open', 'accepted', 'dismissed', 'fixed'].map(d => ({ value: d, label: d }))}
                  />
                </View>
              );
            })}
          </View>
        );
      })}
    </View>
  );
}

/* ================================================================ Checks */

export function ChecksTab({ job, invoke, busy }: { job: JobVM; invoke: Invoke; busy: boolean }) {
  const { colors } = useTheme();
  const runs = useMemo(() => (job.checks?.runs || []).slice().reverse(), [job.checks?.runs]);
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [caseFilter, setCaseFilter] = useState('');
  const [newOnly, setNewOnly] = useState(false);
  const recheck = job.actions.find(a => a.id === 'recheck');
  const live = job.checks?.live || [];
  const running = job.state === 'checking';
  const selected = runs.find(r => r.run === selectedRun) || runs[0];
  const failedCases = (job.checks?.failed_cases || []).filter(c => (!newOnly || c.new) &&
    (!caseFilter.trim() || `${c.suite} ${c.name} ${c.id} ${c.classname || ''} ${c.message}`.toLowerCase().includes(caseFilter.trim().toLowerCase())));
  const history = job.checks?.history || {};
  return (
    <View style={styles.col}>
      <View style={styles.wrap}>
        <View style={{ flex: 1, minWidth: 160 }}>
          <Text variant="sm">
            {running ? <Text variant="sm" style={{ color: colors.running }}>● running · </Text> : null}
            {runs[0] ? `last: run ${runs[0].run} ${runs[0].passed ? 'passed' : 'failed'}` : 'No checks have run yet.'}
          </Text>
          <Text variant="xs" style={{ color: colors.muted }}>fix attempts {job.fix_attempts || 0} · checks come from the approved plan</Text>
        </View>
        {recheck ? <PlanosButton label={actionLabel(recheck)} disabled={busy} ariaDisabled={!recheck.enabled} accessibilityHint={recheck.reason} onPress={() => invoke(recheck)} testID="checks-recheck" /> : null}
      </View>
      {live.length && running ? (
        <Panel title="Live output" glyph="●" glyphTone="running">
          <Pre>{stripAnsi(live.map(e => `[${(e.payload as Record<string, unknown>)?.suite || 'check'}${(e.payload as Record<string, unknown>)?.baseline ? ' · baseline' : ''}] ${(e.payload as Record<string, unknown>)?.text || ''}`).join('')).slice(-8000)}</Pre>
        </Panel>
      ) : null}
      {runs.length ? (
        <Panel title="Runs" badge={runs.length} flush testID="checks-runs">
          {runs.map(r => {
            const k = runCounts(r);
            const on = r === selected;
            const nf = countOf(r.new_failures);
            return (
              <Pressable key={r.run} accessibilityRole="button" accessibilityState={{ selected: on }} accessibilityLabel={`Run ${r.run}, ${r.passed ? 'passed' : 'failed'}`}
                onPress={() => setSelectedRun(r.run)} testID={`checks-run-${r.run}`}
                style={({ pressed }) => [styles.runRow, { borderBottomColor: colors.border, backgroundColor: on ? colors.accentSubtle : pressed ? colors.hover : 'transparent' }]}>
                <View style={styles.row}>
                  <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>Run {r.run}</Text>
                  <StatusPill status={r.passed ? 'done' : 'failed'} label={r.passed ? 'passed' : 'failed'} />
                  {r.trigger ? <Pill tone="muted">{r.trigger}</Pill> : null}
                  <View style={{ flex: 1 }} />
                  <Text variant="xs" style={[styles.num, { color: colors.muted }]}>{r.duration_ms != null ? duration(r.duration_ms) : ''}</Text>
                </View>
                <View style={styles.row}>
                  <View style={{ flex: 1 }}><ResultBar passed={k.passed} failed={k.failed} skipped={k.skipped} /></View>
                  <Text variant="xs" style={styles.num}>{k.passed}✓ {k.failed ? <Text variant="xs" style={{ color: colors.danger }}>{k.failed}✗</Text> : null}</Text>
                </View>
                <Text variant="xs" style={[styles.num, { color: colors.muted }]}>
                  <Text variant="xs" style={{ color: nf ? colors.danger : colors.muted }}>{nf} new</Text> · {countOf(r.preexisting_failures)} pre-existing{r.head_sha ? ` · ${r.head_sha.slice(0, 12)}` : ''}{r.started_at ? ` · ${when(r.started_at)}` : ''}
                </Text>
              </Pressable>
            );
          })}
        </Panel>
      ) : <Empty>No builds or test runs yet.</Empty>}
      {selected ? (() => {
        const k = runCounts(selected);
        const newList = listOf(selected.new_failures);
        const pre = listOf(selected.preexisting_failures);
        return (
          <Panel title={`Run ${selected.run}`} right={<StatusPill status={selected.passed ? 'done' : 'failed'} label={selected.passed ? 'passed' : 'failed'} />} testID="checks-selected-run">
            <ResultBar large passed={k.passed} failed={k.failed} skipped={k.skipped} />
            <View style={styles.wrap}>
              <Text variant="xs" style={{ color: colors.ok }}>✓ {k.passed} passed</Text>
              <Text variant="xs" style={{ color: k.failed ? colors.danger : colors.muted }}>✗ {k.failed} failed</Text>
              {k.skipped ? <Text variant="xs" style={{ color: colors.muted }}>– {k.skipped} skipped</Text> : null}
              <Text variant="xs" style={{ color: colors.muted }}>of {k.total}</Text>
            </View>
            {newList.length ? <View><Text variant="xs" style={{ color: colors.danger, fontWeight: fontWeights.semibold }}>New failures ({newList.length})</Text>{newList.map((f, i) => <Text key={i} variant="xs" style={[styles.mono, { color: colors.danger }]}>✗ {f}</Text>)}</View> : null}
            {pre.length ? <View><Text variant="xs" style={{ fontWeight: fontWeights.semibold }}>Pre-existing on the base commit ({pre.length})</Text><Text variant="xs" style={{ color: colors.muted }}>These also fail before the change, so they are reported to the reviewer and not sent back to the implementer.</Text>{pre.map((f, i) => <Text key={i} variant="xs" style={[styles.mono, { color: colors.muted }]}>{f}</Text>)}</View> : null}
            {(selected.suites || []).map(s => <SuiteRow key={s.suite} suite={s} />)}
          </Panel>
        );
      })() : null}
      {job.checks?.failed_cases ? (
        <Panel title="Failed test cases" badge={(job.checks.failed_cases || []).length} badgeTone={(job.checks.failed_cases || []).length ? 'danger' : ''} testID="checks-failed-cases">
          <TextField accessibilityLabel="Filter failed test cases" placeholder="Filter cases…" value={caseFilter} onChangeText={setCaseFilter} autoCapitalize="none" autoCorrect={false} />
          <CheckRow label="only new failures" checked={newOnly} onChange={setNewOnly} />
          {failedCases.length ? failedCases.map((c, i) => <FailedCaseRow key={`${c.suite}:${c.id}:${i}`} c={c} />)
            : <Empty status={(job.checks.failed_cases || []).length ? undefined : 'done'}>{(job.checks.failed_cases || []).length ? 'No cases match the filter.' : 'No failing test cases in the latest run.'}</Empty>}
        </Panel>
      ) : null}
      {Object.keys(history).length ? (
        <Panel title="Duration history" testID="checks-history">
          <Text variant="xs" style={{ color: colors.muted }}>Last {Math.max(...Object.values(history).map(h => h.length))} runs per suite, oldest → newest. Green passed, red failed.</Text>
          {Object.entries(history).map(([suite, points]) => {
            const last = points[points.length - 1];
            return (
              <View key={suite} style={styles.historyRow}>
                <View style={styles.row}>
                  <Text variant="xs" style={[styles.mono, { flex: 1 }]} numberOfLines={1}>{suite}</Text>
                  <Text variant="xs" style={[styles.num, { color: colors.muted }]}>{last?.duration_ms != null ? duration(last.duration_ms) : '—'}</Text>
                </View>
                <Sparkline
                  height={28}
                  values={points.map(p => p.duration_ms)}
                  colorsFor={i => (points[i].passed ? colors.ok : colors.danger)}
                  accessibilityLabel={`${suite} duration over ${points.length} runs; ${points.filter(p => !p.passed).length} failed; latest ${last?.duration_ms != null ? duration(last.duration_ms) : 'unknown'}`}
                  testID={`history-${suite}`}
                />
              </View>
            );
          })}
        </Panel>
      ) : null}
    </View>
  );
}

function FailedCaseRow({ c }: { c: { suite: string; name: string; id: string; message: string; new: boolean; classname?: string } }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  return (
    <View style={[styles.caseRow, { borderColor: colors.border }]}>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityLabel={`${c.name || c.id} in ${c.suite}${c.new ? ', new failure' : ''}`}
        onPress={() => setOpen(v => !v)} style={[styles.row, { minHeight: touchTarget }]}>
        <Text variant="xs" style={{ color: colors.danger }}>✗</Text>
        <View style={{ flex: 1 }}>
          <Text variant="xs" style={styles.mono} numberOfLines={open ? undefined : 1}>{c.name || c.id}</Text>
          <Text variant="xs" style={{ color: colors.muted }} numberOfLines={1}>{c.suite}{c.classname ? ` · ${c.classname}` : ''}</Text>
        </View>
        {c.new ? <Pill tone="danger">new</Pill> : <Pill tone="muted">pre-existing</Pill>}
      </Pressable>
      {open && c.message ? <Pre>{stripAnsi(c.message)}</Pre> : null}
    </View>
  );
}

function SuiteRow({ suite }: { suite: CheckSuite }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(!suite.passed);
  const c = suiteCounts(suite);
  const out = stripAnsi(String(suite.stdout || '') + (suite.stderr ? `\n${suite.stderr}` : ''));
  return (
    <View style={[styles.suite, { borderTopColor: colors.border }]}>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(v => !v)} style={[styles.row, { minHeight: touchTarget }]}>
        <StatusIcon status={suite.passed ? 'done' : 'failed'} label={suite.passed ? 'passed' : 'failed'} />
        <Text variant="sm" style={{ fontWeight: fontWeights.semibold, flex: 1 }} numberOfLines={1}>{suite.suite}</Text>
        {suite.environment ? <Pill tone="accent" glyph="⬢" accessibilityLabel={`build environment ${suite.environment}`} testID={`suite-env-${suite.suite}`}>{suite.environment}</Pill> : null}
        {(suite.preexisting_failures || []).length ? <Pill tone="muted">pre-existing failures</Pill> : null}
        <Text variant="xs" style={[styles.num, { color: colors.muted }]}>{c.passed}/{c.total}{suite.duration_ms != null ? ` · ${duration(suite.duration_ms)}` : ''}</Text>
      </Pressable>
      <ResultBar passed={c.passed} failed={c.failed} skipped={c.skipped} />
      {open ? (
        <View style={{ gap: spacing.xs, marginTop: spacing.xs }}>
          {suite.command ? <Text variant="xs" style={styles.mono} selectable>{suite.command}</Text> : null}
          <Pre>{out.trim() ? out.slice(-12000) : 'No output.'}</Pre>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  col: { gap: spacing.md },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  num: { fontVariant: ['tabular-nums'] },
  mono: { fontFamily: fonts.mono },
  transcript: { gap: spacing.sm },
  assistant: { alignSelf: 'stretch', borderWidth: 1, borderRadius: radii.panel, borderTopLeftRadius: 2, paddingHorizontal: spacing.md, paddingVertical: spacing.xs },
  user: { alignSelf: 'flex-end', maxWidth: '88%', borderWidth: 1, borderRadius: radii.panel, borderTopRightRadius: 2, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  fold: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, minHeight: 36, borderRadius: radii.control, paddingHorizontal: spacing.xs },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.sm },
  disclosure: { minHeight: touchTarget, justifyContent: 'center' },
  treeRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, minHeight: 36, borderBottomWidth: 1 },
  fileStatus: { minWidth: 20, alignItems: 'center', borderWidth: 1, borderRadius: radii.control, paddingHorizontal: 3 },
  cbar: { flexDirection: 'row', gap: 1 },
  cblock: { width: 6, height: 8, borderRadius: 1 },
  diffFile: { borderWidth: 1, borderRadius: radii.panel, overflow: 'hidden' },
  diffHead: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.sm, borderBottomWidth: 1 },
  hunk: { paddingHorizontal: spacing.sm, paddingVertical: 2 },
  codeRow: { flexDirection: 'row' },
  codeLine: { paddingHorizontal: spacing.sm },
  lineNo: { width: 36, textAlign: 'right', paddingRight: 4 },
  sign: { width: 14, textAlign: 'center' },
  code: { flex: 1, paddingRight: spacing.sm },
  inlineFinding: { margin: spacing.sm, borderWidth: 1, borderLeftWidth: 3, borderRadius: radii.control, padding: spacing.sm, gap: spacing.xs },
  coverage: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  findingCard: { borderWidth: 1, borderLeftWidth: 3, borderRadius: radii.control, padding: spacing.md, gap: spacing.xs },
  runRow: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, gap: spacing.xs, borderBottomWidth: 1, minHeight: touchTarget },
  caseRow: { borderTopWidth: 1, paddingTop: spacing.xs },
  historyRow: { gap: 2 },
  suite: { borderTopWidth: 1, paddingTop: spacing.xs, gap: spacing.xs },
});
