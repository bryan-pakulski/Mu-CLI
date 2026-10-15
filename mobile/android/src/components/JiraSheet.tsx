import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, StyleSheet, TouchableOpacity, View } from 'react-native';

import {
  doneSkippedKeys,
  jiraApi,
  type JiraBoard,
  type JiraSettings,
  type JiraSprint,
  type JiraStatusCount,
  type JiraTicket,
  type TicketImportTarget,
} from '../api/jobs';
import { ModernBottomSheet } from './ModernBottomSheet';
import { JobImportSheet } from './JobSessionPicker';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, radii, spacing, touchTarget } from '../theme/tokens';
import { Banner, Field, Loading, Panel, PlanosButton, SwitchRow, TextField } from './planos';

export type JiraSheetProps = {
  visible: boolean;
  sessionName: string | null;
  onClose: () => void;
  onImported?: (jobIds: string[]) => void;
  onOpenJob?: (jobId: string) => void;
};

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Pure helper (tested): tickets visible under the selected status filter. */
export function filterTickets(tickets: JiraTicket[], statuses: string[]): JiraTicket[] {
  if (!statuses.length) return tickets;
  const wanted = new Set(statuses);
  return tickets.filter(t => wanted.has(t.status));
}

export function JiraSheet({ visible, sessionName, onClose, onImported, onOpenJob }: JiraSheetProps) {
  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title="Jira">
      <JiraBrowser active={visible} sessionName={sessionName} onImported={onImported} onOpenJob={onOpenJob} />
    </ModernBottomSheet>
  );
}

export type JiraBrowserProps = {
  /** Loads settings/boards/tickets while true (sheet visible or screen mounted). */
  active: boolean;
  /** Default session offered when importing (the app's session); the user confirms or picks another. */
  sessionName: string | null;
  onImported?: (jobIds: string[]) => void;
  onOpenJob?: (jobId: string) => void;
  /** 'link' renders only the link/unlink panel (Settings page). */
  section?: 'all' | 'link';
};

/** The Jira browser (link, boards, filters, tickets, import) — used by the
 * Boards screen, the Jira sheet and (link only) the jobs Settings screen. */
export function JiraBrowser({ active, sessionName, onImported, onOpenJob, section = 'all' }: JiraBrowserProps) {
  const visible = active;
  const { colors } = useTheme();
  const [settings, setSettings] = useState<JiraSettings | null>(null);
  const [editingLink, setEditingLink] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [usePat, setUsePat] = useState(false);
  const [boards, setBoards] = useState<JiraBoard[]>([]);
  const [boardQuery, setBoardQuery] = useState('');
  const [boardId, setBoardId] = useState<number | null>(null);
  const [sprints, setSprints] = useState<JiraSprint[]>([]);
  const [sprintId, setSprintId] = useState<number | null>(null);
  const [mine, setMine] = useState(false);
  const [includeDone, setIncludeDone] = useState(false);
  const [tickets, setTickets] = useState<JiraTicket[]>([]);
  const [statuses, setStatuses] = useState<JiraStatusCount[]>([]);
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [askSession, setAskSession] = useState(false);
  // Tickets skipped because their latest job is done: "Run again" re-imports them with force.
  const [rerunKeys, setRerunKeys] = useState<string[]>([]);
  const [rerun, setRerun] = useState(false);

  const run = useCallback(async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError('');
    try { await fn(); } catch (err) { setError(message(err)); } finally { setBusy(''); }
  }, []);

  const applySettings = (next: JiraSettings) => {
    setSettings(next);
    setBaseUrl(next.base_url || '');
    setEmail(next.email || '');
    setUsePat(next.mode === 'pat' || (!next.email && next.pat_set));
    setMine(Boolean(next.preferences?.assigned_to_me));
    setEditingLink(!next.configured);
    const first = next.preferences?.boards?.[0];
    if (first && boardId == null) setBoardId(first.id);
  };

  useEffect(() => {
    if (!visible) return;
    setNotice('');
    void run('load', async () => applySettings(await jiraApi.settings()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const loadBoards = useCallback((query = '') => run('boards', async () => {
    const res = await jiraApi.boards(query);
    setBoards(res.boards || []);
  }), [run]);

  useEffect(() => {
    if (visible && settings?.configured && section !== 'link') void loadBoards('');
  }, [visible, settings?.configured, loadBoards, section]);

  useEffect(() => {
    setSprints([]);
    setSprintId(null);
    if (!visible || boardId == null) return;
    jiraApi.sprints(boardId).then(res => setSprints(res.sprints || [])).catch(() => setSprints([]));
  }, [visible, boardId]);

  const loadTickets = useCallback(() => run('tickets', async () => {
    const res = await jiraApi.tickets({
      board_id: boardId ?? undefined,
      sprint_id: sprintId ?? undefined,
      mine,
      include_done: includeDone,
    });
    setTickets(res.tickets || []);
    setStatuses(res.statuses || []);
    setStatusFilter(prev => prev.filter(s => (res.statuses || []).some(c => c.name === s)));
    setSelected([]);
  }), [run, boardId, sprintId, mine, includeDone]);

  useEffect(() => {
    if (visible && section !== 'link' && settings?.configured && (boardId != null || settings.preferences?.default_jql || mine)) void loadTickets();
  }, [visible, section, settings?.configured, boardId, sprintId, mine, includeDone, loadTickets, settings?.preferences?.default_jql]);

  const saveLink = () => run('save', async () => {
    const body = usePat
      ? { base_url: baseUrl.trim(), email: '', ...(token.trim() ? { pat: token.trim() } : {}) }
      : { base_url: baseUrl.trim(), email: email.trim(), ...(token.trim() ? { api_token: token.trim() } : {}) };
    const next = await jiraApi.save(body);
    setToken('');
    applySettings(next);
    if (next.configured) {
      const test = await jiraApi.test();
      setNotice(`Linked as ${test.user?.displayName || test.user?.emailAddress || 'Jira user'}.`);
    }
  });

  const unlink = () => run('unlink', async () => {
    applySettings(await jiraApi.unlink());
    setTickets([]);
    setBoards([]);
  });

  const togglePreferred = (board: JiraBoard) => run('prefs', async () => {
    const current = settings?.preferences?.boards || [];
    const exists = current.some(b => b.id === board.id);
    const boardsNext = exists
      ? current.filter(b => b.id !== board.id)
      : [...current, { id: board.id, name: board.name, project: board.project, type: board.type }];
    applySettings(await jiraApi.save({ preferences: { boards: boardsNext } }));
    setBoards(prev => prev.map(b => (b.id === board.id ? { ...b, preferred: !exists } : b)));
  });

  const saveMine = (value: boolean) => {
    setMine(value);
    void jiraApi.save({ preferences: { assigned_to_me: value } }).catch(() => undefined);
  };

  /** Import asks where the jobs run: workspace / git URL first, session optional (matched by workspace). */
  const importSelected = () => { setRerun(false); setAskSession(true); };
  const runAgain = () => { setSelected(rerunKeys); setRerun(true); setAskSession(true); };
  const importInto = (target: TicketImportTarget) => run('import', async () => {
    setAskSession(false);
    const res = await jiraApi.importKeys(selected, target);
    const ids = (res.created || []).map(c => c.job_id);
    const skippedRows = (res.skipped || []) as Array<{ key?: string; reason?: string }>;
    const doneKeys = doneSkippedKeys(res.skipped);
    const done = doneKeys.length;
    const skipped = skippedRows.length - done;
    const errors = (res.errors || []).length;
    const where = target.session || target.workspace || 'the workspace';
    setNotice(`Created ${ids.length} job${ids.length === 1 ? '' : 's'} in ${where}${skipped ? `, ${skipped} already linked` : ''}${done ? `, ${done} already done` : ''}${errors ? `, ${errors} failed` : ''}.`);
    setRerunKeys(doneKeys);
    onImported?.(ids);
    await loadTickets();
  });

  const visibleTickets = useMemo(() => filterTickets(tickets, statusFilter), [tickets, statusFilter]);
  const shownBoards = useMemo(() => boards.slice(0, 12), [boards]);
  const chip = (activeChip: boolean) => [styles.chip, { backgroundColor: activeChip ? colors.accent.wash : colors.raised, borderColor: activeChip ? colors.accent.ink : colors.hairline }];
  const linkOnly = section === 'link';

  return (
    <View style={styles.root}>
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      {notice ? <Banner tone="ok" glyph="✓" title={notice} /> : null}
      {rerunKeys.length > 0 && !linkOnly ? (
        <View style={styles.actions}>
          <PlanosButton small testID="jira-run-again" label={`Run again (${rerunKeys.length})`} disabled={!!busy} onPress={runAgain} />
          <Text variant="xs" style={{ color: colors.ink3, flex: 1 }}>Start a new job for {rerunKeys.join(', ')}.</Text>
        </View>
      ) : null}

      {settings?.configured && !editingLink ? (
        <Panel title="Jira link" glyph="✓" glyphTone="ok">
          <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={2}>
            Linked · {settings.base_url}{settings.email ? ` · ${settings.email}` : ''}
          </Text>
          <View style={styles.actions}>
            <PlanosButton small label="Edit" onPress={() => setEditingLink(true)} />
            <PlanosButton small tone="danger" label="Unlink" onPress={unlink} />
          </View>
        </Panel>
      ) : (
        <Panel title="Link Jira">
          <Text variant="xs" style={{ color: colors.ink3, marginBottom: spacing.sm }}>
            Saved on the server in $MUCLI_HOME/integrations/jira.json. Tokens are never shown again.
          </Text>
          <Field label="Jira URL">
            <TextField accessibilityLabel="Jira URL" placeholder="https://your-org.atlassian.net"
              autoCapitalize="none" autoCorrect={false} keyboardType="url" value={baseUrl} onChangeText={setBaseUrl} />
          </Field>
          <SwitchRow label="Data Center personal access token" hint="Use a PAT instead of email + API token." value={usePat} onValueChange={setUsePat} />
          {!usePat ? (
            <Field label="Email">
              <TextField accessibilityLabel="Jira email" placeholder="you@company.com"
                autoCapitalize="none" autoCorrect={false} keyboardType="email-address" value={email} onChangeText={setEmail} />
            </Field>
          ) : null}
          <Field label={usePat ? 'Personal access token' : 'API token'} hint={(usePat ? settings?.pat_set : settings?.api_token_set) ? 'Token saved — leave blank to keep it.' : undefined}>
            <TextField accessibilityLabel={usePat ? 'Personal access token' : 'API token'} secureTextEntry autoCapitalize="none" autoCorrect={false}
              placeholder={(usePat ? settings?.pat_set : settings?.api_token_set) ? 'Token saved — leave blank to keep' : usePat ? 'Personal access token' : 'API token'}
              value={token} onChangeText={setToken} />
          </Field>
          <View style={styles.actions}>
            <PlanosButton primary label="Save and test" disabled={!!busy || !baseUrl.trim()} onPress={saveLink} />
            {settings?.configured ? <PlanosButton ghost label="Cancel" onPress={() => setEditingLink(false)} /> : null}
          </View>
        </Panel>
      )}

      {settings?.configured && !linkOnly ? (
        <>
          <Panel title="Board">
            <Text variant="xs" style={{ color: colors.ink3 }}>Starred and suggested boards (projects with your open work) come first. Long-press to star.</Text>
            <TextField accessibilityLabel="Search boards" placeholder="Search boards"
              value={boardQuery} onChangeText={setBoardQuery} onSubmitEditing={() => void loadBoards(boardQuery.trim())}
              returnKeyType="search" />
            <View style={styles.wrap}>
              {shownBoards.map(board => (
                <TouchableOpacity key={board.id} accessibilityRole="button" accessibilityState={{ selected: boardId === board.id }}
                  onPress={() => setBoardId(boardId === board.id ? null : board.id)} onLongPress={() => togglePreferred(board)} style={chip(boardId === board.id)}>
                  <Text variant="xs" style={{ color: colors.ink }}>
                    {board.preferred ? '★ ' : board.suggested ? '• ' : ''}{board.name}{board.project ? ` · ${board.project}` : ''}
                  </Text>
                </TouchableOpacity>
              ))}
              {busy === 'boards' ? <ActivityIndicator size="small" color={colors.running.ink} /> : null}
            </View>
            {sprints.length ? (
              <View style={styles.wrap}>
                <TouchableOpacity accessibilityRole="button" accessibilityState={{ selected: sprintId == null }} onPress={() => setSprintId(null)} style={chip(sprintId == null)}>
                  <Text variant="xs" style={{ color: colors.ink }}>Whole board</Text>
                </TouchableOpacity>
                {sprints.map(s => (
                  <TouchableOpacity key={s.id} accessibilityRole="button" accessibilityState={{ selected: sprintId === s.id }} onPress={() => setSprintId(s.id)} style={chip(sprintId === s.id)}>
                    <Text variant="xs" style={{ color: colors.ink }}>{s.name}{s.state === 'active' ? ' (active)' : ''}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            ) : null}
          </Panel>

          <Panel title="Filters">
            <SwitchRow label="Assigned to me" value={mine} onValueChange={saveMine} />
            <SwitchRow label="Show done / closed" value={includeDone} onValueChange={setIncludeDone} />
            {statuses.length ? (
              <View style={styles.wrap}>
                {statuses.map(s => {
                  const on = statusFilter.includes(s.name);
                  return (
                    <TouchableOpacity key={s.name} accessibilityRole="button" accessibilityState={{ selected: on }}
                      onPress={() => setStatusFilter(prev => (on ? prev.filter(x => x !== s.name) : [...prev, s.name]))} style={chip(on)}>
                      <Text variant="xs" style={{ color: colors.ink }}>{s.name} · {s.count}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
            ) : null}
          </Panel>

          <Panel
            title="Tickets"
            badge={tickets.length ? visibleTickets.length : ''}
            right={busy === 'tickets' ? <ActivityIndicator size="small" color={colors.running.ink} /> : <PlanosButton small ghost label="Refresh" onPress={() => void loadTickets()} />}
          >
            {!visibleTickets.length && busy !== 'tickets' ? (
              <Text variant="xs" style={{ color: colors.ink3 }}>{boardId == null && !mine ? 'Pick a board or turn on “Assigned to me”.' : 'No tickets match.'}</Text>
            ) : null}
            {visibleTickets.map(ticket => {
              const picked = selected.includes(ticket.key);
              const job = ticket.job;
              return (
                <TouchableOpacity key={ticket.key} accessibilityRole="checkbox" accessibilityState={{ checked: picked, disabled: !!job?.live }}
                  disabled={!!job?.live}
                  onPress={() => setSelected(prev => (picked ? prev.filter(k => k !== ticket.key) : [...prev, ticket.key]))}
                  style={[styles.ticket, { backgroundColor: picked ? colors.accent.wash : colors.canvas, borderColor: picked ? colors.accent.ink : colors.hairline }]}>
                  <View style={styles.row}>
                    <Text variant="xs" style={{ color: picked ? colors.accent.ink : colors.ink3 }}>{picked ? '☑' : '☐'}</Text>
                    <Text variant="xs" style={{ color: colors.ink, fontFamily: fonts.mono, fontWeight: fontWeights.semibold }}>{ticket.key}</Text>
                    <Text variant="xs" style={{ color: colors.ink3, flex: 1 }} numberOfLines={1}>{ticket.status}{ticket.assignee ? ` · ${ticket.assignee}` : ''}</Text>
                  </View>
                  <Text variant="sm" style={{ color: colors.ink }} numberOfLines={2}>{ticket.summary}</Text>
                  {job ? (
                    <TouchableOpacity accessibilityRole="link" onPress={() => onOpenJob?.(job.job_id)} style={styles.jobLink}>
                      <Text variant="xs" style={{ color: job.live ? colors.attention.ink : colors.accent.ink }}>
                        {job.live ? '● ' : '› '}Job {job.job_id.slice(0, 10)} · {job.status.replace(/_/g, ' ')}{job.attention_reason ? ` · ${job.attention_reason.replace(/_/g, ' ')}` : ''}
                      </Text>
                    </TouchableOpacity>
                  ) : null}
                </TouchableOpacity>
              );
            })}
          </Panel>

          <PlanosButton primary disabled={!selected.length || !!busy} onPress={importSelected}
            label={busy === 'import' ? 'Creating jobs…' : `Create ${selected.length || ''} job${selected.length === 1 ? '' : 's'}`} />
          <JobImportSheet
            visible={askSession}
            title="Import tickets"
            intro={`Create ${selected.length} job${selected.length === 1 ? '' : 's'} in which workspace or repository? A saved session on that workspace supplies model, mode and environment.`}
            defaultSession={sessionName}
            initialForce={rerun}
            confirmLabel={`Import ${selected.length || ''}`.trim()}
            onClose={() => setAskSession(false)}
            onConfirm={target => { void importInto(target); }}
          />
        </>
      ) : null}
      {busy === 'load' ? <Loading label="Loading Jira settings…" /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { gap: spacing.md },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 24 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm, marginTop: spacing.xs },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.xs },
  chip: { borderWidth: 1, borderRadius: radii.control, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, minHeight: touchTarget, justifyContent: 'center' },
  ticket: { borderWidth: 1, borderRadius: radii.control, padding: spacing.md, gap: spacing.xs, minHeight: touchTarget },
  jobLink: { minHeight: 32, justifyContent: 'center' },
});
