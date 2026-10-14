import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import {
  ContainerConfiguration,
  ContainerStats,
  ContainerTemplateSummary,
  ManagedContainer,
  containersApi,
} from '../api/containers';
import {
  sessionsApi,
  ContainerDevice,
  ContainerHardwareCapabilities,
  ContainerMount,
} from '../api/sessions';
import { ContainerHardwareSection } from './ContainerHardwareSection'; // MUCLI_CONTAINER_HARDWARE_V1
import { ContainerStatsPanel } from './ContainerStatsPanel'; // MUCLI_CONTAINER_MONITOR_V1
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { ContainerBuildProgress, ContainerProgressLog } from './ContainerBuildProgress';
import { WorkspacePathField } from './WorkspacePathField';
import { SafeAreaModal } from './SafeAreaModal';
import { fontSizes, fontWeights, fonts, radii, spacing, tracking, typography, touchTarget, elevation } from '../theme/tokens';

export type ContainerManagerSheetProps = {
  visible: boolean;
  onClose: () => void;
};

type ViewMode = 'list' | 'form';
type EditorMode = 'dockerfile' | 'network' | null;

export function ContainerManagerSheet({ visible, onClose }: ContainerManagerSheetProps) {
  const { colors, isDark } = useTheme();
  const [mode, setMode] = useState<ViewMode>('list');
  const [containers, setContainers] = useState<ManagedContainer[]>([]);
  const [containerStats, setContainerStats] = useState<Record<string, ContainerStats>>({});
  const [templates, setTemplates] = useState<ContainerTemplateSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [source, setSource] = useState<'dockerfile' | 'template'>('dockerfile');
  const [templateName, setTemplateName] = useState('');
  const [dockerfile, setDockerfile] = useState('');
  const [allow, setAllow] = useState('');
  const [deny, setDeny] = useState('');
  const [mounts, setMounts] = useState<ContainerMount[]>([]);
  const [mountHost, setMountHost] = useState('');
  const [mountTarget, setMountTarget] = useState('/workspace/project');
  const [mountMode, setMountMode] = useState<'rw' | 'ro'>('rw');
  const [hardware, setHardware] = useState<ContainerHardwareCapabilities | null>(null);
  const [gpuRequest, setGpuRequest] = useState('');
  const [devices, setDevices] = useState<ContainerDevice[]>([]);
  const [editor, setEditor] = useState<EditorMode>(null);
  const [saving, setSaving] = useState(false);
  const [jobMessage, setJobMessage] = useState('');
  const [jobLogs, setJobLogs] = useState<ContainerProgressLog[]>([]);
  const [jobExpanded, setJobExpanded] = useState(false);
  const [jobFailed, setJobFailed] = useState(false);
  const [snapshotContainer, setSnapshotContainer] = useState<string | null>(null);
  const [snapshotName, setSnapshotName] = useState('');
  const [snapshotDescription, setSnapshotDescription] = useState('');
  const [actionContainer, setActionContainer] = useState<ManagedContainer | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setJobMessage('');
    setJobLogs([]);
    setJobExpanded(false);
    setJobFailed(false);
    try {
      const [response, defaults] = await Promise.all([
        containersApi.list(),
        sessionsApi.getContainerDefaults(),
      ]);
      setContainers(response.containers || []);
      setTemplates(response.templates || []);
      setHardware(defaults.hardware || null);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    setMode('list');
    setEditor(null);
    load();
  }, [load, visible]);

  useEffect(() => {
    if (!visible || mode !== 'list') return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const response = await containersApi.stats();
        if (!active) return;
        setContainerStats(response.containers || {});
        timer = setTimeout(poll, Math.max(1500, response.poll_after_ms || 2500));
      } catch {
        if (active) timer = setTimeout(poll, 5000);
      }
    };

    poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [mode, visible]);

  const resetForm = useCallback(async () => {
    setEditingName(null);
    setName('');
    setSource('dockerfile');
    setTemplateName('');
    setMounts([]);
    setMountHost('');
    setMountTarget('/workspace/project');
    setMountMode('rw');
    setGpuRequest('');
    setDevices([]);
    setError(null);
    try {
      const defaults = await sessionsApi.getContainerDefaults();
      setDockerfile(defaults.dockerfile || '');
      setAllow((defaults.egress_allow || []).join('\n'));
      setDeny((defaults.egress_deny || []).join('\n'));
    } catch {
      setDockerfile('');
      setAllow('');
      setDeny('');
    }
  }, []);

  const openCreate = async (template?: string) => {
    await resetForm();
    if (template) {
      setSource('template');
      setTemplateName(template);
      setName(`${template}-env`);
    }
    setMode('form');
  };

  const applyConfiguration = (config: ContainerConfiguration, clone: boolean) => {
    setEditingName(clone ? null : config.container_name);
    setName(clone ? `${config.container_name.replace(/^mucli-/, '')}-copy` : config.container_name);
    setSource(config.template_name ? 'template' : 'dockerfile');
    setTemplateName(config.template_name || '');
    setDockerfile(config.dockerfile || '');
    setAllow((config.egress_allow || []).join('\n'));
    setDeny((config.egress_deny || []).join('\n'));
    setMounts(config.mounts || []);
    setGpuRequest(config.gpu_request || '');
    setDevices(config.devices || []);
    setError(null);
    setMode('form');
  };

  const openEdit = async (container: ManagedContainer, clone: boolean) => {
    try {
      setLoading(true);
      const config = await containersApi.configuration(container.name);
      applyConfiguration(config, clone);
    } catch (cause) {
      Alert.alert('Could not load configuration', String(cause));
    } finally {
      setLoading(false);
    }
  };

  const addMount = () => {
    const host = mountHost.trim();
    const target = mountTarget.trim();
    if (!host || !target) return;
    if (mounts.some(item => item.host_path === host || item.container_path === target)) {
      setError('That host folder or container target is already mounted.');
      return;
    }
    setMounts(current => [...current, { host_path: host, container_path: target, mode: mountMode }]);
    setMountHost('');
    setMountTarget('/workspace/project');
  };

  const pollJob = async (jobId: string) => {
    let after = 0;
    for (;;) {
      const job = await containersApi.job(jobId, after);
      setJobMessage(job.message || job.stage);
      const incoming = job.logs || [];
      if (incoming.length) {
        setJobLogs(current => [...current, ...incoming]);
        for (const line of incoming) after = Math.max(after, line.seq);
      }
      if (job.state === 'ready') return;
      if (job.state === 'error') {
        setJobFailed(true);
        setJobExpanded(true);
        throw new Error(job.detail || job.message);
      }
      await new Promise(resolve => setTimeout(resolve, 700));
    }
  };

  const submit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    setJobLogs([]);
    setJobExpanded(false);
    setJobFailed(false);
    setJobMessage(editingName ? 'Updating environment…' : 'Creating environment…');
    try {
      const payload = {
        name: name.trim(),
        template_name: source === 'template' ? templateName : null,
        dockerfile: source === 'dockerfile' ? dockerfile : null,
        mounts,
        gpu_request: gpuRequest,
        devices,
        egress_allow: source === 'dockerfile' ? splitLines(allow) : null,
        egress_deny: source === 'dockerfile' ? splitLines(deny) : null,
        start: true,
      };
      const result = editingName
        ? await containersApi.update(editingName, payload)
        : await containersApi.create(payload);
      await pollJob(result.job_id);
      await load();
      setJobMessage('');
      setMode('list');
    } catch (cause) {
      setJobFailed(true);
      setJobExpanded(true);
      setJobMessage('Container build failed');
      setError(String(cause));
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (container: ManagedContainer, action: 'start' | 'stop' | 'restart') => {
    try {
      await containersApi.action(container.name, action);
      await load();
    } catch (cause) {
      Alert.alert('Container action failed', String(cause));
    }
  };

  const remove = (container: ManagedContainer) => {
    Alert.alert('Remove environment?', `Remove “${container.name}” and its persistent volumes?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Remove', style: 'destructive', onPress: async () => {
        try { await containersApi.remove(container.name); await load(); }
        catch (cause) { Alert.alert('Remove failed', String(cause)); }
      } },
    ]);
  };

  const createSnapshot = async () => {
    if (!snapshotContainer || !snapshotName.trim()) return;
    try {
      await containersApi.snapshot(snapshotContainer, snapshotName.trim(), snapshotDescription.trim());
      setSnapshotContainer(null);
      await load();
    } catch (cause) {
      Alert.alert('Template creation failed', String(cause));
    }
  };

  const canSubmit = Boolean(name.trim() && (source === 'dockerfile' || templateName) && !saving);
  const title = mode === 'list' ? 'Container management' : editingName ? 'Edit environment' : 'Create environment';

  return (
    <SafeAreaModal visible={visible} animationType="slide" onRequestClose={onClose} containerStyle={{ backgroundColor: colors.bg }}>
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={[styles.header, { paddingTop: spacing.base, borderBottomColor: colors.hairline }]}>
          <TouchableOpacity onPress={mode === 'form' ? () => setMode('list') : onClose} style={[styles.iconButton, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}>
            <Ionicons name={mode === 'form' ? 'arrow-back' : 'close'} size={20} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.headerCopy}>
            <Text style={[styles.title, { color: colors.text }]}>{title}</Text>
            <Text variant="xs" dim>{mode === 'list' ? 'Create, edit, clone, and snapshot host environments.' : 'Changes recreate the worker while retaining named volumes.'}</Text>
          </View>
          {mode === 'list' ? (
            <TouchableOpacity onPress={() => openCreate()} style={[styles.iconButton, { backgroundColor: colors.accent }]}>
              <Ionicons name="add" size={20} color={colors.accentOn} />
            </TouchableOpacity>
          ) : <View style={styles.iconButton} />}
        </View>

        {mode === 'list' ? (
          <ScrollView contentContainerStyle={[styles.content, { paddingBottom: spacing.lg }]}>
            {loading ? <ActivityIndicator color={colors.accent} style={styles.loader} /> : null}
            {error ? <Text variant="xs" style={{ color: colors.error, marginBottom: spacing.md }}>{error}</Text> : null}
            <SectionTitle label="Environments" detail={`${containers.length} managed`} />
            {containers.length === 0 && !loading ? <EmptyCard text="No managed environments." /> : null}
            {containers.map(container => (
              <TouchableOpacity key={container.name} onPress={() => openEdit(container, false)} activeOpacity={0.72} style={[styles.card, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}>
                <View style={styles.cardHead}>
                  <View style={styles.cardCopy}><Text variant="sm" style={styles.cardTitle}>{container.name}</Text><Text variant="xs" dim numberOfLines={1}>{container.template_name ? `Template · ${container.template_name}` : container.image}</Text></View>
                  <View style={styles.cardTools}>
                    <View style={[styles.status, { backgroundColor: container.status === 'running' ? colors.successSubtle : colors.bgHover }]}><Text variant="xs" style={{ color: container.status === 'running' ? colors.success : colors.textSoft }}>{container.status}</Text></View>
                    <TouchableOpacity onPress={event => { event.stopPropagation(); runAction(container, container.status === 'running' ? 'stop' : 'start'); }} style={styles.smallIcon} accessibilityLabel={`${container.status === 'running' ? 'Stop' : 'Start'} ${container.name}`}><Ionicons name={container.status === 'running' ? 'stop-outline' : 'play-outline'} size={18} color={colors.textDim} /></TouchableOpacity>
                    <TouchableOpacity onPress={event => { event.stopPropagation(); setActionContainer(container); }} style={styles.smallIcon} accessibilityLabel={`More actions for ${container.name}`}><Ionicons name="ellipsis-horizontal" size={19} color={colors.textDim} /></TouchableOpacity>
                  </View>
                </View>
                <Text variant="xs" dim>{container.attached_sessions?.length ? `Sessions · ${container.attached_sessions.join(', ')}` : 'No attached sessions'}</Text>
                <ContainerStatsPanel stats={containerStats[container.name]} status={container.status} />
                <View style={styles.cardHint}><Text variant="xs" dim>Open configuration</Text><Ionicons name="arrow-forward" size={16} color={colors.textDim} /></View>
              </TouchableOpacity>
            ))}

            <SectionTitle label="Templates" detail={`${templates.length} saved`} />
            {templates.length === 0 ? <EmptyCard text="Snapshots will appear here." /> : null}
            {templates.map(template => (
              <TouchableOpacity key={template.name} onPress={() => openCreate(template.name)} activeOpacity={0.72} style={[styles.templateRow, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}>
                <View style={styles.cardCopy}><Text variant="sm" style={styles.cardTitle}>{template.name}</Text><Text variant="xs" dim numberOfLines={2}>{template.description || template.image}</Text></View>
                <Ionicons name="arrow-forward" size={17} color={colors.textDim} />
                <TouchableOpacity onPress={event => { event.stopPropagation(); Alert.alert('Delete template?', template.name, [{text:'Cancel',style:'cancel'},{text:'Delete',style:'destructive',onPress:async()=>{await containersApi.removeTemplate(template.name);load();}}]); }} style={styles.smallIcon} accessibilityLabel={`Delete template ${template.name}`}><Ionicons name="trash-outline" size={18} color={colors.textDim} /></TouchableOpacity>
              </TouchableOpacity>
            ))}
          </ScrollView>
        ) : (
          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.content, { paddingBottom: spacing['4xl'] }]}>
            <FieldLabel label="Name" />
            <TextInput value={name} onChangeText={setName} editable={!editingName} autoCapitalize="none" autoCorrect={false} placeholder="research-box" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bg, borderColor: colors.border }]} />
            <FieldLabel label="Base" />
            <View style={styles.segment}>
              <Segment selected={source === 'dockerfile'} label="Dockerfile" onPress={() => setSource('dockerfile')} />
              <Segment selected={source === 'template'} label="Template" onPress={() => setSource('template')} />
            </View>
            {source === 'template' ? (
              <View style={styles.templateChoices}>{templates.map(template => <TouchableOpacity key={template.name} onPress={() => setTemplateName(template.name)} accessibilityState={{ selected: templateName === template.name }} style={[styles.templateChoice, { backgroundColor: templateName === template.name ? colors.accentSubtle : colors.surface, borderWidth: 1, borderColor: templateName === template.name ? colors.accent : colors.border }]}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{template.name}</Text><Text variant="xs" dim>{template.description || 'Container snapshot'}</Text></TouchableOpacity>)}</View>
            ) : (
              <View style={styles.editorGrid}>
                <EditorCard title="Dockerfile" detail={`${countLines(dockerfile)} lines`} icon="document-text-outline" onPress={() => setEditor('dockerfile')} />
                <EditorCard title="Network policy" detail={`${countLines(allow)} allowed · ${countLines(deny)} blocked`} icon="globe-outline" onPress={() => setEditor('network')} />
              </View>
            )}

            <FieldLabel label="Folder mounts" optional />
            {mounts.map((mount, index) => <View key={`${mount.host_path}-${index}`} style={[styles.mountRow, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}><View style={styles.cardCopy}><Text variant="xs" style={{ fontWeight: fontWeights.semibold }} numberOfLines={1}>{mount.container_path}</Text><Text variant="xs" dim numberOfLines={1}>{mount.host_path} · {mount.mode}</Text></View><TouchableOpacity onPress={() => setMounts(current => current.filter((_, i) => i !== index))} style={styles.smallIcon}><Ionicons name="close" size={18} color={colors.error} /></TouchableOpacity></View>)}
            <WorkspacePathField value={mountHost} onChangeText={value => { setMountHost(value); const base = value.split('/').filter(Boolean).pop(); if (base) setMountTarget(`/workspace/${base}`); }} placeholder="Browse a host folder" />
            <TextInput value={mountTarget} onChangeText={setMountTarget} autoCapitalize="none" autoCorrect={false} placeholder="/workspace/project" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bg, borderColor: colors.border, marginTop: spacing.sm }]} />
            <View style={styles.mountActions}><View style={styles.segmentCompact}><Segment selected={mountMode === 'rw'} label="RW" onPress={() => setMountMode('rw')} compact /><Segment selected={mountMode === 'ro'} label="RO" onPress={() => setMountMode('ro')} compact /></View><TouchableOpacity onPress={addMount} disabled={!mountHost.trim() || !mountTarget.trim()} style={[styles.addButton, { borderColor: colors.border, opacity: mountHost.trim() && mountTarget.trim() ? 1 : 0.42 }]}><Ionicons name="add" size={18} color={colors.text} /><Text variant="sm" style={{ color: colors.text, fontWeight: fontWeights.medium }}>Add folder</Text></TouchableOpacity></View>

            <ContainerHardwareSection
              capabilities={hardware}
              gpuRequest={gpuRequest}
              onGpuRequestChange={setGpuRequest}
              devices={devices}
              onDevicesChange={setDevices}
            />

            {jobMessage ? (
              <ContainerBuildProgress
                message={jobMessage}
                logs={jobLogs}
                expanded={jobExpanded}
                onToggle={() => setJobExpanded(current => !current)}
                running={saving}
                failed={jobFailed}
              />
            ) : null}
            {error ? <Text variant="xs" style={{ color: colors.error, marginTop: spacing.md }}>{error}</Text> : null}
          </ScrollView>
        )}

        {mode === 'form' ? <View style={[styles.footer, { backgroundColor: colors.bg, borderTopColor: colors.hairline, paddingBottom: spacing.base }]}><TouchableOpacity onPress={submit} disabled={!canSubmit} style={[styles.submit, { backgroundColor: canSubmit ? colors.accent : colors.bgHover }]}>{saving ? <ActivityIndicator color={colors.accentOn} /> : <Text style={{ color: canSubmit ? colors.accentOn : colors.textDim, fontWeight: fontWeights.medium }}>{editingName ? 'Save and recreate' : 'Create environment'}</Text>}</TouchableOpacity></View> : null}
      </KeyboardAvoidingView>

      <SafeAreaModal visible={editor !== null} animationType="slide" onRequestClose={() => setEditor(null)} containerStyle={{ backgroundColor: colors.bg }}>
        <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={[styles.header, { paddingTop: spacing.base, borderBottomColor: colors.hairline }]}><View style={styles.headerCopy}><Text style={[styles.title, { color: colors.text }]}>{editor === 'dockerfile' ? 'Dockerfile' : 'Network policy'}</Text><Text variant="xs" dim>{editor === 'dockerfile' ? 'Edit the worker image.' : 'Blocklist entries override the allowlist.'}</Text></View><TouchableOpacity onPress={() => setEditor(null)} style={[styles.iconButton, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}><Ionicons name="close" size={20} color={colors.text} /></TouchableOpacity></View>
          {editor === 'dockerfile' ? <TextInput value={dockerfile} onChangeText={setDockerfile} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} spellCheck={false} style={[styles.fullEditor, { color: colors.text, backgroundColor: colors.codeBg }]} /> : <ScrollView contentContainerStyle={styles.content}><FieldLabel label="Allowlist" /><TextInput value={allow} onChangeText={setAllow} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor, { color: colors.text, backgroundColor: colors.codeBg, borderColor: colors.border }]} /><FieldLabel label="Blocklist" optional /><TextInput value={deny} onChangeText={setDeny} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor, { color: colors.text, backgroundColor: colors.codeBg, borderColor: colors.border }]} /></ScrollView>}
        </KeyboardAvoidingView>
      </SafeAreaModal>

      <SafeAreaModal visible={actionContainer !== null} transparent animationType="fade" onRequestClose={() => setActionContainer(null)} edges={['bottom']} statusBarTranslucent>
        <View style={[styles.overlay, { backgroundColor: colors.scrim }]}>
          <TouchableOpacity style={styles.sheetBackdrop} activeOpacity={1} onPress={() => setActionContainer(null)} accessibilityLabel="Close environment actions" />
          <View style={[styles.actionSheet, elevation(1, colors, isDark)]}>
            <View style={styles.actionSheetHead}><View style={styles.cardCopy}><Text style={[styles.title, { color: colors.text }]}>{actionContainer?.name}</Text><Text variant="xs" dim>Environment actions</Text></View><TouchableOpacity onPress={() => setActionContainer(null)} style={styles.smallIcon}><Ionicons name="close" size={19} color={colors.textDim} /></TouchableOpacity></View>
            {actionContainer ? <>
              <ActionRow icon="refresh-outline" label="Restart" onPress={() => { const item=actionContainer; setActionContainer(null); runAction(item, 'restart'); }} />
              <ActionRow icon="copy-outline" label="Clone configuration" onPress={() => { const item=actionContainer; setActionContainer(null); openEdit(item, true); }} />
              <ActionRow icon="layers-outline" label="Create template" onPress={() => { const item=actionContainer; setActionContainer(null); setSnapshotContainer(item.name); setSnapshotName(item.name.replace(/^mucli-/, '')); setSnapshotDescription(''); }} />
              <ActionRow icon="trash-outline" label="Remove environment" destructive onPress={() => { const item=actionContainer; setActionContainer(null); remove(item); }} />
            </> : null}
          </View>
        </View>
      </SafeAreaModal>

      <SafeAreaModal visible={snapshotContainer !== null} transparent animationType="fade" onRequestClose={() => setSnapshotContainer(null)} edges={['top', 'bottom']}>
        <View style={[styles.overlay, { backgroundColor: colors.scrim }]}><View style={[styles.snapshotCard, elevation(2, colors, isDark)]}><Text style={[styles.title, { color: colors.text }]}>Create template</Text><TextInput value={snapshotName} onChangeText={setSnapshotName} placeholder="template-name" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bg, borderColor: colors.border }]} /><TextInput value={snapshotDescription} onChangeText={setSnapshotDescription} placeholder="Description" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bg, borderColor: colors.border }]} /><View style={styles.snapshotActions}><TouchableOpacity onPress={() => setSnapshotContainer(null)} style={styles.cancel}><Text variant="sm">Cancel</Text></TouchableOpacity><TouchableOpacity onPress={createSnapshot} style={[styles.smallButton, { backgroundColor: colors.accent }]}><Text variant="sm" style={{ color: colors.accentOn, fontWeight: fontWeights.medium }}>Snapshot</Text></TouchableOpacity></View></View></View>
      </SafeAreaModal>
    </SafeAreaModal>
  );
}

function SectionTitle({ label, detail }: { label: string; detail: string }) { return <View style={styles.sectionTitle}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{label}</Text><Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{detail}</Text></View>; }
function FieldLabel({ label, optional = false }: { label: string; optional?: boolean }) { return <View style={styles.fieldLabel}><Text variant="xs" style={{ fontWeight: fontWeights.semibold }}>{label}</Text>{optional ? <Text variant="xs" dim>optional</Text> : null}</View>; }
function EmptyCard({ text }: { text: string }) { const { colors } = useTheme(); return <View style={[styles.empty, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}><Text variant="xs" dim>{text}</Text></View>; }
function Segment({ selected, label, onPress, compact = false }: { selected: boolean; label: string; onPress: () => void; compact?: boolean }) { const { colors } = useTheme(); return <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityState={{ selected }} style={[compact ? styles.segmentButtonCompact : styles.segmentButton, { backgroundColor: selected ? colors.accentSubtle : 'transparent', borderColor: selected ? colors.accentSubtle : colors.border }]}><Text variant="sm" style={{ color: selected ? colors.accentFg : colors.textSoft, fontWeight: selected ? fontWeights.semibold : fontWeights.medium }}>{label}</Text></TouchableOpacity>; }
function ActionRow({ icon, label, onPress, destructive = false }: { icon: keyof typeof Ionicons.glyphMap; label: string; onPress: () => void; destructive?: boolean }) { const { colors } = useTheme(); return <TouchableOpacity onPress={onPress} style={styles.actionRow}><Ionicons name={icon} size={19} color={destructive ? colors.error : colors.textDim} /><Text variant="sm" style={{ flex: 1, color: destructive ? colors.error : colors.text, fontWeight: fontWeights.semibold }}>{label}</Text><Ionicons name="chevron-forward" size={17} color={colors.textDim} /></TouchableOpacity>; }
function EditorCard({ title, detail, icon, onPress }: { title: string; detail: string; icon: keyof typeof Ionicons.glyphMap; onPress: () => void }) { const { colors } = useTheme(); return <TouchableOpacity onPress={onPress} style={[styles.editorCard, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}><Ionicons name={icon} size={20} color={colors.textSoft} /><View style={styles.cardCopy}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{title}</Text><Text variant="xs" dim>{detail}</Text></View><Ionicons name="expand-outline" size={18} color={colors.textDim} /></TouchableOpacity>; }
function splitLines(value: string): string[] { return value.split(/[\n,]/).map(item => item.trim()).filter(Boolean); }
function countLines(value: string): number { return value.split(/\r?\n/).filter(item => item.trim()).length; }

const styles = StyleSheet.create({
  root: { flex: 1 }, header: { minHeight: 86, paddingHorizontal: spacing.base, paddingBottom: spacing.base, borderBottomWidth: 1, flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  headerCopy: { flex: 1 }, title: { fontSize: fontSizes.lg, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight }, iconButton: { width: touchTarget, height: touchTarget, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  content: { padding: spacing.base }, loader: { marginVertical: spacing.base }, sectionTitle: { marginTop: spacing.md, marginBottom: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  card: { borderRadius: radii.panel, padding: spacing.base, marginBottom: spacing.sm, gap: spacing.md }, cardHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.md }, cardCopy: { flex: 1 }, cardTitle: { fontWeight: fontWeights.semibold }, cardTools: { flexDirection: 'row', alignItems: 'center', gap: 2 }, status: { borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: spacing.xs },
  cardHint: { paddingTop: spacing.xs, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  templateRow: { borderRadius: radii.sm, padding: spacing.base, marginBottom: spacing.sm, flexDirection: 'row', alignItems: 'center', gap: spacing.sm }, smallButton: { minHeight: touchTarget, borderRadius: radii.sm, paddingHorizontal: spacing.base, alignItems: 'center', justifyContent: 'center' }, smallIcon: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
  empty: { minHeight: 88, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center', marginBottom: spacing.md }, fieldLabel: { marginTop: spacing.base, marginBottom: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  input: { minHeight: touchTarget + spacing.xs, borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.base, fontSize: fontSizes.base }, segment: { flexDirection: 'row', gap: spacing.xs, marginBottom: spacing.md }, segmentButton: { flex: 1, minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' }, segmentCompact: { flexDirection: 'row', gap: spacing.xs }, segmentButtonCompact: { minWidth: 48, minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  templateChoices: { gap: spacing.sm }, templateChoice: { borderRadius: radii.sm, padding: spacing.base, gap: spacing.xs }, editorGrid: { gap: spacing.sm }, editorCard: { minHeight: 64, borderRadius: radii.sm, paddingHorizontal: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  mountRow: { minHeight: 58, borderRadius: radii.sm, paddingHorizontal: spacing.md, marginBottom: spacing.sm, flexDirection: 'row', alignItems: 'center', gap: spacing.sm }, mountActions: { marginTop: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm }, addButton: { minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  progress: { marginTop: spacing.base, borderRadius: radii.sm, padding: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.sm }, footer: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: spacing.base, paddingTop: spacing.md, borderTopWidth: 1 }, submit: { minHeight: touchTarget + spacing.xs, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  fullEditor: { flex: 1, margin: spacing.base, borderRadius: radii.sm, padding: spacing.base, fontFamily: fonts.mono, fontSize: fontSizes.sm, lineHeight: typography.sm.lineHeight }, policyEditor: { minHeight: 220, borderWidth: 1, borderRadius: radii.sm, padding: spacing.base, fontFamily: fonts.mono, fontSize: fontSizes.sm, lineHeight: typography.sm.lineHeight },
  overlay: { flex: 1, justifyContent: 'flex-end', padding: spacing.lg }, sheetBackdrop: { ...StyleSheet.absoluteFillObject }, actionSheet: { borderRadius: radii.lg, padding: spacing.base, marginBottom: spacing.sm }, actionSheetHead: { minHeight: 56, paddingHorizontal: spacing.sm, flexDirection: 'row', alignItems: 'center', gap: spacing.sm }, actionRow: { minHeight: touchTarget + spacing.sm, borderRadius: radii.sm, paddingHorizontal: spacing.sm, flexDirection: 'row', alignItems: 'center', gap: spacing.md }, snapshotCard: { borderRadius: radii.lg, padding: spacing.base, gap: spacing.md }, snapshotActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm }, cancel: { minHeight: touchTarget, paddingHorizontal: spacing.base, alignItems: 'center', justifyContent: 'center' },
});
