// The fleet chat and the Studio discovery chat on the phone (web: mu/gui/static/js/orchestration/
// chat.js). Both are ordinary chat sessions the orchestration routes open —
//   POST /api/orchestration/chat/session   {fresh?, provider?, model?}   the fleet chat
//   POST /api/orchestration/studio/session {ref, provider?, model?}      a draft's discovery chat
// — answering 400 when there is no model to borrow; then the model picker asks once (the server
// remembers it for the fleet chat). After that the transport is the main chat's: history on open,
// POST /api/chat/send and /api/chat/interrupt, the session's SSE stream through the shared hub, and
// prompts answered inline with the chat kit's InlinePrompt. Built only from the Chat kit: Composer,
// ToolList (buildToolCards), Markdown and the approval model.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { ApiError } from '../../api/client';
import { chatApi } from '../../api/chat';
import { orchestrationApi, type OrchChatSession } from '../../api/orchestration';
import { promptsApi, type PendingPrompt } from '../../api/prompts';
import { providersApi, type ProviderInfo } from '../../api/providers';
import { sessionsApi } from '../../api/sessions';
import { subscribeToEvents } from '../../api/sse';
import { asPendingPrompt } from '../../store/prompts';
import { useTheme } from '../../theme/ThemeContext';
import { radii, spacing } from '../../theme/tokens';
import { Banner, Button, Chip, Composer, EmptyState, Field, InkDrop, Markdown, Select, Sheet, Text, TextField } from '../../ui';
import { ToolList } from '../../ui/ToolList';
import { buildToolCards, type ToolEvent } from '../../ui/toolList';
import { resolvedRecord, summarizeAnswer, type ResolvedPrompt } from '../../ui/approval';
import { InlinePrompt, ResolvedLine } from '../chat/InlinePrompt';

export type ChatOpener = (extra: { fresh?: boolean; provider?: string; model?: string }) => Promise<OrchChatSession>;

export type Entry =
  | { type: 'user'; id: string; text: string }
  | { type: 'assistant'; id: string; text: string; streaming?: boolean }
  | { type: 'tools'; id: string; events: ToolEvent[] }
  | { type: 'note'; id: string; text: string; error?: boolean }
  | { type: 'resolved'; id: string; record: ResolvedPrompt };

let seq = 0;
const nid = () => `e${++seq}`;

/** History turns (GET /api/sessions/current/history) → transcript entries, tool steps grouped. */
export function historyEntries(turns: Array<{ role: string; parts: Array<Record<string, unknown>> }>): Entry[] {
  const out: Entry[] = [];
  const tools = () => {
    const last = out[out.length - 1];
    if (last?.type === 'tools') return last;
    const group: Entry = { type: 'tools', id: nid(), events: [] };
    out.push(group);
    return group;
  };
  for (const turn of turns || []) {
    for (const part of turn.parts || []) {
      if (part.type === 'text' && String(part.text || '').trim()) {
        out.push(turn.role === 'user' ? { type: 'user', id: nid(), text: String(part.text) } : { type: 'assistant', id: nid(), text: String(part.text) });
      } else if (part.type === 'tool_call') {
        (tools() as Extract<Entry, { type: 'tools' }>).events.push({ kind: 'tool_call', name: String(part.tool_name || ''), args: part.tool_args });
      } else if (part.type === 'tool_result') {
        (tools() as Extract<Entry, { type: 'tools' }>).events.push({ kind: 'tool_result', name: String(part.tool_name || ''), text: String(part.preview || '') });
      }
    }
  }
  return out;
}

/** One SSE event folded into the transcript (web chat.js handle). Returns the next entries. */
export function applyEvent(entries: Entry[], ev: Record<string, any>, lastSent: string): Entry[] {
  const next = entries.slice();
  const last = next[next.length - 1];
  const settle = () => { if (last?.type === 'assistant' && last.streaming) next[next.length - 1] = { ...last, streaming: false }; };
  switch (ev.kind) {
    case 'assistant_delta':
      if (last?.type === 'assistant' && last.streaming) next[next.length - 1] = { ...last, text: last.text + String(ev.text || '') };
      else next.push({ type: 'assistant', id: nid(), text: String(ev.text || ''), streaming: true });
      return next;
    case 'assistant_end':
      settle();
      return next;
    case 'user_message':
      if (String(ev.text || '') !== lastSent) next.push({ type: 'user', id: nid(), text: String(ev.text || '') });
      return next;
    case 'tool_call': {
      settle();
      const call: ToolEvent = { kind: 'tool_call', id: ev.call_id || ev.id, name: String(ev.tool_name || ''), args: ev.tool_args, at: Date.now() };
      const tail = next[next.length - 1];
      if (tail?.type === 'tools') next[next.length - 1] = { ...tail, events: [...tail.events, call] };
      else next.push({ type: 'tools', id: nid(), events: [call] });
      return next;
    }
    case 'tool_result': {
      for (let i = next.length - 1; i >= 0; i -= 1) {
        const e = next[i];
        if (e.type === 'tools') { next[i] = { ...e, events: [...e.events, { kind: 'tool_result', name: String(ev.tool_name || ''), text: String(ev.text || ''), at: Date.now() }] }; break; }
      }
      return next;
    }
    case 'turn_complete':
      settle();
      return next;
    case 'error':
      settle();
      next.push({ type: 'note', id: nid(), text: String(ev.text || 'Something went wrong.'), error: true });
      return next;
    default:
      return entries;
  }
}

export const FLEET_SUGGESTIONS = ['Which nodes need attention?', 'What changed on the fleet today?', 'Check disk usage across web nodes'];

export type OrchChatSheetProps = {
  visible: boolean;
  onClose: () => void;
  title: string;
  /** Opens (or reuses) the session; called with {provider, model} after the picker, {fresh} for New. */
  open: ChatOpener;
  /** Offer "New conversation" (the fleet chat starts over; the old one is deleted). */
  canStartOver?: boolean;
  emptyTitle: string;
  emptyBody: string;
  suggestions?: string[];
  pickerLede: string;
  /** A question to put in the composer, unsent (results "Ask about this"). */
  prefill?: string;
  testID?: string;
};

export function OrchChatSheet({ visible, onClose, title, open, canStartOver, emptyTitle, emptyBody, suggestions = [], pickerLede, prefill, testID = 'orch-chat' }: OrchChatSheetProps) {
  const { colors } = useTheme();
  const [phase, setPhase] = useState<'idle' | 'connecting' | 'model' | 'ready' | 'error'>('idle');
  const [problem, setProblem] = useState('');
  const [session, setSession] = useState<string | null>(null);
  const [model, setModel] = useState('');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [prompts, setPrompts] = useState<PendingPrompt[]>([]);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState('');
  useEffect(() => { if (visible && prefill) setText(prefill); }, [visible, prefill]);
  const [submitting, setSubmitting] = useState(false);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const lastSent = useRef('');
  const scroller = useRef<ScrollView>(null);

  const adopt = useCallback(async (info: OrchChatSession) => {
    setSession(info.session_name);
    setModel(info.model || '');
    setBusy(!!info.busy);
    try {
      const history = await sessionsApi.getHistory(info.session_name, { limitTurns: 40 });
      setEntries(historyEntries(history.turns || []));
    } catch { setEntries([]); }
    try {
      const pending = await promptsApi.listPending();
      setPrompts((pending.pending || []).filter(p => p.session_name === info.session_name));
    } catch { /* none */ }
    setPhase('ready');
  }, []);

  const connect = useCallback(async (extra: { fresh?: boolean; provider?: string; model?: string } = {}) => {
    setPhase('connecting'); setProblem('');
    try {
      await adopt(await open(extra));
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 400 && !extra.provider) { setPhase('model'); setPicking(true); return false; }
      setProblem(err instanceof Error ? err.message : String(err));
      setPhase(extra.provider ? 'model' : 'error');
      return false;
    }
  }, [adopt, open]);

  useEffect(() => { if (visible && phase === 'idle') void connect(); }, [visible, phase, connect]);

  // The session's stream (the shared SSE hub, filtered with ?session=).
  useEffect(() => {
    if (!visible || !session) return undefined;
    const sub = subscribeToEvents({
      onMessage: ev => {
        const e = ev as Record<string, any>;
        if (e.kind === 'hello') { setBusy(((e.busy as string[]) || []).includes(session)); return; }
        if (e.session_name && e.session_name !== session) return;
        if (e.kind === 'prompt') {
          const p = asPendingPrompt(e);
          if (p && (!p.session_name || p.session_name === session)) setPrompts(list => (list.some(x => x.id === p.id) ? list : [...list, p]));
          return;
        }
        if (e.kind === 'prompt_resolved' || e.kind === 'prompt_cancelled') { setPrompts(list => list.filter(x => x.id !== e.id)); return; }
        if (e.kind === 'history_refresh') return;
        if (['assistant_delta', 'tool_call'].includes(e.kind)) setBusy(true);
        if (e.kind === 'turn_complete' || e.kind === 'error') setBusy(false);
        // Read before clearing: the updater runs later.
        const sent = lastSent.current;
        if (e.kind === 'user_message') lastSent.current = '';
        setEntries(list => applyEvent(list, e, sent));
      },
    }, { sessionName: session });
    return () => sub.close();
  }, [visible, session]);

  useEffect(() => { scroller.current?.scrollToEnd?.({ animated: true }); }, [entries, prompts, busy]);

  const send = async (raw?: string) => {
    const msg = String(raw ?? text).trim();
    if (!msg || !session) return;
    setEntries(list => [...list, { type: 'user', id: nid(), text: msg }]);
    lastSent.current = msg;
    setText('');
    setBusy(true);
    try {
      await chatApi.send(msg, session);
    } catch (err) {
      setEntries(list => [...list, { type: 'note', id: nid(), error: true,
        text: err instanceof ApiError && err.status === 409 ? 'It’s still answering. Wait for it, or stop it first.' : `Couldn’t send: ${err instanceof Error ? err.message : String(err)}` }]);
      setBusy(false);
    }
  };
  const stop = async () => { try { await chatApi.interrupt(session || undefined); } catch { /* best effort */ } setBusy(false); };
  const startOver = async () => { if (busy) await stop(); setEntries([]); setPrompts([]); setSession(null); await connect({ fresh: true }); };

  const answer = async (p: PendingPrompt, payload: Record<string, unknown>) => {
    setSubmitting(true); setPromptError(null);
    try {
      await promptsApi.answer(p.id, payload);
      setPrompts(list => list.filter(x => x.id !== p.id));
      setEntries(list => [...list, { type: 'resolved', id: nid(), record: resolvedRecord(p, { answer: summarizeAnswer(p, payload), cancelled: payload.approved === false }) }]);
    } catch (err) { setPromptError(`Couldn’t answer: ${err instanceof Error ? err.message : String(err)}`); } finally { setSubmitting(false); }
  };
  const cancel = async (p: PendingPrompt) => {
    setSubmitting(true); setPromptError(null);
    try {
      await promptsApi.cancel(p.id);
      setPrompts(list => list.filter(x => x.id !== p.id));
      setEntries(list => [...list, { type: 'resolved', id: nid(), record: resolvedRecord(p, { cancelled: true }) }]);
    } catch (err) { setPromptError(`Couldn’t answer: ${err instanceof Error ? err.message : String(err)}`); } finally { setSubmitting(false); }
  };

  const streaming = entries[entries.length - 1]?.type === 'assistant' && (entries[entries.length - 1] as Extract<Entry, { type: 'assistant' }>).streaming;
  const runningTools = entries[entries.length - 1]?.type === 'tools';

  return (
    <Sheet visible={visible} onClose={onClose} title={title} full scroll={false} testID={testID}
      headerRight={canStartOver && phase === 'ready' ? <Button size="md" variant="ghost" label="New" onPress={() => { void startOver(); }} testID={`${testID}-new`} accessibilityLabel="New conversation" /> : undefined}>
      <View style={styles.body}>
        <ScrollView ref={scroller} style={{ flex: 1 }} contentContainerStyle={styles.log} keyboardShouldPersistTaps="handled" testID={`${testID}-log`}>
          {phase === 'connecting' ? <View style={styles.working}><InkDrop /><Text variant="caption" tone="ink3">Connecting…</Text></View> : null}
          {phase === 'error' ? <Banner tone="attention" glyph="◆" title="The chat isn’t available." action={{ label: 'Try again', onPress: () => { void connect(); } }} testID={`${testID}-error`}>{problem}</Banner> : null}
          {phase === 'model' && !picking ? (
            <EmptyState icon="sparkles" title="Choose a model" message="No chat session is open to borrow one from." actionLabel="Choose a model" onAction={() => setPicking(true)} testID={`${testID}-needs-model`} />
          ) : null}
          {phase === 'ready' && !entries.length && !prompts.length ? (
            <View style={styles.empty} testID={`${testID}-empty`}>
              <EmptyState icon="sparkles" title={emptyTitle} message={emptyBody} />
              <View style={styles.suggest}>{suggestions.map(s => <Chip key={s} variant="meta" onPress={() => { void send(s); }}>{s}</Chip>)}</View>
            </View>
          ) : null}
          {entries.map(e => {
            // Same as the main chat: user turns are accent-wash bubbles, answers sit on the page.
            if (e.type === 'user') return <View key={e.id} style={[styles.user, { backgroundColor: colors.accent.wash }]} testID={`${testID}-user`}><Text variant="bodyLg">{e.text}</Text></View>;
            if (e.type === 'assistant') return <View key={e.id} testID={`${testID}-assistant`}><Markdown>{e.text || ' '}</Markdown></View>;
            if (e.type === 'tools') return <ToolList key={e.id} items={buildToolCards(e.events, { running: busy && e === entries[entries.length - 1] })} running={busy && e === entries[entries.length - 1]} testID={`${testID}-tools`} />;
            if (e.type === 'resolved') return <ResolvedLine key={e.id} record={e.record} />;
            return <Text key={e.id} variant="caption" tone={e.error ? 'danger' : 'ink3'}>{e.error ? '✗ ' : ''}{e.text}</Text>;
          })}
          {prompts.length ? (
            <InlinePrompt prompt={prompts[0]} index={0} total={prompts.length} submitting={submitting} error={promptError}
              onAnswer={(p, payload) => { void answer(p, payload); }} onCancel={p => { void cancel(p); }} />
          ) : null}
          {busy && !streaming && !prompts.length ? (
            <View style={styles.working} accessibilityLiveRegion="polite" testID={`${testID}-working`}><InkDrop /><Text variant="caption" tone="ink3">{runningTools ? 'Working…' : 'Thinking…'}</Text></View>
          ) : null}
        </ScrollView>
        <Composer value={text} onChangeText={setText} onSend={() => { void send(); }} onStop={() => { void stop(); }} busy={busy}
          disabled={phase !== 'ready'} placeholder="Ask about the fleet, or ask for something to be done"
          foot={model ? { model: { label: model, onPress: () => setPicking(true) } } : undefined} testID={`${testID}-composer`} />
      </View>
      <ModelPicker visible={picking} lede={pickerLede} error={phase === 'model' ? problem : ''} onClose={() => setPicking(false)}
        onChoose={async (provider, m) => { setPicking(false); setSession(null); await connect({ provider, model: m }); }} />
    </Sheet>
  );
}

/** Provider then model (GET /api/providers, /api/providers/<name>/models); free text when a provider can't list. */
export function ModelPicker({ visible, lede, error, onClose, onChoose }: {
  visible: boolean; lede: string; error?: string; onClose: () => void; onChoose: (provider: string, model: string) => void;
}) {
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [provider, setProvider] = useState('');
  const [models, setModels] = useState<string[] | null>(null);
  const [modelName, setModelName] = useState('');
  const [hint, setHint] = useState('');
  useEffect(() => {
    if (!visible) return;
    providersApi.list().then(res => {
      const ordered = [...(res.providers || [])].sort((a, b) => Number(b.configured) - Number(a.configured));
      setProviders(ordered);
      setProvider(p => p || ordered.find(x => x.configured)?.name || '');
    }).catch(() => setProviders([]));
  }, [visible]);
  useEffect(() => {
    if (!provider) return;
    let alive = true;
    setModels(null); setHint('');
    providersApi.listModels(provider).then(res => {
      if (!alive) return;
      const list = (res.models || []).map(String);
      setModels(list);
      if (list.length) setModelName(m => (list.includes(m) ? m : list[0]));
      else setHint(res.error ? `Couldn’t list ${provider}’s models (${res.error}); type the model name.` : `${provider} listed no models; type the model name.`);
    }).catch(err => { if (alive) { setModels([]); setHint(`Couldn’t list ${provider}’s models (${err instanceof Error ? err.message : String(err)}); type the model name.`); } });
    return () => { alive = false; };
  }, [provider]);
  const usable = (providers || []).filter(p => p.configured);
  return (
    <Sheet visible={visible} onClose={onClose} title="Choose a model" testID="orch-chat-model"
      footer={<><Button variant="secondary" label="Cancel" onPress={onClose} /><Button variant="primary" label="Start chatting" disabled={!provider || !modelName.trim()}
        onPress={() => onChoose(provider, modelName.trim())} testID="orch-chat-model-ok" /></>}>
      <View style={styles.form}>
        <Text variant="body" tone="ink2">{lede}</Text>
        {providers && !usable.length ? <Text variant="caption" tone="attention">◆ No provider is configured on the computer: set an API key (or run Ollama) there first.</Text> : null}
        <Select label="Provider" value={provider} onChange={setProvider} testID="orch-chat-provider"
          options={usable.map(p => ({ value: p.name, label: p.name }))} />
        {models && models.length ? (
          <Select label="Model" value={modelName} onChange={setModelName} testID="orch-chat-model-select" options={models.map(m => ({ value: m, label: m }))} />
        ) : (
          <Field label="Model" hint={hint || undefined}><TextField value={modelName} onChangeText={setModelName} mono autoCapitalize="none" autoCorrect={false} placeholder="model name" testID="orch-chat-model-text" /></Field>
        )}
        {error ? <Text variant="caption" tone="danger">✗ {error}</Text> : null}
      </View>
    </Sheet>
  );
}

/** The fleet chat (Orchestration → Ask). */
export function FleetChat({ visible, onClose, prefill }: { visible: boolean; onClose: () => void; prefill?: string }) {
  const open = useCallback<ChatOpener>(extra => orchestrationApi.chatSession(extra), []);
  return <OrchChatSheet visible={visible} onClose={onClose} title="Ask about the fleet" open={open} canStartOver prefill={prefill} testID="orch-fleet-chat"
    emptyTitle="Ask about your fleet" emptyBody="Or ask for something to be done. Runs it proposes wait for your approval." suggestions={FLEET_SUGGESTIONS}
    pickerLede="No chat session is open to borrow a model from. Choose the one the fleet chat uses; it’s remembered." />;
}

/** A draft's discovery chat in the Studio: the AI drafts, lints, tests on canaries and submits. */
export function DiscoveryChat({ visible, onClose, draftRef }: { visible: boolean; onClose: () => void; draftRef: string }) {
  const open = useCallback<ChatOpener>(extra => orchestrationApi.studioSession({ ref: draftRef, provider: extra.provider, model: extra.model }), [draftRef]);
  return <OrchChatSheet key={draftRef} visible={visible} onClose={onClose} title={draftRef ? `Discovery chat · ${draftRef}` : 'Discovery chat'} open={open} testID="orch-discovery-chat"
    emptyTitle="Design a tool together" emptyBody="Describe what it should do; the AI asks follow-up questions, drafts the spec and script, lints and tests it on a canary. Only you publish."
    pickerLede="No chat session is open to borrow a model from. Choose the one the discovery chat uses." />;
}

const styles = StyleSheet.create({
  body: { flex: 1, gap: spacing.sm },
  log: { gap: spacing.md, paddingBottom: spacing.base },
  user: { alignSelf: 'flex-end', maxWidth: '80%', paddingVertical: spacing.sm, paddingHorizontal: spacing.md, borderRadius: radii.lg, borderBottomRightRadius: 6 },
  working: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  empty: { gap: spacing.md },
  suggest: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  form: { gap: spacing.md },
});
