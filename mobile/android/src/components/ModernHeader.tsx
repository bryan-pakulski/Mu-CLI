import React, { useState } from 'react';
import { StyleSheet, Switch, TouchableOpacity, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { AdvancedSettingsSheet } from './AdvancedSettingsSheet';
import { ArtifactsSheet } from './ArtifactsSheet';
import { ModernBottomSheet } from './ModernBottomSheet';
import { Text } from './Text';

export type ModernHeaderProps = {
  onOpenSessions: () => void;
  onOpenWorkspace: () => void;
  onOpenTraces: () => void;
  onOpenConnection: () => void;
  onOpenModes: () => void;
  onOpenProviders: () => void;
};

export function ModernHeader({
  onOpenSessions,
  onOpenWorkspace,
  onOpenTraces,
  onOpenConnection,
  onOpenModes,
  onOpenProviders,
}: ModernHeaderProps) {
  const insets = useSafeAreaInsets();
  const { colors, isDark, toggleTheme } = useTheme();
  const {
    activeSessionName,
    activeProvider,
    activeModel,
    isConnected,
    yolo,
    setYolo,
  } = useConnectionStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [artifactsOpen, setArtifactsOpen] = useState(false);

  const sessionTitle = activeSessionName || 'New session';
  const sessionMeta = [activeProvider, activeModel].filter(Boolean).join(' · ') || (isConnected ? 'Connected' : 'Connect to MuCLI');

  const openFromMenu = (action: () => void) => {
    setMenuOpen(false);
    action();
  };

  return (
    <>
      <View
        style={[
          styles.container,
          {
            backgroundColor: colors.bg,
            borderBottomColor: colors.border,
            paddingTop: insets.top + 4,
          },
        ]}
      >
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Open sessions"
          onPress={onOpenSessions}
          style={[styles.iconButton, { backgroundColor: colors.bgLift }]}
        >
          {/* MUCLI_MOBILE_MU_LOGO_V1: same μ mark as the web GUI favicon. */}
          <Text style={[styles.brandMark, { color: colors.accent }]}>μ</Text>
        </TouchableOpacity>

        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel={isConnected ? 'Current session' : 'Configure MuCLI connection'}
          disabled={isConnected}
          onPress={onOpenConnection}
          activeOpacity={0.72}
          style={styles.titleBlock}
        >
          <View style={styles.titleRow}>
            <Text style={[styles.title, { color: colors.text }]} numberOfLines={1}>
              {sessionTitle}
            </Text>
            <View style={[styles.statusDot, { backgroundColor: isConnected ? colors.success : colors.error }]} />
          </View>
          <Text style={[styles.subtitle, { color: isConnected ? colors.textDim : colors.error }]} numberOfLines={1}>
            {sessionMeta}
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Open settings"
          onPress={() => setMenuOpen(true)}
          style={[styles.iconButton, { backgroundColor: colors.bgLift }]}
        >
          <Ionicons name="ellipsis-horizontal" size={22} color={colors.text} />
        </TouchableOpacity>
      </View>

      <ModernBottomSheet visible={menuOpen} onClose={() => setMenuOpen(false)} title="Settings">
        {!isConnected && (
          <TouchableOpacity
            onPress={() => openFromMenu(onOpenConnection)}
            style={[styles.connectionBanner, { backgroundColor: colors.accentSoft }]}
          >
            <View style={[styles.connectionIcon, { backgroundColor: colors.bg }]}>
              <Ionicons name="wifi-outline" size={20} color={colors.accent} />
            </View>
            <View style={styles.menuCopy}>
              <Text variant="base" style={{ color: colors.accent, fontWeight: '700' }}>Connect to MuCLI</Text>
              <Text variant="xs" style={{ color: colors.textSoft }}>Configure a reachable GUI server</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.accent} />
          </TouchableOpacity>
        )}

        <SettingsSection title="SESSION">
          <MenuRow icon="options-outline" label="Mode" detail="Choose the active agent strategy" onPress={() => openFromMenu(onOpenModes)} />
          <MenuRow icon="server-outline" label="Provider and model" detail={[activeProvider, activeModel].filter(Boolean).join(' · ') || 'Not selected'} onPress={() => openFromMenu(onOpenProviders)} />
          <MenuRow icon="wifi-outline" label="Connection" detail={isConnected ? 'Connected to MuCLI' : 'Not connected'} onPress={() => openFromMenu(onOpenConnection)} />
          <MenuRow
            icon="download-outline"
            label="Artifacts"
            detail={activeSessionName ? 'Download or remove session deliverables' : 'Load a session to view artifacts'}
            onPress={() => openFromMenu(() => setArtifactsOpen(true))}
          />
        </SettingsSection>

        <SettingsSection title="BEHAVIOUR">
          <ToggleRow icon="flash-outline" label="Auto-approve writes" detail="YOLO mode" value={yolo} onValueChange={setYolo} />
          <MenuRow icon="grid-outline" label="Workspace tools" detail="Context, workflows, and runtime controls" onPress={() => openFromMenu(onOpenWorkspace)} />
        </SettingsSection>

        <SettingsSection title="ADVANCED">
          <MenuRow
            icon="options-outline"
            label="Session variables"
            detail={activeSessionName ? 'Grouped runtime overrides for this session' : 'Load a session to edit variables'}
            onPress={() => openFromMenu(() => setAdvancedOpen(true))}
          />
        </SettingsSection>

        <SettingsSection title="APPEARANCE">
          <ToggleRow
            icon={isDark ? 'moon-outline' : 'sunny-outline'}
            label="Dark appearance"
            detail="Use the alternate colour scheme"
            value={isDark}
            onValueChange={() => toggleTheme()}
          />
        </SettingsSection>

        <SettingsSection title="DIAGNOSTICS">
          <MenuRow icon="analytics-outline" label="Session trace" detail="Context, tokens, tools, latency, and compaction" onPress={() => openFromMenu(onOpenTraces)} />
        </SettingsSection>

      </ModernBottomSheet>
      <AdvancedSettingsSheet visible={advancedOpen} onClose={() => setAdvancedOpen(false)} />
      <ArtifactsSheet visible={artifactsOpen} onClose={() => setArtifactsOpen(false)} />
    </>
  );
}

function SettingsSection({ title, children }: { title: string; children: React.ReactNode }) {
  const { colors } = useTheme();
  return (
    <View style={styles.section}>
      <Text variant="xs" style={[styles.sectionTitle, { color: colors.textDim }]}>{title}</Text>
      <View style={[styles.sectionBody, { borderColor: colors.border }]}>{children}</View>
    </View>
  );
}

type MenuRowProps = {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  detail: string;
  onPress: () => void;
};

function MenuRow({ icon, label, detail, onPress }: MenuRowProps) {
  const { colors } = useTheme();
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.68} style={styles.menuRow}>
      <View style={[styles.menuIcon, { backgroundColor: colors.bgHover }]}>
        <Ionicons name={icon} size={20} color={colors.text} />
      </View>
      <View style={styles.menuCopy}>
        <Text variant="base" style={{ color: colors.text, fontWeight: '600' }}>{label}</Text>
        <Text variant="xs" dim numberOfLines={2}>{detail}</Text>
      </View>
      <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
    </TouchableOpacity>
  );
}

type ToggleRowProps = Omit<MenuRowProps, 'onPress'> & {
  value: boolean;
  onValueChange: (value: boolean) => void;
};

function ToggleRow({ icon, label, detail, value, onValueChange }: ToggleRowProps) {
  const { colors } = useTheme();
  return (
    <View style={styles.menuRow}>
      <View style={[styles.menuIcon, { backgroundColor: colors.bgHover }]}>
        <Ionicons name={icon} size={20} color={colors.text} />
      </View>
      <View style={styles.menuCopy}>
        <Text variant="base" style={{ fontWeight: '600' }}>{label}</Text>
        <Text variant="xs" dim>{detail}</Text>
      </View>
      <Switch
        value={value}
        onValueChange={onValueChange}
        trackColor={{ false: colors.borderStrong, true: colors.accent }}
        thumbColor={colors.bgLift}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { minHeight: 72, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, paddingBottom: 10, borderBottomWidth: StyleSheet.hairlineWidth },
  iconButton: { width: 44, height: 44, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  brandMark: {
    fontFamily: 'serif',
    fontSize: 32,
    lineHeight: 38,
    fontWeight: '400',
    textAlign: 'center',
    includeFontPadding: false,
  },
  titleBlock: { flex: 1, alignItems: 'center', paddingHorizontal: 12 },
  titleRow: { maxWidth: '100%', flexDirection: 'row', alignItems: 'center', gap: 7 },
  title: { maxWidth: '92%', fontSize: 15, lineHeight: 20, fontWeight: '600' },
  subtitle: { maxWidth: '100%', marginTop: 1, fontSize: 11, lineHeight: 15 },
  statusDot: { width: 7, height: 7, borderRadius: 4 },
  connectionBanner: { minHeight: 68, flexDirection: 'row', alignItems: 'center', borderRadius: 16, paddingHorizontal: 12, marginBottom: 18 },
  connectionIcon: { width: 42, height: 42, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  section: { marginBottom: 18 },
  sectionTitle: { fontWeight: '700', letterSpacing: 0.8, marginBottom: 7, marginLeft: 4 },
  sectionBody: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 17, paddingHorizontal: 12, overflow: 'hidden' },
  menuRow: { minHeight: 68, flexDirection: 'row', alignItems: 'center', paddingVertical: 8 },
  menuIcon: { width: 42, height: 42, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  menuCopy: { flex: 1, marginHorizontal: 14 },
});
