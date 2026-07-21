import React, { useState } from 'react';
import { View, StyleSheet, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { BottomSheet } from './BottomSheet';
import { ConnectionScreen } from '../screens/ConnectionScreen';
import { Text } from './Text';

export function Header() {
  const { colors, spacing } = useTheme();
  const { activeSessionName, activeProvider, activeModel, isConnected } = useConnectionStore();
  const [settingsOpen, setSettingsOpen] = useState(false);

  const providerModel = activeProvider && activeModel
    ? `${activeProvider} · ${activeModel}`
    : activeProvider || 'No provider';

  return (
    <View style={[styles.container, { backgroundColor: colors.bgLift, borderBottomColor: colors.border }]}>
      <View style={styles.left}>
        <Text style={[styles.sessionName, { color: colors.text }]}>
          {activeSessionName || 'No session'}
        </Text>
        <Text style={[styles.providerModel, { color: colors.textDim }]}>
          {providerModel}
        </Text>
      </View>
      <View style={styles.right}>
        <View style={[styles.statusDot, { backgroundColor: isConnected ? colors.success : colors.textDim }]} />
        <TouchableOpacity onPress={() => setSettingsOpen(true)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
          <Ionicons name="settings-outline" size={22} color={colors.textDim} />
        </TouchableOpacity>
      </View>
      <BottomSheet visible={settingsOpen} onClose={() => setSettingsOpen(false)}>
        <ConnectionScreen />
      </BottomSheet>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
  },
  left: { flex: 1 },
  right: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  sessionName: { fontSize: 14, fontWeight: '600' },
  providerModel: { fontSize: 11, marginTop: 2 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
});