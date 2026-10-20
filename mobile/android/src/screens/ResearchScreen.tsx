// Research mode (web research_panel.html): claims, the source ledger and the citation output.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { openExternalUrl } from '../api/urlSafety';
import { ModeWorkspaceIntro, useModeWorkspaceView, workspaceState, workspaceTabs } from '../components';
import { EntryRow, Fact, PanelPage, PanelSection } from '../components/chat/PanelPage';
import { researchApi, type ResearchState } from '../api/research';
import { spacing } from '../theme/tokens';
import { Chip, Meter, Pre, Row, Text } from '../ui';

export function ResearchScreen() {
  const [state, setState] = useState<ResearchState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedSource, setExpandedSource] = useState<number | null>(null);
  const workspaceView = useModeWorkspaceView(state?.workspace);

  const load = useCallback(async () => {
    try { setError(null); setState(await researchApi.getState()); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const workspace = state?.workspace;
  const empty = !state || (!state.active && !state.sources.length && !state.findings.length);
  return (
    <PanelPage testID="research-screen" title={workspace?.title || 'Research'} state={workspaceState(workspace)}
      tabs={workspaceTabs(workspace)} tab={workspaceView.selectedView} onTab={workspaceView.selectView}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && empty ? { title: 'No research session', message: 'Start a research conversation to build a source-backed evidence desk.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}>
      {state && workspace ? (
        <>
          <ModeWorkspaceIntro workspace={workspace} />
          {workspaceView.shows('claims') ? (
            <PanelSection title="Claims" count={state.findings.length} empty="No claims captured yet.">
              {state.findings.map(finding => (
                <EntryRow key={finding.id} status={finding.source ? 'done' : 'waiting'} content={finding.content}
                  source={finding.source || 'Accuracy and relevance have not been independently assessed'}
                  tags={[finding.record_type === 'claim' ? 'research claim' : 'legacy note', finding.source ? 'source linked' : 'evidence gap', ...finding.tags.slice(0, 3)]} />
              ))}
            </PanelSection>
          ) : null}
          {workspaceView.shows('sources') ? (
            <PanelSection title="Source ledger" count={state.sources.length} empty="No sources cited yet.">
              {state.sources.map(source => {
                const expanded = expandedSource === source.id;
                const credibility = Math.round((source.credibility_score || 0) * 100);
                return (
                  <Row key={source.id} testID={`source-${source.id}`} title={source.title} when={`${credibility}%`}
                    onPress={() => setExpandedSource(expanded ? null : source.id)}
                    accessibilityLabel={`${source.title}, source quality ${credibility}%`}
                    sub={(
                      <View style={styles.source}>
                        <View style={styles.sourceLine}>
                          <Chip variant="meta" label={source.source_type} />
                          <View style={styles.flex}><Meter value={credibility} max={100} accessibilityLabel={`Source quality ${credibility}%`} /></View>
                        </View>
                        <Text variant="caption" tone="accent" numberOfLines={expanded ? undefined : 1} accessibilityRole="link" onPress={() => void openExternalUrl(source.url)}>{source.url}</Text>
                        {expanded ? (
                          <View>
                            {source.authors.length ? <Fact label="Authors" value={source.authors.join(', ')} /> : null}
                            <Fact label="Published" value={source.date || 'Not recorded'} />
                            <Fact label="Accessed" value={source.accessed_date || 'Not recorded'} />
                            <Text variant="caption" tone="ink3">Credibility describes the source. It does not establish the accuracy of every claim.</Text>
                          </View>
                        ) : null}
                      </View>
                    )} />
                );
              })}
            </PanelSection>
          ) : null}
          {workspaceView.shows('bibliography') && state.bibliography ? (
            <PanelSection title="Citation output" count={state.source_count}>
              <Pre>{state.bibliography}</Pre>
            </PanelSection>
          ) : null}
        </>
      ) : null}
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  source: { gap: spacing.xs, marginTop: spacing.xs },
  sourceLine: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
});
