import React, { useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Switch, TextInput, TouchableOpacity, View } from 'react-native';

import {
  jobsApi,
  compactStageModels,
  JOB_STAGE_NAMES,
  type JobStageName,
} from '../api/jobs';
import { providersApi, type ProviderInfo } from '../api/providers';
import { ModernBottomSheet } from './ModernBottomSheet';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';

const STAGE_LABELS: Record<JobStageName, string> = {
  plan: 'Plan',
  implement: 'Implement',
  review: 'Review',
};

const STAGE_HINTS: Record<JobStageName, string> = {
  plan: 'Writes the plan, acceptance criteria and checks. A larger model works best.',
  implement: 'Makes the code changes. A smaller, cheaper model is usually fine.',
  review: 'Reviews the diff and can send work back. A larger model works best.',
};

type Draft = Partial<Record<JobStageName, { provider?: string; model?: string }>>;

export type JobStageModelsSheetProps = {
  visible: boolean;
  onClose: () => void;
  onSaved?: (missing: JobStageName[]) => void;
};

export function JobStageModelsSheet({ visible, onClose, onSaved }: JobStageModelsSheetProps) {
  const { colors } = useTheme();
  const [draft, setDraft] = useState<Draft>({});
  const [planApproval, setPlanApproval] = useState(true);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [openStage, setOpenStage] = useState<JobStageName | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setLoading(true);
    setError('');
    Promise.all([jobsApi.getSettings(), providersApi.list().catch(() => ({ providers: [] as ProviderInfo[] }))])
      .then(([settings, list]) => {
        if (!alive) return;
        setDraft({ ...(settings.stage_models || {}) });
        setPlanApproval(settings.plan_approval !== false);
        setProviders(list.providers || []);
      })
      .catch(err => alive && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [visible]);

  const loadModels = async (provider: string) => {
    if (!provider || models[provider]) return;
    try {
      const res = await providersApi.listModels(provider);
      setModels(prev => ({ ...prev, [provider]: res.models || [] }));
    } catch {
      setModels(prev => ({ ...prev, [provider]: [] }));
    }
  };

  const setStage = (stage: JobStageName, patch: { provider?: string; model?: string }) => {
    setDraft(prev => ({ ...prev, [stage]: { ...(prev[stage] || {}), ...patch } }));
  };

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const result = await jobsApi.saveSettings({ stage_models: compactStageModels(draft), plan_approval: planApproval });
      onSaved?.((result.missing || []) as JobStageName[]);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title="Job stage models">
      {loading ? (
        <ActivityIndicator color={colors.accent} style={styles.loader} />
      ) : (
        <ScrollView keyboardShouldPersistTaps="handled">
          <Text variant="xs" dim style={styles.intro}>
            Default model for each pipeline stage. A model set on a job itself takes priority.
          </Text>
          {JOB_STAGE_NAMES.map(stage => {
            const entry = draft[stage] || {};
            const provider = (entry.provider || '').trim();
            const suggestions = (models[provider] || [])
              .filter(name => !entry.model || name.toLowerCase().includes(entry.model.toLowerCase()))
              .slice(0, 8);
            return (
              <View key={stage} style={[styles.stage, { borderBottomColor: colors.hairline }]}>
                <Text variant="sm" style={styles.stageTitle}>{STAGE_LABELS[stage]}</Text>
                <Text variant="xs" dim style={styles.stageHint}>{STAGE_HINTS[stage]}</Text>
                <View style={styles.chips} accessibilityLabel={`${STAGE_LABELS[stage]} provider`}>
                  {providers.map(item => {
                    const selected = item.name === provider;
                    return (
                      <TouchableOpacity
                        key={item.name}
                        accessibilityRole="button"
                        accessibilityState={{ selected }}
                        onPress={() => {
                          setStage(stage, { provider: item.name, model: selected ? entry.model : '' });
                          setOpenStage(stage);
                          void loadModels(item.name);
                        }}
                        style={[
                          styles.chip,
                          {
                            borderColor: selected ? colors.accent : colors.hairline,
                            backgroundColor: selected ? colors.accentSoft : 'transparent',
                            opacity: item.configured ? 1 : 0.55,
                          },
                        ]}
                      >
                        <Text variant="xs" style={{ color: selected ? colors.text : colors.textSoft }}>{item.name}</Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
                <TextInput
                  accessibilityLabel={`${STAGE_LABELS[stage]} model`}
                  value={entry.model || ''}
                  onFocus={() => { setOpenStage(stage); void loadModels(provider); }}
                  onChangeText={value => setStage(stage, { model: value })}
                  placeholder={provider ? 'Model name' : 'Pick a provider first'}
                  placeholderTextColor={colors.textDim}
                  autoCapitalize="none"
                  autoCorrect={false}
                  style={[styles.input, { color: colors.text, borderBottomColor: colors.hairline }]}
                />
                {openStage === stage && suggestions.length ? (
                  <View style={styles.suggestions}>
                    {suggestions.map(name => (
                      <TouchableOpacity
                        key={name}
                        accessibilityRole="button"
                        onPress={() => { setStage(stage, { model: name }); setOpenStage(null); }}
                        style={styles.suggestion}
                      >
                        <Text variant="xs" style={{ color: colors.textSoft }}>{name}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                ) : null}
              </View>
            );
          })}
          <View style={styles.toggleRow}>
            <View style={{ flex: 1 }}>
              <Text variant="sm">Review plan before implementing</Text>
              <Text variant="xs" dim>After planning, wait for you to approve the plan, acceptance criteria and checks.</Text>
            </View>
            <Switch value={planApproval} onValueChange={setPlanApproval} accessibilityLabel="Review plan before implementing" />
          </View>
          {error ? <Text variant="xs" style={{ color: colors.error, marginTop: 8 }}>{error}</Text> : null}
          <View style={styles.actions}>
            <TouchableOpacity accessibilityRole="button" onPress={onClose} style={[styles.button, { borderColor: colors.hairline }]}>
              <Text variant="xs" style={{ color: colors.textSoft }}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              accessibilityRole="button"
              disabled={saving}
              onPress={() => void save()}
              style={[styles.button, { borderColor: colors.accent, backgroundColor: colors.accentSoft, opacity: saving ? 0.5 : 1 }]}
            >
              <Text variant="xs" style={{ color: colors.text, fontWeight: '600' }}>{saving ? 'Saving…' : 'Save'}</Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      )}
    </ModernBottomSheet>
  );
}

const styles = StyleSheet.create({
  loader: { marginVertical: 24 },
  intro: { marginBottom: 10, lineHeight: 18 },
  stage: { paddingVertical: 12, borderBottomWidth: StyleSheet.hairlineWidth },
  stageTitle: { fontWeight: '600' },
  stageHint: { marginTop: 2, marginBottom: 8, lineHeight: 17 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { minHeight: 32, paddingHorizontal: 10, borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, justifyContent: 'center' },
  input: { minHeight: 40, borderBottomWidth: StyleSheet.hairlineWidth, marginTop: 8, fontSize: 14 },
  suggestions: { marginTop: 4 },
  suggestion: { minHeight: 36, justifyContent: 'center', paddingHorizontal: 4 },
  toggleRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14 },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 10, marginTop: 12, marginBottom: 8 },
  button: { minWidth: 88, minHeight: 38, borderWidth: StyleSheet.hairlineWidth, borderRadius: 8, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 12 },
});
