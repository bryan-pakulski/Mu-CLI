import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Alert, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TextInput, View } from 'react-native';
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
import { ContainerBuildProgress, ContainerProgressLog } from './ContainerBuildProgress';
import { WorkspacePathField } from './WorkspacePathField';
import { fontSizes, fontWeights, fonts, radii, spacing, tracking, typography, touchTarget, elevation } from '../theme/tokens';
import { ModalFrame, Pressable, Text, Icon, type IconName } from '../ui';

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
    <ModalFrame statusBarTranslucent={false} transparent={false} visible={visible} animationType="slide" onRequestClose={onClose} containerStyle={{ backgroundColor: colors.canvas }}>
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={[styles.header, { paddingTop: spacing.base, borderBottomColor: colors.hairline }]}>
          <Pressable accessibilityLabel={mode === 'form' ? 'Back' : 'Close'} onPress={mode === 'form' ? () => setMode('list') : onClose} style={[styles.iconButton, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
            <Icon name={mode === 'form' ? 'arrow-left' : 'x'} size="md" color={colors.ink} />
          </Pressable>
          <View style={styles.headerCopy}>
            <Text style={[styles.title, { color: colors.ink }]}>{title}</Text>
            <Text variant="xs" dim>{mode === 'list' ? 'Create, edit, clone, and snapshot host environments.' : 'Changes recreate the worker while retaining named volumes.'}</Text>
          </View>
          {mode === 'list' ? (
            <Pressable accessibilityLabel="Create environment" onPress={() => openCreate()} style={[styles.iconButton, { backgroundColor: colors.accent.ink }]}>
              <Icon name="plus" size="md" color={colors.onInk} />
            </Pressable>
          ) : <View style={styles.iconButton} />}
        </View>

        {mode === 'list' ? (
          <ScrollView contentContainerStyle={[styles.content, { paddingBottom: spacing.lg }]}>
            {loading ? <ActivityIndicator color={colors.accent.ink} style={styles.loader} /> : null}
            {error ? <Text variant="xs" style={{ color: colors.danger.ink, marginBottom: spacing.md }}>{error}</Text> : null}
            <SectionTitle label="Environments" detail={`${containers.length} managed`} />
            {containers.length === 0 && !loading ? <EmptyCard text="No managed environments." /> : null}
            {containers.map(container => (
              <Pressable key={container.name} onPress={() => openEdit(container, false)} activeOpacity={0.72} style={[styles.card, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
                <View style={styles.cardHead}>
                  <View style={styles.cardCopy}><Text variant="sm" style={styles.cardTitle}>{container.name}</Text><Text variant="xs" dim numberOfLines={1}>{container.template_name ? `Template · ${container.template_name}` : container.image}</Text></View>
                  <View style={styles.cardTools}>
                    <View style={[styles.status, { backgroundColor: container.status === 'running' ? colors.ok.wash : colors.sunken }]}><Text variant="xs" style={{ color: container.status === 'running' ? colors.ok.ink : colors.ink2 }}>{container.status}</Text></View>
                    <Pressable onPress={event => { event.stopPropagation(); runAction(container, container.status === 'running' ? 'stop' : 'start'); }} style={styles.smallIcon} accessibilityLabel={`${container.status === 'running' ? 'Stop' : 'Start'} ${container.name}`}><Icon name={container.status === 'running' ? 'square' : 'play'} size="md" color={colors.ink3} /></Pressable>
                    <Pressable onPress={event => { event.stopPropagation(); setActionContainer(container); }} style={styles.smallIcon} accessibilityLabel={`More actions for ${container.name}`}><Icon name="more-horizontal" size="md" color={colors.ink3} /></Pressable>
                  </View>
                </View>
                <Text variant="xs" dim>{container.attached_sessions?.length ? `Sessions · ${container.attached_sessions.join(', ')}` : 'No attached sessions'}</Text>
                <ContainerStatsPanel stats={containerStats[container.name]} status={container.status} />
                <View style={styles.cardHint}><Text variant="xs" dim>Open configuration</Text><Icon name="arrow-right" size="sm" color={colors.ink3} /></View>
              </Pressable>
            ))}

            <SectionTitle label="Templates" detail={`${templates.length} saved`} />
            {templates.length === 0 ? <EmptyCard text="Snapshots will appear here." /> : null}
            {templates.map(template => (
              <Pressable key={template.name} onPress={() => openCreate(template.name)} activeOpacity={0.72} style={[styles.templateRow, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
                <View style={styles.cardCopy}><Text variant="sm" style={styles.cardTitle}>{template.name}</Text><Text variant="xs" dim numberOfLines={2}>{template.description || template.image}</Text></View>
                <Icon name="arrow-right" size="sm" color={colors.ink3} />
                <Pressable onPress={event => { event.stopPropagation(); Alert.alert('Delete template?', template.name, [{text:'Cancel',style:'cancel'},{text:'Delete',style:'destructive',onPress:async()=>{await containersApi.removeTemplate(template.name);load();}}]); }} style={styles.smallIcon} accessibilityLabel={`Delete template ${template.name}`}><Icon name="trash-2" size="md" color={colors.ink3} /></Pressable>
              </Pressable>
            ))}
          </ScrollView>
        ) : (
          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.content, { paddingBottom: spacing['4xl'] }]}>
            <FieldLabel label="Name" />
            <TextInput value={name} onChangeText={setName} editable={!editingName} autoCapitalize="none" autoCorrect={false} placeholder="research-box" placeholderTextColor={colors.ink3} style={[styles.input, { color: colors.ink, backgroundColor: colors.canvas, borderColor: colors.hairline }]} />
            <FieldLabel label="Base" />
            <View style={styles.segment}>
              <Segment selected={source === 'dockerfile'} label="Dockerfile" onPress={() => setSource('dockerfile')} />
              <Segment selected={source === 'template'} label="Template" onPress={() => setSource('template')} />
            </View>
            {source === 'template' ? (
              <View style={styles.templateChoices}>{templates.map(template => <Pressable key={template.name} onPress={() => setTemplateName(template.name)} accessibilityState={{ selected: templateName === template.name }} style={[styles.templateChoice, { backgroundColor: templateName === template.name ? colors.accent.wash : colors.surface, borderWidth: 1, borderColor: templateName === template.name ? colors.accent.ink : colors.hairline }]}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{template.name}</Text><Text variant="xs" dim>{template.description || 'Container snapshot'}</Text></Pressable>)}</View>
            ) : (
              <View style={styles.editorGrid}>
                <EditorCard title="Dockerfile" detail={`${countLines(dockerfile)} lines`} icon="file-text" onPress={() => setEditor('dockerfile')} />
                <EditorCard title="Network policy" detail={`${countLines(allow)} allowed · ${countLines(deny)} blocked`} icon="globe" onPress={() => setEditor('network')} />
              </View>
            )}

            <FieldLabel label="Folder mounts" optional />
            {mounts.map((mount, index) => <View key={`${mount.host_path}-${index}`} style={[styles.mountRow, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><View style={styles.cardCopy}><Text variant="xs" style={{ fontWeight: fontWeights.semibold }} numberOfLines={1}>{mount.container_path}</Text><Text variant="xs" dim numberOfLines={1}>{mount.host_path} · {mount.mode}</Text></View><Pressable accessibilityLabel="Remove mount" onPress={() => setMounts(current => current.filter((_, i) => i !== index))} style={styles.smallIcon}><Icon name="x" size="md" color={colors.danger.ink} /></Pressable></View>)}
            <WorkspacePathField value={mountHost} onChangeText={value => { setMountHost(value); const base = value.split('/').filter(Boolean).pop(); if (base) setMountTarget(`/workspace/${base}`); }} placeholder="Browse a host folder" />
            <TextInput value={mountTarget} onChangeText={setMountTarget} autoCapitalize="none" autoCorrect={false} placeholder="/workspace/project" placeholderTextColor={colors.ink3} style={[styles.input, { color: colors.ink, backgroundColor: colors.canvas, borderColor: colors.hairline, marginTop: spacing.sm }]} />
            <View style={styles.mountActions}><View style={styles.segmentCompact}><Segment selected={mountMode === 'rw'} label="RW" onPress={() => setMountMode('rw')} compact /><Segment selected={mountMode === 'ro'} label="RO" onPress={() => setMountMode('ro')} compact /></View><Pressable onPress={addMount} disabled={!mountHost.trim() || !mountTarget.trim()} style={[styles.addButton, { borderColor: colors.hairline, opacity: mountHost.trim() && mountTarget.trim() ? 1 : 0.42 }]}><Icon name="plus" size="md" color={colors.ink} /><Text variant="sm" style={{ color: colors.ink, fontWeight: fontWeights.medium }}>Add folder</Text></Pressable></View>

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
            {error ? <Text variant="xs" style={{ color: colors.danger.ink, marginTop: spacing.md }}>{error}</Text> : null}
          </ScrollView>
        )}

        {mode === 'form' ? <View style={[styles.footer, { backgroundColor: colors.canvas, borderTopColor: colors.hairline, paddingBottom: spacing.base }]}><Pressable onPress={submit} disabled={!canSubmit} style={[styles.submit, { backgroundColor: canSubmit ? colors.accent.ink : colors.sunken }]}>{saving ? <ActivityIndicator color={colors.onInk} /> : <Text style={{ color: canSubmit ? colors.onInk : colors.ink3, fontWeight: fontWeights.medium }}>{editingName ? 'Save and recreate' : 'Create environment'}</Text>}</Pressable></View> : null}
      </KeyboardAvoidingView>

      <ModalFrame statusBarTranslucent={false} transparent={false} visible={editor !== null} animationType="slide" onRequestClose={() => setEditor(null)} containerStyle={{ backgroundColor: colors.canvas }}>
        <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={[styles.header, { paddingTop: spacing.base, borderBottomColor: colors.hairline }]}><View style={styles.headerCopy}><Text style={[styles.title, { color: colors.ink }]}>{editor === 'dockerfile' ? 'Dockerfile' : 'Network policy'}</Text><Text variant="xs" dim>{editor === 'dockerfile' ? 'Edit the worker image.' : 'Blocklist entries override the allowlist.'}</Text></View><Pressable accessibilityLabel="Close editor" onPress={() => setEditor(null)} style={[styles.iconButton, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><Icon name="x" size="md" color={colors.ink} /></Pressable></View>
          {editor === 'dockerfile' ? <TextInput value={dockerfile} onChangeText={setDockerfile} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} spellCheck={false} style={[styles.fullEditor, { color: colors.ink, backgroundColor: colors.code }]} /> : <ScrollView contentContainerStyle={styles.content}><FieldLabel label="Allowlist" /><TextInput value={allow} onChangeText={setAllow} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor, { color: colors.ink, backgroundColor: colors.code, borderColor: colors.hairline }]} /><FieldLabel label="Blocklist" optional /><TextInput value={deny} onChangeText={setDeny} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor, { color: colors.ink, backgroundColor: colors.code, borderColor: colors.hairline }]} /></ScrollView>}
        </KeyboardAvoidingView>
      </ModalFrame>

      <ModalFrame visible={actionContainer !== null} transparent animationType="fade" onRequestClose={() => setActionContainer(null)} edges={['bottom']} statusBarTranslucent>
        <View style={[styles.overlay, { backgroundColor: colors.scrim }]}>
          <Pressable style={styles.sheetBackdrop} activeOpacity={1} onPress={() => setActionContainer(null)} accessibilityLabel="Close environment actions" />
          <View style={[styles.actionSheet, elevation(1, colors, isDark)]}>
            <View style={styles.actionSheetHead}><View style={styles.cardCopy}><Text style={[styles.title, { color: colors.ink }]}>{actionContainer?.name}</Text><Text variant="xs" dim>Environment actions</Text></View><Pressable accessibilityLabel="Close actions" onPress={() => setActionContainer(null)} style={styles.smallIcon}><Icon name="x" size="md" color={colors.ink3} /></Pressable></View>
            {actionContainer ? <>
              <ActionRow icon="refresh-cw" label="Restart" onPress={() => { const item=actionContainer; setActionContainer(null); runAction(item, 'restart'); }} />
              <ActionRow icon="copy" label="Clone configuration" onPress={() => { const item=actionContainer; setActionContainer(null); openEdit(item, true); }} />
              <ActionRow icon="layers" label="Create template" onPress={() => { const item=actionContainer; setActionContainer(null); setSnapshotContainer(item.name); setSnapshotName(item.name.replace(/^mucli-/, '')); setSnapshotDescription(''); }} />
              <ActionRow icon="trash-2" label="Remove environment" destructive onPress={() => { const item=actionContainer; setActionContainer(null); remove(item); }} />
            </> : null}
          </View>
        </View>
      </ModalFrame>

      <ModalFrame statusBarTranslucent={false} visible={snapshotContainer !== null} transparent animationType="fade" onRequestClose={() => setSnapshotContainer(null)} edges={['top', 'bottom']}>
        <View style={[styles.overlay, { backgroundColor: colors.scrim }]}><View style={[styles.snapshotCard, elevation(2, colors, isDark)]}><Text style={[styles.title, { color: colors.ink }]}>Create template</Text><TextInput value={snapshotName} onChangeText={setSnapshotName} placeholder="template-name" placeholderTextColor={colors.ink3} style={[styles.input, { color: colors.ink, backgroundColor: colors.canvas, borderColor: colors.hairline }]} /><TextInput value={snapshotDescription} onChangeText={setSnapshotDescription} placeholder="Description" placeholderTextColor={colors.ink3} style={[styles.input, { color: colors.ink, backgroundColor: colors.canvas, borderColor: colors.hairline }]} /><View style={styles.snapshotActions}><Pressable onPress={() => setSnapshotContainer(null)} style={styles.cancel}><Text variant="sm">Cancel</Text></Pressable><Pressable onPress={createSnapshot} style={[styles.smallButton, { backgroundColor: colors.accent.ink }]}><Text variant="sm" style={{ color: colors.onInk, fontWeight: fontWeights.medium }}>Snapshot</Text></Pressable></View></View></View>
      </ModalFrame>
    </ModalFrame>
  );
}

function SectionTitle({ label, detail }: { label: string; detail: string }) { return <View style={styles.sectionTitle}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{label}</Text><Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{detail}</Text></View>; }
function FieldLabel({ label, optional = false }: { label: string; optional?: boolean }) { return <View style={styles.fieldLabel}><Text variant="xs" style={{ fontWeight: fontWeights.semibold }}>{label}</Text>{optional ? <Text variant="xs" dim>optional</Text> : null}</View>; }
function EmptyCard({ text }: { text: string }) { const { colors } = useTheme(); return <View style={[styles.empty, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><Text variant="xs" dim>{text}</Text></View>; }
function Segment({ selected, label, onPress, compact = false }: { selected: boolean; label: string; onPress: () => void; compact?: boolean }) { const { colors } = useTheme(); return <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected }} style={[compact ? styles.segmentButtonCompact : styles.segmentButton, { backgroundColor: selected ? colors.accent.wash : 'transparent', borderColor: selected ? colors.accent.wash : colors.hairline }]}><Text variant="sm" style={{ color: selected ? colors.accent.ink : colors.ink2, fontWeight: selected ? fontWeights.semibold : fontWeights.medium }}>{label}</Text></Pressable>; }
function ActionRow({ icon, label, onPress, destructive = false }: { icon: IconName; label: string; onPress: () => void; destructive?: boolean }) { const { colors } = useTheme(); return <Pressable onPress={onPress} style={styles.actionRow}><Icon name={icon} size="md" color={destructive ? colors.danger.ink : colors.ink3} /><Text variant="sm" style={{ flex: 1, color: destructive ? colors.danger.ink : colors.ink, fontWeight: fontWeights.semibold }}>{label}</Text><Icon name="chevron-right" size="sm" color={colors.ink3} /></Pressable>; }
function EditorCard({ title, detail, icon, onPress }: { title: string; detail: string; icon: IconName; onPress: () => void }) { const { colors } = useTheme(); return <Pressable onPress={onPress} style={[styles.editorCard, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><Icon name={icon} size="md" color={colors.ink2} /><View style={styles.cardCopy}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{title}</Text><Text variant="xs" dim>{detail}</Text></View><Icon name="maximize-2" size="md" color={colors.ink3} /></Pressable>; }
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
