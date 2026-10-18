// Job build environments on Android (plan §10): the New job "Build environments"
// field, the editor it shares with the job panel's Change, the job header pill +
// environments sheet (status, size, probe, skipped/secret paths, Refresh, Keep,
// Change) and the Settings card (Docker-in-Docker status, install steps, kept jobs).

import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';

import {
  bindingLabel, bindingsFromDrafts, draftError, draftFromBinding, draftFromSpec, emptyDraft, environmentsApi,
  environmentsPillLabel, pendingEnvironmentApprovals, sourceLabel, statusTone,
  type BindingSource, type EnvironmentDraft, type EnvironmentsVM, type JobBuildEnvironments, type JobEnvironmentsSummary,
} from '../api/environments';
import { jobCoreApi, type JobAction } from '../api/jobCore';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, radii, spacing, touchTarget } from '../theme/tokens';
import { bytes, when } from '../theme/status';
import { Banner, Button, Empty, Field, KV, Loading, Panel, Pill, Pre, Pressable, Segmented, Sheet, SwitchRow, Text, TextField, useToast } from '../ui';

type Invoke = (idOrAction: string | JobAction, extra?: Record<string, unknown>) => void;

const KIND_OPTIONS: Array<{ value: BindingSource; label: string }> = [
  { value: 'environment', label: 'Environment' }, { value: 'template', label: 'Template' }, { value: 'image', label: 'Image' },
];

/** Loads the sources (and the resolved preview) while `active`. */
export function useEnvironmentSources(active: boolean, query: { repository?: string; session?: string } = {}) {
  const [vm, setVm] = useState<EnvironmentsVM | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { repository, session } = query;
  const reload = useCallback(() => {
    setLoading(true);
    environmentsApi.get({ repository, session })
      .then(next => { setVm(next); setError(''); })
      .catch(err => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [repository, session]);
  useEffect(() => { if (active) reload(); }, [active, reload]);
  return { vm, error, loading, reload };
}

/* ------------------------------------------------------------ editor */

/** Rows of {source, ref, paths, Docker, GPUs}; the source picker lists environments, templates and local images. */
export function EnvironmentsEditor({ drafts, onChange, sources }: { drafts: EnvironmentDraft[]; onChange: (next: EnvironmentDraft[]) => void; sources: EnvironmentsVM | null }) {
  const { colors } = useTheme();
  const set = (i: number, patch: Partial<EnvironmentDraft>) => onChange(drafts.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  return (
    <View style={styles.col} testID="environments-editor">
      {drafts.map((d, i) => {
        const options = d.source === 'environment' ? sources?.environments || [] : d.source === 'template' ? sources?.templates || [] : sources?.images || [];
        const error = draftError(d);
        return (
          <View key={i} style={[styles.draft, { borderColor: colors.hairline, backgroundColor: colors.canvas }]} testID={`environment-draft-${i}`}>
            <Segmented accessibilityLabel={`Source kind ${i + 1}`} options={KIND_OPTIONS} value={d.source} onChange={source => set(i, { source, ref: '' })} />
            {options.length ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips} accessibilityLabel="Sources">
                {options.slice(0, 60).map(o => {
                  const on = draftFromSpec(o.spec).ref === d.ref;
                  return (
                    <Pressable key={o.spec} accessibilityRole="button" accessibilityState={{ selected: on }} accessibilityLabel={`Use ${o.name}`}
                      onPress={() => onChange(drafts.map((x, j) => (j === i ? draftFromSpec(o.spec, x) : x)))}
                      style={[styles.chip, { borderColor: on ? colors.accent.ink : colors.hairline, backgroundColor: on ? colors.accent.wash : colors.surface }]}>
                      <Text variant="xs" style={[styles.mono, { color: on ? colors.accent.ink : colors.ink }]} numberOfLines={1}>{o.name}</Text>
                      {o.status || o.size ? <Text variant="xs" style={{ color: colors.ink3 }}>{o.status || o.size}</Text> : null}
                    </Pressable>
                  );
                })}
              </ScrollView>
            ) : null}
            <Field label="Name" error={error || undefined}>
              <TextField mono accessibilityLabel={`Build environment ${i + 1} name`} value={d.ref} autoCapitalize="none" autoCorrect={false}
                placeholder={d.source === 'image' ? 'rust:1.89-bookworm' : d.source === 'template' ? 'trt' : 'rust-build'}
                onChangeText={ref => set(i, { ref })} invalid={!!error} />
            </Field>
            <Field label="Paths" hint="Repository-relative, comma-separated. Blank = whole repository.">
              <TextField mono accessibilityLabel={`Build environment ${i + 1} paths`} value={d.paths} autoCapitalize="none" autoCorrect={false}
                placeholder="resq-backend, stream-processor-rs" onChangeText={paths => set(i, { paths })} />
            </Field>
            <SwitchRow label="Docker-in-Docker" hint={sources && !sources.docker_in_docker?.available ? 'Needs Sysbox on the host.' : 'A private Docker engine for this job.'}
              value={d.docker} onValueChange={docker => set(i, { docker })} testID={`environment-docker-${i}`} />
            <Field label="GPUs" hint="Blank inherits from the environment · none · all · 0,1">
              <TextField mono accessibilityLabel={`Build environment ${i + 1} GPUs`} value={d.gpus} autoCapitalize="none" autoCorrect={false}
                placeholder="inherit" onChangeText={gpus => set(i, { gpus })} />
            </Field>
            <View style={styles.rowEnd}>
              <Button small ghost tone="danger" label="Remove" accessibilityLabel={`Remove build environment ${i + 1}`} onPress={() => onChange(drafts.filter((_, j) => j !== i))} />
            </View>
          </View>
        );
      })}
      <Button small label="Add environment" onPress={() => onChange([...drafts, emptyDraft()])} testID="environment-add" />
    </View>
  );
}

/* ------------------------------------------------------------ New job field */

/** New job "Build environments": the resolved set + its source; Change edits a job override.
 * `value` null = use the resolved set (no override is sent). */
export function BuildEnvironmentsField({ active, repository, session, value, onChange }: {
  active: boolean; repository?: string; session?: string | null; value: EnvironmentDraft[] | null; onChange: (next: EnvironmentDraft[] | null) => void;
}) {
  const { colors } = useTheme();
  const { vm, error, loading } = useEnvironmentSources(active, { repository, session: session || undefined });
  const resolved = vm?.resolved;
  const summary = resolved?.bindings?.length ? resolved.bindings.map(bindingLabel).join(' · ') : 'shared runtime image';
  return (
    <Field label="Build environments" hint={value ? 'Job override' : resolved ? `from ${sourceLabel(resolved.source)}` : undefined} testID="new-job-environments">
      {loading && !vm ? <Loading label="Resolving build environments…" /> : null}
      {error ? <Text variant="xs" style={{ color: colors.ink3 }}>Build environments unavailable: {error}</Text> : null}
      {resolved?.error ? <Banner tone="danger" glyph="✗" title={resolved.error} /> : null}
      {value ? (
        <>
          <EnvironmentsEditor drafts={value} onChange={onChange} sources={vm} />
          <Button small ghost label="Use resolved set" onPress={() => onChange(null)} testID="environments-reset" />
        </>
      ) : vm ? (
        <View style={styles.rowWrap}>
          <Text variant="xs" style={[styles.mono, { flex: 1 }]} testID="environments-summary">{summary}</Text>
          <Button small label="Change" testID="environments-change"
            onPress={() => onChange(resolved?.bindings?.length ? resolved.bindings.map(draftFromBinding) : [emptyDraft()])} />
        </View>
      ) : null}
    </Field>
  );
}

/* ------------------------------------------------------------ New job: repository environment approval */

/** The image/Dockerfile a repository declares (or its devcontainer suggests) with an explicit Approve button.
 * Debounced on the typed workspace; renders nothing when the repository asks for nothing. */
export function RepositoryEnvironmentApproval({ repository, session, debounceMs = 600 }: { repository?: string; session?: string | null; debounceMs?: number }) {
  const { colors } = useTheme();
  const toast = useToast();
  const [requests, setRequests] = useState<ReturnType<typeof pendingEnvironmentApprovals>>([]);
  const [busy, setBusy] = useState('');
  const [approved, setApproved] = useState<string[]>([]);
  const repo = (repository || '').trim();
  useEffect(() => {
    setRequests([]);
    if (!repo) return undefined;
    let alive = true;
    const timer = setTimeout(() => {
      environmentsApi.get({ repository: repo, session: session || undefined, images: false })
        .then(vm => { if (alive) setRequests(pendingEnvironmentApprovals(vm.resolved, repo)); })
        .catch(() => undefined);
    }, debounceMs);
    return () => { alive = false; clearTimeout(timer); };
  }, [repo, session, debounceMs]);
  const pending = requests.filter(r => !approved.includes(r.fingerprint));
  if (!pending.length) return null;
  return (
    <View style={styles.col} testID="new-job-environment-approval">
      {pending.map(r => (
        <Banner key={r.fingerprint} tone="warn" glyph="◆" title={`Repository build environment: ${r.kind === 'build' ? 'build and use' : 'use'} ${r.label || r.ref}`}>
          <Text variant="xs"><Text variant="xs" style={{ color: colors.ink3 }}>Ref: </Text><Text variant="xs" style={styles.mono}>{r.ref}</Text></Text>
          <Text variant="xs"><Text variant="xs" style={{ color: colors.ink3 }}>Fingerprint: </Text><Text variant="xs" style={styles.mono}>{r.fingerprint}</Text></Text>
          {r.ignored_features?.length ? <Text variant="xs" style={{ color: colors.ink3 }}>Ignored features: {r.ignored_features.join(', ')}</Text> : null}
          {r.message ? <Text variant="xs" style={{ color: colors.ink3 }}>{r.message}</Text> : null}
          <View style={styles.rowWrap}>
            <Button small tone="warn" label="Approve environment" disabled={!!busy} testID={`approve-environment-${r.fingerprint}`}
              onPress={() => {
                setBusy(r.fingerprint);
                environmentsApi.approve({ repository: r.repository || repo, fingerprint: r.fingerprint, session: session || undefined })
                  .then(() => { setApproved(a => [...a, r.fingerprint]); toast('Build environment approved', 'ok'); })
                  .catch(err => toast(err instanceof Error ? err.message : String(err), 'danger'))
                  .finally(() => setBusy(''));
              }} />
          </View>
        </Banner>
      ))}
    </View>
  );
}

/* ------------------------------------------------------------ job header pill */

export function EnvironmentsPill({ summary, onPress }: { summary?: JobEnvironmentsSummary | null; onPress: () => void }) {
  const label = environmentsPillLabel(summary);
  if (!label) return null;
  const tone = summary?.status === 'failed' ? 'danger' : summary?.status === 'preparing' ? 'running' : summary?.kept ? 'accent' : 'muted';
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`Build environments: ${label}`} onPress={onPress} testID="job-environments-pill" style={styles.pillHit}>
      <Pill tone={tone}>{label}</Pill>
    </Pressable>
  );
}

/* ------------------------------------------------------------ job environments sheet */

export function JobEnvironmentsSheet({ visible, onClose, environments, actions, invoke, busy, repository, session }: {
  visible: boolean; onClose: () => void; environments?: JobBuildEnvironments | null; actions: JobAction[];
  invoke: Invoke; busy: boolean; repository?: string; session?: string;
}) {
  const { colors } = useTheme();
  const [editing, setEditing] = useState<EnvironmentDraft[] | null>(null);
  const { vm } = useEnvironmentSources(visible && editing !== null, { repository, session });
  useEffect(() => { if (!visible) setEditing(null); }, [visible]);
  const find = (id: string) => actions.find(a => a.id === id);
  const refresh = find('refreshEnvironments');
  const keep = find('keepEnvironments');
  const change = find('setEnvironments');
  const env = environments;
  const editErrors = (editing || []).map(draftError).filter(Boolean);
  return (
    <Sheet visible={visible} onClose={onClose} title="Build environments">
      <View style={styles.col} testID="job-environments-sheet">
        {editing ? (
          <>
            <Text variant="xs" style={{ color: colors.ink3 }}>Saved as this job's override; its snapshots are re-created before the next stage. Remove every row to fall back to repo config, session or Settings.</Text>
            <EnvironmentsEditor drafts={editing} onChange={setEditing} sources={vm} />
            <View style={styles.rowEnd}>
              <Button label="Cancel" onPress={() => setEditing(null)} />
              <Button primary label="Save environments" disabled={busy || !!editErrors.length} testID="environments-save"
                onPress={() => { invoke('setEnvironments', { environments: bindingsFromDrafts(editing) }); setEditing(null); }} />
            </View>
          </>
        ) : (
          <>
            <View style={styles.rowWrap}>
              <Text variant="xs" style={{ color: colors.ink3, flex: 1 }}>
                {env?.count ? `${env.count} binding${env.count === 1 ? '' : 's'} from ${sourceLabel(env.source)}${env.bytes ? ` · ${bytes(env.bytes)}` : ''}` : 'This job uses the shared runtime image.'}
              </Text>
              {env?.kept ? <Pill tone="accent">⬢ kept</Pill> : null}
              {env?.refresh_pending ? <Pill tone="warn">refresh pending</Pill> : null}
            </View>
            {(env?.bindings || []).map(b => (
              <Panel key={b.name} title={b.name} badge={b.status || undefined} badgeTone={statusTone(b.status)} testID={`job-environment-${b.name}`}
                right={b.primary ? <Pill tone="accent">primary</Pill> : undefined}>
                <KV label="Source"><Text variant="xs" style={styles.mono}>{b.source}:{b.ref}</Text></KV>
                <KV label="Paths">{b.paths?.length ? b.paths.join(', ') : 'whole repository'}</KV>
                {b.docker ? <KV label="Engine">{b.engine ? `${b.engine} (Docker-in-Docker)` : 'Docker-in-Docker'}</KV> : null}
                {b.gpus ? <KV label="GPUs">{b.gpus}</KV> : null}
                {b.image_bytes ? <KV label="Image">{bytes(b.image_bytes)}{b.home_bytes ? ` · home ${bytes(b.home_bytes)}` : ''}</KV> : null}
                {b.prepared_at ? <KV label="Prepared">{when(b.prepared_at)}</KV> : null}
                {b.probed_env?.length ? <KV label="Probed env">{b.probed_env.join(', ')}</KV> : null}
                {b.error ? <Banner tone="danger" glyph="✗" role="alert" title={b.error} /> : null}
                {b.secret_paths?.length ? (
                  <View><Text variant="xs" style={{ color: colors.danger.ink, fontWeight: fontWeights.semibold }}>Secret scan hits ({b.secret_paths.length})</Text><Pre numberOfLines={12}>{b.secret_paths.join('\n')}</Pre></View>
                ) : null}
                {b.skipped_paths?.length ? <SkippedPaths paths={b.skipped_paths} /> : null}
              </Panel>
            ))}
            <View style={styles.rowWrap}>
              {refresh ? <Button label={`${refresh.label} (E)`} tone="warn" disabled={busy} ariaDisabled={!refresh.enabled} accessibilityHint={refresh.reason} onPress={() => invoke(refresh)} testID="environments-refresh" /> : null}
              {keep ? <Button label={keep.label} disabled={busy} ariaDisabled={!keep.enabled} accessibilityHint={keep.reason} onPress={() => invoke(keep)} testID="environments-keep" /> : null}
              {change ? (
                <Button label="Change" disabled={busy} ariaDisabled={!change.enabled} accessibilityHint={change.reason} testID="environments-edit"
                  onPress={() => {
                    if (!change.enabled) { invoke(change); return; }
                    setEditing(env?.bindings?.length ? env.bindings.map(draftFromBinding) : [emptyDraft()]);
                  }} />
              ) : null}
            </View>
          </>
        )}
      </View>
    </Sheet>
  );
}

function SkippedPaths({ paths }: { paths: string[] }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  return (
    <View>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(v => !v)} style={styles.disclosure}>
        <Text variant="xs" style={{ color: colors.accent.ink }}>{open ? '▾' : '▸'} {paths.length} home path{paths.length === 1 ? '' : 's'} left out</Text>
      </Pressable>
      {open ? <Pre numberOfLines={30}>{paths.join('\n')}</Pre> : null}
    </View>
  );
}

/* ------------------------------------------------------------ Settings card */

/** Settings → Build environments: Docker-in-Docker (Sysbox) status + install steps, GPUs and kept jobs. */
export function BuildEnvironmentsSettings({ active }: { active: boolean }) {
  const { colors } = useTheme();
  const toast = useToast();
  const { vm, error, loading, reload } = useEnvironmentSources(active);
  const [busy, setBusy] = useState('');
  const unkeep = async (id: string) => {
    setBusy(id);
    try { await jobCoreApi.command(id, 'keepEnvironments', { keep: false }); toast('Environments released at the next sweep', 'ok'); reload(); }
    catch (err) { toast(err instanceof Error ? err.message : String(err), 'danger'); }
    finally { setBusy(''); }
  };
  const dind = vm?.docker_in_docker;
  const kept = vm?.kept_jobs || [];
  return (
    <Panel title="Build environment host" testID="build-environments-settings">
      {loading && !vm ? <Loading label="Checking the host…" /> : null}
      {error ? <Text variant="xs" style={{ color: colors.ink3 }}>Unavailable: {error}</Text> : null}
      {vm ? (
        <>
          <KV label="Docker-in-Docker">
            <Text variant="xs" style={{ color: dind?.available ? colors.ok.ink : colors.attention.ink }}>{dind?.available ? `✓ available (${dind.engine})` : '◆ not installed'}</Text>
          </KV>
          {!dind?.available ? (
            <View style={styles.col} testID="sysbox-guidance">
              <Text variant="xs" style={{ color: colors.ink3 }}>{dind?.message}</Text>
              {(dind?.install || []).map((step, i) => <Text key={i} variant="xs" selectable>{i + 1}. {step}</Text>)}
            </View>
          ) : null}
          <KV label="GPUs"><Text variant="xs" style={{ color: vm.gpu?.supported ? colors.ok.ink : colors.ink3 }}>{vm.gpu?.supported ? `✓ ${vm.gpu.reason}` : vm.gpu?.reason || 'unavailable'}</Text></KV>
          <Text variant="sm" style={{ fontWeight: fontWeights.semibold, marginTop: spacing.sm }}>Kept jobs</Text>
          {kept.length ? kept.map(k => (
            <View key={k.id} style={[styles.kept, { borderColor: colors.hairline }]} testID={`kept-job-${k.id}`}>
              <View style={{ flex: 1 }}>
                <Text variant="xs" numberOfLines={1}>{k.title || k.id.slice(0, 8)}</Text>
                <Text variant="xs" style={[styles.mono, { color: colors.ink3 }]} numberOfLines={1}>{k.bindings || '—'}{k.bytes ? ` · ${bytes(k.bytes)}` : ''}</Text>
              </View>
              <Button small label="Unkeep" loading={busy === k.id} disabled={!!busy} onPress={() => void unkeep(k.id)} testID={`unkeep-${k.id}`} />
            </View>
          )) : <Empty>No job keeps its environments past idle retention.</Empty>}
        </>
      ) : null}
    </Panel>
  );
}

const styles = StyleSheet.create({
  col: { gap: spacing.sm },
  rowWrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  rowEnd: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm },
  mono: { fontFamily: fonts.mono },
  draft: { borderWidth: 1, borderRadius: radii.control, padding: spacing.sm, gap: spacing.xs },
  chips: { gap: spacing.xs, paddingVertical: 2 },
  chip: { borderWidth: 1, borderRadius: radii.control, paddingHorizontal: spacing.sm, paddingVertical: 4, minHeight: 36, justifyContent: 'center', maxWidth: 220 },
  pillHit: { minHeight: 32, justifyContent: 'center' },
  disclosure: { minHeight: touchTarget, justifyContent: 'center' },
  kept: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, borderTopWidth: 1, paddingTop: spacing.xs },
});
