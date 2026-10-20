// Work (documentation/plans/mobile_parity.md §6 Work): the page header (title, state sentence,
// Now · Jobs · Tickets · History; Activity, Settings and Setup under ⋯), one tab body at a time,
// and the one primary action, New job, as a floating pill. A job opens as a pushed page.

import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { jobsApi, type JobStageName } from '../api/jobs';
import { jobCoreApi, optional, type JobsStatusVM } from '../api/jobCore';
import { setupApi } from '../api/setup';
import { JobStageModelsSheet } from '../components/JobStageModelsSheet';
import { NewJobSheet } from '../components/NewJobSheet';
import { useJobsHome } from '../components/JobsChrome';
import { WORK_TABS, asWorkTab, workStateParts, type WorkTab } from '../components/workModel';
import { useConnectionStore } from '../store/connection';
import { PageHeader, PrimaryPill, useInterval } from '../ui';
import { NowBoard } from './work/NowBoard';
import { JobsPanel } from './work/JobsPanel';
import { TicketsPanel } from './work/TicketsPanel';
import { HistoryPanel } from './work/HistoryPanel';

type Nav = { navigate: (name: string, params?: object) => void; setParams?: (params: object) => void };

export function WorkScreen({ navigation, route }: { navigation: Nav; route?: { params?: { tab?: string } } }) {
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const activeProvider = useConnectionStore(state => state.activeProvider);
  const activeModel = useConnectionStore(state => state.activeModel);
  const { home, loading, error, reload } = useJobsHome();
  const [tab, setTab] = useState<WorkTab>(asWorkTab(route?.params?.tab));
  const [status, setStatus] = useState<JobsStatusVM | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [modelsOpen, setModelsOpen] = useState(false);
  const [missingStages, setMissingStages] = useState<JobStageName[]>([]);
  const [setupPending, setSetupPending] = useState(false);

  // A deep link or Settings → Areas can ask for a tab.
  useEffect(() => { if (route?.params?.tab) setTab(asWorkTab(route.params.tab)); }, [route?.params?.tab]);

  const refreshStatus = useCallback(() => { optional(jobCoreApi.status()).then(setStatus).catch(() => undefined); }, []);
  useEffect(() => { refreshStatus(); }, [refreshStatus, home]);
  useInterval(refreshStatus, 15_000);

  useEffect(() => {
    // One setup reminder until the guided setup has been finished once (never blocks the board).
    let live = true;
    setupApi.get().then(vm => { if (live) setSetupPending(!vm?.completed_at); }).catch(() => undefined);
    jobsApi.getSettings().then(s => { if (live) setMissingStages(Array.isArray(s.missing) ? s.missing : []); }).catch(() => undefined);
    return () => { live = false; };
  }, []);

  const openJob = (jobId: string, action?: string) => navigation.navigate('JobDetail', action ? { jobId, action } : { jobId });
  const setup = setupPending
    ? { text: `Finish jobs setup: check and test everything jobs need.${missingStages.length ? ` No default model for ${missingStages.join(', ')} (jobs use their session’s model).` : ''}`, action: 'Open setup', onPress: () => navigation.navigate('JobSetup') }
    : missingStages.length
      ? { text: `No default model for ${missingStages.join(', ')}: jobs use their session’s model for those stages.`, action: 'Set models', onPress: () => setModelsOpen(true) }
      : null;

  return (
    <View style={styles.root} testID="work-screen">
      <PageHeader
        testID="work-header"
        title="Work"
        state={workStateParts(status, home)}
        tabs={WORK_TABS.map(t => ({ key: t.key, label: t.label, count: t.key === 'now' ? home?.needs_you.length || null : t.key === 'jobs' ? home?.jobs.length || null : null }))}
        tab={tab}
        onTab={key => setTab(asWorkTab(key))}
        menu={[
          { key: 'activity', label: 'Activity', icon: 'activity', onPress: () => navigation.navigate('JobActivity') },
          { key: 'settings', label: 'Settings', icon: 'settings', onPress: () => navigation.navigate('JobSettings') },
          { key: 'setup', label: 'Setup and doctor', icon: 'wrench', onPress: () => navigation.navigate('JobSetup') },
        ]}
      />
      <View style={styles.body}>
        {tab === 'now' ? (
          <NowBoard home={home} loading={loading} error={error} onRefresh={() => reload(true)} onOpenJob={openJob} setup={setup}
            onNewJob={() => setCreateOpen(true)} onBrowseTickets={() => setTab('tickets')} onShowJobs={() => setTab('jobs')} />
        ) : null}
        {tab === 'jobs' ? <JobsPanel onOpenJob={id => openJob(id)} /> : null}
        {tab === 'tickets' ? <TicketsPanel onOpenJob={id => openJob(id)} /> : null}
        {tab === 'history' ? <HistoryPanel onOpenJob={id => openJob(id)} /> : null}
      </View>
      <PrimaryPill label="New job" icon="plus" onPress={() => setCreateOpen(true)} testID="work-new-job" />
      <JobStageModelsSheet visible={modelsOpen} onClose={() => setModelsOpen(false)} onSaved={missing => { setMissingStages(missing); setModelsOpen(false); }} />
      <NewJobSheet
        visible={createOpen}
        sessionName={activeSessionName}
        provider={activeProvider}
        model={activeModel}
        onClose={() => setCreateOpen(false)}
        onCreated={job => { setCreateOpen(false); void reload(true); openJob(job.id); }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  body: { flex: 1 },
});
