import React, { useEffect, useMemo, useState } from 'react';
import {
  PanResponder,
  StyleSheet,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { modesApi, ModeInfo } from '../api/modes';
import { useTheme } from '../theme/ThemeContext';
import { DebugScreen } from '../screens/DebugScreen';
import { FeatureExplorerScreen } from '../screens/FeatureExplorerScreen';
import { LoopScreen } from '../screens/LoopScreen';
import { ResearchScreen } from '../screens/ResearchScreen';
import { SecurityScreen } from '../screens/SecurityScreen';
import { TeacherScreen } from '../screens/TeacherScreen';
import { Button } from './Button';
import { Text } from './Text';
import { SafeAreaModal } from './SafeAreaModal';

export type ModeDrawerProps = {
  visible: boolean;
  onClose: () => void;
  onOpenModes: () => void;
};

const MODE_COMPONENTS: Record<string, React.ComponentType | undefined> = {
  debug: DebugScreen,
  feature: FeatureExplorerScreen,
  loop: LoopScreen,
  research: ResearchScreen,
  security: SecurityScreen,
  teacher: TeacherScreen,
};

export function ModeDrawer({ visible, onClose, onOpenModes }: ModeDrawerProps) {
  const { colors } = useTheme();
  const [activeMode, setActiveMode] = useState<ModeInfo | null>(null);
  const [loading, setLoading] = useState(false);

  const swipeResponder = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponder: (_event, gesture) =>
          gesture.dx > 10 && Math.abs(gesture.dx) > Math.abs(gesture.dy) * 1.2,
        onPanResponderRelease: (_event, gesture) => {
          if (gesture.dx > 64) onClose();
        },
      }),
    [onClose],
  );

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    modesApi.list()
      .then(response => {
        setActiveMode(response.modes.find(mode => mode.name === response.current) ?? null);
      })
      .catch(() => setActiveMode(null))
      .finally(() => setLoading(false));
  }, [visible]);

  const ModeContent = activeMode ? MODE_COMPONENTS[activeMode.name] : undefined;

  const openModes = () => {
    onClose();
    onOpenModes();
  };

  return (
    <SafeAreaModal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <TouchableOpacity style={styles.backdrop} onPress={onClose} activeOpacity={1} />
        <View
          {...swipeResponder.panHandlers}
          style={[
            styles.drawer,
            {
              backgroundColor: colors.glassStrong,
              borderLeftColor: colors.hairline,
              paddingTop: 16,
            },
          ]}
        >
          <View style={[styles.header, { borderBottomColor: colors.hairline }]}>
            <View style={styles.headerCopy}>
              <Text variant="xs" dim>Active mode</Text>
              <Text style={[styles.title, { color: colors.text }]} numberOfLines={1}>
                {activeMode?.display_name || (loading ? 'Loading…' : 'Default mode')}
              </Text>
              <Text variant="xs" dim numberOfLines={2}>
                {activeMode?.description || 'No mode-specific explorer is active.'}
              </Text>
            </View>
            <TouchableOpacity onPress={onClose} style={styles.iconButton} accessibilityLabel="Close mode panel">
              <Ionicons name="close" size={20} color={colors.textDim} />
            </TouchableOpacity>
          </View>

          <View style={styles.content}>
            {ModeContent ? (
              <ModeContent />
            ) : (
              <View style={styles.emptyState}>
                <Ionicons name="options-outline" size={25} color={colors.textDim} />
                <Text variant="lg" style={styles.emptyTitle}>No mode explorer</Text>
                <Text variant="sm" dim style={styles.emptyBody}>
                  Select Feature, Research, Debug, Security, Loop, or Teacher mode to expose its dedicated controls here.
                </Text>
                <Button title="Choose a mode" variant="secondary" onPress={openModes} style={styles.modeButton} />
              </View>
            )}
          </View>
        </View>
      </View>
    </SafeAreaModal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, flexDirection: 'row' },
  backdrop: { flex: 1, backgroundColor: 'rgba(5,10,16,0.42)' },
  drawer: {
    width: '92%',
    maxWidth: 520,
    borderLeftWidth: StyleSheet.hairlineWidth,
    elevation: 4,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 18,
    shadowOffset: { width: -6, height: 0 },
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  headerCopy: { flex: 1, paddingRight: 12 },
  title: { fontSize: 18, lineHeight: 24, fontWeight: '600', letterSpacing: -0.25, marginTop: 2 },
  iconButton: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  content: { flex: 1 },
  emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 28 },
  emptyTitle: { fontWeight: '600', marginTop: 12 },
  emptyBody: { textAlign: 'center', maxWidth: 330, marginTop: 6, lineHeight: 21 },
  modeButton: { marginTop: 20, minWidth: 180 },
});
