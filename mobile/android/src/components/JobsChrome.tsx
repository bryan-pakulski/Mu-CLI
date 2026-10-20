// Shared Work pieces: the job row (kit Row: status sign, key + title, one sentence, mini pipeline,
// PR and origin session; time on the right), the PR pill, and the home VM hook
// (GET /api/jobs/vm/home + the jobs change stream).

import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { jobCoreApi, subscribeToJobChanges, type HomeVM, type JobPR, type JobStep, type JobSummary } from '../api/jobCore';
import { openExternalUrl } from '../api/urlSafety';
import { prPill } from './jobViews';
import { rowSentence } from './workModel';
import { spacing } from '../theme/tokens';
import { age, stateLabel, stateStatus } from '../theme/status';
import { MiniStepper, Pill, Pressable, Row, Text } from '../ui';

export const jobKey = (row: { key?: string; id: string }) => row.key || `#${String(row.id || '').slice(0, 8)}`;
export const stepsOf = (row: { pipeline?: JobStep[]; steps?: JobStep[] }) => row.pipeline || row.steps || [];
export const focusStep = (steps: JobStep[]) => steps.find(s => s.status === 'failed') || steps.find(s => s.status === 'active' || s.status === 'waiting');

/** One job as a row. `sub` replaces the default sentence (Now and Running rows say more). */
export function JobRow({ job, onPress, now = Date.now() / 1000, actions, sub, pinned, testID }: {
  job: JobSummary; onPress: () => void; now?: number; actions?: React.ReactNode; sub?: string; pinned?: boolean; testID?: string;
}) {
  const steps = stepsOf(job);
  const id = testID || `job-row-${job.id}`;
  const stamp = job.updated_at || job.created_at || now;
  const meta = [job.origin_session ? `session · ${job.origin_session}` : '', job.repository || ''].filter(Boolean).join('  ');
  return (
    <Row
      testID={id}
      status={stateStatus(job.state)}
      pinned={pinned}
      onPress={onPress}
      accessibilityLabel={`${jobKey(job)} ${job.title}, ${stateLabel(job.state)}`}
      when={age(now - stamp)}
      title={<Text variant="body" numberOfLines={2}><Text variant="mono" tone="ink3">{`${jobKey(job)}  `}</Text>{job.title}</Text>}
      sub={(
        <View style={styles.sub}>
          <Text variant="caption" tone={stateStatus(job.state) === 'waiting' ? 'attention' : stateStatus(job.state) === 'failed' ? 'danger' : 'ink3'} numberOfLines={2}>{sub ?? rowSentence(job)}</Text>
          {steps.length || job.pr ? (
            <View style={styles.line}>
              {steps.length ? <MiniStepper steps={steps} /> : null}
              <PrPill pr={job.pr} testID={`${id}-pr`} />
            </View>
          ) : null}
          {meta ? <Text variant="monoSm" tone="ink3" numberOfLines={1} testID={`${id}-meta`}>{meta}</Text> : null}
        </View>
      )}
      actions={actions}
    />
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
  sub: { gap: spacing.xs },
  line: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
});
