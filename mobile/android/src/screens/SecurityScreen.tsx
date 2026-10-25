// Security mode (web security_panel.html): the scan brief and findings, each with its proof and
// remediation state; approve or refute after confirming; details in a sheet.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { useModeWorkspaceView, workspaceState, workspaceTabs } from '../components';
import { Fact, PanelPage, PanelSection } from '../components/chat/PanelPage';
import { securityApi, type SecurityFinding, type SecurityState } from '../api/security';
import { CheckRunRow, useCheckRunSheet } from '../components/modes';
import { modeState } from '../theme/status';
import { spacing } from '../theme/tokens';
import { Button, Chip, Row, Sheet, Text, useServerActions, type ServerAction } from '../ui';

/** Severity → status sign: critical/high fail, medium needs attention, the rest are quiet. */
export function severitySign(severity: string): string {
  if (severity === 'critical' || severity === 'high') return 'failed';
  if (severity === 'medium') return 'waiting';
  return 'pending';
}

function proofText(f: SecurityFinding): string {
  return `PoC ${f.proof_verified ? 'verified' : f.has_proof ? 'pending' : 'none'} · fix ${f.remediation_verified ? 'verified' : f.has_remediation ? 'pending' : 'none'}`;
}

export function SecurityScreen() {
  const [state, setState] = useState<SecurityState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<SecurityFinding | null>(null);
  const workspaceView = useModeWorkspaceView(state?.workspace);
  const actions = useServerActions();
  const runSheet = useCheckRunSheet(state?.runs || []);

  const load = useCallback(async () => {
    try { setError(null); setState(await securityApi.getState()); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const decide = async (finding: SecurityFinding, action: ServerAction) => {
    if (!(await actions.ask(action))) return;
    try {
      if (action.id === 'approve') await securityApi.approveFinding(finding.finding_id);
      else await securityApi.refuteFinding(finding.finding_id, 'Refuted via mobile UI');
      await load();
    } catch (e) { setError(String(e)); }
  };

  const workspace = state?.workspace;
  const findings = (state?.findings || []).filter(item => {
    if (workspaceView.selectedView === 'evidence') return item.has_proof;
    if (workspaceView.selectedView === 'remediation') return item.has_remediation;
    return true;
  });
  const empty = !state || (!state.report && state.findings.length === 0);

  return (
    <PanelPage testID="security-screen" title={workspace?.title || 'Security'} state={workspaceState(workspace)}
      tabs={workspaceTabs(workspace)} tab={workspaceView.selectedView} onTab={workspaceView.selectView}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && empty ? { title: 'No security findings', message: 'No security scan or findings available.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={<>{actions.element}{runSheet.element}</>}>
      {state && workspace ? (
        <>

          {state.report ? (
            <PanelSection title="Scan brief">
              <Text variant="body" tone="ink2">{state.report.summary || state.report.title}</Text>
              {state.runner ? <Fact label="Runner" value={state.runner} /> : null}
              {state.report.coverage ? <Fact label="Coverage" value={`${state.report.coverage.reviewed} of ${state.report.coverage.total} surface items reviewed`} /> : null}
              <Text variant="caption" tone="ink3">Review PoC execution commands on desktop.</Text>
            </PanelSection>
          ) : null}
          <PanelSection title="Findings" count={findings.length} empty="No records match this evidence lens.">
            {findings.map(item => (
              <Row key={item.finding_id} testID={`finding-${item.finding_id}`} status={severitySign(item.severity)} title={item.title} when={`${item.cvss_score ?? ""} ${item.severity}`.trim()}
                onPress={() => setSelected(item)}
                sub={(
                  <View style={styles.sub}>
                    <Text variant="caption" tone="ink3" numberOfLines={3}>{item.summary}</Text>
                    <View style={styles.chips}>
                      <Chip variant="meta" label={item.status} />
                      <Text variant="caption" tone="ink3">{proofText(item)}</Text>
                    </View>
                  </View>
                )}
                actions={(
                  <View style={styles.chips}>
                    {!state.plan_mode && item.status !== 'approved' && item.status !== 'refuted' && item.proof_verified && item.remediation_verified && item.severity_confirmed ? <Button label="Approve" variant="secondary" testID={`approve-${item.finding_id}`}
                      onPress={() => void decide(item, { id: 'approve', label: 'Approve', confirm: 'Finalize this security finding?' })} /> : null}
                    {!state.plan_mode && !['approved', 'refuted'].includes(item.status) ? <Button label="Refute" variant="ghost" testID={`refute-${item.finding_id}`}
                      onPress={() => void decide(item, { id: 'refute', label: 'Refute', danger: true, confirm: 'Abandon this finding as invalid?' })} /> : null}
                  </View>
                )} />
            ))}
          </PanelSection>
          {state.surface?.length ? <PanelSection title="Coverage">
            {state.surface.map(item => <Row key={item.id} status={modeState('security_surface', item.status).status}
              title={`${item.path}:${item.line}`} sub={item.note || item.snippet} />)}
          </PanelSection> : null}
          {state.observations?.length ? <PanelSection title="Observations">
            {state.observations.map(o => <Row key={o.id} title={o.title} sub={o.note} />)}
          </PanelSection> : null}
        </>
      ) : null}
      <Sheet visible={!!selected} onClose={() => setSelected(null)} title={selected?.title} testID="finding-sheet">
        {selected ? (
          <View>
            <Fact label="Severity" value={`${selected.cvss_score ?? ''} ${selected.severity}`.trim()} />
            {selected.cvss_vector ? <Fact label="CVSS 3.1" value={selected.cvss_vector} /> : null}
            {selected.demonstrated_impact ? <Fact label="Demonstrated impact" value={selected.demonstrated_impact} /> : null}
            <Fact label="Class" value={selected.vulnerability_class} />
            <Fact label="Status" value={selected.status} />
            <Fact label="Exploit path" value={selected.exploit_path} mono />
            <Fact label="PoC command" value={selected.proof_command} mono />
            <Fact label="Affected paths" value={selected.affected_paths.join(', ')} mono />
            <Fact label="Evidence" value={proofText(selected)} />
            {(selected.runs || []).map(run => <CheckRunRow key={run.id} run={run} onOpen={runSheet.open} />)}
          </View>
        ) : null}
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  sub: { gap: spacing.xs },
  chips: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
});
