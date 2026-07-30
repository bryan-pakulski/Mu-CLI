import React from 'react';
import { StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { ContainerStats } from '../api/containers';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';

const MUCLI_CONTAINER_MONITOR_V1 = true;

interface Props {
  stats?: ContainerStats;
  status: string;
}

export function ContainerStatsPanel({ stats, status }: Props) {
  const { colors } = useTheme();
  const running = status === 'running';
  const gpu = stats?.gpu;
  const gpuValue = gpu?.requested
    ? gpu.utilization_percent == null
      ? 'N/A'
      : `${formatPercent(gpu.utilization_percent)}`
    : '—';

  const metrics = [
    {
      icon: 'speedometer-outline' as const,
      label: 'CPU',
      value: running && stats ? formatPercent(stats.cpu_percent) : '—',
      progress: running ? stats?.cpu_percent : undefined,
    },
    {
      icon: 'hardware-chip-outline' as const,
      label: 'Memory',
      value: stats ? `${formatBytes(stats.memory_used_bytes)} / ${formatBytes(stats.memory_limit_bytes)}` : '—',
      progress: running ? stats?.memory_percent : undefined,
    },
    {
      icon: 'flash-outline' as const,
      label: 'GPU',
      value: gpuValue,
      progress: gpu?.utilization_percent ?? undefined,
      detail: gpu?.requested && gpu.memory_total_bytes
        ? `${formatBytes(gpu.memory_used_bytes)} / ${formatBytes(gpu.memory_total_bytes)}`
        : undefined,
    },
    {
      icon: 'swap-vertical-outline' as const,
      label: 'Network',
      value: stats
        ? `↓ ${formatRate(stats.network_rx_bytes_per_second)}  ↑ ${formatRate(stats.network_tx_bytes_per_second)}`
        : '—',
      detail: stats
        ? `${formatBytes(stats.network_rx_bytes)} received · ${formatBytes(stats.network_tx_bytes)} sent`
        : undefined,
    },
    {
      icon: 'server-outline' as const,
      label: 'Storage',
      value: stats ? formatBytes(stats.storage_writable_bytes) : '—',
      detail: stats ? `Writable layer · rootfs ${formatBytes(stats.storage_rootfs_bytes)}` : 'Writable container layer',
    },
    {
      icon: 'disc-outline' as const,
      label: 'Block I/O',
      value: stats
        ? `R ${formatBytes(stats.block_read_bytes)} · W ${formatBytes(stats.block_write_bytes)}`
        : '—',
    },
  ];

  return (
    <View style={[styles.root, { borderTopColor: colors.border }]}>
      <View style={styles.heading}>
        <View style={styles.headingCopy}>
          <Text variant="xs" style={{ fontWeight: '700' }}>Live monitor</Text>
          <Text variant="xs" dim>
            {running
              ? `${stats?.pids ?? 0} processes · up ${formatDuration(stats?.uptime_seconds)}${stats?.restart_count ? ` · ${stats.restart_count} restarts` : ''}`
              : `${status} · retained storage ${formatBytes(stats?.storage_writable_bytes || 0)}`}
          </Text>
        </View>
        <View style={[styles.liveDot, { backgroundColor: running ? colors.success : colors.textDim }]} />
      </View>

      <View style={styles.grid}>
        {metrics.map(metric => (
          <View key={metric.label} style={[styles.metric, { backgroundColor: colors.bgHover }]}>
            <View style={styles.metricHead}>
              <Ionicons name={metric.icon} size={14} color={colors.textDim} />
              <Text variant="xs" dim>{metric.label}</Text>
            </View>
            <Text variant="xs" style={styles.value} numberOfLines={1}>{metric.value}</Text>
            {metric.detail ? <Text variant="xs" dim numberOfLines={1}>{metric.detail}</Text> : null}
            {typeof metric.progress === 'number' ? (
              <View style={[styles.track, { backgroundColor: colors.border }]}>
                <View
                  style={[
                    styles.fill,
                    {
                      backgroundColor: colors.accent,
                      width: `${Math.max(0, Math.min(100, metric.progress))}%`,
                    },
                  ]}
                />
              </View>
            ) : null}
          </View>
        ))}
      </View>

      {stats?.gpu?.requested && stats.gpu.scope === 'assigned_device_total' ? (
        <Text variant="xs" dim style={styles.note}>
          GPU values are totals for the assigned physical device.
        </Text>
      ) : null}
      {stats?.error ? <Text variant="xs" style={{ color: colors.error }}>{stats.error}</Text> : null}
    </View>
  );
}

function formatPercent(value?: number | null): string {
  return `${Math.max(0, Number(value) || 0).toFixed(1)}%`;
}

function formatBytes(value?: number | null): string {
  const bytes = Math.max(0, Number(value) || 0);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let amount = bytes;
  let index = 0;
  while (amount >= 1000 && index < units.length - 1) {
    amount /= 1000;
    index += 1;
  }
  return `${amount >= 100 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function formatRate(value?: number | null): string {
  return `${formatBytes(value)}/s`;
}

function formatDuration(value?: number | null): string {
  const seconds = Math.max(0, Number(value) || 0);
  if (!seconds) return '0s';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

const styles = StyleSheet.create({
  root: {
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: 12,
    marginTop: 11,
  },
  heading: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 9,
  },
  headingCopy: { flex: 1 },
  liveDot: { width: 7, height: 7, borderRadius: 4 },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 7,
  },
  metric: {
    width: '48.7%',
    minHeight: 72,
    borderRadius: 12,
    padding: 9,
  },
  metricHead: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    marginBottom: 5,
  },
  value: { fontWeight: '700' },
  track: {
    height: 4,
    borderRadius: 2,
    marginTop: 7,
    overflow: 'hidden',
  },
  fill: { height: 4, borderRadius: 2 },
  note: { marginTop: 8, lineHeight: 16 },
});
