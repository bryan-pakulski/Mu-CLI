import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ProviderInfo, providersApi } from '../api/providers';
import { containersApi, ManagedContainer, ContainerTemplateSummary } from '../api/containers';
import {
  ContainerDevice,
  ContainerHardwareCapabilities,
  ContainerMount,
  SessionType,
  sessionsApi,
} from '../api/sessions';
import { ContainerHardwareSection } from './ContainerHardwareSection'; // MUCLI_CONTAINER_HARDWARE_V1
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { ContainerBuildProgress, ContainerProgressLog } from './ContainerBuildProgress';
import { WorkspacePathField } from './WorkspacePathField';
import { SafeAreaModal } from './SafeAreaModal';

export type NewSessionSheetProps = {
  visible: boolean;
  onClose: () => void;
  onCreated: (session: { name: string; provider: string; model: string }) => void;
};

type EditorMode = 'dockerfile' | 'network' | null;
type ContainerSource = 'new' | 'existing';

const SESSION_TYPES: Array<{ type: SessionType; label: string; detail: string; icon: keyof typeof Ionicons.glyphMap }> = [
  { type: 'chat', label: 'Chat', detail: 'Conversation and research without local execution.', icon: 'chatbubble-ellipses-outline' },
  { type: 'workspace', label: 'Workspace', detail: 'Host tools scoped to an attached folder.', icon: 'folder-open-outline' },
  { type: 'container', label: 'Container', detail: 'Full Mode OS in isolated Docker with controlled egress.', icon: 'cube-outline' },
];

export function NewSessionSheet({ visible, onClose, onCreated }: NewSessionSheetProps) {
  const { colors } = useTheme();
  const [step, setStep] = useState(1);
  const [sessionType, setSessionType] = useState<SessionType>('workspace');
  const [name, setName] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [ollamaMode, setOllamaMode] = useState<'local' | 'cloud'>('local');
  const [ollamaApiKey, setOllamaApiKey] = useState('');
  const [ollamaKeySet, setOllamaKeySet] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [containerSource, setContainerSource] = useState<ContainerSource>('new');
  const [containers, setContainers] = useState<ManagedContainer[]>([]);
  const [templates, setTemplates] = useState<ContainerTemplateSummary[]>([]);
  const [existingContainer, setExistingContainer] = useState('');
  const [containerName, setContainerName] = useState('');
  const [templateName, setTemplateName] = useState('');
  const [dockerfile, setDockerfile] = useState('');
  const [egressAllow, setEgressAllow] = useState('');
  const [egressDeny, setEgressDeny] = useState('');
  const [mounts, setMounts] = useState<ContainerMount[]>([]);
  const [mountHost, setMountHost] = useState('');
  const [mountTarget, setMountTarget] = useState('/workspace/project');
  const [mountMode, setMountMode] = useState<'rw' | 'ro'>('rw');
  const [hardware, setHardware] = useState<ContainerHardwareCapabilities | null>(null);
  const [gpuRequest, setGpuRequest] = useState('');
  const [devices, setDevices] = useState<ContainerDevice[]>([]);
  const [containerEditor, setContainerEditor] = useState<EditorMode>(null);
  const [creating, setCreating] = useState(false);
  const [progress, setProgress] = useState('');
  const [progressLogs, setProgressLogs] = useState<ContainerProgressLog[]>([]);
  const [progressExpanded, setProgressExpanded] = useState(false);
  const [progressFailed, setProgressFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setStep(1); setSessionType('workspace'); setName(''); setWorkspace(''); setProvider(''); setModels([]); setModel('');
    setOllamaMode('local'); setOllamaApiKey(''); setContainerSource('new'); setExistingContainer(''); setContainerName('');
    setTemplateName(''); setMounts([]); setMountHost(''); setMountTarget('/workspace/project'); setMountMode('rw');
    setHardware(null); setGpuRequest(''); setDevices([]); setContainerEditor(null);
    setCreating(false); setProgress(''); setProgressLogs([]); setProgressExpanded(false); setProgressFailed(false); setError(null);
  }, []);

  const loadInitial = useCallback(async () => {
    try {
      const [providerResponse, containerResponse, defaults] = await Promise.all([
        providersApi.list(), containersApi.list(), sessionsApi.getContainerDefaults(),
      ]);
      const available = providerResponse.providers || [];
      setProviders(available);
      const first = available.find(item => item.configured) || available[0];
      if (first) setProvider(first.name);
      const ollama = available.find(item => item.name === 'ollama');
      setOllamaKeySet(Boolean(ollama?.cloud_key_set));
      setContainers(containerResponse.containers || []);
      setTemplates(containerResponse.templates || []);
      setDockerfile(defaults.dockerfile || '');
      setEgressAllow((defaults.egress_allow || []).join('\n'));
      setEgressDeny((defaults.egress_deny || []).join('\n'));
      setHardware(defaults.hardware || null);
    } catch (cause) {
      setError(`Could not load session options: ${String(cause)}`);
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    reset();
    loadInitial();
  }, [loadInitial, reset, visible]);

  const loadModels = useCallback(async () => {
    if (!provider) return;
    if (provider === 'ollama' && ollamaMode === 'cloud' && !ollamaApiKey && !ollamaKeySet) {
      setModels([]); setModel(''); return;
    }
    setLoadingModels(true); setError(null);
    try {
      const response = await providersApi.listModels(provider, provider === 'ollama' ? ollamaMode : undefined, provider === 'ollama' && ollamaMode === 'cloud' ? ollamaApiKey : undefined);
      setModels(response.models || []);
      setModel(response.models?.[0] || '');
      if (response.error) setError(response.error);
    } catch (cause) {
      setError(`Model discovery failed: ${String(cause)}`);
    } finally { setLoadingModels(false); }
  }, [ollamaApiKey, ollamaKeySet, ollamaMode, provider]);

  useEffect(() => { if (visible && provider) loadModels(); }, [loadModels, provider, visible]);
  useEffect(() => { if (sessionType === 'container' && name.trim() && !containerName) setContainerName(`mucli-${name.trim()}`); }, [containerName, name, sessionType]);

  const validName = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name.trim());
  const canContinue = useMemo(() => {
    if (step === 1) return validName;
    if (step === 2) return Boolean(provider && model.trim());
    if (step === 3 && sessionType === 'container') return containerSource === 'existing' ? Boolean(existingContainer) : Boolean(containerName.trim());
    return true;
  }, [containerName, containerSource, existingContainer, model, provider, sessionType, step, validName]);

  const addMount = () => {
    const host = mountHost.trim(); const target = mountTarget.trim();
    if (!host || !target) return;
    if (mounts.some(item => item.host_path === host || item.container_path === target)) { setError('That folder or container target is already mounted.'); return; }
    setMounts(current => [...current, { host_path: host, container_path: target, mode: mountMode }]);
    setMountHost(''); setMountTarget('/workspace/project');
  };

  const pollCreation = async () => {
    let after = 0;
    for (;;) {
      const status = await sessionsApi.getContainerCreationStatus(name.trim(), after);
      setProgress(status.message || status.stage);
      const incoming = status.logs || [];
      if (incoming.length) {
        setProgressLogs(current => [...current, ...incoming]);
        for (const line of incoming) after = Math.max(after, line.seq);
      }
      if (status.state === 'ready') return;
      if (status.state === 'error') {
        setProgressFailed(true);
        setProgressExpanded(true);
        throw new Error(status.detail || status.message);
      }
      await new Promise(resolve => setTimeout(resolve, 700));
    }
  };

  const create = async () => {
    if (!canContinue || creating) return;
    setCreating(true); setError(null); setProgressLogs([]); setProgressExpanded(false); setProgressFailed(false); setProgress(sessionType === 'container' ? 'Queueing container setup…' : 'Creating session…');
    try {
      await sessionsApi.create(name.trim(), provider, model.trim(), sessionType === 'workspace' ? workspace.trim() || undefined : undefined, {
        sessionType,
        ollamaMode: provider === 'ollama' ? ollamaMode : undefined,
        ollamaApiKey: provider === 'ollama' ? ollamaApiKey.trim() || undefined : undefined,
        container: sessionType === 'container' ? {
          source: containerSource,
          existingContainer: containerSource === 'existing' ? existingContainer : undefined,
          containerName: containerName.trim(),
          templateName: templateName || undefined,
          dockerfile: templateName ? undefined : dockerfile,
          mounts,
          gpuRequest,
          devices,
          egressAllow: splitLines(egressAllow),
          egressDeny: splitLines(egressDeny),
        } : undefined,
      });
      if (sessionType === 'container') await pollCreation();
      onCreated({ name: name.trim(), provider, model: model.trim() });
      setProgress('');
    } catch (cause) {
      setProgressFailed(sessionType === 'container');
      setProgressExpanded(sessionType === 'container');
      setProgress(sessionType === 'container' ? 'Container creation failed' : 'Session creation failed');
      setError(String(cause));
    } finally { setCreating(false); }
  };

  const stepTitle = ['','Choose a boundary','Select a provider','Configure access','Review and create'][step];

  return (
    <SafeAreaModal visible={visible} animationType="slide" onRequestClose={onClose} containerStyle={{ backgroundColor: colors.bg }}>
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={[styles.header, { paddingTop: 16, borderBottomColor: colors.border }]}>
          <TouchableOpacity onPress={onClose} disabled={creating} style={[styles.iconButton, { backgroundColor: colors.bgHover }]}><Ionicons name="close" size={20} color={colors.text} /></TouchableOpacity>
          <View style={styles.headerCopy}><Text style={[styles.title, { color: colors.text }]}>{stepTitle}</Text><Text variant="xs" dim>New session · step {step} of 4</Text></View>
          <View style={styles.iconButton} />
        </View>
        <View style={styles.progressDots}>{[1,2,3,4].map(item => <TouchableOpacity key={item} onPress={() => item < step && !creating && setStep(item)} style={[styles.dot, { backgroundColor: item <= step ? colors.accent : colors.bgHover }]} />)}</View>

        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.content, { paddingBottom: 104 }]}>
          {step === 1 ? <>
            <Text variant="sm" dim style={styles.intro}>Name the session and choose its execution boundary.</Text>
            <FieldLabel label="Session name" />
            <TextInput value={name} onChangeText={setName} autoCapitalize="none" autoCorrect={false} placeholder="project-research" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} />
            {name.trim() && !validName ? <Text variant="xs" style={{ color: colors.error, marginTop: 7 }}>Use letters, numbers, dots, dashes, or underscores.</Text> : null}
            <View style={styles.typeGrid}>{SESSION_TYPES.map(item => { const selected = item.type === sessionType; return <TouchableOpacity key={item.type} onPress={() => setSessionType(item.type)} style={[styles.typeCard, { backgroundColor: colors.bgLift, borderColor: selected ? colors.accent : colors.border }]}><View style={[styles.typeIcon, { backgroundColor: colors.bgHover }]}><Ionicons name={item.icon} size={20} color={selected ? colors.accent : colors.textDim} /></View><View style={styles.cardCopy}><Text variant="sm" style={styles.typeTitle}>{item.label}</Text><Text variant="xs" dim style={styles.typeDetail}>{item.detail}</Text></View>{selected ? <Ionicons name="checkmark-circle" size={19} color={colors.accent} /> : null}</TouchableOpacity>; })}</View>
          </> : null}

          {step === 2 ? <>
            <Text variant="sm" dim style={styles.intro}>Select the provider and model for this session.</Text>
            <FieldLabel label="Provider" />
            <View style={styles.choiceList}>{providers.map(item => <TouchableOpacity key={item.name} onPress={() => setProvider(item.name)} style={[styles.choiceRow, { backgroundColor: colors.bgLift, borderColor: provider === item.name ? colors.accent : colors.border }]}><Text variant="sm" style={{ fontWeight: '600' }}>{item.name}</Text><Text variant="xs" dim>{item.configured ? 'configured' : 'unconfigured'}</Text></TouchableOpacity>)}</View>
            {provider === 'ollama' ? <View style={styles.segment}><Segment label="Local" selected={ollamaMode === 'local'} onPress={() => setOllamaMode('local')} /><Segment label="Cloud" selected={ollamaMode === 'cloud'} onPress={() => setOllamaMode('cloud')} /></View> : null}
            {provider === 'ollama' && ollamaMode === 'cloud' ? <><FieldLabel label="Ollama cloud API key" optional={ollamaKeySet} /><TextInput value={ollamaApiKey} onChangeText={setOllamaApiKey} onBlur={loadModels} secureTextEntry placeholder={ollamaKeySet ? 'Key available · enter to replace' : 'API key'} placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} /></> : null}
            <FieldLabel label="Model" />
            {loadingModels ? <ActivityIndicator color={colors.accent} style={{ marginVertical: 18 }} /> : <View style={styles.choiceList}>{models.slice(0, 30).map(item => <TouchableOpacity key={item} onPress={() => setModel(item)} style={[styles.choiceRow, { backgroundColor: colors.bgLift, borderColor: model === item ? colors.accent : colors.border }]}><Text variant="sm" numberOfLines={1}>{item}</Text>{model === item ? <Ionicons name="checkmark" size={18} color={colors.accent} /> : null}</TouchableOpacity>)}</View>}
            {!models.length && !loadingModels ? <TextInput value={model} onChangeText={setModel} autoCapitalize="none" autoCorrect={false} placeholder="Enter model name" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} /> : null}
          </> : null}

          {step === 3 ? <>
            {sessionType === 'chat' ? <Notice icon="shield-checkmark-outline" text="Filesystem and shell tools are unavailable in chat sessions." /> : null}
            {sessionType === 'workspace' ? <><Text variant="sm" dim style={styles.intro}>Choose a host folder. Leave empty for a tool-limited workspace session.</Text><WorkspacePathField value={workspace} onChangeText={setWorkspace} placeholder="Browse a workspace folder" /></> : null}
            {sessionType === 'container' ? <>
              <Text variant="sm" dim style={styles.intro}>Create a new environment or attach to one already managed by MuCLI.</Text>
              <View style={styles.segment}><Segment label="Create new" selected={containerSource === 'new'} onPress={() => setContainerSource('new')} /><Segment label="Attach existing" selected={containerSource === 'existing'} onPress={() => setContainerSource('existing')} /></View>
              {containerSource === 'existing' ? <View style={styles.choiceList}>{containers.length ? containers.map(item => <TouchableOpacity key={item.name} onPress={() => setExistingContainer(item.name)} style={[styles.choiceRow, { backgroundColor: colors.bgLift, borderColor: existingContainer === item.name ? colors.accent : colors.border }]}><View style={styles.cardCopy}><Text variant="sm" style={{ fontWeight: '600' }}>{item.name}</Text><Text variant="xs" dim>{item.status} · {item.template_name || 'custom'}</Text></View>{existingContainer === item.name ? <Ionicons name="checkmark-circle" size={19} color={colors.accent} /> : null}</TouchableOpacity>) : <Notice icon="information-circle-outline" text="No managed containers. Create one here or from Container management." />}</View> : <>
                <FieldLabel label="Container name" /><TextInput value={containerName} onChangeText={setContainerName} autoCapitalize="none" autoCorrect={false} placeholder="mucli-project" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} />
                <FieldLabel label="Base" /><View style={styles.choiceList}><TouchableOpacity onPress={() => setTemplateName('')} style={[styles.choiceRow, { backgroundColor: colors.bgLift, borderColor: !templateName ? colors.accent : colors.border }]}><Text variant="sm">Editable Dockerfile</Text>{!templateName ? <Ionicons name="checkmark" size={18} color={colors.accent} /> : null}</TouchableOpacity>{templates.map(item => <TouchableOpacity key={item.name} onPress={() => setTemplateName(item.name)} style={[styles.choiceRow, { backgroundColor: colors.bgLift, borderColor: templateName === item.name ? colors.accent : colors.border }]}><Text variant="sm">Template · {item.name}</Text>{templateName === item.name ? <Ionicons name="checkmark" size={18} color={colors.accent} /> : null}</TouchableOpacity>)}</View>
                {!templateName ? <View style={styles.editorGrid}><EditorCard icon="document-text-outline" title="Worker image template" detail={`${countLines(dockerfile)} lines`} onPress={() => setContainerEditor('dockerfile')} /><EditorCard icon="globe-outline" title="Allowlist and blocklist" detail={`${countLines(egressAllow)} allowed · ${countLines(egressDeny)} blocked`} onPress={() => setContainerEditor('network')} /></View> : null}
                <FieldLabel label="Folder mounts" optional />
                {mounts.map((mount, index) => <View key={`${mount.host_path}-${index}`} style={[styles.mountRow, { backgroundColor: colors.bgLift }]}><View style={styles.cardCopy}><Text variant="xs" style={{ fontWeight: '600' }}>{mount.container_path}</Text><Text variant="xs" dim numberOfLines={1}>{mount.host_path} · {mount.mode}</Text></View><TouchableOpacity onPress={() => setMounts(current => current.filter((_, i) => i !== index))}><Ionicons name="close" size={18} color={colors.error} /></TouchableOpacity></View>)}
                <WorkspacePathField value={mountHost} onChangeText={value => { setMountHost(value); const base=value.split('/').filter(Boolean).pop(); if(base)setMountTarget(`/workspace/${base}`); }} placeholder="Browse a host folder" />
                <TextInput value={mountTarget} onChangeText={setMountTarget} autoCapitalize="none" autoCorrect={false} placeholder="/workspace/project" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift, marginTop: 8 }]} />
                <View style={styles.mountActions}><View style={styles.segmentCompact}><Segment label="RW" selected={mountMode === 'rw'} onPress={() => setMountMode('rw')} compact /><Segment label="RO" selected={mountMode === 'ro'} onPress={() => setMountMode('ro')} compact /></View><TouchableOpacity onPress={addMount} disabled={!mountHost.trim() || !mountTarget.trim()} style={[styles.addMount, { backgroundColor: mountHost.trim() && mountTarget.trim() ? colors.accent : colors.bgHover }]}><Ionicons name="add" size={18} color={mountHost.trim() && mountTarget.trim() ? colors.accentText : colors.textDim} /><Text variant="xs" style={{ color: mountHost.trim() && mountTarget.trim() ? colors.accentText : colors.textDim, fontWeight: '700' }}>Add folder</Text></TouchableOpacity></View>
                <ContainerHardwareSection capabilities={hardware} gpuRequest={gpuRequest} onGpuRequestChange={setGpuRequest} devices={devices} onDevicesChange={setDevices} />
              </>}
            </> : null}
          </> : null}

          {step === 4 ? <>
            <Text variant="sm" dim style={styles.intro}>Review the configuration before MuCLI creates and loads the session.</Text>
            <View style={[styles.review, { borderColor: colors.border }]}><Review label="Session" value={name} /><Review label="Type" value={sessionType} /><Review label="Provider" value={`${provider} · ${model}`} />{sessionType === 'workspace' ? <Review label="Workspace" value={workspace || 'none'} /> : null}{sessionType === 'container' ? <Review label="Container" value={containerSource === 'existing' ? `Attach · ${existingContainer}` : `Create · ${containerName}`} /> : null}{sessionType === 'container' && containerSource === 'new' ? <Review label="Hardware" value={`${gpuRequest ? `GPU ${gpuRequest}` : 'No GPU'} · ${devices.length} device${devices.length === 1 ? '' : 's'}`} /> : null}</View>
            {(creating || progressFailed) && sessionType === 'container' ? (
              <ContainerBuildProgress
                message={progress}
                logs={progressLogs}
                expanded={progressExpanded}
                onToggle={() => setProgressExpanded(current => !current)}
                running={creating}
                failed={progressFailed}
              />
            ) : creating ? (
              <View style={[styles.progressBox, { backgroundColor: colors.bgLift }]}>
                <ActivityIndicator color={colors.accent} />
                <View style={styles.cardCopy}>
                  <Text variant="sm" style={{ fontWeight: '600' }}>Preparing session</Text>
                  <Text variant="xs" dim>{progress}</Text>
                </View>
              </View>
            ) : null}
          </> : null}
          {error ? <Text variant="xs" style={{ color: colors.error, marginTop: 14 }}>{error}</Text> : null}
        </ScrollView>

        <View style={[styles.footer, { paddingBottom: 14, borderTopColor: colors.border, backgroundColor: colors.bg }]}>
          {step > 1 ? <TouchableOpacity onPress={() => setStep(current => current - 1)} disabled={creating} style={styles.backButton}><Text variant="sm">Back</Text></TouchableOpacity> : <View />}
          <TouchableOpacity onPress={() => step < 4 ? canContinue && setStep(current => current + 1) : create()} disabled={!canContinue || creating} style={[styles.primaryButton, { backgroundColor: canContinue && !creating ? colors.text : colors.bgHover }]}>{creating ? <ActivityIndicator color={colors.bg} /> : <Text style={{ color: canContinue ? colors.bg : colors.textDim, fontWeight: '700' }}>{step < 4 ? 'Continue' : 'Create and load'}</Text>}</TouchableOpacity>
        </View>
      </KeyboardAvoidingView>

      <SafeAreaModal visible={containerEditor !== null} animationType="slide" onRequestClose={() => setContainerEditor(null)} containerStyle={{ backgroundColor: colors.bg }}>
        <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={[styles.header, { paddingTop: 16, borderBottomColor: colors.border }]}><View style={styles.headerCopy}><Text style={[styles.title, { color: colors.text }]}>{containerEditor === 'dockerfile' ? 'Worker image template' : 'Network policy'}</Text><Text variant="xs" dim>{containerEditor === 'dockerfile' ? 'Edit the worker image.' : 'Blocklist entries override the allowlist.'}</Text></View><TouchableOpacity onPress={() => setContainerEditor(null)} style={[styles.iconButton, { backgroundColor: colors.bgHover }]}><Ionicons name="close" size={20} color={colors.text} /></TouchableOpacity></View>
          {containerEditor === 'dockerfile' ? <TextInput value={dockerfile} onChangeText={setDockerfile} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} spellCheck={false} style={[styles.fullEditor, { color: colors.text, backgroundColor: colors.bgLift }]} /> : <ScrollView contentContainerStyle={styles.content}><FieldLabel label="Allowlist" /><TextInput value={egressAllow} onChangeText={setEgressAllow} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor, { color: colors.text, backgroundColor: colors.bgLift }]} /><FieldLabel label="Blocklist" optional /><TextInput value={egressDeny} onChangeText={setEgressDeny} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor, { color: colors.text, backgroundColor: colors.bgLift }]} /></ScrollView>}
        </KeyboardAvoidingView>
      </SafeAreaModal>
    </SafeAreaModal>
  );
}

function FieldLabel({ label, optional = false }: { label: string; optional?: boolean }) { return <View style={styles.fieldLabel}><Text variant="xs" style={{ fontWeight: '700' }}>{label}</Text>{optional ? <Text variant="xs" dim>optional</Text> : null}</View>; }
function Segment({ label, selected, onPress, compact = false }: { label: string; selected: boolean; onPress: () => void; compact?: boolean }) { const { colors } = useTheme(); return <TouchableOpacity onPress={onPress} style={[compact ? styles.segmentButtonCompact : styles.segmentButton, { backgroundColor: selected ? colors.text : colors.bgLift }]}><Text variant="xs" style={{ color: selected ? colors.bg : colors.textDim, fontWeight: '700' }}>{label}</Text></TouchableOpacity>; }
function Notice({ icon, text }: { icon: keyof typeof Ionicons.glyphMap; text: string }) { const { colors } = useTheme(); return <View style={[styles.notice, { backgroundColor: colors.bgLift }]}><Ionicons name={icon} size={20} color={colors.accent} /><Text variant="xs" dim style={{ flex: 1, lineHeight: 18 }}>{text}</Text></View>; }
function EditorCard({ icon, title, detail, onPress }: { icon: keyof typeof Ionicons.glyphMap; title: string; detail: string; onPress: () => void }) { const { colors } = useTheme(); return <TouchableOpacity onPress={onPress} style={[styles.editorCard, { backgroundColor: colors.bgLift, borderColor: colors.border }]}><Ionicons name={icon} size={20} color={colors.accent} /><View style={styles.cardCopy}><Text variant="sm" style={{ fontWeight: '600' }}>{title}</Text><Text variant="xs" dim>{detail}</Text></View><Ionicons name="expand-outline" size={18} color={colors.textDim} /></TouchableOpacity>; }
function Review({ label, value }: { label: string; value: string }) { const { colors } = useTheme(); return <View style={[styles.reviewRow, { borderBottomColor: colors.border }]}><Text variant="xs" dim style={styles.reviewLabel}>{label}</Text><Text variant="xs" style={styles.reviewValue}>{value}</Text></View>; }
function splitLines(value: string): string[] { return value.split(/[\n,]/).map(item => item.trim()).filter(Boolean); }
function countLines(value: string): number { return value.split(/\r?\n/).filter(item => item.trim()).length; }

const styles = StyleSheet.create({
  root: { flex: 1 }, header: { minHeight: 86, paddingHorizontal: 18, paddingBottom: 14, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'center', gap: 12 }, headerCopy: { flex: 1 }, title: { fontSize: 22, fontWeight: '700', letterSpacing: -0.5 }, iconButton: { width: 42, height: 42, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  progressDots: { height: 26, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 9 }, dot: { width: 8, height: 8, borderRadius: 4 }, content: { padding: 18 }, intro: { lineHeight: 21, marginBottom: 18 }, fieldLabel: { marginTop: 15, marginBottom: 7, flexDirection: 'row', justifyContent: 'space-between' }, input: { minHeight: 50, borderRadius: 15, paddingHorizontal: 14, fontSize: 15 },
  typeGrid: { gap: 9, marginTop: 18 }, typeCard: { minHeight: 92, borderWidth: StyleSheet.hairlineWidth, borderRadius: 18, padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }, typeIcon: { width: 42, height: 42, borderRadius: 14, alignItems: 'center', justifyContent: 'center' }, cardCopy: { flex: 1 }, typeTitle: { fontWeight: '700' }, typeDetail: { marginTop: 4, lineHeight: 17 },
  choiceList: { gap: 7 }, choiceRow: { minHeight: 50, borderWidth: StyleSheet.hairlineWidth, borderRadius: 14, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 }, segment: { flexDirection: 'row', gap: 6, marginTop: 15, marginBottom: 12 }, segmentButton: { flex: 1, minHeight: 44, borderRadius: 13, alignItems: 'center', justifyContent: 'center' }, segmentCompact: { flexDirection: 'row', gap: 4 }, segmentButtonCompact: { minWidth: 48, minHeight: 38, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  notice: { minHeight: 70, borderRadius: 16, padding: 14, flexDirection: 'row', alignItems: 'center', gap: 11 }, editorGrid: { gap: 8, marginTop: 14 }, editorCard: { minHeight: 66, borderWidth: StyleSheet.hairlineWidth, borderRadius: 15, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 11 },
  mountRow: { minHeight: 58, borderRadius: 14, paddingHorizontal: 12, marginBottom: 7, flexDirection: 'row', alignItems: 'center', gap: 10 }, mountActions: { marginTop: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }, addMount: { minHeight: 40, borderRadius: 12, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', gap: 6 },
  review: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, overflow: 'hidden' }, reviewRow: { minHeight: 52, borderBottomWidth: StyleSheet.hairlineWidth, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }, reviewLabel: { width: 86 }, reviewValue: { flex: 1, fontWeight: '600' }, progressBox: { marginTop: 14, borderRadius: 15, padding: 14, flexDirection: 'row', alignItems: 'center', gap: 11 },
  footer: { position: 'absolute', left: 0, right: 0, bottom: 0, minHeight: 78, paddingHorizontal: 18, paddingTop: 12, borderTopWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }, backButton: { minHeight: 48, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center' }, primaryButton: { minWidth: 150, minHeight: 48, borderRadius: 15, paddingHorizontal: 18, alignItems: 'center', justifyContent: 'center' },
  fullEditor: { flex: 1, margin: 16, borderRadius: 15, padding: 14, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 13, lineHeight: 20 }, policyEditor: { minHeight: 220, borderRadius: 15, padding: 14, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 13, lineHeight: 20 },
});
