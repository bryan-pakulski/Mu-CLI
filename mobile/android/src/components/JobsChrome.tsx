// Shared jobs-mode chrome: the page switcher (Dashboard · Jobs · Activity ·
// Tickets · History · Settings) with Wash status chips, the job row with a
// mini pipeline stepper, and the home VM hook (GET /api/jobs/vm/home + SSE).

import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';

import { jobCoreApi, optional, subscribeToJobChanges, type HomeVM, type JobPR, type JobsStatusVM, type JobStep, type JobSummary } from '../api/jobCore';
import { openExternalUrl } from '../api/urlSafety';
import { prPill } from './jobViews';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, spacing, toneInk, touchTarget } from '../theme/tokens';
import { STATUS, age, money, stateLabel, stateStatus, verdictStyle } from '../theme/status';
import { MiniStepper, Pill, Pressable, StatusIcon, Text, useInterval } from '../ui';

export type JobsPage = 'Work' | 'JobsList' | 'JobActivity' | 'JobBoards' | 'JobHistory' | 'JobSettings';

export const JOBS_PAGES: Array<{ id: JobsPage; label: string }> = [
  { id: 'Work', label: 'Dashboard' },
  { id: 'JobsList', label: 'Jobs' },
  { id: 'JobActivity', label: 'Activity' },
  { id: 'JobBoards', label: 'Tickets' },
  { id: 'JobHistory', label: 'History' },
  { id: 'JobSettings', label: 'Settings' },
];

/**
 * Switch jobs-mode tabs in place: the page replaces the current one (fading in, see
 * AppNavigator) instead of being pushed, so Back leaves jobs mode rather than replaying
 * every tab visited.
 */
export function switchJobsPage(navigation: { replace?: (name: any) => void; navigate: (name: any, params?: any) => void },
                               current: JobsPage, page: JobsPage) {
  if (page === current) return;
  // The dashboard lives in the tab shell; returning to it pops back to the tabs.
  if (page === 'Work') { navigation.navigate('Tabs', { screen: 'Work' }); return; }
  if (navigation.replace) navigation.replace(page);
  else navigation.navigate(page);
}

export const jobKey = (row: { key?: string; id: string }) => row.key || `#${String(row.id || '').slice(0, 8)}`;
export const stepsOf = (row: { pipeline?: JobStep[]; steps?: JobStep[] }) => row.pipeline || row.steps || [];
export const focusStep = (steps: JobStep[]) => steps.find(s => s.status === 'failed') || steps.find(s => s.status === 'active' || s.status === 'waiting');

/** Page switcher with status chips (● agents · ● checks · ◆ needs you). */
export function JobsNavBar({ current, onNavigate, home }: { current: JobsPage; onNavigate: (page: JobsPage) => void; home?: HomeVM | null }) {
  const { colors } = useTheme();
  const [status, setStatus] = useState<JobsStatusVM | null>(null);
  const refresh = useCallback(() => { optional(jobCoreApi.status()).then(setStatus).catch(() => undefined); }, []);
  useEffect(() => { refresh(); }, [refresh]);
  useInterval(refresh, 15_000);
  const needs = status?.needs_you ?? home?.counts.needs_you ?? 0;
  const agents = status?.agents ?? home?.running.filter(j => j.state !== 'checking').length ?? 0;
  const checks = status?.checks ?? home?.running.filter(j => j.state === 'checking').length ?? 0;
  const chip = (glyph: string, n: number, label: string, tone: 'running' | 'attention') => (
    <View accessible accessibilityLabel={`${n} ${label}`} style={styles.chip}>
      <Text variant="xs" style={{ color: n ? toneInk(colors, tone) : colors.ink3 }}>{glyph} {n}</Text>
      <Text variant="xs" style={{ color: colors.ink3 }}>{label}</Text>
    </View>
  );
  return (
    <View style={[styles.nav, { borderBottomColor: colors.hairline, backgroundColor: colors.surface }]}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.navRow} accessibilityRole="tablist" accessibilityLabel="Jobs pages">
        {JOBS_PAGES.map(p => {
          const on = p.id === current;
          return (
            <Pressable key={p.id} testID={`jobs-nav-${p.id}`} accessibilityRole="tab" accessibilityLabel={p.label} accessibilityState={{ selected: on }}
              onPress={() => { if (!on) onNavigate(p.id); }}
              style={({ pressed }) => [styles.navItem, { borderBottomColor: on ? colors.accent.ink : 'transparent', backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
              <Text variant="sm" style={{ color: on ? colors.ink : colors.ink3, fontWeight: on ? fontWeights.semibold : fontWeights.medium }}>{p.label}</Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <View style={styles.chips}>
        <Pill tone="muted" glyph="⧉" testID="jobs-scope-chip" accessibilityLabel="Showing jobs from all sessions">all sessions</Pill>
        {chip(STATUS.active.icon, agents, agents === 1 ? 'agent' : 'agents', 'running')}
        {chip(STATUS.active.icon, checks, checks === 1 ? 'check' : 'checks', 'running')}
        {chip(STATUS.waiting.icon, needs, STATUS.waiting.label, 'attention')}
        {status?.engine && status.engine !== 'up' ? <Pill tone="muted" glyph="⬢">{`engine ${status.engine}`}</Pill> : null}
      </View>
    </View>
  );
}

/** Job row: key, live dot, mini pipeline stepper + focus step, state pill, cost, age, title. */
export function JobRow({ job, onPress, now = Date.now() / 1000, right, testID }: { job: JobSummary; onPress: () => void; now?: number; right?: React.ReactNode; testID?: string }) {
  const { colors } = useTheme();
  const steps = stepsOf(job);
  const step = focusStep(steps);
  const st = stateStatus(job.state);
  const s = STATUS[st];
  const [vTone, vLabel] = verdictStyle(job.verdict);
  const needs = st === 'waiting' && job.attention_detail;
  return (
    <Pressable testID={testID || `job-row-${job.id}`} accessibilityRole="button" accessibilityLabel={`${jobKey(job)} ${job.title}, ${stateLabel(job.state)}`} onPress={onPress}
      style={({ pressed }) => [styles.row, { borderBottomColor: colors.hairline, backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
      <View style={styles.rowTop}>
        <Text variant="sm" style={[styles.key, { color: colors.accent.ink }]} numberOfLines={1}>{jobKey(job)}</Text>
        {st === 'active' ? <Text variant="xs" accessibilityLabel="running" style={{ color: colors.running.ink }}>●</Text> : null}
        <MiniStepper steps={steps} />
        <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={1}>{step?.label?.toLowerCase() || ''}</Text>
        <View style={{ flex: 1 }} />
        <Text variant="xs" style={[styles.num, { color: colors.ink }]}>{money(job.cost_usd)}{job.max_cost_usd ? <Text variant="xs" style={{ color: colors.ink3 }}>{` / ${money(job.max_cost_usd)}`}</Text> : null}</Text>
        <Text variant="xs" style={[styles.num, { color: colors.ink3 }]}>{age(now - (job.created_at || now))}</Text>
      </View>
      <Text variant="sm" numberOfLines={1}>{job.title}</Text>
      <View style={styles.rowBottom}>
        <Pill tone={s.tone} glyph={s.icon}>{stateLabel(job.state)}</Pill>
        {job.verdict ? <Text variant="xs" style={{ color: toneInk(colors, vTone) }}>{vLabel}</Text> : null}
        <PrPill pr={job.pr} testID={`job-row-${job.id}-pr`} />
        {needs ? <Text variant="xs" style={{ color: colors.attention.ink, flex: 1 }} numberOfLines={1}>{job.limit?.summary || job.attention_detail}</Text> : <View style={{ flex: 1 }} />}
        {right}
      </View>
      {job.origin_session || job.repository ? (
        <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono }} numberOfLines={1} testID={`job-row-${job.id}-meta`}>
          {[job.origin_session ? `session · ${job.origin_session}` : '', job.repository || ''].filter(Boolean).join('  ')}
        </Text>
      ) : null}
    </Pressable>
  );
}

/** The job's PR (number + state); tapping opens the PR URL (http/https only). */
export function PrPill({ pr, testID }: { pr: JobPR | null | undefined; testID?: string }) {
  const view = prPill(pr);
  if (!view) return null;
  const pill = <Pill tone={view.tone} glyph="⇡">{view.label}</Pill>;
  if (!view.url) return <View testID={testID} accessibilityLabel={view.accessibilityLabel}>{pill}</View>;
  return (
    <Pressable testID={testID} accessibilityRole="link" accessibilityLabel={view.accessibilityLabel} hitSlop={6}
      onPress={() => { void openExternalUrl(view.url); }}>
      {pill}
    </Pressable>
  );
}

/** Small "recent result" row. */
export function ResultRow({ job, onPress, now }: { job: JobSummary; onPress: () => void; now: number }) {
  const { colors } = useTheme();
  const [vTone, vLabel] = verdictStyle(job.verdict);
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${jobKey(job)} ${job.title}, ${stateLabel(job.state)}`} onPress={onPress}
      style={({ pressed }) => [styles.result, { backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
      <StatusIcon status={stateStatus(job.state)} label={stateLabel(job.state)} />
      <Text variant="xs" style={{ fontFamily: fonts.mono, color: colors.accent.ink }}>{jobKey(job)}</Text>
      <Text variant="sm" style={{ flex: 1 }} numberOfLines={1}>{job.title}</Text>
      {job.verdict ? <Pill tone={vTone}>{vLabel}</Pill> : null}
      <Text variant="xs" style={[styles.num, { color: colors.ink3 }]}>{age(now - job.updated_at)} ago</Text>
    </Pressable>
  );
}

/** Home VM + change stream (scope 'home' → refetch). */
export function useJobsHome() {
  const [home, setHome] = useState<HomeVM | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const next = await jobCoreApi.home();
      setHome(next);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
    const stream = subscribeToJobChanges(change => { if (change.scope === 'home') void load(true); }, () => { void load(true); });
    return () => stream.close();
  }, [load]);
  return { home, loading, error, reload: load };
}

const styles = StyleSheet.create({
  num: { fontVariant: ['tabular-nums'] },
  nav: { borderBottomWidth: 1 },
  navRow: { paddingHorizontal: spacing.sm },
  navItem: { minHeight: touchTarget, justifyContent: 'center', paddingHorizontal: spacing.md, borderBottomWidth: 2 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.md, paddingHorizontal: spacing.base, paddingVertical: spacing.xs },
  chip: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  row: { paddingHorizontal: spacing.base, paddingVertical: spacing.sm, gap: 2, borderBottomWidth: 1, minHeight: touchTarget },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  rowBottom: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginTop: 2 },
  key: { fontFamily: fonts.mono, fontWeight: fontWeights.semibold },
  result: { minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.xs },
});
