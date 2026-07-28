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
import { sessionsApi } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { WorkspacePathField } from './WorkspacePathField';

export type NewSessionSheetProps = {
  visible: boolean;
  onClose: () => void;
  onCreated: (session: { name: string; provider: string; model: string }) => void;
};

const SESSION_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

export function NewSessionSheet({ visible, onClose, onCreated }: NewSessionSheetProps) {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
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

  const reset = useCallback(() => {
    setName('');
    setWorkspace('');
    setProvider('');
    setModels([]);
    setModel('');
    setError(null);
    setOllamaMode('local');
    setOllamaHost('');
    setOllamaApiKey('');
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
  }, [loadProviders, reset, visible]);

  useEffect(() => {
    if (!visible || !provider) return;
    loadModels(provider);
  }, [loadModels, provider, visible]);

  const nameError = useMemo(() => {
    if (!name.trim()) return null;
    return SESSION_NAME_PATTERN.test(name.trim())
      ? null
      : 'Use letters, numbers, dots, dashes, or underscores.';
  }, [name]);

  const canCreate = Boolean(
    name.trim()
      && !nameError
      && provider
      && model.trim()
      && !creating,
  );

  const createSession = async () => {
    if (!canCreate) return;
    setCreating(true);
    setError(null);
    try {
      await sessionsApi.create(
        name.trim(),
        provider,
        model.trim(),
        workspace.trim() || undefined,
        provider === 'ollama'
          ? {
              ollamaMode,
              ollamaHost: ollamaHost.trim() || undefined,
              ollamaApiKey: ollamaApiKey.trim() || undefined,
            }
          : undefined,
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
            <Text variant="xs" dim>Choose where and how MuCLI should run.</Text>
          </View>
          <View style={styles.iconSpacer} />
        </View>

        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={[styles.content, { paddingBottom: Math.max(insets.bottom, 20) + 96 }]}
        >
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

          <FieldLabel label="Workspace" optional />
          <WorkspacePathField
            value={workspace}
            onChangeText={setWorkspace}
            placeholder="/home/user/dev/project"
          />
          <Text variant="xs" dim style={styles.help}>Type to browse folders on the MuCLI host.</Text>

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
                    style={[
                      styles.segment,
                      { backgroundColor: ollamaMode === mode ? colors.text : colors.bgLift },
                    ]}
                  >
                    <Text variant="sm" style={{ color: ollamaMode === mode ? colors.bg : colors.text }}>
                      {mode === 'local' ? 'Local' : 'Cloud'}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>

              {ollamaMode === 'local' ? (
                <>
                  <FieldLabel label="Host override" optional />
                  <TextInput
                    value={ollamaHost}
                    onChangeText={setOllamaHost}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="url"
                    placeholder="http://127.0.0.1:11434"
                    placeholderTextColor={colors.textDim}
                    style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]}
                  />
                </>
              ) : (
                <>
                  <FieldLabel label="Ollama API key" />
                  <TextInput
                    value={ollamaApiKey}
                    onChangeText={setOllamaApiKey}
                    autoCapitalize="none"
                    autoCorrect={false}
                    secureTextEntry
                    placeholder="API key"
                    placeholderTextColor={colors.textDim}
                    style={[styles.input, { color: colors.text, backgroundColor: colors.bgLift }]}
                  />
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
                <TouchableOpacity
                  key={item}
                  onPress={() => setModel(item)}
                  style={[
                    styles.modelChip,
                    { backgroundColor: item === model ? colors.text : colors.bgLift },
                  ]}
                >
                  <Text variant="xs" style={{ color: item === model ? colors.bg : colors.text }} numberOfLines={1}>
                    {item}
                  </Text>
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
          <TouchableOpacity
            onPress={createSession}
            disabled={!canCreate}
            style={[
              styles.createButton,
              { backgroundColor: canCreate ? colors.text : colors.bgHover },
            ]}
          >
            {creating ? (
              <ActivityIndicator color={colors.bg} />
            ) : (
              <>
                <Text style={{ color: canCreate ? colors.bg : colors.textDim, fontWeight: '700' }}>Create session</Text>
                <Ionicons name="arrow-forward" size={18} color={canCreate ? colors.bg : colors.textDim} />
              </>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
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
  fieldLabelRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 7 },
  fieldLabel: { fontWeight: '600' },
  input: { minHeight: 48, borderRadius: 15, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15 },
  help: { marginTop: 7, lineHeight: 17 },
  loader: { marginVertical: 18 },
  inlineLoader: { marginTop: 10, alignSelf: 'flex-start' },
  choiceGrid: { gap: 8 },
  choice: { minHeight: 50, borderWidth: 1, borderColor: 'transparent', borderRadius: 15, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center' },
  choiceText: { flex: 1, fontWeight: '600' },
  providerDot: { width: 8, height: 8, borderRadius: 4, marginRight: 10 },
  ollamaBlock: { marginTop: 16 },
  segmentRow: { flexDirection: 'row', gap: 8, marginBottom: 14 },
  segment: { flex: 1, minHeight: 42, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  modelStrip: { gap: 8, paddingTop: 10, paddingBottom: 2 },
  modelChip: { maxWidth: 220, minHeight: 36, borderRadius: 12, paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center' },
  errorBox: { flexDirection: 'row', gap: 9, borderRadius: 14, padding: 12, marginTop: 16 },
  footer: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: 18, paddingTop: 12 },
  createButton: { minHeight: 50, borderRadius: 16, flexDirection: 'row', gap: 8, alignItems: 'center', justifyContent: 'center' },
});
