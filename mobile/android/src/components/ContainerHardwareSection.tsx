import React, { useMemo, useState } from 'react';

const MUCLI_CONTAINER_HARDWARE_V1 = true;
import { StyleSheet, TextInput, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type {
  ContainerDevice,
  ContainerHardwareCapabilities,
} from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { fontSizes, fontWeights, radii, spacing, touchTarget, fonts } from '../theme/tokens';

interface Props {
  capabilities?: ContainerHardwareCapabilities | null;
  gpuRequest: string;
  onGpuRequestChange: (value: string) => void;
  devices: ContainerDevice[];
  onDevicesChange: (value: ContainerDevice[]) => void;
}

type GpuMode = 'none' | 'all' | 'selected';

function modeFor(value: string): GpuMode {
  if (!value) return 'none';
  if (value === 'all') return 'all';
  return 'selected';
}

function idsFor(value: string): string[] {
  if (!value || value === 'all') return [];
  return value.replace(/^device=/, '').split(',').map(item => item.trim()).filter(Boolean);
}

export function ContainerHardwareSection({
  capabilities,
  gpuRequest,
  onGpuRequestChange,
  devices,
  onDevicesChange,
}: Props) {
  const { colors } = useTheme();
  const [deviceHost, setDeviceHost] = useState('');
  const [deviceTarget, setDeviceTarget] = useState('');
  const [permissions, setPermissions] = useState<'r' | 'rw' | 'rwm'>('rwm');
  const gpu = capabilities?.gpu;
  const selectedGpuIds = useMemo(() => new Set(idsFor(gpuRequest)), [gpuRequest]);
  const gpuMode = modeFor(gpuRequest);

  const setMode = (mode: GpuMode) => {
    if (mode === 'none') onGpuRequestChange('');
    else if (mode === 'all') onGpuRequestChange('all');
    else {
      const first = gpu?.devices?.[0]?.id || gpu?.devices?.[0]?.uuid || '';
      onGpuRequestChange(first ? `device=${first}` : 'device=');
    }
  };

  const toggleGpu = (id: string) => {
    const next = new Set(selectedGpuIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    onGpuRequestChange(next.size ? `device=${Array.from(next).join(',')}` : '');
  };

  const addDevice = (hostPath: string, targetPath?: string) => {
    const host = hostPath.trim();
    const target = (targetPath || host).trim();
    if (!host || !target) return;
    if (devices.some(item => item.host_path === host || item.container_path === target)) return;
    onDevicesChange([
      ...devices,
      { host_path: host, container_path: target, permissions },
    ]);
    setDeviceHost('');
    setDeviceTarget('');
  };

  return (
    <View style={styles.section}>
      <View style={styles.heading}>
        <View style={styles.headingCopy}>
          <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>Hardware</Text>
          <Text variant="xs" dim>GPU acceleration and explicit host device passthrough.</Text>
        </View>
        <Ionicons name="hardware-chip-outline" size={20} color={colors.ink2} />
      </View>

      <Text variant="xs" style={styles.label}>GPU access</Text>
      <View style={styles.segment}>
        <Choice label="None" selected={gpuMode === 'none'} onPress={() => setMode('none')} />
        <Choice label="All" selected={gpuMode === 'all'} disabled={!gpu?.supported} onPress={() => setMode('all')} />
        <Choice label="Selected" selected={gpuMode === 'selected'} disabled={!gpu?.supported || !(gpu?.devices?.length)} onPress={() => setMode('selected')} />
      </View>
      <Text variant="xs" style={{ color: gpu?.supported ? colors.ok.ink : colors.ink3, marginBottom: spacing.sm }}>
        {gpu?.reason || 'Hardware capability has not been loaded.'}
      </Text>

      {gpuMode === 'selected' && gpu?.devices?.length ? (
        <View style={styles.detectedList}>
          {gpu.devices.map(item => {
            const id = String(item.id || item.index || item.uuid);
            const active = selectedGpuIds.has(id);
            return (
              <TouchableOpacity
                key={item.uuid || id}
                onPress={() => toggleGpu(id)}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: active }}
                style={[styles.detectedRow, { backgroundColor: active ? colors.accent.wash : colors.surface, borderWidth: 1, borderColor: active ? colors.accent.ink : colors.hairline }]}
              >
                <Ionicons name={active ? 'checkbox' : 'square-outline'} size={18} color={active ? colors.accent.ink : colors.ink3} />
                <View style={styles.copy}>
                  <Text variant="xs" style={{ fontWeight: fontWeights.semibold }}>{item.name || `GPU ${id}`}</Text>
                  <Text variant="xs" dim style={styles.mono}>{item.uuid || `index ${id}`}</Text>
                </View>
              </TouchableOpacity>
            );
          })}
        </View>
      ) : null}

      <Text variant="xs" style={[styles.label, { marginTop: spacing.md }]}>Attached devices</Text>
      {devices.map((device, index) => (
        <View key={`${device.host_path}-${index}`} style={[styles.deviceRow, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
          <Ionicons name="hardware-chip-outline" size={17} color={colors.ink3} />
          <View style={styles.copy}>
            <Text variant="xs" style={{ fontWeight: fontWeights.semibold }} numberOfLines={1}>{device.container_path}</Text>
            <Text variant="xs" dim numberOfLines={1}>{device.host_path} · {device.permissions}</Text>
          </View>
          <TouchableOpacity onPress={() => onDevicesChange(devices.filter((_, itemIndex) => itemIndex !== index))} style={styles.removeButton}>
            <Ionicons name="close" size={18} color={colors.danger.ink} />
          </TouchableOpacity>
        </View>
      ))}

      {capabilities?.devices?.length ? (
        <View style={styles.presets}>
          {capabilities.devices.slice(0, 12).map(device => (
            <TouchableOpacity
              key={device.host_path}
              onPress={() => addDevice(device.host_path, device.container_path)}
              style={[styles.preset, { borderColor: colors.hairline }]}
            >
              <Text variant="xs" numberOfLines={1}>{device.name || device.host_path}</Text>
              <Text variant="xs" dim>{device.kind}</Text>
            </TouchableOpacity>
          ))}
        </View>
      ) : null}

      <TextInput
        value={deviceHost}
        onChangeText={value => {
          setDeviceHost(value);
          if (!deviceTarget || deviceTarget === deviceHost) setDeviceTarget(value);
        }}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="/dev/video0"
        placeholderTextColor={colors.ink3}
        style={[styles.input, { color: colors.ink, backgroundColor: colors.canvas, borderColor: colors.hairline }]}
      />
      <TextInput
        value={deviceTarget}
        onChangeText={setDeviceTarget}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="Container path, e.g. /dev/video0"
        placeholderTextColor={colors.ink3}
        style={[styles.input, { color: colors.ink, backgroundColor: colors.canvas, borderColor: colors.hairline }]}
      />
      <View style={styles.deviceActions}>
        <View style={styles.permissionChoices}>
          {(['r', 'rw', 'rwm'] as const).map(value => (
            <Choice key={value} label={value.toUpperCase()} selected={permissions === value} compact onPress={() => setPermissions(value)} />
          ))}
        </View>
        <TouchableOpacity
          onPress={() => addDevice(deviceHost, deviceTarget)}
          disabled={!deviceHost.trim()}
          style={[styles.addButton, { borderColor: colors.hairline, opacity: deviceHost.trim() ? 1 : 0.42 }]}
        >
          <Ionicons name="add" size={18} color={colors.ink} />
          <Text variant="sm" style={{ color: colors.ink, fontWeight: fontWeights.medium }}>Add device</Text>
        </TouchableOpacity>
      </View>

      <View style={[styles.warning, { backgroundColor: colors.attention.wash }]}>
        <Text variant="sm" importantForAccessibility="no" style={{ color: colors.attention.ink }}>◆</Text>
        <Text variant="xs" style={[styles.warningText, { color: colors.ink2 }]}>
          {capabilities?.warning || 'Device passthrough grants direct access to selected host hardware.'}
        </Text>
      </View>
    </View>
  );
}

function Choice({
  label,
  selected,
  onPress,
  disabled = false,
  compact = false,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const { colors } = useTheme();
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      style={[
        compact ? styles.choiceCompact : styles.choice,
        { backgroundColor: selected ? colors.accent.wash : 'transparent', borderColor: selected ? colors.accent.wash : colors.hairline, opacity: disabled ? 0.42 : 1 },
      ]}
      accessibilityRole="button"
      accessibilityState={{ selected, disabled }}
    >
      <Text variant="sm" style={{ color: selected ? colors.accent.ink : colors.ink2, fontWeight: selected ? fontWeights.semibold : fontWeights.medium }}>{label}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  section: { marginTop: spacing.base },
  heading: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginBottom: spacing.md },
  headingCopy: { flex: 1 },
  label: { fontWeight: fontWeights.semibold, marginBottom: spacing.sm },
  segment: { flexDirection: 'row', gap: spacing.xs, marginBottom: spacing.sm },
  choice: { flex: 1, minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  choiceCompact: { minWidth: touchTarget, minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  detectedList: { gap: spacing.xs },
  detectedRow: { minHeight: 56, borderRadius: radii.sm, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  copy: { flex: 1, minWidth: 0 },
  deviceRow: { minHeight: 56, borderRadius: radii.sm, paddingHorizontal: spacing.md, marginBottom: spacing.xs, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  presets: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginBottom: spacing.sm },
  preset: { maxWidth: '48%', minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  input: { minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.md, fontSize: fontSizes.base, marginBottom: spacing.sm },
  deviceActions: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  permissionChoices: { flexDirection: 'row', gap: spacing.xs },
  addButton: { minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  warning: { minHeight: 56, borderRadius: radii.sm, padding: spacing.md, marginTop: spacing.md, flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  warningText: { flex: 1 },
  mono: { fontFamily: fonts.mono },
  removeButton: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
});
