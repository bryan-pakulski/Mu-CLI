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
  ContainerTemplateSummary,
  ManagedContainer,
  containersApi,
} from '../api/containers';
import { sessionsApi, ContainerMount } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { ContainerBuildProgress, ContainerProgressLog } from './ContainerBuildProgress';
import { WorkspacePathField } from './WorkspacePathField';
import { SafeAreaModal } from './SafeAreaModal';

export type ContainerManagerSheetProps = {
  visible: boolean;
  onClose: () => void;
};

type ViewMode = 'list' | 'form';
type EditorMode = 'dockerfile' | 'network' | null;

export function ContainerManagerSheet({ visible, onClose }: ContainerManagerSheetProps) {
  const { colors } = useTheme();
  const [mode, setMode] = useState<ViewMode>('list');
  const [containers, setContainers] = useState<ManagedContainer[]>([]);
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
      const response = await containersApi.list();
      setContainers(response.containers || []);
      setTemplates(response.templates || []);
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

  const resetForm = useCallback(async () => {
    setEditingName(null);
    setName('');
    setSource('dockerfile');
    setTemplateName('');
    setMounts([]);
    setMountHost('');
    setMountTarget('/workspace/project');
    setMountMode('rw');
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
        <View style={[styles.header, { paddingTop: 16, borderBottomColor: colors.border }]}>
          <TouchableOpacity onPress={mode === 'form' ? () => setMode('list') : onClose} style={[styles.iconButton, { backgroundColor: colors.bgHover }]}>
            <Ionicons name={mode === 'form' ? 'arrow-back' : 'close'} size={20} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.headerCopy}>
            <Text style={[styles.title, { color: colors.text }]}>{title}</Text>
            <Text variant="xs" dim>{mode === 'list' ? 'Create, edit, clone, and snapshot host environments.' : 'Changes recreate the worker while retaining named volumes.'}</Text>
          </View>
          {mode === 'list' ? (
            <TouchableOpacity onPress={() => openCreate()} style={[styles.iconButton, { backgroundColor: colors.accent }]}>
              <Ionicons name="add" size={21} color={colors.accentText} />
            </TouchableOpacity>
          ) : <View style={styles.iconButton} />}
        </View>

        {mode === 'list' ? (
          <ScrollView contentContainerStyle={[styles.content, { paddingBottom: 24 }]}>
            {loading ? <ActivityIndicator color={colors.accent} style={styles.loader} /> : null}
            {error ? <Text variant="xs" style={{ color: colors.error, marginBottom: 12 }}>{error}</Text> : null}
            <SectionTitle label="Environments" detail={`${containers.length} managed`} />
            {containers.length === 0 && !loading ? <EmptyCard text="No managed environments." /> : null}
            {containers.map(container => (
              <TouchableOpacity key={container.name} onPress={() => openEdit(container, false)} activeOpacity={0.72} style={[styles.card, { backgroundColor: colors.bgLift, borderColor: colors.border }]}>
                <View style={styles.cardHead}>
                  <View style={styles.cardCopy}><Text variant="sm" style={styles.cardTitle}>{container.name}</Text><Text variant="xs" dim numberOfLines={1}>{container.template_name ? `Template · ${container.template_name}` : container.image}</Text></View>
                  <View style={styles.cardTools}>
                    <View style={[styles.status, { backgroundColor: colors.bgHover }]}><Text variant="xs" style={{ color: container.status === 'running' ? colors.success : colors.textDim }}>{container.status}</Text></View>
                    <TouchableOpacity onPress={event => { event.stopPropagation(); runAction(container, container.status === 'running' ? 'stop' : 'start'); }} style={styles.smallIcon} accessibilityLabel={`${container.status === 'running' ? 'Stop' : 'Start'} ${container.name}`}><Ionicons name={container.status === 'running' ? 'stop-outline' : 'play-outline'} size={18} color={colors.textDim} /></TouchableOpacity>
                    <TouchableOpacity onPress={event => { event.stopPropagation(); setActionContainer(container); }} style={styles.smallIcon} accessibilityLabel={`More actions for ${container.name}`}><Ionicons name="ellipsis-horizontal" size={19} color={colors.textDim} /></TouchableOpacity>
                  </View>
                </View>
                <Text variant="xs" dim>{container.attached_sessions?.length ? `Sessions · ${container.attached_sessions.join(', ')}` : 'No attached sessions'}</Text>
                <View style={[styles.cardHint, { borderTopColor: colors.border }]}><Text variant="xs" dim>Open configuration</Text><Ionicons name="arrow-forward" size={16} color={colors.textDim} /></View>
              </TouchableOpacity>
            ))}

            <SectionTitle label="Templates" detail={`${templates.length} saved`} />
            {templates.length === 0 ? <EmptyCard text="Snapshots will appear here." /> : null}
            {templates.map(template => (
              <TouchableOpacity key={template.name} onPress={() => openCreate(template.name)} activeOpacity={0.72} style={[styles.templateRow, { backgroundColor: colors.bgLift, borderColor: colors.border }]}>
                <View style={styles.cardCopy}><Text variant="sm" style={styles.cardTitle}>{template.name}</Text><Text variant="xs" dim numberOfLines={2}>{template.description || template.image}</Text></View>
                <Ionicons name="arrow-forward" size={17} color={colors.textDim} />
                <TouchableOpacity onPress={event => { event.stopPropagation(); Alert.alert('Delete template?', template.name, [{text:'Cancel',style:'cancel'},{text:'Delete',style:'destructive',onPress:async()=>{await containersApi.removeTemplate(template.name);load();}}]); }} style={styles.smallIcon} accessibilityLabel={`Delete template ${template.name}`}><Ionicons name="trash-outline" size={18} color={colors.textDim} /></TouchableOpacity>
              </TouchableOpacity>
            ))}
          </ScrollView>
        ) : (
          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.content, { paddingBottom: 108 }]}>
            <FieldLabel label="Name" />
            <TextInput value={name} onChangeText={setName} editable={!editingName} autoCapitalize="none" autoCorrect={false} placeholder="research-box" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} />
            <FieldLabel label="Base" />
            <View style={styles.segment}>
              <Segment selected={source === 'dockerfile'} label="Dockerfile" onPress={() => setSource('dockerfile')} />
              <Segment selected={source === 'template'} label="Template" onPress={() => setSource('template')} />
            </View>
            {source === 'template' ? (
              <View style={styles.templateChoices}>{templates.map(template => <TouchableOpacity key={template.name} onPress={() => setTemplateName(template.name)} style={[styles.templateChoice, { backgroundColor: templateName === template.name ? colors.bgHover : colors.bgLift, borderColor: templateName === template.name ? colors.accent : colors.border }]}><Text variant="sm" style={{ fontWeight: '600' }}>{template.name}</Text><Text variant="xs" dim>{template.description || 'Container snapshot'}</Text></TouchableOpacity>)}</View>
            ) : (
              <View style={styles.editorGrid}>
                <EditorCard title="Dockerfile" detail={`${countLines(dockerfile)} lines`} icon="document-text-outline" onPress={() => setEditor('dockerfile')} />
                <EditorCard title="Network policy" detail={`${countLines(allow)} allowed · ${countLines(deny)} blocked`} icon="globe-outline" onPress={() => setEditor('network')} />
              </View>
            )}

            <FieldLabel label="Folder mounts" optional />
            {mounts.map((mount, index) => <View key={`${mount.host_path}-${index}`} style={[styles.mountRow, { backgroundColor: colors.bgLift }]}><View style={styles.cardCopy}><Text variant="xs" style={{ fontWeight: '600' }} numberOfLines={1}>{mount.container_path}</Text><Text variant="xs" dim numberOfLines={1}>{mount.host_path} · {mount.mode}</Text></View><TouchableOpacity onPress={() => setMounts(current => current.filter((_, i) => i !== index))}><Ionicons name="close" size={18} color={colors.error} /></TouchableOpacity></View>)}
            <WorkspacePathField value={mountHost} onChangeText={value => { setMountHost(value); const base = value.split('/').filter(Boolean).pop(); if (base) setMountTarget(`/workspace/${base}`); }} placeholder="Browse a host folder" />
            <TextInput value={mountTarget} onChangeText={setMountTarget} autoCapitalize="none" autoCorrect={false} placeholder="/workspace/project" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift, marginTop: 8 }]} />
            <View style={styles.mountActions}><View style={styles.segmentCompact}><Segment selected={mountMode === 'rw'} label="RW" onPress={() => setMountMode('rw')} compact /><Segment selected={mountMode === 'ro'} label="RO" onPress={() => setMountMode('ro')} compact /></View><TouchableOpacity onPress={addMount} disabled={!mountHost.trim() || !mountTarget.trim()} style={[styles.addButton, { backgroundColor: mountHost.trim() && mountTarget.trim() ? colors.accent : colors.bgHover }]}><Ionicons name="add" size={18} color={mountHost.trim() && mountTarget.trim() ? colors.accentText : colors.textDim} /><Text variant="xs" style={{ color: mountHost.trim() && mountTarget.trim() ? colors.accentText : colors.textDim, fontWeight: '700' }}>Add folder</Text></TouchableOpacity></View>

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
            {error ? <Text variant="xs" style={{ color: colors.error, marginTop: 12 }}>{error}</Text> : null}
          </ScrollView>
        )}

        {mode === 'form' ? <View style={[styles.footer, { backgroundColor: colors.bg, borderTopColor: colors.border, paddingBottom: 14 }]}><TouchableOpacity onPress={submit} disabled={!canSubmit} style={[styles.submit, { backgroundColor: canSubmit ? colors.text : colors.bgHover }]}>{saving ? <ActivityIndicator color={colors.bg} /> : <Text style={{ color: canSubmit ? colors.bg : colors.textDim, fontWeight: '700' }}>{editingName ? 'Save and recreate' : 'Create environment'}</Text>}</TouchableOpacity></View> : null}
      </KeyboardAvoidingView>

      <SafeAreaModal visible={editor !== null} animationType="slide" onRequestClose={() => setEditor(null)} containerStyle={{ backgroundColor: colors.bg }}>
        <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={[styles.header, { paddingTop: 16, borderBottomColor: colors.border }]}><View style={styles.headerCopy}><Text style={[styles.title, { color: colors.text }]}>{editor === 'dockerfile' ? 'Dockerfile' : 'Network policy'}</Text><Text variant="xs" dim>{editor === 'dockerfile' ? 'Edit the worker image.' : 'Blocklist entries override the allowlist.'}</Text></View><TouchableOpacity onPress={() => setEditor(null)} style={[styles.iconButton, { backgroundColor: colors.bgHover }]}><Ionicons name="close" size={20} color={colors.text} /></TouchableOpacity></View>
          {editor === 'dockerfile' ? <TextInput value={dockerfile} onChangeText={setDockerfile} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} spellCheck={false} style={[styles.fullEditor, { color: colors.text, backgroundColor: colors.bgLift }]} /> : <ScrollView contentContainerStyle={styles.content}><FieldLabel label="Allowlist" /><TextInput value={allow} onChangeText={setAllow} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor, { color: colors.text, backgroundColor: colors.bgLift }]} /><FieldLabel label="Blocklist" optional /><TextInput value={deny} onChangeText={setDeny} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor, { color: colors.text, backgroundColor: colors.bgLift }]} /></ScrollView>}
        </KeyboardAvoidingView>
      </SafeAreaModal>

      <SafeAreaModal visible={actionContainer !== null} transparent animationType="fade" onRequestClose={() => setActionContainer(null)} edges={['bottom']} statusBarTranslucent>
        <View style={styles.overlay}>
          <TouchableOpacity style={styles.sheetBackdrop} activeOpacity={1} onPress={() => setActionContainer(null)} accessibilityLabel="Close environment actions" />
          <View style={[styles.actionSheet, { backgroundColor: colors.bg, borderColor: colors.border }]}>
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
        <View style={styles.overlay}><View style={[styles.snapshotCard, { backgroundColor: colors.bg, borderColor: colors.border }]}><Text style={[styles.title, { color: colors.text }]}>Create template</Text><TextInput value={snapshotName} onChangeText={setSnapshotName} placeholder="template-name" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} /><TextInput value={snapshotDescription} onChangeText={setSnapshotDescription} placeholder="Description" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} /><View style={styles.snapshotActions}><TouchableOpacity onPress={() => setSnapshotContainer(null)} style={styles.cancel}><Text variant="sm">Cancel</Text></TouchableOpacity><TouchableOpacity onPress={createSnapshot} style={[styles.smallButton, { backgroundColor: colors.text }]}><Text variant="sm" style={{ color: colors.bg, fontWeight: '700' }}>Snapshot</Text></TouchableOpacity></View></View></View>
      </SafeAreaModal>
    </SafeAreaModal>
  );
}

function SectionTitle({ label, detail }: { label: string; detail: string }) { return <View style={styles.sectionTitle}><Text variant="sm" style={{ fontWeight: '700' }}>{label}</Text><Text variant="xs" dim>{detail}</Text></View>; }
function FieldLabel({ label, optional = false }: { label: string; optional?: boolean }) { return <View style={styles.fieldLabel}><Text variant="xs" style={{ fontWeight: '700' }}>{label}</Text>{optional ? <Text variant="xs" dim>optional</Text> : null}</View>; }
function EmptyCard({ text }: { text: string }) { const { colors } = useTheme(); return <View style={[styles.empty, { borderColor: colors.border }]}><Text variant="xs" dim>{text}</Text></View>; }
function Segment({ selected, label, onPress, compact = false }: { selected: boolean; label: string; onPress: () => void; compact?: boolean }) { const { colors } = useTheme(); return <TouchableOpacity onPress={onPress} style={[compact ? styles.segmentButtonCompact : styles.segmentButton, { backgroundColor: selected ? colors.text : 'transparent' }]}><Text variant="xs" style={{ color: selected ? colors.bg : colors.textDim, fontWeight: '700' }}>{label}</Text></TouchableOpacity>; }
function ActionRow({ icon, label, onPress, destructive = false }: { icon: keyof typeof Ionicons.glyphMap; label: string; onPress: () => void; destructive?: boolean }) { const { colors } = useTheme(); return <TouchableOpacity onPress={onPress} style={[styles.actionRow, { borderTopColor: colors.border }]}><Ionicons name={icon} size={19} color={destructive ? colors.error : colors.textDim} /><Text variant="sm" style={{ flex: 1, color: destructive ? colors.error : colors.text, fontWeight: '600' }}>{label}</Text><Ionicons name="chevron-forward" size={17} color={colors.textDim} /></TouchableOpacity>; }
function EditorCard({ title, detail, icon, onPress }: { title: string; detail: string; icon: keyof typeof Ionicons.glyphMap; onPress: () => void }) { const { colors } = useTheme(); return <TouchableOpacity onPress={onPress} style={[styles.editorCard, { backgroundColor: colors.bgLift, borderColor: colors.border }]}><Ionicons name={icon} size={20} color={colors.accent} /><View style={styles.cardCopy}><Text variant="sm" style={{ fontWeight: '600' }}>{title}</Text><Text variant="xs" dim>{detail}</Text></View><Ionicons name="expand-outline" size={18} color={colors.textDim} /></TouchableOpacity>; }
function splitLines(value: string): string[] { return value.split(/[\n,]/).map(item => item.trim()).filter(Boolean); }
function countLines(value: string): number { return value.split(/\r?\n/).filter(item => item.trim()).length; }

const styles = StyleSheet.create({
  root: { flex: 1 }, header: { minHeight: 86, paddingHorizontal: 18, paddingBottom: 14, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'center', gap: 12 },
  headerCopy: { flex: 1 }, title: { fontSize: 22, fontWeight: '700', letterSpacing: -0.5 }, iconButton: { width: 42, height: 42, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  content: { padding: 18 }, loader: { marginVertical: 20 }, sectionTitle: { marginTop: 12, marginBottom: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  card: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 18, padding: 16, marginBottom: 10, gap: 12 }, cardHead: { flexDirection: 'row', alignItems: 'center', gap: 12 }, cardCopy: { flex: 1 }, cardTitle: { fontWeight: '700' }, cardTools: { flexDirection: 'row', alignItems: 'center', gap: 2 }, status: { borderRadius: 999, paddingHorizontal: 9, paddingVertical: 5 },
  cardHint: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  templateRow: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, padding: 14, marginBottom: 8, flexDirection: 'row', alignItems: 'center', gap: 10 }, smallButton: { minHeight: 38, borderRadius: 12, paddingHorizontal: 14, alignItems: 'center', justifyContent: 'center' }, smallIcon: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center' },
  empty: { minHeight: 90, borderWidth: StyleSheet.hairlineWidth, borderStyle: 'dashed', borderRadius: 16, alignItems: 'center', justifyContent: 'center', marginBottom: 12 }, fieldLabel: { marginTop: 16, marginBottom: 7, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  input: { minHeight: 50, borderRadius: 15, paddingHorizontal: 14, fontSize: 15 }, segment: { flexDirection: 'row', gap: 5, marginBottom: 12 }, segmentButton: { flex: 1, minHeight: 44, borderRadius: 13, alignItems: 'center', justifyContent: 'center' }, segmentCompact: { flexDirection: 'row', gap: 4 }, segmentButtonCompact: { minWidth: 48, minHeight: 38, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  templateChoices: { gap: 8 }, templateChoice: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 15, padding: 14, gap: 4 }, editorGrid: { gap: 8 }, editorCard: { minHeight: 66, borderWidth: StyleSheet.hairlineWidth, borderRadius: 15, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 11 },
  mountRow: { minHeight: 58, borderRadius: 14, paddingHorizontal: 12, marginBottom: 7, flexDirection: 'row', alignItems: 'center', gap: 10 }, mountActions: { marginTop: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }, addButton: { minHeight: 40, borderRadius: 12, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', gap: 6 },
  progress: { marginTop: 16, borderRadius: 14, padding: 13, flexDirection: 'row', alignItems: 'center', gap: 10 }, footer: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: 18, paddingTop: 12, borderTopWidth: StyleSheet.hairlineWidth }, submit: { minHeight: 50, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  fullEditor: { flex: 1, margin: 16, borderRadius: 15, padding: 14, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 13, lineHeight: 20 }, policyEditor: { minHeight: 220, borderRadius: 15, padding: 14, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 13, lineHeight: 20 },
  overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end', padding: 20 }, sheetBackdrop: { ...StyleSheet.absoluteFillObject }, actionSheet: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 22, padding: 14, marginBottom: 10 }, actionSheetHead: { minHeight: 58, paddingHorizontal: 6, flexDirection: 'row', alignItems: 'center', gap: 10 }, actionRow: { minHeight: 52, borderTopWidth: StyleSheet.hairlineWidth, paddingHorizontal: 7, flexDirection: 'row', alignItems: 'center', gap: 11 }, snapshotCard: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 20, padding: 18, gap: 12 }, snapshotActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 10 }, cancel: { minHeight: 38, paddingHorizontal: 14, alignItems: 'center', justifyContent: 'center' },
});
