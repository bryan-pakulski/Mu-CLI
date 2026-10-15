// "Run in" picker (documentation/plans/job_templates.md §3.1): one field that opens a sheet with
// Default runtime, every container template and every environment, plus "Advanced…" for the
// multi-environment editor where the surface has one. Template rows carry description, GPU,
// job setup and size; a template that can't reach a registry the workspace needs shows
// `◆ can't reach <domain>` with an Allow action (confirmed, then PATCHes the template's egress).

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { containersApi } from '../api/containers';
import { environmentsApi, type EnvironmentSourceOption, type EnvironmentsVM, type TemplateSourceOption } from '../api/environments';
import { isRepositoryUrl } from '../api/jobs';
import {
  DEFAULT_RUN_IN, allowEgress, environmentSummary, fitWarnings, loadLastRunIn, preselectRunIn, runInGroups, specKind, specName,
  templateSummary, type RunInSpec,
} from '../api/runIn';
import { useTheme } from '../theme/ThemeContext';
import { fonts, radii, spacing, touchTarget } from '../theme/tokens';
import { Button } from './Button';
import { Loading } from './Card';
import { Banner, useToast } from './Feedback';
import { Field, SwitchRow } from './Form';
import { Icon } from './Icon';
import { Pressable } from './Pressable';
import { Dialog, Sheet } from './Sheet';
import { Text } from './Text';
import { useGlass } from './Glass';

const GLYPH = { default: '○', template: '◇', environment: '⬢' } as const;

export type RunInPickerProps = {
  /** Loads sources while true (the host sheet is open). */
  active: boolean;
  /** Workspace path or git URL the job works in. */
  workspace: string;
  session?: string | null;
  /** The matched session's container-mode environment (preselection step 2). */
  sessionEnvironment?: string | null;
  value: RunInSpec;
  onChange: (spec: RunInSpec) => void;
  remember: boolean;
  onRememberChange: (value: boolean) => void;
  /** Opens the host's multi-environment editor; the row is hidden when absent. */
  onAdvanced?: () => void;
  /** Set while the host's Advanced override is active (e.g. "2 environments"): it replaces the choice. */
  advancedSummary?: string | null;
  /** Wait before fetching after the workspace changes (typing). */
  debounceMs?: number;
  testID?: string;
};

type Allow = { template: TemplateSourceOption; domain: string };

export function RunInPicker({
  active, workspace, session, sessionEnvironment, value, onChange, remember, onRememberChange, onAdvanced, advancedSummary,
  debounceMs = 400, testID = 'run-in',
}: RunInPickerProps) {
  const { colors } = useTheme();
  const glass = useGlass();
  const toast = useToast();
  const [vm, setVm] = useState<EnvironmentsVM | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [allow, setAllow] = useState<Allow | null>(null);
  const [allowing, setAllowing] = useState(false);
  // Preselection runs until the user picks; a new workspace starts over.
  const picked = useRef(false);
  const path = workspace.trim();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const remote = isRepositoryUrl(path);
      const [next, lastUsed] = await Promise.all([
        environmentsApi.get({ workspace: remote ? undefined : path || undefined, repository: remote ? path : undefined, session: session || undefined, images: false }),
        loadLastRunIn(path).catch(() => null),
      ]);
      setVm(next);
      setError('');
      if (!picked.current) onChange(preselectRunIn({ vm: next, sessionEnvironment, lastUsed }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
    // onChange is the host's setter; preselection must not refetch when its identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, session, sessionEnvironment]);

  useEffect(() => { picked.current = false; }, [path]);
  useEffect(() => {
    if (!active) return undefined;
    const timer = setTimeout(() => { void load(); }, debounceMs);
    return () => clearTimeout(timer);
  }, [active, load, debounceMs]);

  const groups = useMemo(() => runInGroups(vm), [vm]);
  const kind = specKind(value);
  const selectedTemplate = kind === 'template' ? groups.templates.find(t => t.spec === value) || null : null;
  const label = advancedSummary
    ? `Advanced: ${advancedSummary}`
    : kind === 'default' ? 'Default runtime' : specName(value);
  const glyph = advancedSummary ? GLYPH.environment : GLYPH[kind];
  const remembered = vm?.remembered?.source || '';

  const choose = (spec: RunInSpec) => {
    picked.current = true;
    onChange(spec);
    if (specKind(spec) !== 'template') onRememberChange(false);
    setOpen(false);
  };

  const confirmAllow = async () => {
    if (!allow) return;
    setAllowing(true);
    try {
      await containersApi.patchTemplate(allow.template.name, { egress_allow: allowEgress(allow.template, [allow.domain]) });
      toast(`${allow.template.name} can now reach ${allow.domain}`, 'ok');
      setAllow(null);
      await load();
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'danger');
    } finally {
      setAllowing(false);
    }
  };

  const warnings = (t: TemplateSourceOption, where: string) => fitWarnings(t).map(domain => (
    <View key={domain} style={styles.warning} testID={`${testID}-fit-${where}-${t.name}-${domain}`}>
      <Text variant="caption" importantForAccessibility="no" style={{ color: colors.attention.ink, fontWeight: '600' }}>◆</Text>
      <Text variant="caption" style={{ color: colors.attention.ink, flex: 1 }}>can't reach {domain}</Text>
      <Button small variant="tonal" tone="attention" label="Allow" style={styles.allow}
        accessibilityLabel={`Allow ${t.name} to reach ${domain}`} testID={`${testID}-allow-${t.name}-${domain}`}
        onPress={() => setAllow({ template: t, domain })} />
    </View>
  ));

  const row = (spec: RunInSpec, name: string, detail: string, rowGlyph: string, extra?: React.ReactNode) => {
    const on = !advancedSummary && spec === value;
    return (
      <View key={spec || 'default'} style={[styles.rowWrap, { backgroundColor: on ? colors.accent.wash : 'transparent' }]}>
        <Pressable accessibilityRole="radio" accessibilityLabel={`${name}${detail ? `, ${detail}` : ''}${spec && spec === remembered ? ', remembered for this workspace' : ''}`}
          accessibilityState={{ checked: on, selected: on }} scale={false} onPress={() => choose(spec)} testID={`${testID}-option-${spec || 'default'}`}
          style={({ pressed }) => [styles.row, { backgroundColor: pressed && !on ? colors.sunken : 'transparent' }]}>
          <Text variant="body" importantForAccessibility="no" style={{ color: on ? colors.accent.ink : colors.ink3, width: 18 }}>{rowGlyph}</Text>
          <View style={{ flex: 1 }}>
            <Text variant="body" numberOfLines={1} style={[styles.mono, { color: on ? colors.accent.ink : colors.ink, fontWeight: on ? '600' : '400' }]}>{name}</Text>
            {detail ? <Text variant="caption" tone="ink3" numberOfLines={2}>{detail}</Text> : null}
          </View>
          {spec && spec === remembered ? <Text variant="caption" tone="ink3">remembered</Text> : null}
          {on ? <Icon name="check" size="sm" color={colors.accent.ink} /> : null}
        </Pressable>
        {extra}
      </View>
    );
  };

  const header = (text: string) => (
    <Text variant="label" tone="ink2" accessibilityRole="header" style={styles.group}>{text}</Text>
  );

  return (
    <Field label="Run in" testID={testID}
      hint={advancedSummary ? 'Advanced environments replace the Run in choice.' : kind === 'environment' ? 'Snapshotted when the job starts.' : undefined}>
      <Pressable testID={`${testID}-trigger`} accessibilityRole="button" accessibilityLabel={`Run in: ${label}`} accessibilityHint="Opens a list of runtimes"
        accessibilityState={{ expanded: open }} scale={false} onPress={() => setOpen(true)}
        style={[styles.trigger, { borderColor: colors.controlBorder, backgroundColor: glass.solid ? colors.surface : glass.style('subtle').backgroundColor }]}>
        <Text variant="body" importantForAccessibility="no" style={{ color: colors.ink3 }}>{glyph}</Text>
        <Text variant="body" numberOfLines={1} style={[styles.mono, { flex: 1, color: colors.ink }]} testID={`${testID}-value`}>{label}</Text>
        {loading && !vm ? <Text variant="caption" tone="ink3">loading…</Text> : null}
        <Icon name="chevron-down" size="sm" color={colors.ink3} />
      </Pressable>
      {error ? <Text variant="caption" tone="ink3">Templates unavailable: {error}</Text> : null}
      {selectedTemplate && !advancedSummary ? warnings(selectedTemplate, 'field') : null}
      {kind === 'template' && !advancedSummary ? (
        <SwitchRow label="Remember for this workspace" testID={`${testID}-remember`} value={remember} onValueChange={onRememberChange}
          hint={remembered === value ? 'Already the default for this repository.' : 'Also used by scheduled Jira imports.'} />
      ) : null}

      <Sheet visible={open} onClose={() => setOpen(false)} title="Run in" testID={`${testID}-sheet`}>
        {loading && !vm ? <Loading label="Loading templates…" /> : null}
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={`Templates unavailable: ${error}`} /> : null}
        <View accessibilityRole="radiogroup" accessibilityLabel="Run in" style={styles.list}>
          {header('Default runtime')}
          {row(DEFAULT_RUN_IN, 'Default runtime', 'Shared runtime image · no project tools', GLYPH.default)}
          {groups.templates.length ? header('Templates') : null}
          {groups.templates.map(t => row(t.spec, t.name, templateSummary(t), GLYPH.template, fitWarnings(t).length ? <View style={styles.rowWarnings}>{warnings(t, 'row')}</View> : null))}
          {groups.environments.length ? header('Environments (snapshotted at start)') : null}
          {groups.environments.map((e: EnvironmentSourceOption) => row(e.spec, e.name, environmentSummary(e), GLYPH.environment))}
        </View>
        {onAdvanced ? (
          <Pressable testID={`${testID}-advanced`} accessibilityRole="button" accessibilityLabel="Advanced: several environments, paths, Docker, GPUs"
            onPress={() => { setOpen(false); onAdvanced(); }}
            style={({ pressed }) => [styles.row, styles.advanced, { borderTopColor: colors.hairline, backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
            <View style={{ flex: 1 }}>
              <Text variant="body" style={{ color: colors.ink }}>Advanced…</Text>
              <Text variant="caption" tone="ink3">Several environments, paths, Docker, GPUs</Text>
            </View>
            <Icon name="chevron-right" size="sm" color={colors.ink3} />
          </Pressable>
        ) : null}
      </Sheet>

      <Dialog visible={allow !== null} onClose={() => setAllow(null)} title={allow ? `Allow ${allow.domain}?` : ''} testID={`${testID}-allow-dialog`}
        footer={(
          <>
            <Button label="Cancel" onPress={() => setAllow(null)} />
            <Button primary label="Allow" loading={allowing} disabled={allowing} onPress={() => void confirmAllow()} testID={`${testID}-allow-confirm`} />
          </>
        )}>
        {allow ? (
          <Text variant="body">
            Adds <Text variant="body" style={styles.mono}>{allow.domain}</Text> to the network allowlist of template{' '}
            <Text variant="body" style={styles.mono}>{allow.template.name}</Text>. Jobs started from it later can reach that host; running jobs keep their snapshot.
          </Text>
        ) : null}
      </Dialog>
    </Field>
  );
}

const styles = StyleSheet.create({
  mono: { fontFamily: fonts.mono },
  trigger: { minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, borderRadius: radii.sm, borderWidth: 1, paddingHorizontal: spacing.md },
  list: { gap: spacing.xs },
  group: { marginTop: spacing.sm },
  rowWrap: { borderRadius: radii.md },
  row: { minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.md, borderRadius: radii.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  rowWarnings: { paddingLeft: spacing.md + 18 + spacing.md, paddingRight: spacing.md, paddingBottom: spacing.sm },
  warning: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, minHeight: touchTarget },
  allow: { minHeight: touchTarget },
  advanced: { marginTop: spacing.sm, borderTopWidth: 1, borderRadius: 0 },
});
