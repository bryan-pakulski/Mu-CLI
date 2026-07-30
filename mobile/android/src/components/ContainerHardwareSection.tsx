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
          <Text variant="sm" style={{ fontWeight: '700' }}>Hardware</Text>
          <Text variant="xs" dim>GPU acceleration and explicit host device passthrough.</Text>
        </View>
        <Ionicons name="hardware-chip-outline" size={21} color={colors.accent} />
      </View>

      <Text variant="xs" style={styles.label}>GPU access</Text>
      <View style={styles.segment}>
        <Choice label="None" selected={gpuMode === 'none'} onPress={() => setMode('none')} />
        <Choice label="All" selected={gpuMode === 'all'} disabled={!gpu?.supported} onPress={() => setMode('all')} />
        <Choice label="Selected" selected={gpuMode === 'selected'} disabled={!gpu?.supported || !(gpu?.devices?.length)} onPress={() => setMode('selected')} />
      </View>
      <Text variant="xs" style={{ color: gpu?.supported ? colors.success : colors.textDim, marginBottom: 8 }}>
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
                style={[styles.detectedRow, { borderColor: active ? colors.accent : colors.border, backgroundColor: colors.bgLift }]}
              >
                <Ionicons name={active ? 'checkbox' : 'square-outline'} size={18} color={active ? colors.accent : colors.textDim} />
                <View style={styles.copy}>
                  <Text variant="xs" style={{ fontWeight: '700' }}>{item.name || `GPU ${id}`}</Text>
                  <Text variant="xs" dim>{item.uuid || `index ${id}`}</Text>
                </View>
              </TouchableOpacity>
            );
          })}
        </View>
      ) : null}

      <Text variant="xs" style={[styles.label, { marginTop: 12 }]}>Attached devices</Text>
      {devices.map((device, index) => (
        <View key={`${device.host_path}-${index}`} style={[styles.deviceRow, { backgroundColor: colors.bgLift }]}>
          <Ionicons name="hardware-chip-outline" size={17} color={colors.textDim} />
          <View style={styles.copy}>
            <Text variant="xs" style={{ fontWeight: '700' }} numberOfLines={1}>{device.container_path}</Text>
            <Text variant="xs" dim numberOfLines={1}>{device.host_path} · {device.permissions}</Text>
          </View>
          <TouchableOpacity onPress={() => onDevicesChange(devices.filter((_, itemIndex) => itemIndex !== index))}>
            <Ionicons name="close" size={18} color={colors.error} />
          </TouchableOpacity>
        </View>
      ))}

      {capabilities?.devices?.length ? (
        <View style={styles.presets}>
          {capabilities.devices.slice(0, 12).map(device => (
            <TouchableOpacity
              key={device.host_path}
              onPress={() => addDevice(device.host_path, device.container_path)}
              style={[styles.preset, { borderColor: colors.border }]}
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
        placeholderTextColor={colors.textDim}
        style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]}
      />
      <TextInput
        value={deviceTarget}
        onChangeText={setDeviceTarget}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="Container path, e.g. /dev/video0"
        placeholderTextColor={colors.textDim}
        style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]}
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
          style={[styles.addButton, { backgroundColor: deviceHost.trim() ? colors.accent : colors.bgHover }]}
        >
          <Ionicons name="add" size={17} color={deviceHost.trim() ? colors.accentText : colors.textDim} />
          <Text variant="xs" style={{ color: deviceHost.trim() ? colors.accentText : colors.textDim, fontWeight: '700' }}>Add device</Text>
        </TouchableOpacity>
      </View>

      <View style={[styles.warning, { backgroundColor: colors.bgHover }]}>
        <Ionicons name="warning-outline" size={17} color={colors.warning} />
        <Text variant="xs" dim style={styles.warningText}>
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
        { backgroundColor: selected ? colors.text : colors.bgLift, opacity: disabled ? 0.42 : 1 },
      ]}
    >
      <Text variant="xs" style={{ color: selected ? colors.bg : colors.textDim, fontWeight: '700' }}>{label}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  section: { marginTop: 18 },
  heading: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 12 },
  headingCopy: { flex: 1 },
  label: { fontWeight: '700', marginBottom: 7 },
  segment: { flexDirection: 'row', gap: 5, marginBottom: 7 },
  choice: { flex: 1, minHeight: 40, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  choiceCompact: { minWidth: 44, minHeight: 36, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  detectedList: { gap: 6 },
  detectedRow: { minHeight: 56, borderWidth: StyleSheet.hairlineWidth, borderRadius: 13, paddingHorizontal: 11, flexDirection: 'row', alignItems: 'center', gap: 9 },
  copy: { flex: 1, minWidth: 0 },
  deviceRow: { minHeight: 56, borderRadius: 13, paddingHorizontal: 11, marginBottom: 6, flexDirection: 'row', alignItems: 'center', gap: 9 },
  presets: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 9 },
  preset: { maxWidth: '48%', minHeight: 44, borderWidth: StyleSheet.hairlineWidth, borderRadius: 11, paddingHorizontal: 10, paddingVertical: 6 },
  input: { minHeight: 46, borderRadius: 13, paddingHorizontal: 12, fontSize: 14, marginBottom: 7 },
  deviceActions: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  permissionChoices: { flexDirection: 'row', gap: 4 },
  addButton: { minHeight: 38, borderRadius: 11, paddingHorizontal: 11, flexDirection: 'row', alignItems: 'center', gap: 5 },
  warning: { minHeight: 56, borderRadius: 13, padding: 11, marginTop: 11, flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  warningText: { flex: 1, lineHeight: 17 },
});
