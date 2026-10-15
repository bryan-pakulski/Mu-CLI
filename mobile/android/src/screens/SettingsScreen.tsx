// Settings as a real screen under More (G§8.3), replacing the long settings
// sheet that used to hang off the chat header. Groups are sunken cards
// (G§9.3); rows are 44dp+ with a Wash icon, label and detail.
//
// Behaviour is unchanged from the sheet: auto-approve toggles still queue
// offline with If-Match and roll back on failure; plan mode uses the same
// hook; appearance now offers System / Paper / Dusk (persisted) and Reduce
// decoration.

import React, { useCallback, useEffect, useState } from 'react';
import { Alert, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation, useIsFocused } from '@react-navigation/native';
import { useTheme, type ThemePreference } from '../theme/ThemeContext';
import { queueIfOffline, useConnectionStore } from '../store/connection';
import { inspectorApi } from '../api/inspector';
import { AdvancedSettingsSheet } from '../components/AdvancedSettingsSheet';
import { ContainerManagerSheet } from '../components/ContainerManagerSheet';
import { EffortControl } from '../components/EffortControl';
import { usePlanMode } from '../components/PlanModeIndicator';
import { spacing } from '../theme/tokens';
import { Banner, Card, Icon, Pressable, Segmented, Switch, Text, type IconName } from '../ui';

export function SettingsScreen() {
  const navigation = useNavigation<any>();
  const focused = useIsFocused();
  const { colors, preference, setPreference, reduceDecoration, setReduceDecoration } = useTheme();
  const activeSessionName = useConnectionStore(s => s.activeSessionName);
  const activeProvider = useConnectionStore(s => s.activeProvider);
  const activeModel = useConnectionStore(s => s.activeModel);
  const isConnected = useConnectionStore(s => s.isConnected);
  const yolo = useConnectionStore(s => s.yolo);
  const setYolo = useConnectionStore(s => s.setYolo);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [containersOpen, setContainersOpen] = useState(false);
  const [yoloSyncing, setYoloSyncing] = useState(false);
  const plan = usePlanMode();

  const refreshYolo = useCallback(async () => {
    if (!isConnected || !activeSessionName) return;
    try {
      const response = await inspectorApi.getVariables(activeSessionName);
      for (const group of response.groups || []) {
        const variable = (group.variables || []).find(item => item.key === 'yolo');
        if (variable) {
          setYolo(Boolean(variable.value));
          return;
        }
      }
    } catch {
      // Preserve the last known value during a transient disconnect.
    }
  }, [activeSessionName, isConnected, setYolo]);

  useEffect(() => {
    if (focused) void refreshYolo();
  }, [focused, refreshYolo]);

  const updateYolo = useCallback(async (next: boolean) => {
    if (!activeSessionName || yoloSyncing) return;
    // Offline toggles join the outbound queue and replay with If-Match on
    // reconnect — the optimistic local value stays visible. The last-known
    // session revision provides CAS protection.
    if (await queueIfOffline('set_variable', { key: 'yolo', value: next }, {
      sessionName: activeSessionName,
      ifMatch: useConnectionStore.getState().sessionRevision ?? undefined,
    })) {
      setYolo(next);
      return;
    }
    const previous = yolo;
    setYolo(next);
    setYoloSyncing(true);
    try {
      const response = await inspectorApi.setVariable('yolo', next, activeSessionName);
      setYolo(Boolean(response.value));
    } catch (error) {
      setYolo(previous);
      Alert.alert('Could not update auto-approve', String(error));
    } finally {
      setYoloSyncing(false);
    }
  }, [activeSessionName, setYolo, yolo, yoloSyncing]);

  const go = (name: string) => () => navigation.navigate(name);
  const needsSession = (name: string) => () => (activeSessionName ? navigation.navigate(name) : navigation.navigate('Tabs', { screen: 'Chat' }));

  return (
    <ScrollView style={{ flex: 1, backgroundColor: 'transparent' }} contentContainerStyle={styles.content} testID="settings-screen">
      {!isConnected ? (
        <Banner tone="danger" glyph="✗" title="Not connected to Mucli" action={{ label: 'Connect', onPress: go('Connection') }} />
      ) : null}

      {yolo ? (
        <Banner tone="caution" glyph="◆" title="Auto-approve is on — Mucli will run tools without asking"
          action={{ label: 'Turn off', onPress: () => { void updateYolo(false); } }} />
      ) : null}

      <Section title="Session">
        <Row icon="sliders-horizontal" label="Mode" detail="Choose the active agent strategy" onPress={needsSession('Modes')} />
        <Row icon="git-branch" label="Agent threads"
          detail={activeSessionName ? 'Switch conversations and review coordination activity' : 'Load a session to see its threads'} onPress={needsSession('Threads')} />
        <Row icon="cpu" label="Provider and model" detail={[activeProvider, activeModel].filter(Boolean).join(' · ') || 'Not selected'} onPress={go('Providers')} />
        <EffortControl variant="row" />
        <Row icon="network" label="Connection" detail={isConnected ? 'Connected to Mucli' : 'Not connected'} onPress={go('Connection')} />
        <Row icon="paperclip" label="Session files"
          detail={activeSessionName ? 'Visualizations, model artifacts, and uploads' : 'Load a session to view files'} onPress={needsSession('Artifacts')} />
      </Section>

      <Section title="Safety">
        <ToggleRow icon="zap" label="Auto-approve writes" detail={yoloSyncing ? 'Updating session…' : 'Run tools without asking in this session'}
          value={yolo} onValueChange={next => { void updateYolo(next); }} disabled={!isConnected || !activeSessionName || yoloSyncing} />
        <ToggleRow icon="lock" label="Plan mode (read-only)" detail={plan.busy ? 'Updating session…' : 'Block file edits, shell writes and other write tools'}
          value={plan.planMode} onValueChange={next => { void plan.setPlanMode(next); }} disabled={!plan.canToggle} />
      </Section>

      <Section title="Runtime">
        <Row icon="box" label="Containers" detail="Create, inspect, start, stop, and remove containers" onPress={() => setContainersOpen(true)} />
        <Row icon="sliders-horizontal" label="Session variables"
          detail={activeSessionName ? 'Grouped runtime overrides for this session' : 'Load a session to edit variables'} onPress={() => setAdvancedOpen(true)} />
      </Section>

      <Section title="Appearance">
        <View style={styles.appearance}>
          <Text variant="label" tone="ink2">Theme</Text>
          <Segmented<ThemePreference>
            accessibilityLabel="Theme"
            testID="theme-preference"
            value={preference}
            onChange={setPreference}
            options={[
              { value: 'system', label: 'System', testID: 'theme-system' },
              { value: 'paper', label: 'Paper', testID: 'theme-paper' },
              { value: 'dusk', label: 'Dusk', testID: 'theme-dusk' },
            ]}
          />
        </View>
        <ToggleRow icon="sparkles" label="Reduce decoration" detail="Replace watercolour art with a flat wash"
          value={reduceDecoration} onValueChange={setReduceDecoration} testID="reduce-decoration" />
      </Section>

      <AdvancedSettingsSheet visible={advancedOpen} onClose={() => setAdvancedOpen(false)} />
      <ContainerManagerSheet visible={containersOpen} onClose={() => setContainersOpen(false)} />
    </ScrollView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text variant="label" tone="ink3" accessibilityRole="header" style={styles.sectionTitle}>{title}</Text>
      <Card variant="sunken" bodyStyle={styles.sectionBody}>{children}</Card>
    </View>
  );
}

function Row({ icon, label, detail, onPress }: { icon: IconName; label: string; detail: string; onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityLabel={label} accessibilityHint={detail} scale={false}
      style={({ pressed }) => [styles.row, pressed ? { backgroundColor: colors.hairline } : null]}>
      <Icon name={icon} color={colors.ink3} />
      <View style={styles.copy}>
        <Text variant="body" style={{ fontWeight: '500' }}>{label}</Text>
        <Text variant="caption" tone="ink3" numberOfLines={2}>{detail}</Text>
      </View>
      <Icon name="chevron-right" size="sm" color={colors.ink3} />
    </Pressable>
  );
}

function ToggleRow({ icon, label, detail, value, onValueChange, disabled, testID }: {
  icon: IconName; label: string; detail: string; value: boolean; onValueChange: (v: boolean) => void; disabled?: boolean; testID?: string;
}) {
  const { colors } = useTheme();
  return (
    <View style={styles.row}>
      <Icon name={icon} color={colors.ink3} />
      <View style={styles.copy}>
        <Text variant="body" style={{ fontWeight: '500' }}>{label}</Text>
        <Text variant="caption" tone="ink3">{detail}</Text>
      </View>
      <Switch testID={testID} label={label} value={value} onValueChange={onValueChange} disabled={disabled} />
    </View>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  section: { gap: spacing.sm },
  sectionTitle: { marginLeft: spacing.xs },
  sectionBody: { paddingVertical: spacing.xs, paddingHorizontal: spacing.md, gap: 0 },
  row: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.sm, borderRadius: 10 },
  copy: { flex: 1 },
  appearance: { gap: spacing.sm, paddingVertical: spacing.sm },
});
