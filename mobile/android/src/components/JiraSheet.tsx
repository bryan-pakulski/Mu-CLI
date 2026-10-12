import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, StyleSheet, Switch, TextInput, TouchableOpacity, View } from 'react-native';

import {
  jiraApi,
  type JiraBoard,
  type JiraSettings,
  type JiraSprint,
  type JiraStatusCount,
  type JiraTicket,
} from '../api/jobs';
import { ModernBottomSheet } from './ModernBottomSheet';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';

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
    if (visible && settings?.configured) void loadBoards('');
  }, [visible, settings?.configured, loadBoards]);

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
    if (visible && settings?.configured && (boardId != null || settings.preferences?.default_jql || mine)) void loadTickets();
  }, [visible, settings?.configured, boardId, sprintId, mine, includeDone, loadTickets, settings?.preferences?.default_jql]);

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

  const importSelected = () => run('import', async () => {
    if (!sessionName) throw new Error('Open Work from a session with an attached workspace to import tickets.');
    const res = await jiraApi.importKeys(selected, sessionName);
    const ids = (res.created || []).map(c => c.job_id);
    const skipped = (res.skipped || []).length;
    const errors = (res.errors || []).length;
    setNotice(`Created ${ids.length} job${ids.length === 1 ? '' : 's'}${skipped ? `, ${skipped} already linked` : ''}${errors ? `, ${errors} failed` : ''}.`);
    onImported?.(ids);
    await loadTickets();
  });

  const visibleTickets = useMemo(() => filterTickets(tickets, statusFilter), [tickets, statusFilter]);
  const shownBoards = useMemo(() => boards.slice(0, 12), [boards]);
  const inputStyle = [styles.input, { borderColor: colors.hairline, color: colors.text }];
  const chip = (active: boolean) => [styles.chip, { borderColor: active ? colors.accent : colors.hairline, backgroundColor: active ? colors.accentSoft : 'transparent' }];

  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title="Jira">
      {error ? <Text variant="xs" style={{ color: colors.error, marginBottom: 8 }}>{error}</Text> : null}
      {notice ? <Text variant="xs" style={{ color: colors.success, marginBottom: 8 }}>{notice}</Text> : null}

      {settings?.configured && !editingLink ? (
        <View style={[styles.row, styles.section]}>
          <Text variant="xs" dim style={{ flex: 1 }} numberOfLines={1}>
            Linked · {settings.base_url}{settings.email ? ` · ${settings.email}` : ''}
          </Text>
          <TouchableOpacity accessibilityRole="button" onPress={() => setEditingLink(true)}>
            <Text variant="xs" style={{ color: colors.accent }}>Edit</Text>
          </TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" onPress={unlink} style={{ marginLeft: 14 }}>
            <Text variant="xs" style={{ color: colors.error }}>Unlink</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <View style={styles.section}>
          <Text variant="sm" style={styles.heading}>Link Jira</Text>
          <Text variant="xs" dim style={{ marginBottom: 8 }}>
            Saved on the server in $MUCLI_HOME/integrations/jira.json. Tokens are never shown again.
          </Text>
          <TextInput accessibilityLabel="Jira URL" placeholder="https://your-org.atlassian.net" placeholderTextColor={colors.textDim}
            autoCapitalize="none" autoCorrect={false} keyboardType="url" value={baseUrl} onChangeText={setBaseUrl} style={inputStyle} />
          <View style={[styles.row, { marginVertical: 6 }]}>
            <Text variant="xs" style={{ flex: 1, color: colors.textSoft }}>Data Center personal access token</Text>
            <Switch value={usePat} onValueChange={setUsePat} />
          </View>
          {!usePat ? (
            <TextInput accessibilityLabel="Jira email" placeholder="you@company.com" placeholderTextColor={colors.textDim}
              autoCapitalize="none" autoCorrect={false} keyboardType="email-address" value={email} onChangeText={setEmail} style={inputStyle} />
          ) : null}
          <TextInput accessibilityLabel={usePat ? 'Personal access token' : 'API token'} secureTextEntry autoCapitalize="none" autoCorrect={false}
            placeholder={(usePat ? settings?.pat_set : settings?.api_token_set) ? 'Token saved — leave blank to keep' : usePat ? 'Personal access token' : 'API token'}
            placeholderTextColor={colors.textDim} value={token} onChangeText={setToken} style={inputStyle} />
          <View style={styles.row}>
            <TouchableOpacity accessibilityRole="button" disabled={!!busy || !baseUrl.trim()} onPress={saveLink}
              style={[styles.button, { backgroundColor: colors.accent, opacity: busy || !baseUrl.trim() ? 0.5 : 1 }]}>
              <Text variant="xs" style={{ color: '#fff', fontWeight: '600' }}>Save and test</Text>
            </TouchableOpacity>
            {settings?.configured ? (
              <TouchableOpacity accessibilityRole="button" onPress={() => setEditingLink(false)} style={{ marginLeft: 14 }}>
                <Text variant="xs" dim>Cancel</Text>
              </TouchableOpacity>
            ) : null}
          </View>
        </View>
      )}

      {settings?.configured ? (
        <>
          <View style={styles.section}>
            <Text variant="sm" style={styles.heading}>Board</Text>
            <Text variant="xs" dim style={{ marginBottom: 6 }}>Starred and suggested boards (projects with your open work) come first. Long-press to star.</Text>
            <TextInput accessibilityLabel="Search boards" placeholder="Search boards" placeholderTextColor={colors.textDim}
              value={boardQuery} onChangeText={setBoardQuery} onSubmitEditing={() => void loadBoards(boardQuery.trim())}
              returnKeyType="search" style={inputStyle} />
            <View style={styles.wrap}>
              {shownBoards.map(board => (
                <TouchableOpacity key={board.id} accessibilityRole="button" accessibilityState={{ selected: boardId === board.id }}
                  onPress={() => setBoardId(boardId === board.id ? null : board.id)} onLongPress={() => togglePreferred(board)} style={chip(boardId === board.id)}>
                  <Text variant="xs" style={{ color: colors.text }}>
                    {board.preferred ? '★ ' : board.suggested ? '• ' : ''}{board.name}{board.project ? ` · ${board.project}` : ''}
                  </Text>
                </TouchableOpacity>
              ))}
              {busy === 'boards' ? <ActivityIndicator size="small" /> : null}
            </View>
            {sprints.length ? (
              <View style={styles.wrap}>
                <TouchableOpacity onPress={() => setSprintId(null)} style={chip(sprintId == null)}>
                  <Text variant="xs" style={{ color: colors.text }}>Whole board</Text>
                </TouchableOpacity>
                {sprints.map(s => (
                  <TouchableOpacity key={s.id} onPress={() => setSprintId(s.id)} style={chip(sprintId === s.id)}>
                    <Text variant="xs" style={{ color: colors.text }}>{s.name}{s.state === 'active' ? ' (active)' : ''}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            ) : null}
          </View>

          <View style={styles.section}>
            <View style={styles.row}>
              <Text variant="xs" style={{ flex: 1, color: colors.textSoft }}>Assigned to me</Text>
              <Switch value={mine} onValueChange={saveMine} />
            </View>
            <View style={styles.row}>
              <Text variant="xs" style={{ flex: 1, color: colors.textSoft }}>Show done / closed</Text>
              <Switch value={includeDone} onValueChange={setIncludeDone} />
            </View>
            {statuses.length ? (
              <View style={styles.wrap}>
                {statuses.map(s => {
                  const on = statusFilter.includes(s.name);
                  return (
                    <TouchableOpacity key={s.name} accessibilityRole="button" accessibilityState={{ selected: on }}
                      onPress={() => setStatusFilter(prev => (on ? prev.filter(x => x !== s.name) : [...prev, s.name]))} style={chip(on)}>
                      <Text variant="xs" style={{ color: colors.text }}>{s.name} · {s.count}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
            ) : null}
          </View>

          <View style={styles.section}>
            <View style={styles.row}>
              <Text variant="sm" style={[styles.heading, { flex: 1 }]}>Tickets {tickets.length ? `(${visibleTickets.length})` : ''}</Text>
              {busy === 'tickets' ? <ActivityIndicator size="small" /> : (
                <TouchableOpacity accessibilityRole="button" onPress={() => void loadTickets()}>
                  <Text variant="xs" style={{ color: colors.accent }}>Refresh</Text>
                </TouchableOpacity>
              )}
            </View>
            {!visibleTickets.length && busy !== 'tickets' ? (
              <Text variant="xs" dim>{boardId == null && !mine ? 'Pick a board or turn on “Assigned to me”.' : 'No tickets match.'}</Text>
            ) : null}
            {visibleTickets.map(ticket => {
              const picked = selected.includes(ticket.key);
              const job = ticket.job;
              return (
                <TouchableOpacity key={ticket.key} accessibilityRole="checkbox" accessibilityState={{ checked: picked, disabled: !!job?.live }}
                  disabled={!!job?.live}
                  onPress={() => setSelected(prev => (picked ? prev.filter(k => k !== ticket.key) : [...prev, ticket.key]))}
                  style={[styles.ticket, { borderColor: picked ? colors.accent : colors.hairline }]}>
                  <View style={styles.row}>
                    <Text variant="xs" style={{ color: colors.textSoft, fontWeight: '600' }}>{ticket.key}</Text>
                    <Text variant="xs" dim style={{ marginLeft: 8, flex: 1 }} numberOfLines={1}>{ticket.status}{ticket.assignee ? ` · ${ticket.assignee}` : ''}</Text>
                  </View>
                  <Text variant="sm" style={{ color: colors.text }} numberOfLines={2}>{ticket.summary}</Text>
                  {job ? (
                    <TouchableOpacity accessibilityRole="link" onPress={() => onOpenJob?.(job.job_id)}>
                      <Text variant="xs" style={{ color: job.live ? colors.warning : colors.textDim, marginTop: 4 }}>
                        Job {job.job_id.slice(0, 10)} · {job.status.replace(/_/g, ' ')}{job.attention_reason ? ` · ${job.attention_reason.replace(/_/g, ' ')}` : ''}
                      </Text>
                    </TouchableOpacity>
                  ) : null}
                </TouchableOpacity>
              );
            })}
          </View>

          <TouchableOpacity accessibilityRole="button" disabled={!selected.length || !!busy} onPress={importSelected}
            style={[styles.button, styles.importButton, { backgroundColor: colors.accent, opacity: !selected.length || busy ? 0.5 : 1 }]}>
            <Text variant="sm" style={{ color: '#fff', fontWeight: '600' }}>
              {busy === 'import' ? 'Creating jobs…' : `Create ${selected.length || ''} job${selected.length === 1 ? '' : 's'}`}
            </Text>
          </TouchableOpacity>
          {!sessionName ? <Text variant="xs" style={{ color: colors.warning, marginTop: 6 }}>No active session: jobs need a session with an attached workspace.</Text> : null}
        </>
      ) : null}
      {busy === 'load' ? <ActivityIndicator style={{ marginTop: 12 }} /> : null}
    </ModernBottomSheet>
  );
}

const styles = StyleSheet.create({
  section: { marginBottom: 16 },
  heading: { fontWeight: '600', marginBottom: 6 },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 36 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 },
  input: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, marginBottom: 8, fontSize: 14 },
  chip: { borderWidth: 1, borderRadius: 14, paddingHorizontal: 10, paddingVertical: 6, minHeight: 32, justifyContent: 'center' },
  button: { borderRadius: 8, paddingHorizontal: 14, paddingVertical: 10, alignItems: 'center', justifyContent: 'center', minHeight: 44 },
  importButton: { marginTop: 4 },
  ticket: { borderWidth: 1, borderRadius: 8, padding: 10, marginTop: 8 },
});
