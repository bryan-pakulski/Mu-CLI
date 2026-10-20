// Work → Now (GET /api/jobs/vm/home): Needs you (each job's decisive action on its row), Running
// (what each job is doing), Cost (last 7 days) and Recently finished. Rows, never cards; the
// page header's state sentence carries the counts.

import React, { useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet } from 'react-native';

import type { HomeVM } from '../../api/jobCore';
import { JobRow } from '../../components/JobsChrome';
import { decisiveAction, decisiveLabel, needsSentence, nowBoard, runningSentence, spendByDay } from '../../components/workModel';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { duration, money, stateLabel } from '../../theme/status';
import { Banner, Board, BoardSection, Button, EmptyState, Loading, Sparkline, Text } from '../../ui';

export type NowBoardProps = {
  home: HomeVM | null;
  loading: boolean;
  error: string;
  onRefresh: () => Promise<void>;
  onOpenJob: (jobId: string, action?: string) => void;
  onNewJob: () => void;
  onBrowseTickets: () => void;
  onShowJobs: () => void;
  /** The one setup reminder (unfinished setup or stages without a default model). */
  setup?: { text: string; action: string; onPress: () => void } | null;
};

export function NowBoard({ home, loading, error, onRefresh, onOpenJob, onNewJob, onBrowseTickets, onShowJobs, setup }: NowBoardProps) {
  const { colors } = useTheme();
  const [refreshing, setRefreshing] = useState(false);
  const now = Date.now() / 1000;
  const board = home ? nowBoard(home) : null;
  const spend = home ? spendByDay(home.jobs, now) : null;
  return (
    <ScrollView contentContainerStyle={styles.content} testID="work-now"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await onRefresh(); setRefreshing(false); }} tintColor={colors.ink3} />}>
      {setup ? (
        <Banner tone="accent" title={setup.text} action={{ label: setup.action, onPress: setup.onPress, testID: 'work-setup-cta' }} testID="work-setup" />
      ) : null}
      {error ? <Banner tone="danger" role="alert" title={error} /> : null}
      {!home && loading ? <Loading label="Loading jobs…" /> : null}
      {home && !home.jobs.length ? (
        <>
          <EmptyState icon="briefcase" title="No jobs yet" testID="work-first-run"
            message="Delegate engineering work to agents that plan, implement, check and review it in isolated sandboxes. You step in only when a job needs you."
            actionLabel="Create a job" onAction={onNewJob} />
          <Button variant="ghost" label="Browse tickets" onPress={onBrowseTickets} />
        </>
      ) : null}
      {board && home?.jobs.length ? (
        <Board>
          <BoardSection title="Needs you" count={board.needs.length} empty="Nothing needs you." testID="now-needs-you">
            {board.needs.map(job => {
              const action = decisiveAction(job);
              return (
                <JobRow key={job.id} job={job} now={now} pinned sub={needsSentence(job)} onPress={() => onOpenJob(job.id)}
                  actions={action ? <Button small variant="primary" label={decisiveLabel(job)} onPress={() => onOpenJob(job.id, action.id)} testID={`needs-you-${job.id}-${action.id}`} /> : undefined} />
              );
            })}
          </BoardSection>
          <BoardSection title="Running" count={board.running.length} empty="Nothing running." testID="now-running">
            {board.running.map(job => {
              const r = runningSentence(job);
              return <JobRow key={job.id} job={job} now={now} onPress={() => onOpenJob(job.id)} sub={`${r.text}${r.total ? ` · ${r.done} of ${r.total} stages done` : ''}`} />;
            })}
          </BoardSection>
          {spend && spend.buckets.some(Boolean) ? (
            <BoardSection title="Cost" testID="now-cost">
              <Text variant="title3" numeric>{money(spend.week)}</Text>
              <Text variant="caption" tone="ink3">last 7 days{spend.today ? ` · ${money(spend.today)} today` : ''}</Text>
              <Sparkline values={spend.buckets} height={36} tone="accent" accessibilityLabel={`Job cost by day, last 14 days; ${money(spend.week)} in the last 7`} />
            </BoardSection>
          ) : null}
          {board.finished.length ? (
            <BoardSection title="Recently finished" count={board.finished.length} testID="now-recent"
              right={<Button small variant="ghost" label="All jobs" onPress={onShowJobs} />}>
              {board.finished.map(job => (
                <JobRow key={job.id} job={job} now={now} onPress={() => onOpenJob(job.id)}
                  sub={[stateLabel(job.state), Number(job.cost_usd) ? money(job.cost_usd) : '', job.updated_at && job.created_at ? duration((job.updated_at - job.created_at) * 1000) : ''].filter(Boolean).join(' · ')} />
              ))}
            </BoardSection>
          ) : null}
        </Board>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.md, paddingBottom: spacing['3xl'] + spacing['2xl'] },
});
