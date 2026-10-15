import React, { useEffect, useState } from 'react';
import { StyleSheet, TouchableOpacity, View } from 'react-native';

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
import { fontWeights, fonts, radii, spacing, touchTarget } from '../theme/tokens';
import { Banner, Field, Loading, Panel, PlanosButton, SwitchRow, TextField } from './planos';

const STAGE_LABELS: Record<JobStageName, string> = {
  plan: 'Plan',
  implement: 'Implement',
  review: 'Review',
};

const STAGE_HINTS: Record<JobStageName, string> = {
  plan: 'Writes the plan, acceptance criteria and checks. A larger model works best.',
  implement: 'Makes the code changes. A smaller, cheaper model is usually fine.',
  review: 'Reviews the diff and reports findings (report-only: you decide what follows). A larger model works best.',
};

type Draft = Partial<Record<JobStageName, { provider?: string; model?: string }>>;

export type JobStageModelsSheetProps = {
  visible: boolean;
  onClose: () => void;
  onSaved?: (missing: JobStageName[]) => void;
};

export function JobStageModelsSheet({ visible, onClose, onSaved }: JobStageModelsSheetProps) {
  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title="Job stage models">
      <JobStageModelsForm active={visible} onCancel={onClose} onSaved={missing => { onSaved?.(missing); onClose(); }} />
    </ModernBottomSheet>
  );
}

export type JobStageModelsFormProps = {
  /** Loads settings + providers while true. */
  active: boolean;
  onSaved?: (missing: JobStageName[]) => void;
  /** Shows a Cancel button when set (sheet use). */
  onCancel?: () => void;
};

/** Stage models + plan approval form (the jobs Settings page and the sheet). */
export function JobStageModelsForm({ active, onSaved, onCancel }: JobStageModelsFormProps) {
  const visible = active;
  const { colors } = useTheme();
  const [notice, setNotice] = useState('');
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
    setNotice('');
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
      setNotice((result.missing || []).length ? `Saved. No default for ${(result.missing || []).join(', ')}: those stages use the job’s session model.` : 'Saved.');
      onSaved?.((result.missing || []) as JobStageName[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Loading label="Loading job settings…" />;
  return (
    <View style={styles.root}>
      <Panel title="Stage models">
        <Text variant="xs" style={{ color: colors.ink3 }}>
          Default model for each pipeline stage. A model set on a job itself takes priority; a blank stage uses the job’s session model.
        </Text>
        {JOB_STAGE_NAMES.map(stage => {
          const entry = draft[stage] || {};
          const provider = (entry.provider || '').trim();
          const suggestions = (models[provider] || [])
            .filter(name => !entry.model || name.toLowerCase().includes(entry.model.toLowerCase()))
            .slice(0, 8);
          return (
            <View key={stage} style={[styles.stage, { borderTopColor: colors.hairline }]}>
              <Text variant="sm" style={styles.stageTitle}>{STAGE_LABELS[stage]}</Text>
              <Text variant="xs" style={[styles.stageHint, { color: colors.ink3 }]}>{STAGE_HINTS[stage]}</Text>
              <Text variant="xs" style={{ color: colors.ink, fontWeight: fontWeights.medium }}>Provider</Text>
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
                          borderColor: selected ? colors.accent.ink : colors.hairline,
                          backgroundColor: selected ? colors.accent.wash : colors.raised,
                          opacity: item.configured ? 1 : 0.55,
                        },
                      ]}
                    >
                      <Text variant="sm" style={{ color: selected ? colors.accent.ink : colors.ink, fontWeight: selected ? fontWeights.semibold : fontWeights.regular }}>{item.name}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
              <Field label="Model" hint={provider ? undefined : 'Pick a provider first.'}>
                <TextField
                  accessibilityLabel={`${STAGE_LABELS[stage]} model`}
                  value={entry.model || ''}
                  onFocus={() => { setOpenStage(stage); void loadModels(provider); }}
                  onChangeText={value => setStage(stage, { model: value })}
                  placeholder={provider ? 'Model name' : 'Pick a provider first'}
                  autoCapitalize="none"
                  autoCorrect={false}
                  mono
                />
              </Field>
              {openStage === stage && suggestions.length ? (
                <View style={[styles.suggestions, { borderColor: colors.hairline, backgroundColor: colors.raised }]}>
                  {suggestions.map(name => (
                    <TouchableOpacity
                      key={name}
                      accessibilityRole="button"
                      onPress={() => { setStage(stage, { model: name }); setOpenStage(null); }}
                      style={styles.suggestion}
                    >
                      <Text variant="xs" style={{ color: colors.ink, fontFamily: fonts.mono }}>{name}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              ) : null}
            </View>
          );
        })}
      </Panel>
      <Panel title="Plan approval">
        <SwitchRow
          label="Review plan before implementing"
          hint="After planning, wait for you to approve the plan, acceptance criteria and checks."
          value={planApproval}
          onValueChange={setPlanApproval}
        />
      </Panel>
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      {notice ? <Banner tone="ok" glyph="✓" title={notice} /> : null}
      <View style={styles.actions}>
        {onCancel ? <PlanosButton label="Cancel" onPress={onCancel} /> : null}
        <PlanosButton primary label={saving ? 'Saving…' : 'Save'} disabled={saving} onPress={() => void save()} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { gap: spacing.md },
  stage: { paddingTop: spacing.md, marginTop: spacing.xs, borderTopWidth: 1, gap: spacing.xs },
  stageTitle: { fontWeight: fontWeights.semibold },
  stageHint: { marginBottom: spacing.xs },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.sm },
  chip: { minHeight: touchTarget, paddingHorizontal: spacing.md, borderRadius: radii.control, borderWidth: 1, justifyContent: 'center' },
  suggestions: { marginTop: -spacing.xs, marginBottom: spacing.sm, borderWidth: 1, borderRadius: radii.control },
  suggestion: { minHeight: touchTarget, justifyContent: 'center', paddingHorizontal: spacing.md },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginBottom: spacing.sm },
});
