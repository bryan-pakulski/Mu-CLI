// Debug mode (web debug_panel.html): competing hypotheses, suspects, notes and past findings.

import React, { useCallback, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { ModeWorkspaceIntro, useModeWorkspaceView, workspaceState, workspaceTabs } from '../components';
import { EntryRow, PanelPage, PanelSection } from '../components/chat/PanelPage';
import { debugApi, type DebugHypothesis, type DebugState } from '../api/debug';
import { Text } from '../ui';

/** Hypothesis status → the shared status vocabulary. */
export function hypothesisStatus(status: string): string {
  if (status === 'confirmed' || status === 'supported') return 'done';
  if (status === 'disproved') return 'failed';
  return 'waiting';
}

export function DebugScreen() {
  const [state, setState] = useState<DebugState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const workspaceView = useModeWorkspaceView(state?.workspace);

  const load = useCallback(async () => {
    try { setError(null); setState(await debugApi.getState()); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const workspace = state?.workspace;
  return (
    <PanelPage testID="debug-screen" title={workspace?.title || 'Debug'} state={workspaceState(workspace)}
      tabs={workspaceTabs(workspace)} tab={workspaceView.selectedView} onTab={workspaceView.selectView}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && (!state || !state.active) ? { title: 'No debug state', message: 'Start debugging to build a hypothesis-and-evidence board.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}>
      {state && workspace ? (
        <>
          <ModeWorkspaceIntro workspace={workspace} />
          {workspaceView.shows('hypotheses') ? (
            <PanelSection title="Hypotheses" count={state.hypotheses.length} empty="No hypotheses captured yet.">
              {state.hypotheses.map((item: DebugHypothesis) => (
                <EntryRow key={item.id} status={hypothesisStatus(item.status)} content={item.content} when={item.status}
                  source={expanded === item.id ? item.source : null} tags={expanded === item.id ? item.tags : undefined}
                  onPress={() => setExpanded(expanded === item.id ? null : item.id)} testID={`hypothesis-${item.id}`}
                  actions={expanded === item.id ? (
                    <Text variant="caption" tone="ink3">{item.status === 'untested' ? 'Needs a discriminating test.' : `Investigation state: ${item.status}. This is not an independent accuracy score.`}</Text>
                  ) : undefined} />
              ))}
            </PanelSection>
          ) : null}
          {workspaceView.shows('evidence') ? (
            <>
              <PanelSection title="Suspects" count={state.suspects.length} empty="No suspect files or symbols recorded.">
                {state.suspects.map(item => <EntryRow key={item.id} status="waiting" content={item.content} source={item.source} />)}
              </PanelSection>
              <PanelSection title="Notes" count={state.notes.length} empty="No reproduction or test observations recorded.">
                {state.notes.map(item => <EntryRow key={item.id} content={item.content} source={item.source} />)}
              </PanelSection>
            </>
          ) : null}
          {workspaceView.shows('findings') ? (
            <PanelSection title="Past findings" count={state.findings.length} empty="The investigation has not produced a durable root cause.">
              {state.findings.map(item => <EntryRow key={item.id} status="done" content={item.content} source={item.source} />)}
            </PanelSection>
          ) : null}
        </>
      ) : null}
    </PanelPage>
  );
}
