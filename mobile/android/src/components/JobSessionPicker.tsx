// Session picker for jobs mode. Jobs are one management plane across saved
// sessions: a new job (or a Jira import) names the session whose workspace,
// provider/model, mode and approval policy it inherits.

import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import {
  jobsApi, defaultJobSession, defaultWorkspace, isRepositoryUrl, recentWorkspaces, sessionForWorkspace, sessionModelLabel,
  type JobSessionOption, type TicketImportTarget,
} from '../api/jobs';
import { isMissingEndpoint } from '../api/jobCore';
import { ModernBottomSheet } from './ModernBottomSheet';
import { Banner, Empty, Field, Loading, PlanosButton, SwitchRow, TextField } from './planos';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, radii, spacing, touchTarget } from '../theme/tokens';

export type JobSessionsState = {
  sessions: JobSessionOption[];
  /** False when the server predates GET /api/jobs/sessions (fall back to the app's session). */
  supported: boolean;
  loading: boolean;
  error: string;
  reload: () => void;
};

/** Loads the saved sessions while `active`. */
export function useJobSessions(active: boolean): JobSessionsState {
  const [sessions, setSessions] = useState<JobSessionOption[]>([]);
  const [supported, setSupported] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const reload = useCallback(() => {
    setLoading(true);
    jobsApi.sessions()
      .then(res => { setSessions(Array.isArray(res?.sessions) ? res.sessions : []); setSupported(true); setError(''); })
      .catch(err => {
        if (isMissingEndpoint(err)) { setSupported(false); setError(''); }
        else setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { if (active) reload(); }, [active, reload]);
  return { sessions, supported, loading, error, reload };
}

/** Radio list of sessions: name, workspace, provider · model. */
export function JobSessionPicker({ sessions, value, onChange, loading, error }: {
  sessions: JobSessionOption[]; value: string | null; onChange: (name: string) => void; loading?: boolean; error?: string;
}) {
  const { colors } = useTheme();
  if (loading && !sessions.length) return <Loading label="Loading sessions…" />;
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel="Session" testID="job-session-picker" style={styles.list}>
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      {!sessions.length && !error ? <Empty>No saved sessions. Start a session with a workspace first.</Empty> : null}
      {sessions.map(s => {
        const on = s.name === value;
        const meta = sessionModelLabel(s);
        return (
          <Pressable key={s.name} testID={`job-session-${s.name}`} accessibilityRole="radio"
            accessibilityLabel={`Session ${s.name}${s.workspace ? `, ${s.workspace}` : ''}${meta ? `, ${meta}` : ''}`}
            accessibilityState={{ selected: on, checked: on }} onPress={() => onChange(s.name)}
            style={({ pressed }) => [styles.row, {
              borderColor: on ? colors.accent : colors.border,
              backgroundColor: on ? colors.accentSubtle : pressed ? colors.hover : colors.bg,
            }]}>
            <View style={styles.head}>
              <Text variant="xs" style={{ color: on ? colors.accent : colors.muted }}>{on ? '◉' : '○'}</Text>
              <Text variant="sm" style={{ fontWeight: fontWeights.semibold, flex: 1 }} numberOfLines={1}>{s.name}</Text>
              {s.current ? <Text variant="xs" style={{ color: colors.muted }}>current</Text> : null}
            </View>
            <Text variant="xs" style={{ color: colors.muted, fontFamily: fonts.mono }} numberOfLines={1}>{s.workspace || 'no workspace'}</Text>
            {meta ? <Text variant="xs" style={{ color: colors.muted }} numberOfLines={1}>{meta}{s.agent_mode && s.agent_mode !== 'default' ? ` · ${s.agent_mode}` : ''}</Text> : null}
          </Pressable>
        );
      })}
    </View>
  );
}

/** Bottom sheet that asks which session to use before an action (Jira import). */
export function JobSessionSheet({ visible, title = 'Choose session', intro, defaultSession, confirmLabel, onClose, onConfirm }: {
  visible: boolean; title?: string; intro?: string; defaultSession: string | null; confirmLabel: string;
  onClose: () => void; onConfirm: (session: string) => void;
}) {
  const { colors } = useTheme();
  const { sessions, supported, loading, error } = useJobSessions(visible);
  const [value, setValue] = useState<string | null>(null);
  useEffect(() => { if (visible) setValue(null); }, [visible]);
  const chosen = value ?? (supported ? defaultJobSession(sessions, defaultSession) : defaultSession);
  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title={title}>
      {intro ? <Text variant="xs" style={{ color: colors.muted, marginBottom: spacing.sm }}>{intro}</Text> : null}
      {supported
        ? <JobSessionPicker sessions={sessions} value={chosen} onChange={setValue} loading={loading} error={error} />
        : <Text variant="sm">{defaultSession ? `Session: ${defaultSession}` : 'This server cannot list sessions. Open jobs from a session first.'}</Text>}
      <View style={styles.actions}>
        <PlanosButton label="Cancel" onPress={onClose} />
        <PlanosButton primary testID="job-session-confirm" label={confirmLabel} disabled={!chosen} onPress={() => { if (chosen) onConfirm(chosen); }} />
      </View>
    </ModernBottomSheet>
  );
}

/** Hint under a workspace field: git URL clone, matched session or Settings stage models. */
export function workspaceHint(sessions: JobSessionOption[], path: string, fallbackSession?: string | null): string {
  const value = path.trim();
  if (!value) return fallbackSession ? `Blank uses ${fallbackSession}’s workspace.` : 'Pick a recent workspace, type the repository path or paste a git URL.';
  if (isRepositoryUrl(value)) return 'Git URL: the server clones this repository for the job.';
  const matched = sessionForWorkspace(sessions, value);
  return matched
    ? `Model, mode and environment from session ${matched.name}${sessionModelLabel(matched) ? ` (${sessionModelLabel(matched)})` : ''}.`
    : 'No saved session uses this workspace: models come from Settings → Stage models (or the current session).';
}

/** Workspace (path or git URL) text field with recent-workspace chips — shared by the New job
 * sheet and ticket import so both are workspace/repository-first. */
export function WorkspaceField({ sessions, value, onChange, hint, error, testID = 'job-workspace' }: {
  sessions: JobSessionOption[]; value: string; onChange: (path: string) => void; hint: string; error?: string; testID?: string;
}) {
  const { colors } = useTheme();
  const recent = recentWorkspaces(sessions);
  return (
    <Field label="Workspace" hint={hint}>
      <TextField mono accessibilityLabel="Workspace" testID={testID} value={value} onChangeText={onChange}
        placeholder="/home/me/src/project or https://github.com/org/repo" autoCapitalize="none" autoCorrect={false} />
      {recent.length ? (
        <View style={styles.chips} accessibilityLabel="Recent workspaces">
          {recent.map(w => {
            const on = w === value.trim();
            return (
              <Pressable key={w} testID={`${testID}-${w}`} accessibilityRole="radio" accessibilityLabel={`Workspace ${w}`}
                accessibilityState={{ selected: on, checked: on }} onPress={() => onChange(w)}
                style={[styles.chip, { borderColor: on ? colors.accent : colors.border, backgroundColor: on ? colors.accentSubtle : colors.bg }]}>
                <Text variant="xs" style={{ color: on ? colors.accent : colors.text, fontFamily: fonts.mono }} numberOfLines={1}>
                  {w.split('/').filter(Boolean).pop() || w}
                </Text>
              </Pressable>
            );
          })}
        </View>
      ) : null}
      {error ? <Text variant="xs" style={{ color: colors.danger }}>{error}</Text> : null}
    </Field>
  );
}

/** Ticket import target: workspace / git URL first (like the New job sheet), session optional
 * (a saved session on that workspace is matched), plus "Run again" (force) for done tickets. */
export function JobImportSheet({ visible, title = 'Import tickets', intro, defaultSession, confirmLabel, initialForce = false, onClose, onConfirm }: {
  visible: boolean; title?: string; intro?: string; defaultSession: string | null; confirmLabel: string; initialForce?: boolean;
  onClose: () => void; onConfirm: (target: TicketImportTarget) => void;
}) {
  const { colors } = useTheme();
  const { sessions, supported, error } = useJobSessions(visible);
  const [workspace, setWorkspace] = useState('');
  const [touched, setTouched] = useState(false);
  const [force, setForce] = useState(initialForce);
  const [problem, setProblem] = useState('');
  useEffect(() => { if (visible) { setTouched(false); setForce(initialForce); setProblem(''); } }, [visible, initialForce]);
  useEffect(() => { if (visible && !touched) setWorkspace(defaultWorkspace(sessions, defaultSession)); }, [visible, touched, sessions, defaultSession]);
  const fallbackSession = !supported ? defaultSession : null;
  const pick = (path: string) => { setTouched(true); setWorkspace(path); setProblem(''); };
  const confirm = () => {
    const path = workspace.trim();
    if (!path && !fallbackSession) { setProblem('Choose a workspace or repository for the imported jobs.'); return; }
    const matched = path && !isRepositoryUrl(path) ? sessionForWorkspace(sessions, path)?.name : undefined;
    onConfirm({ workspace: path || undefined, session: matched || (path ? null : fallbackSession), force });
  };
  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title={title}>
      {intro ? <Text variant="xs" style={{ color: colors.muted, marginBottom: spacing.sm }}>{intro}</Text> : null}
      <WorkspaceField sessions={sessions} value={workspace} onChange={pick} testID="job-import-workspace"
        hint={workspaceHint(sessions, workspace, fallbackSession)} error={error} />
      <SwitchRow label="Run again" testID="job-import-force" value={force} onValueChange={setForce}
        hint="Also start a new job for tickets whose latest job is done." />
      {problem ? <Banner tone="danger" glyph="✗" role="alert" title={problem} /> : null}
      <View style={styles.actions}>
        <PlanosButton label="Cancel" onPress={onClose} />
        <PlanosButton primary testID="job-import-confirm" label={confirmLabel} onPress={confirm} />
      </View>
    </ModernBottomSheet>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.xs },
  chip: { borderWidth: 1, borderRadius: radii.control, paddingHorizontal: spacing.sm, paddingVertical: spacing.xs, minHeight: 32, justifyContent: 'center', maxWidth: 200 },
  list: { gap: spacing.xs },
  row: { borderWidth: 1, borderRadius: radii.control, padding: spacing.sm, gap: 2, minHeight: touchTarget },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.base },
});
