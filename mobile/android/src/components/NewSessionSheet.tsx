import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ProviderInfo, providersApi } from '../api/providers';
import { ContainerMount, SessionType, sessionsApi } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { WorkspacePathField } from './WorkspacePathField';

export type NewSessionSheetProps = {
  visible: boolean;
  onClose: () => void;
  onCreated: (session: { name: string; provider: string; model: string }) => void;
};

const SESSION_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const DEFAULT_EGRESS = 'api.openai.com\napi.anthropic.com\ngenerativelanguage.googleapis.com\nollama.com';
const DEFAULT_DOCKERFILE = String.raw`FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUCLI_CONTAINER_MODE=1 \
    PYTHONPATH=/opt/mucli

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git patch python3 python3-pip python3-venv ripgrep fd-find \
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/mucli
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /opt/mucli/requirements.txt

WORKDIR /workspace
EXPOSE 9090
ENTRYPOINT ["python3", "-m", "mu.container.worker"]
`;

type SessionTypeMeta = {
  type: SessionType;
  label: string;
  detail: string;
  icon: keyof typeof Ionicons.glyphMap;
};

const SESSION_TYPES: SessionTypeMeta[] = [
  { type: 'chat', label: 'Chat', detail: 'Research and conversation without local tools.', icon: 'chatbubble-ellipses-outline' },
  { type: 'workspace', label: 'Workspace', detail: 'Tools run on the MuCLI host in attached folders.', icon: 'folder-open-outline' },
  { type: 'container', label: 'Container', detail: 'Disposable Docker sandbox with native tools and isolated egress.', icon: 'cube-outline' },
];

export function NewSessionSheet({ visible, onClose, onCreated }: NewSessionSheetProps) {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const [sessionType, setSessionType] = useState<SessionType>('workspace');
  const [name, setName] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ollamaMode, setOllamaMode] = useState<'local' | 'cloud'>('local');
  const [ollamaHost, setOllamaHost] = useState('');
  const [ollamaApiKey, setOllamaApiKey] = useState('');
  const [containerName, setContainerName] = useState('');
  const [dockerfile, setDockerfile] = useState(DEFAULT_DOCKERFILE);
  const [egressAllow, setEgressAllow] = useState(DEFAULT_EGRESS);
  const [egressDeny, setEgressDeny] = useState('');
  const [containerEditor, setContainerEditor] = useState<'dockerfile' | 'network' | null>(null);
  const [mounts, setMounts] = useState<ContainerMount[]>([]);
  const [mountHost, setMountHost] = useState('');
  const [mountContainer, setMountContainer] = useState('/workspace/project');
  const [mountMode, setMountMode] = useState<'ro' | 'rw'>('rw');

  const reset = useCallback(() => {
    setSessionType('workspace');
    setName('');
    setWorkspace('');
    setProvider('');
    setModels([]);
    setModel('');
    setError(null);
    setOllamaMode('local');
    setOllamaHost('');
    setOllamaApiKey('');
    setContainerName('');
    setDockerfile(DEFAULT_DOCKERFILE);
    setEgressAllow(DEFAULT_EGRESS);
    setEgressDeny('');
    setContainerEditor(null);
    setMounts([]);
    setMountHost('');
    setMountContainer('/workspace/project');
    setMountMode('rw');
  }, []);

  const loadContainerDefaults = useCallback(async () => {
    try {
      const defaults = await sessionsApi.getContainerDefaults();
      setDockerfile(defaults.dockerfile || DEFAULT_DOCKERFILE);
      setEgressAllow((defaults.egress_allow || []).join('\n') || DEFAULT_EGRESS);
      setEgressDeny((defaults.egress_deny || []).join('\n'));
    } catch {
      // The bundled defaults keep creation usable with an older GUI daemon.
    }
  }, []);

  const loadProviders = useCallback(async () => {
    setLoadingProviders(true);
    setError(null);
    try {
      const response = await providersApi.list();
      const available = response.providers || [];
      setProviders(available);
      const first = available.find(item => item.configured) || available[0];
      if (first) setProvider(first.name);
    } catch (cause) {
      setError(`Could not load providers: ${String(cause)}`);
    } finally {
      setLoadingProviders(false);
    }
  }, []);

  const loadModels = useCallback(async (providerName: string) => {
    if (!providerName) return;
    setLoadingModels(true);
    setModels([]);
    setModel('');
    setError(null);
    try {
      const response = await providersApi.listModels(
        providerName,
        providerName === 'ollama' ? ollamaMode : undefined,
        providerName === 'ollama' && ollamaMode === 'cloud' ? ollamaApiKey : undefined,
      );
      const available = response.models || [];
      setModels(available);
      if (available.length > 0) setModel(available[0]);
      if (response.error) setError(response.error);
    } catch (cause) {
      setError(`Model discovery failed. Enter a model manually. ${String(cause)}`);
    } finally {
      setLoadingModels(false);
    }
  }, [ollamaApiKey, ollamaMode]);

  useEffect(() => {
    if (!visible) return;
    reset();
    loadProviders();
    loadContainerDefaults();
  }, [loadContainerDefaults, loadProviders, reset, visible]);

  useEffect(() => {
    if (!visible || !provider) return;
    loadModels(provider);
  }, [loadModels, provider, visible]);

  useEffect(() => {
    if (sessionType === 'container' && !containerName && name.trim()) {
      setContainerName(`mucli-${name.trim()}`);
    }
  }, [containerName, name, sessionType]);

  const nameError = useMemo(() => {
    if (!name.trim()) return null;
    return SESSION_NAME_PATTERN.test(name.trim())
      ? null
      : 'Use letters, numbers, dots, dashes, or underscores.';
  }, [name]);

  const containerError = useMemo(() => {
    if (sessionType !== 'container') return null;
    if (!containerName.trim()) return 'Container name is required.';
    const invalidMount = mounts.find(item => !item.host_path || !item.container_path);
    return invalidMount ? 'Every mount requires host and container paths.' : null;
  }, [containerName, mounts, sessionType]);

  const canCreate = Boolean(
    name.trim()
      && !nameError
      && !containerError
      && provider
      && model.trim()
      && !creating,
  );

  const addMount = () => {
    const host = mountHost.trim();
    const target = mountContainer.trim();
    if (!host || !target) return;
    if (mounts.some(item => item.container_path === target)) {
      setError(`Container path already mounted: ${target}`);
      return;
    }
    setMounts(current => [...current, { host_path: host, container_path: target, mode: mountMode }]);
    setMountHost('');
    setMountContainer('/workspace/project');
  };

  const createSession = async () => {
    if (!canCreate) return;
    setCreating(true);
    setError(null);
    try {
      await sessionsApi.create(
        name.trim(),
        provider,
        model.trim(),
        sessionType === 'workspace' ? workspace.trim() || undefined : undefined,
        {
          sessionType,
          ollamaMode: provider === 'ollama' ? ollamaMode : undefined,
          ollamaHost: provider === 'ollama' ? ollamaHost.trim() || undefined : undefined,
          ollamaApiKey: provider === 'ollama' ? ollamaApiKey.trim() || undefined : undefined,
          container: sessionType === 'container'
            ? {
                containerName: containerName.trim(),
                dockerfile: dockerfile.trim() || undefined,
                mounts,
                egressAllow: splitLines(egressAllow),
                egressDeny: splitLines(egressDeny),
              }
            : undefined,
        },
      );
      onCreated({ name: name.trim(), provider, model: model.trim() });
    } catch (cause) {
      setError(String(cause));
    } finally {
      setCreating(false);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <KeyboardAvoidingView
        style={[styles.root, { backgroundColor: colors.bg }]}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={[styles.header, { paddingTop: Math.max(insets.top, 16) }]}>
          <TouchableOpacity onPress={onClose} style={[styles.iconButton, { backgroundColor: colors.bgHover }]}>
            <Ionicons name="close" size={20} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.headerCopy}>
            <Text style={[styles.title, { color: colors.text }]}>New session</Text>
            <Text variant="xs" dim>Choose capability, provider, and execution boundary.</Text>
          </View>
          <View style={styles.iconSpacer} />
        </View>

        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={[styles.content, { paddingBottom: Math.max(insets.bottom, 20) + 96 }]}
        >
          <SectionLabel label="Session type" />
          <View style={styles.typeGrid}>
            {SESSION_TYPES.map(item => {
              const selected = item.type === sessionType;
              return (
                <TouchableOpacity
                  key={item.type}
                  onPress={() => setSessionType(item.type)}
                  style={[
                    styles.typeCard,
                    { backgroundColor: selected ? colors.bgHover : colors.bgLift, borderColor: selected ? colors.accent : colors.border },
                  ]}
                >
                  <View style={styles.typeTitleRow}>
                    <Ionicons name={item.icon} size={20} color={selected ? colors.accent : colors.textDim} />
                    <Text variant="sm" style={styles.typeTitle}>{item.label}</Text>
                    {selected ? <Ionicons name="checkmark-circle" size={18} color={colors.accent} /> : null}
                  </View>
                  <Text variant="xs" dim style={styles.typeDetail}>{item.detail}</Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <SectionLabel label="Session" />
          <FieldLabel label="Name" />
          <TextInput
            value={name}
            onChangeText={setName}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="my-session"
            placeholderTextColor={colors.textDim}
            style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]}
          />
          {nameError ? <Text variant="xs" style={{ color: colors.error, marginTop: 6 }}>{nameError}</Text> : null}

          {sessionType === 'workspace' ? (
            <>
              <FieldLabel label="Workspace" optional />
              <WorkspacePathField
                value={workspace}
                onChangeText={setWorkspace}
                placeholder="/home/user/dev/project"
              />
              <Text variant="xs" dim style={styles.help}>Type to browse folders on the MuCLI host.</Text>
            </>
          ) : null}

          {sessionType === 'chat' ? (
            <View style={[styles.notice, { backgroundColor: colors.bgLift }]}>
              <Ionicons name="shield-checkmark-outline" size={19} color={colors.accent} />
              <Text variant="xs" dim style={styles.noticeText}>Filesystem and shell tools are omitted from the provider schema and rejected by the dispatcher.</Text>
            </View>
          ) : null}

          {sessionType === 'container' ? (
            <>
              <SectionLabel label="Container" />
              <FieldLabel label="Container name" />
              <TextInput
                value={containerName}
                onChangeText={setContainerName}
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="mucli-my-session"
                placeholderTextColor={colors.textDim}
                style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]}
              />
              <Text variant="xs" dim style={styles.help}>An existing managed container is reused; otherwise MuCLI builds it.</Text>

              <FieldLabel label="Dockerfile" />
              <ContainerEditorRow
                icon="document-text-outline"
                title="Worker image template"
                detail={`${countLines(dockerfile)} lines · tap to edit`}
                onPress={() => setContainerEditor('dockerfile')}
              />

              <FieldLabel label="Initial mounts" optional />
              {mounts.map((item, index) => (
                <View key={`${item.container_path}-${index}`} style={[styles.mountRow, { backgroundColor: colors.bgLift }]}>
                  <View style={styles.mountCopy}>
                    <Text variant="xs" style={styles.mountPath} numberOfLines={1}>{item.container_path}</Text>
                    <Text variant="xs" dim numberOfLines={1}>{item.host_path} · {item.mode}</Text>
                  </View>
                  <TouchableOpacity onPress={() => setMounts(current => current.filter((_, itemIndex) => itemIndex !== index))} style={styles.removeMount}>
                    <Ionicons name="close" size={17} color={colors.error} />
                  </TouchableOpacity>
                </View>
              ))}
              <WorkspacePathField value={mountHost} onChangeText={setMountHost} placeholder="Host folder" />
              <TextInput
                value={mountContainer}
                onChangeText={setMountContainer}
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="/workspace/project"
                placeholderTextColor={colors.textDim}
                style={[styles.input, styles.mountTarget, { color: colors.text, backgroundColor: colors.bgLift }]}
              />
              <View style={styles.mountActionRow}>
                <View style={styles.segmentRowCompact}>
                  {(['rw', 'ro'] as const).map(mode => (
                    <TouchableOpacity
                      key={mode}
                      onPress={() => setMountMode(mode)}
                      style={[styles.modeChip, { backgroundColor: mountMode === mode ? colors.text : colors.bgLift }]}
                    >
                      <Text variant="xs" style={{ color: mountMode === mode ? colors.bg : colors.text }}>{mode.toUpperCase()}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
                <TouchableOpacity onPress={addMount} disabled={!mountHost.trim() || !mountContainer.trim()} style={[styles.addMount, { backgroundColor: mountHost.trim() && mountContainer.trim() ? colors.accent : colors.bgHover }]}>
                  <Ionicons name="add" size={17} color={mountHost.trim() && mountContainer.trim() ? colors.accentText : colors.textDim} />
                  <Text variant="xs" style={{ color: mountHost.trim() && mountContainer.trim() ? colors.accentText : colors.textDim, fontWeight: '700' }}>Add mount</Text>
                </TouchableOpacity>
              </View>

              <FieldLabel label="Network policy" />
              <ContainerEditorRow
                icon="globe-outline"
                title="Allowlist and blocklist"
                detail={`${countLines(egressAllow)} allowed · ${countLines(egressDeny)} blocked`}
                onPress={() => setContainerEditor('network')}
              />
              <Text variant="xs" dim style={styles.help}>Blocklist entries override the allowlist. All other forwarded traffic is denied.</Text>
              {containerError ? <Text variant="xs" style={{ color: colors.error, marginTop: 6 }}>{containerError}</Text> : null}
            </>
          ) : null}

          <SectionLabel label="Provider" />
          {loadingProviders ? (
            <ActivityIndicator color={colors.accent} style={styles.loader} />
          ) : (
            <View style={styles.choiceGrid}>
              {providers.map(item => {
                const selected = item.name === provider;
                return (
                  <TouchableOpacity
                    key={item.name}
                    onPress={() => setProvider(item.name)}
                    style={[
                      styles.choice,
                      { backgroundColor: selected ? colors.bgHover : colors.bgLift },
                      selected && { borderColor: colors.accent },
                    ]}
                  >
                    <View style={[styles.providerDot, { backgroundColor: item.configured ? colors.success : colors.warning }]} />
                    <Text variant="sm" style={styles.choiceText}>{item.name}</Text>
                    {selected ? <Ionicons name="checkmark" size={17} color={colors.accent} /> : null}
                  </TouchableOpacity>
                );
              })}
            </View>
          )}

          {provider === 'ollama' ? (
            <View style={styles.ollamaBlock}>
              <FieldLabel label="Ollama mode" />
              <View style={styles.segmentRow}>
                {(['local', 'cloud'] as const).map(mode => (
                  <TouchableOpacity
                    key={mode}
                    onPress={() => setOllamaMode(mode)}
                    style={[styles.segment, { backgroundColor: ollamaMode === mode ? colors.text : colors.bgLift }]}
                  >
                    <Text variant="sm" style={{ color: ollamaMode === mode ? colors.bg : colors.text }}>{mode === 'local' ? 'Local' : 'Cloud'}</Text>
                  </TouchableOpacity>
                ))}
              </View>
              {ollamaMode === 'local' ? (
                <>
                  <FieldLabel label="Host override" optional />
                  <TextInput value={ollamaHost} onChangeText={setOllamaHost} autoCapitalize="none" autoCorrect={false} keyboardType="url" placeholder="http://127.0.0.1:11434" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} />
                </>
              ) : (
                <>
                  <FieldLabel label="Ollama API key" />
                  <TextInput value={ollamaApiKey} onChangeText={setOllamaApiKey} autoCapitalize="none" autoCorrect={false} secureTextEntry placeholder="API key" placeholderTextColor={colors.textDim} style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]} />
                </>
              )}
            </View>
          ) : null}

          <SectionLabel label="Model" />
          <TextInput
            value={model}
            onChangeText={setModel}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder={loadingModels ? 'Loading models…' : 'Model name'}
            placeholderTextColor={colors.textDim}
            style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]}
          />
          {loadingModels ? <ActivityIndicator color={colors.accent} style={styles.inlineLoader} /> : null}
          {models.length > 0 ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.modelStrip}>
              {models.map(item => (
                <TouchableOpacity key={item} onPress={() => setModel(item)} style={[styles.modelChip, { backgroundColor: item === model ? colors.text : colors.bgLift }]}>
                  <Text variant="xs" style={{ color: item === model ? colors.bg : colors.text }} numberOfLines={1}>{item}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          ) : null}

          {error ? (
            <View style={[styles.errorBox, { backgroundColor: colors.bgLift }]}>
              <Ionicons name="alert-circle-outline" size={18} color={colors.error} />
              <Text variant="xs" style={{ color: colors.error, flex: 1 }}>{error}</Text>
            </View>
          ) : null}
        </ScrollView>

        <View style={[styles.footer, { backgroundColor: colors.bg, paddingBottom: Math.max(insets.bottom, 14) }]}>
          <TouchableOpacity onPress={createSession} disabled={!canCreate} style={[styles.createButton, { backgroundColor: canCreate ? colors.text : colors.bgHover }]}>
            {creating ? <ActivityIndicator color={colors.bg} /> : (
              <>
                <Text style={{ color: canCreate ? colors.bg : colors.textDim, fontWeight: '700' }}>Create {sessionType} session</Text>
                <Ionicons name="arrow-forward" size={18} color={canCreate ? colors.bg : colors.textDim} />
              </>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>

      <Modal
        visible={containerEditor !== null}
        animationType="slide"
        onRequestClose={() => setContainerEditor(null)}
        statusBarTranslucent
      >
        <KeyboardAvoidingView
          style={[styles.editorRoot, { backgroundColor: colors.bg }]}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
          <View style={[styles.editorHeader, { paddingTop: Math.max(insets.top, 16), borderBottomColor: colors.border }]}>
            <View style={styles.editorHeaderCopy}>
              <Text style={[styles.editorTitle, { color: colors.text }]}>
                {containerEditor === 'dockerfile' ? 'Dockerfile' : 'Network policy'}
              </Text>
              <Text variant="xs" dim>
                {containerEditor === 'dockerfile'
                  ? 'The maintained template is loaded and editable.'
                  : 'Blocklist entries take precedence over allowed domains.'}
              </Text>
            </View>
            <TouchableOpacity
              onPress={() => setContainerEditor(null)}
              style={[styles.iconButton, { backgroundColor: colors.bgHover }]}
            >
              <Ionicons name="close" size={20} color={colors.text} />
            </TouchableOpacity>
          </View>

          {containerEditor === 'dockerfile' ? (
            <TextInput
              value={dockerfile}
              onChangeText={setDockerfile}
              multiline
              textAlignVertical="top"
              autoCapitalize="none"
              autoCorrect={false}
              spellCheck={false}
              style={[styles.fullEditor, { color: colors.text, backgroundColor: colors.bgLift }]}
            />
          ) : (
            <ScrollView
              keyboardShouldPersistTaps="handled"
              contentContainerStyle={[styles.networkEditorContent, { paddingBottom: Math.max(insets.bottom, 16) + 84 }]}
            >
              <FieldLabel label="Allowlist" />
              <Text variant="xs" dim style={styles.editorHelp}>One domain or IPv4 address per line.</Text>
              <TextInput
                value={egressAllow}
                onChangeText={setEgressAllow}
                multiline
                textAlignVertical="top"
                autoCapitalize="none"
                autoCorrect={false}
                spellCheck={false}
                placeholder="api.openai.com"
                placeholderTextColor={colors.textDim}
                style={[styles.policyEditor, { color: colors.text, backgroundColor: colors.bgLift }]}
              />

              <FieldLabel label="Blocklist" optional />
              <Text variant="xs" dim style={styles.editorHelp}>Matching entries are removed from the allowlist.</Text>
              <TextInput
                value={egressDeny}
                onChangeText={setEgressDeny}
                multiline
                textAlignVertical="top"
                autoCapitalize="none"
                autoCorrect={false}
                spellCheck={false}
                placeholder="telemetry.example.com"
                placeholderTextColor={colors.textDim}
                style={[styles.policyEditor, { color: colors.text, backgroundColor: colors.bgLift }]}
              />
            </ScrollView>
          )}

          <View style={[styles.editorFooter, { backgroundColor: colors.bg, paddingBottom: Math.max(insets.bottom, 14), borderTopColor: colors.border }]}>
            <TouchableOpacity
              onPress={() => setContainerEditor(null)}
              style={[styles.editorDoneButton, { backgroundColor: colors.text }]}
            >
              <Text style={{ color: colors.bg, fontWeight: '700' }}>Done</Text>
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </Modal>
  );
}

function ContainerEditorRow({
  icon,
  title,
  detail,
  onPress,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  detail: string;
  onPress: () => void;
}) {
  const { colors } = useTheme();
  return (
    <TouchableOpacity
      onPress={onPress}
      style={[styles.editorRow, { backgroundColor: colors.bgLift, borderColor: colors.border }]}
    >
      <View style={[styles.editorRowIcon, { backgroundColor: colors.bgHover }]}>
        <Ionicons name={icon} size={19} color={colors.accent} />
      </View>
      <View style={styles.editorRowCopy}>
        <Text variant="sm" style={styles.editorRowTitle}>{title}</Text>
        <Text variant="xs" dim>{detail}</Text>
      </View>
      <Ionicons name="expand-outline" size={18} color={colors.textDim} />
    </TouchableOpacity>
  );
}

function countLines(value: string): number {
  return value.split(/\r?\n/).filter(line => line.trim()).length;
}

function splitLines(value: string): string[] {
  return [...new Set(value.split(/[\n,]/).map(item => item.trim()).filter(Boolean))];
}

function SectionLabel({ label }: { label: string }) {
  return <Text variant="xs" dim style={styles.sectionLabel}>{label.toUpperCase()}</Text>;
}

function FieldLabel({ label, optional = false }: { label: string; optional?: boolean }) {
  return (
    <View style={styles.fieldLabelRow}>
      <Text variant="sm" style={styles.fieldLabel}>{label}</Text>
      {optional ? <Text variant="xs" dim>Optional</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingBottom: 14 },
  headerCopy: { flex: 1, alignItems: 'center' },
  title: { fontSize: 18, fontWeight: '700', letterSpacing: -0.3 },
  iconButton: { width: 40, height: 40, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  iconSpacer: { width: 40 },
  content: { paddingHorizontal: 18, paddingTop: 10 },
  sectionLabel: { marginTop: 22, marginBottom: 12, letterSpacing: 0.7, fontWeight: '700' },
  fieldLabelRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 14, marginBottom: 7 },
  fieldLabel: { fontWeight: '600' },
  input: { minHeight: 48, borderRadius: 15, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15 },
  help: { marginTop: 7, lineHeight: 17 },
  typeGrid: { gap: 8 },
  typeCard: { minHeight: 76, borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, padding: 13 },
  typeTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  typeTitle: { flex: 1, fontWeight: '700' },
  typeDetail: { marginTop: 6, lineHeight: 17 },
  notice: { flexDirection: 'row', gap: 10, borderRadius: 14, padding: 12, marginTop: 14 },
  noticeText: { flex: 1, lineHeight: 18 },
  loader: { marginVertical: 18 },
  inlineLoader: { marginTop: 10, alignSelf: 'flex-start' },
  choiceGrid: { gap: 8 },
  choice: { minHeight: 50, borderWidth: 1, borderColor: 'transparent', borderRadius: 15, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center' },
  choiceText: { flex: 1, fontWeight: '600' },
  providerDot: { width: 8, height: 8, borderRadius: 4, marginRight: 10 },
  ollamaBlock: { marginTop: 16 },
  segmentRow: { flexDirection: 'row', gap: 8, marginBottom: 14 },
  segment: { flex: 1, minHeight: 42, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  editorRow: { minHeight: 64, borderWidth: StyleSheet.hairlineWidth, borderRadius: 15, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 11 },
  editorRowIcon: { width: 38, height: 38, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  editorRowCopy: { flex: 1, gap: 3 },
  editorRowTitle: { fontWeight: '600' },
  mountRow: { minHeight: 56, borderRadius: 13, paddingLeft: 12, flexDirection: 'row', alignItems: 'center', marginBottom: 7 },
  mountCopy: { flex: 1 },
  mountPath: { fontWeight: '600' },
  removeMount: { width: 42, height: 48, alignItems: 'center', justifyContent: 'center' },
  mountTarget: { marginTop: 8 },
  mountActionRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 8, gap: 8 },
  segmentRowCompact: { flexDirection: 'row', gap: 6 },
  modeChip: { minWidth: 42, minHeight: 36, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  addMount: { minHeight: 38, borderRadius: 12, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 5 },
  modelStrip: { gap: 8, paddingTop: 10, paddingBottom: 2 },
  modelChip: { maxWidth: 220, minHeight: 36, borderRadius: 12, paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center' },
  errorBox: { flexDirection: 'row', gap: 9, borderRadius: 14, padding: 12, marginTop: 16 },
  footer: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: 18, paddingTop: 12 },
  createButton: { minHeight: 50, borderRadius: 16, flexDirection: 'row', gap: 8, alignItems: 'center', justifyContent: 'center' },
  editorRoot: { flex: 1 },
  editorHeader: { minHeight: 82, paddingHorizontal: 16, paddingBottom: 14, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'center', gap: 12 },
  editorHeaderCopy: { flex: 1, gap: 3 },
  editorTitle: { fontSize: 18, fontWeight: '700', letterSpacing: -0.3 },
  fullEditor: { flex: 1, margin: 14, borderRadius: 15, padding: 14, fontSize: 13, lineHeight: 20, fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace' }) },
  networkEditorContent: { paddingHorizontal: 18, paddingTop: 4 },
  editorHelp: { marginBottom: 7, lineHeight: 17 },
  policyEditor: { minHeight: 190, borderRadius: 15, padding: 14, fontSize: 13, lineHeight: 20, fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace' }) },
  editorFooter: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: 18, paddingTop: 12, borderTopWidth: StyleSheet.hairlineWidth },
  editorDoneButton: { minHeight: 50, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
});
