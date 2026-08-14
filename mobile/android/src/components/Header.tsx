import React, { useState } from 'react';
import { View, StyleSheet, TouchableOpacity, FlatList, ScrollView } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { BottomSheet } from './BottomSheet';
import { Text } from './Text';
import { sessionsApi } from '../api/sessions';

export type ViewPanel = 'chat' | 'modes' | 'prompts' | 'systemPrompts' | 'teacher' | 'feature' | 'research' | 'security' | 'loop' | 'debug' | 'history' | 'memory' | 'files' | 'skills' | 'audio' | 'traces' | 'providers' | 'connection';

export const VIEW_PANELS: { id: ViewPanel; label: string; icon: keyof typeof Ionicons.glyphMap }[] = [
  { id: 'chat', label: 'Chat', icon: 'chatbubbles' as const },
  { id: 'modes', label: 'Modes', icon: 'options' as const },
  { id: 'prompts', label: 'Prompts', icon: 'chatbox' as const },
  { id: 'systemPrompts', label: 'System Prompts', icon: 'document-text' as const },
  { id: 'teacher', label: 'Teacher', icon: 'school' as const },
  { id: 'feature', label: 'Feature', icon: 'cube' as const },
  { id: 'research', label: 'Research', icon: 'telescope' as const },
  { id: 'security', label: 'Security', icon: 'shield' as const },
  { id: 'loop', label: 'Loop', icon: 'repeat' as const },
  { id: 'debug', label: 'Debug', icon: 'terminal' as const },
  { id: 'history', label: 'History', icon: 'time' as const },
  { id: 'memory', label: 'Memory Center', icon: 'layers' as const },
  { id: 'files', label: 'Files', icon: 'folder' as const },
  { id: 'skills', label: 'Skills', icon: 'sparkles' as const },
  { id: 'audio', label: 'Audio', icon: 'mic' as const },
  { id: 'traces', label: 'Traces', icon: 'analytics' as const },
  { id: 'providers', label: 'Providers', icon: 'server' as const },
  { id: 'connection', label: 'Connection', icon: 'wifi' as const },
];

export type HeaderProps = {
  activeView: ViewPanel;
  onViewChange: (view: ViewPanel) => void;
  onOpenSessions: () => void;
  onOpenInspector: () => void;
};

export function Header({ activeView, onViewChange, onOpenSessions, onOpenInspector }: HeaderProps) {
  const { colors, isDark, toggleTheme } = useTheme();
  const { activeSessionName, activeProvider, activeModel, isConnected, yolo, setYolo, setActiveSession } = useConnectionStore();
  const [viewPickerOpen, setViewPickerOpen] = useState(false);

  const currentPanel = VIEW_PANELS.find(p => p.id === activeView);
  const crumb = [
    activeSessionName || 'no session',
    activeProvider || 'no provider',
    activeModel || 'no model',
  ].join(' · ');

  const handleLeave = async () => {
    try {
      await sessionsApi.unloadActive();
      setActiveSession(null);
    } catch (e) {
      // best effort
    }
  };

  return (
    <View style={[styles.container, { backgroundColor: colors.bgLift, borderBottomColor: colors.border }]}>
      {/* Left: hamburger + brand */}
      <View style={styles.left}>
        <TouchableOpacity onPress={onOpenSessions} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.iconBtn}>
          <Ionicons name="menu" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={[styles.brand, { color: colors.text }]}>μcli</Text>
      </View>

      {/* Center: crumb + view picker */}
      <View style={styles.center}>
        <TouchableOpacity
          onPress={() => setViewPickerOpen(true)}
          style={[styles.viewPicker, { backgroundColor: colors.bg, borderColor: colors.border }]}
          hitSlop={{ top: 4, bottom: 4, left: 4, right: 4 }}
        >
          <Ionicons name={currentPanel?.icon || 'apps'} size={16} color={colors.textDim} />
          <Text style={[styles.viewLabel, { color: colors.text }]} numberOfLines={1}>
            {currentPanel?.label || 'View'}
          </Text>
          <Ionicons name="chevron-down" size={14} color={colors.textDim} />
        </TouchableOpacity>
      </View>

      {/* Right: YOLO + inspector + theme + leave */}
      <View style={styles.right}>
        <TouchableOpacity
          onPress={() => setYolo(!yolo)}
          style={[styles.yoloPill, { backgroundColor: yolo ? colors.accent : 'transparent', borderColor: yolo ? colors.accent : colors.border }]}
          hitSlop={{ top: 4, bottom: 4, left: 4, right: 4 }}
        >
          <Text style={[styles.yoloText, { color: yolo ? '#fff' : colors.textDim }]}>YOLO</Text>
        </TouchableOpacity>

        <TouchableOpacity onPress={onOpenInspector} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.iconBtn}>
          <Ionicons name="construct" size={20} color={colors.textDim} />
        </TouchableOpacity>

        <TouchableOpacity onPress={toggleTheme} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.iconBtn}>
          <Ionicons name={isDark ? 'sunny' : 'moon'} size={20} color={colors.textDim} />
        </TouchableOpacity>

        <TouchableOpacity onPress={handleLeave} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.iconBtn}>
          <Ionicons name="log-out" size={20} color={colors.textDim} />
        </TouchableOpacity>
      </View>

      {/* View picker bottom sheet */}
      <BottomSheet visible={viewPickerOpen} onClose={() => setViewPickerOpen(false)} title="View">
        <FlatList
          data={VIEW_PANELS}
          keyExtractor={item => item.id}
          numColumns={3}
          scrollEnabled={false}
          renderItem={({ item }) => (
            <TouchableOpacity
              onPress={() => {
                onViewChange(item.id);
                setViewPickerOpen(false);
              }}
              style={[
                styles.viewPickerItem,
                { backgroundColor: activeView === item.id ? colors.accent : colors.bg, borderColor: activeView === item.id ? colors.accent : colors.border },
              ]}
            >
              <Ionicons name={item.icon} size={24} color={activeView === item.id ? '#fff' : colors.text} />
              <Text
                style={[styles.viewPickerLabel, { color: activeView === item.id ? '#fff' : colors.text }]}
                numberOfLines={1}
              >
                {item.label}
              </Text>
            </TouchableOpacity>
          )}
        />
      </BottomSheet>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderBottomWidth: 1,
  },
  left: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    marginHorizontal: 8,
  },
  right: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  iconBtn: {
    minHeight: 44,
    minWidth: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  brand: {
    fontSize: 16,
    fontWeight: '700',
  },
  viewPicker: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 6,
    borderWidth: 1,
    minHeight: 36,
  },
  viewLabel: {
    fontSize: 13,
    fontWeight: '500',
  },
  yoloPill: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 12,
    borderWidth: 1,
    minHeight: 28,
    alignItems: 'center',
    justifyContent: 'center',
  },
  yoloText: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  viewPickerItem: {
    flex: 1 / 3,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 16,
    margin: 4,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 80,
  },
  viewPickerLabel: {
    fontSize: 12,
    fontWeight: '500',
  },
});
