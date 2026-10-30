import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from 'react-native';
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
import { ContainerBuildProgress, ContainerProgressLog } from './ContainerBuildProgress';
import { WorkspacePathField } from './WorkspacePathField';
import { fontSizes, fontWeights, fonts, radii, spacing, touchTarget, tracking, typography } from '../theme/tokens';
import { Button, ModalFrame, Pressable, Text, Icon, type IconName, InkDrop, TextField } from '../ui';

export type NewSessionSheetProps = {
  visible: boolean;
  onClose: () => void;
  onCreated: (session: { name: string; provider: string; model: string }) => void;
};

type EditorMode = 'dockerfile' | 'network' | null;
type ContainerSource = 'new' | 'existing';

const SESSION_TYPES: Array<{ type: SessionType; label: string; detail: string; icon: IconName }> = [
  { type: 'chat', label: 'Chat', detail: 'Conversation and research without local execution.', icon: 'message-circle' },
  { type: 'workspace', label: 'Workspace', detail: 'Host tools scoped to an attached folder.', icon: 'folder-open' },
  { type: 'container', label: 'Container', detail: 'Full Mode OS in isolated Docker with controlled egress.', icon: 'box' },
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
        // A workspace folder that doesn't exist yet is created by the server.
        createWorkspace: sessionType === 'workspace' && Boolean(workspace.trim()),
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
    <ModalFrame statusBarTranslucent={false} transparent={false} visible={visible} animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={[styles.header, { paddingTop: spacing.base, borderBottomColor: colors.hairline }]}>
          <Pressable accessibilityLabel="Close" onPress={onClose} disabled={creating} style={[styles.iconButton, { backgroundColor: colors.sunken }]}><Icon name="x" size="md" color={colors.ink} /></Pressable>
          <View style={styles.headerCopy}><Text style={[styles.title, { color: colors.ink }]}>{stepTitle}</Text><Text variant="xs" dim>New session · step {step} of 4</Text></View>
          <View style={styles.iconButton} />
        </View>
        <View style={styles.progressDots}>{[1,2,3,4].map(item => <Pressable accessibilityLabel={`Go to step ${item}`} key={item} hitSlop={{ top: 18, bottom: 18, left: 6, right: 6 }} onPress={() => item < step && !creating && setStep(item)} style={[styles.dot, { backgroundColor: item <= step ? colors.accent.ink : colors.sunken }]} />)}</View>

        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.content, { paddingBottom: spacing['4xl'] }]}>
          {step === 1 ? <>
            <Text variant="sm" dim style={styles.intro}>Name the session and choose its execution boundary.</Text>
            <FieldLabel label="Session name" />
            <TextField value={name} onChangeText={setName} autoCapitalize="none" autoCorrect={false} placeholder="project-research" style={[styles.input]} />
            {name.trim() && !validName ? <Text variant="xs" style={{ color: colors.danger.ink, marginTop: spacing.sm }}>Use letters, numbers, dots, dashes, or underscores.</Text> : null}
            <View style={styles.typeGrid}>{SESSION_TYPES.map(item => { const selected = item.type === sessionType; return <Pressable key={item.type} onPress={() => setSessionType(item.type)} style={[styles.typeCard, { backgroundColor: selected ? colors.accent.wash : colors.raised, borderColor: selected ? colors.accent.ink : colors.hairline }]}><View style={[styles.typeIcon, { backgroundColor: colors.sunken }]}><Icon name={item.icon} size="md" color={selected ? colors.accent.ink : colors.ink3} /></View><View style={styles.cardCopy}><Text variant="sm" style={styles.typeTitle}>{item.label}</Text><Text variant="xs" dim style={styles.typeDetail}>{item.detail}</Text></View>{selected ? <Icon name="circle-check" size="md" color={colors.accent.ink} /> : null}</Pressable>; })}</View>
          </> : null}

          {step === 2 ? <>
            <Text variant="sm" dim style={styles.intro}>Select the provider and model for this session.</Text>
            <FieldLabel label="Provider" />
            <View style={styles.choiceList}>{providers.map(item => <Pressable key={item.name} onPress={() => setProvider(item.name)} style={[styles.choiceRow, { backgroundColor: provider === item.name ? colors.accent.wash : colors.raised, borderColor: provider === item.name ? colors.accent.ink : colors.hairline }]}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{item.name}</Text><Text variant="xs" dim>{item.configured ? 'configured' : 'unconfigured'}</Text></Pressable>)}</View>
            {provider === 'ollama' ? <View style={styles.segment}><Segment label="Local" selected={ollamaMode === 'local'} onPress={() => setOllamaMode('local')} /><Segment label="Cloud" selected={ollamaMode === 'cloud'} onPress={() => setOllamaMode('cloud')} /></View> : null}
            {provider === 'ollama' && ollamaMode === 'cloud' ? <><FieldLabel label="Ollama cloud API key" optional={ollamaKeySet} /><TextField value={ollamaApiKey} onChangeText={setOllamaApiKey} onBlur={loadModels} secureTextEntry placeholder={ollamaKeySet ? 'Key available · enter to replace' : 'API key'} style={[styles.input]} /></> : null}
            <FieldLabel label="Model" />
            {loadingModels ? <View style={{ marginVertical: spacing.base }}><InkDrop /></View> : <View style={styles.choiceList}>{models.slice(0, 30).map(item => <Pressable key={item} onPress={() => setModel(item)} style={[styles.choiceRow, { backgroundColor: model === item ? colors.accent.wash : colors.raised, borderColor: model === item ? colors.accent.ink : colors.hairline }]}><Text variant="sm" numberOfLines={1}>{item}</Text>{model === item ? <Icon name="check" size="md" color={colors.accent.ink} /> : null}</Pressable>)}</View>}
            {!models.length && !loadingModels ? <TextField value={model} onChangeText={setModel} autoCapitalize="none" autoCorrect={false} placeholder="Enter model name" style={[styles.input]} /> : null}
          </> : null}

          {step === 3 ? <>
            {sessionType === 'chat' ? <Notice icon="shield-check" text="Filesystem and shell tools are unavailable in chat sessions." /> : null}
            {sessionType === 'workspace' ? <><Text variant="sm" dim style={styles.intro}>Choose a host folder; a new path is created. Leave empty for a tool-limited workspace session.</Text><WorkspacePathField value={workspace} onChangeText={setWorkspace} placeholder="Browse a workspace folder" createMissing /></> : null}
            {sessionType === 'container' ? <>
              <Text variant="sm" dim style={styles.intro}>Create a new environment or attach to one already managed by MuCLI.</Text>
              <View style={styles.segment}><Segment label="Create new" selected={containerSource === 'new'} onPress={() => setContainerSource('new')} /><Segment label="Attach existing" selected={containerSource === 'existing'} onPress={() => setContainerSource('existing')} /></View>
              {containerSource === 'existing' ? <View style={styles.choiceList}>{containers.length ? containers.map(item => <Pressable key={item.name} onPress={() => setExistingContainer(item.name)} style={[styles.choiceRow, { backgroundColor: existingContainer === item.name ? colors.accent.wash : colors.raised, borderColor: existingContainer === item.name ? colors.accent.ink : colors.hairline }]}><View style={styles.cardCopy}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{item.name}</Text><Text variant="xs" dim>{item.status} · {item.template_name || 'custom'}</Text></View>{existingContainer === item.name ? <Icon name="circle-check" size="md" color={colors.accent.ink} /> : null}</Pressable>) : <Notice icon="info" text="No managed containers. Create one here or from Container management." />}</View> : <>
                <FieldLabel label="Container name" /><TextField value={containerName} onChangeText={setContainerName} autoCapitalize="none" autoCorrect={false} placeholder="mucli-project" style={[styles.input]} />
                <FieldLabel label="Base" /><View style={styles.choiceList}><Pressable onPress={() => setTemplateName('')} style={[styles.choiceRow, { backgroundColor: !templateName ? colors.accent.wash : colors.raised, borderColor: !templateName ? colors.accent.ink : colors.hairline }]}><Text variant="sm">Editable Dockerfile</Text>{!templateName ? <Icon name="check" size="md" color={colors.accent.ink} /> : null}</Pressable>{templates.map(item => <Pressable key={item.name} onPress={() => setTemplateName(item.name)} style={[styles.choiceRow, { backgroundColor: templateName === item.name ? colors.accent.wash : colors.raised, borderColor: templateName === item.name ? colors.accent.ink : colors.hairline }]}><Text variant="sm">Template · {item.name}</Text>{templateName === item.name ? <Icon name="check" size="md" color={colors.accent.ink} /> : null}</Pressable>)}</View>
                {!templateName ? <View style={styles.editorGrid}><EditorCard icon="file-text" title="Worker image template" detail={`${countLines(dockerfile)} lines`} onPress={() => setContainerEditor('dockerfile')} /><EditorCard icon="globe" title="Allowlist and blocklist" detail={`${countLines(egressAllow)} allowed · ${countLines(egressDeny)} blocked`} onPress={() => setContainerEditor('network')} /></View> : null}
                <FieldLabel label="Folder mounts" optional />
                {mounts.map((mount, index) => <View key={`${mount.host_path}-${index}`} style={[styles.mountRow, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><View style={styles.cardCopy}><Text variant="xs" style={{ fontWeight: fontWeights.semibold }}>{mount.container_path}</Text><Text variant="xs" dim numberOfLines={1}>{mount.host_path} · {mount.mode}</Text></View><Pressable accessibilityLabel="Remove mount" onPress={() => setMounts(current => current.filter((_, i) => i !== index))}><Icon name="x" size="md" color={colors.danger.ink} /></Pressable></View>)}
                <WorkspacePathField value={mountHost} onChangeText={value => { setMountHost(value); const base=value.split('/').filter(Boolean).pop(); if(base)setMountTarget(`/workspace/${base}`); }} placeholder="Browse a host folder" />
                <TextField value={mountTarget} onChangeText={setMountTarget} autoCapitalize="none" autoCorrect={false} placeholder="/workspace/project" style={[styles.input, { marginTop: spacing.sm }]} />
                <View style={styles.mountActions}><View style={styles.segmentCompact}><Segment label="RW" selected={mountMode === 'rw'} onPress={() => setMountMode('rw')} compact /><Segment label="RO" selected={mountMode === 'ro'} onPress={() => setMountMode('ro')} compact /></View><Pressable onPress={addMount} disabled={!mountHost.trim() || !mountTarget.trim()} style={[styles.addMount, { backgroundColor: mountHost.trim() && mountTarget.trim() ? colors.accent.wash : colors.sunken }]}><Icon name="plus" size="md" color={mountHost.trim() && mountTarget.trim() ? colors.accent.ink : colors.ink3} /><Text variant="xs" style={{ color: mountHost.trim() && mountTarget.trim() ? colors.accent.ink : colors.ink3, fontWeight: fontWeights.semibold }}>Add folder</Text></Pressable></View>
                <ContainerHardwareSection capabilities={hardware} gpuRequest={gpuRequest} onGpuRequestChange={setGpuRequest} devices={devices} onDevicesChange={setDevices} />
              </>}
            </> : null}
          </> : null}

          {step === 4 ? <>
            <Text variant="sm" dim style={styles.intro}>Review the configuration before MuCLI creates and loads the session.</Text>
            <View style={[styles.review, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><Review label="Session" value={name} /><Review label="Type" value={sessionType} /><Review label="Provider" value={`${provider} · ${model}`} />{sessionType === 'workspace' ? <Review label="Workspace" value={workspace || 'none'} /> : null}{sessionType === 'container' ? <Review label="Container" value={containerSource === 'existing' ? `Attach · ${existingContainer}` : `Create · ${containerName}`} /> : null}{sessionType === 'container' && containerSource === 'new' ? <Review label="Hardware" value={`${gpuRequest ? `GPU ${gpuRequest}` : 'No GPU'} · ${devices.length} device${devices.length === 1 ? '' : 's'}`} /> : null}</View>
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
              <View style={[styles.progressBox, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
                <InkDrop />
                <View style={styles.cardCopy}>
                  <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>Preparing session</Text>
                  <Text variant="xs" dim>{progress}</Text>
                </View>
              </View>
            ) : null}
          </> : null}
          {error ? <Text variant="xs" style={{ color: colors.danger.ink, marginTop: spacing.md }}>{error}</Text> : null}
        </ScrollView>

        <View style={[styles.footer, { paddingBottom: spacing.base, borderTopColor: colors.hairline, backgroundColor: colors.canvas }]}>
          {step > 1 ? <Button variant="ghost" label="Back" onPress={() => setStep(current => current - 1)} disabled={creating} /> : <View />}
          <Button variant="primary" label={step < 4 ? 'Continue' : 'Create and load'} loading={creating} disabled={!canContinue || creating}
            onPress={() => (step < 4 ? canContinue && setStep(current => current + 1) : create())} />
        </View>
      </KeyboardAvoidingView>

      <ModalFrame statusBarTranslucent={false} transparent={false} visible={containerEditor !== null} animationType="slide" onRequestClose={() => setContainerEditor(null)}>
        <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={[styles.header, { paddingTop: spacing.base, borderBottomColor: colors.hairline }]}><View style={styles.headerCopy}><Text style={[styles.title, { color: colors.ink }]}>{containerEditor === 'dockerfile' ? 'Worker image template' : 'Network policy'}</Text><Text variant="xs" dim>{containerEditor === 'dockerfile' ? 'Edit the worker image.' : 'Blocklist entries override the allowlist.'}</Text></View><Pressable accessibilityLabel="Close editor" onPress={() => setContainerEditor(null)} style={[styles.iconButton, { backgroundColor: colors.sunken }]}><Icon name="x" size="md" color={colors.ink} /></Pressable></View>
          {containerEditor === 'dockerfile' ? <TextField value={dockerfile} onChangeText={setDockerfile} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} spellCheck={false} style={[styles.fullEditor]} /> : <ScrollView contentContainerStyle={styles.content}><FieldLabel label="Allowlist" /><TextField value={egressAllow} onChangeText={setEgressAllow} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor]} /><FieldLabel label="Blocklist" optional /><TextField value={egressDeny} onChangeText={setEgressDeny} multiline textAlignVertical="top" autoCapitalize="none" autoCorrect={false} style={[styles.policyEditor]} /></ScrollView>}
        </KeyboardAvoidingView>
      </ModalFrame>
    </ModalFrame>
  );
}

function FieldLabel({ label, optional = false }: { label: string; optional?: boolean }) { return <View style={styles.fieldLabel}><Text variant="xs" style={{ fontWeight: fontWeights.medium }}>{label}{optional ? <Text variant="xs" dim> (optional)</Text> : null}</Text></View>; }
function Segment({ label, selected, onPress, compact = false }: { label: string; selected: boolean; onPress: () => void; compact?: boolean }) { const { colors } = useTheme(); return <Pressable onPress={onPress} style={[compact ? styles.segmentButtonCompact : styles.segmentButton, { backgroundColor: selected ? colors.accent.wash : colors.surface, borderWidth: 1, borderColor: selected ? colors.accent.ink : colors.hairline }]}><Text variant="xs" style={{ color: selected ? colors.accent.ink : colors.ink2, fontWeight: selected ? fontWeights.semibold : fontWeights.medium }}>{label}</Text></Pressable>; }
function Notice({ icon, text }: { icon: IconName; text: string }) { const { colors } = useTheme(); return <View style={[styles.notice, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><Icon name={icon} size="md" color={colors.ink2} /><Text variant="xs" dim style={{ flex: 1, lineHeight: typography.xs.lineHeight }}>{text}</Text></View>; }
function EditorCard({ icon, title, detail, onPress }: { icon: IconName; title: string; detail: string; onPress: () => void }) { const { colors } = useTheme(); return <Pressable onPress={onPress} style={[styles.editorCard, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><Icon name={icon} size="md" color={colors.ink2} /><View style={styles.cardCopy}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{title}</Text><Text variant="xs" dim>{detail}</Text></View><Icon name="maximize-2" size="md" color={colors.ink3} /></Pressable>; }
function Review({ label, value }: { label: string; value: string }) { const { colors } = useTheme(); return <View style={styles.reviewRow}><Text variant="xs" dim style={styles.reviewLabel}>{label}</Text><Text variant="xs" style={styles.reviewValue}>{value}</Text></View>; }
function splitLines(value: string): string[] { return value.split(/[\n,]/).map(item => item.trim()).filter(Boolean); }
function countLines(value: string): number { return value.split(/\r?\n/).filter(item => item.trim()).length; }

const styles = StyleSheet.create({
  root: { flex: 1 }, header: { minHeight: 86, paddingHorizontal: spacing.base, paddingBottom: spacing.base, borderBottomWidth: 1, flexDirection: 'row', alignItems: 'center', gap: spacing.md }, headerCopy: { flex: 1 }, title: { fontSize: fontSizes.lg, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight }, iconButton: { width: touchTarget, height: touchTarget, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  progressDots: { height: 26, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: spacing.sm }, dot: { width: 8, height: 8, borderRadius: radii.pill }, content: { padding: spacing.base }, intro: { lineHeight: typography.base.lineHeight, marginBottom: spacing.base }, fieldLabel: { marginTop: spacing.base, marginBottom: spacing.xs, flexDirection: 'row' }, input: { minHeight: 48, borderWidth: 1, borderRadius: radii.control, paddingHorizontal: spacing.md, fontSize: fontSizes.base },
  typeGrid: { gap: spacing.sm, marginTop: spacing.base }, typeCard: { minHeight: 92, borderWidth: 1, borderRadius: radii.panel, padding: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.md }, typeIcon: { width: 40, height: 40, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' }, cardCopy: { flex: 1 }, typeTitle: { fontWeight: fontWeights.semibold }, typeDetail: { marginTop: spacing.xs, lineHeight: typography.xs.lineHeight },
  choiceList: { gap: spacing.sm }, choiceRow: { minHeight: 50, borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm }, segment: { flexDirection: 'row', gap: spacing.xs, marginTop: spacing.base, marginBottom: spacing.md }, segmentButton: { flex: 1, minHeight: 44, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' }, segmentCompact: { flexDirection: 'row', gap: spacing.xs }, segmentButtonCompact: { minWidth: 48, minHeight: touchTarget, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  notice: { minHeight: 70, borderRadius: radii.panel, padding: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.md }, editorGrid: { gap: spacing.sm, marginTop: spacing.md }, editorCard: { minHeight: 64, borderRadius: radii.panel, paddingHorizontal: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  mountRow: { minHeight: 58, borderRadius: radii.sm, paddingHorizontal: spacing.md, marginBottom: spacing.sm, flexDirection: 'row', alignItems: 'center', gap: spacing.sm }, mountActions: { marginTop: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm }, addMount: { minHeight: touchTarget, borderRadius: radii.sm, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  review: { borderRadius: radii.panel, overflow: 'hidden' }, reviewRow: { minHeight: 48, paddingHorizontal: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.md }, reviewLabel: { width: 86 }, reviewValue: { flex: 1, fontWeight: fontWeights.semibold }, progressBox: { marginTop: spacing.md, borderRadius: radii.panel, padding: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  footer: { position: 'absolute', left: 0, right: 0, bottom: 0, minHeight: 78, paddingHorizontal: spacing.base, paddingTop: spacing.md, borderTopWidth: 1, flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: spacing.sm }, backButton: { minHeight: 48, paddingHorizontal: spacing.base, alignItems: 'center', justifyContent: 'center' }, primaryButton: { minWidth: 150, minHeight: 48, borderRadius: radii.sm, paddingHorizontal: spacing.base, alignItems: 'center', justifyContent: 'center' },
  fullEditor: { flex: 1, margin: spacing.base, borderWidth: 1, borderRadius: radii.sm, padding: spacing.base, fontFamily: fonts.mono, fontSize: fontSizes.sm, lineHeight: typography.sm.lineHeight }, policyEditor: { minHeight: 220, borderWidth: 1, borderRadius: radii.sm, padding: spacing.base, fontFamily: fonts.mono, fontSize: fontSizes.sm, lineHeight: typography.sm.lineHeight },
});
