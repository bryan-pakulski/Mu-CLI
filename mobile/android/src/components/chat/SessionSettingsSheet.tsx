// Session settings (Chat ⋯ → Session settings; the composer's model label opens it too): the open
// session's provider and model, effort, mode, plan mode, auto-approve and every session variable.
// These used to be split over Settings, a session sheet and an advanced sheet; now they live with
// the conversation, as on the web (mobile_parity.md §6 Settings).

import React from 'react';
import { StyleSheet, View } from 'react-native';
import type { ModeInfo } from '../../api/modes';
import { useConnectionStore } from '../../store/connection';
import { spacing } from '../../theme/tokens';
import { Banner } from '../../ui/Feedback';
import { ChoiceRow, SwitchRow } from '../../ui/Form';
import { Row, Rows } from '../../ui/Mu';
import { Sheet } from '../../ui/Sheet';
import { Text } from '../../ui/Text';
import { EffortControl } from '../EffortControl';
import { usePlanMode } from '../PlanModeIndicator';
import { SessionVariables } from './SessionVariables';
import { useAutoApprove } from './useAutoApprove';

export function SessionSettingsSheet({ visible, onClose, modes, activeMode, onSelectMode, onOpenProviders }: {
  visible: boolean;
  onClose: () => void;
  modes: ModeInfo[];
  activeMode: string | null;
  onSelectMode: (name: string) => void;
  onOpenProviders: () => void;
}) {
  const sessionName = useConnectionStore(s => s.activeSessionName);
  const provider = useConnectionStore(s => s.activeProvider);
  const model = useConnectionStore(s => s.activeModel);
  const auto = useAutoApprove();
  const plan = usePlanMode();
  return (
    <Sheet visible={visible} onClose={onClose} title="Session settings" testID="session-settings">
      <View style={styles.body}>
        {sessionName ? <Text variant="caption" tone="ink3">For {sessionName}. Changes apply to this session only.</Text> : null}
        <Section title="Model">
          <Rows>
            <Row testID="settings-provider" title="Provider and model" sub={[provider, model].filter(Boolean).join(' · ') || 'Not selected'} onPress={onOpenProviders} />
          </Rows>
          <EffortControl variant="row" />
        </Section>
        <Section title="Safety">
          <SwitchRow label="Auto-approve writes" hint={auto.syncing ? 'Updating session…' : 'Run tools without asking in this session'}
            value={auto.yolo} onValueChange={next => { void auto.update(next); }} disabled={!auto.canToggle} testID="settings-auto-approve" />
          <SwitchRow label="Plan mode (read-only)" hint={plan.busy ? 'Updating session…' : 'Block file edits, shell writes and other write tools'}
            value={plan.planMode} onValueChange={next => { void plan.setPlanMode(next); }} disabled={!plan.canToggle} testID="settings-plan-mode" />
          {auto.error ? <Banner tone="danger" title="Auto-approve">{auto.error}</Banner> : null}
        </Section>
        {modes.length ? (
          <Section title="Mode">
            {modes.map(mode => (
              <ChoiceRow key={mode.name} label={mode.display_name} description={mode.disabled ? 'Requires a workspace or container' : mode.description || undefined}
                selected={activeMode === mode.name} disabled={mode.disabled} onPress={() => onSelectMode(mode.name)} testID={`settings-mode-${mode.name}`} />
            ))}
          </Section>
        ) : null}
        <Section title="Variables">
          <SessionVariables sessionName={sessionName} active={visible} />
        </Section>
      </View>
    </Sheet>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text variant="label" tone="ink3" accessibilityRole="header">{title}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  body: { gap: spacing.lg },
  section: { gap: spacing.xs },
});
