// Pending approvals and questions for the active session (mobile_parity.md §6 Chat).
//
// One owner keeps the queue in sync (usePromptSync, mounted once by PromptHost): pending prompts
// are recovered from GET /api/prompts and kept current from the session's event stream
// (`prompt`, `prompt_resolved`, `prompt_cancelled`). The chat transcript shows the head of the
// queue inline (n of m) and a resolved breadcrumb for each answer; off the chat, PromptHost shows
// a "needs you" banner that opens the chat. With auto-approve on, tool approvals are answered
// automatically and recorded as "Auto-approved". Payloads are the web's (src/ui/approval.ts).

import { useCallback, useEffect, useRef } from 'react';
import { AccessibilityInfo } from 'react-native';
import { create } from 'zustand';
import { promptsApi, type PendingPrompt } from '../api/prompts';
import { subscribeToEvents } from '../api/sse';
import { useConnectionStore } from './connection';
import { announcement, resolvedRecord, summarizeAnswer, type ResolvedPrompt } from '../ui/approval';

export type ResolvedEntry = ResolvedPrompt & { /** Transcript message the breadcrumb follows. */ after?: string | null };

type PromptState = {
  session: string | null;
  queue: PendingPrompt[];
  /** Index of the prompt shown (queue navigation, n of m). */
  index: number;
  submitting: boolean;
  error: string | null;
  resolved: Record<string, ResolvedEntry[]>;
  /** The chat transcript is on screen (the inline card is the surface). */
  chatVisible: boolean;
  /** The last transcript message id (breadcrumb anchor). */
  lastMessageId: string | null;
  reset: (session: string | null) => void;
  setQueue: (queue: PendingPrompt[]) => void;
  merge: (prompts: PendingPrompt[]) => void;
  remove: (id: string) => void;
  setIndex: (index: number) => void;
  addResolved: (entry: ResolvedPrompt) => void;
  setChatVisible: (visible: boolean) => void;
  setLastMessageId: (id: string | null) => void;
  answer: (prompt: PendingPrompt, payload: Record<string, unknown>) => Promise<boolean>;
  cancel: (prompt: PendingPrompt) => Promise<boolean>;
  recover: () => Promise<void>;
};

export function promptMatchesSession(prompt: PendingPrompt, session: string | null): boolean {
  if (!session) return false;
  return !prompt.session_name || prompt.session_name === session;
}

export function asPendingPrompt(event: Record<string, unknown>): PendingPrompt | null {
  const id = typeof event.id === 'string' ? event.id : null;
  const raw = event.prompt;
  if (!id || !raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const prompt = raw as Record<string, unknown>;
  return { ...prompt, id, shape: typeof prompt.shape === 'string' ? prompt.shape : '' } as PendingPrompt;
}

export const usePromptStore = create<PromptState>((set, get) => ({
  session: null,
  queue: [],
  index: 0,
  submitting: false,
  error: null,
  resolved: {},
  chatVisible: false,
  lastMessageId: null,

  reset: session => set({ session, queue: [], index: 0, submitting: false, error: null }),
  setQueue: queue => set(s => ({ queue: queue.filter(p => promptMatchesSession(p, s.session)), index: 0 })),
  merge: prompts => set(s => {
    const relevant = prompts.filter(p => promptMatchesSession(p, s.session));
    if (!relevant.length) return s;
    const byId = new Map(s.queue.map(p => [p.id, p]));
    relevant.forEach(p => byId.set(p.id, p));
    return { queue: Array.from(byId.values()) };
  }),
  remove: id => set(s => {
    const queue = s.queue.filter(p => p.id !== id);
    return { queue, index: Math.min(s.index, Math.max(0, queue.length - 1)) };
  }),
  setIndex: index => set(s => ({ index: Math.max(0, Math.min(index, s.queue.length - 1)) })),
  addResolved: entry => set(s => {
    const session = s.session || '';
    const list = (s.resolved[session] || []).filter(r => r.id !== entry.id);
    return { resolved: { ...s.resolved, [session]: [...list, { ...entry, after: s.lastMessageId }].slice(-50) } };
  }),
  setChatVisible: chatVisible => { if (get().chatVisible !== chatVisible) set({ chatVisible }); },
  setLastMessageId: lastMessageId => { if (get().lastMessageId !== lastMessageId) set({ lastMessageId }); },

  answer: async (prompt, payload) => {
    if (get().submitting) return false;
    set({ submitting: true, error: null });
    try {
      await promptsApi.answer(prompt.id, payload);
      get().remove(prompt.id);
      get().addResolved(resolvedRecord(prompt, { answer: summarizeAnswer(prompt, payload) }));
      return true;
    } catch (error) {
      // Keep the card: the blocked agent has no other way to get an answer.
      set({ error: `Couldn't send the answer: ${error instanceof Error ? error.message : String(error)}` });
      void get().recover();
      return false;
    } finally {
      set({ submitting: false });
    }
  },

  cancel: async prompt => {
    if (get().submitting) return false;
    set({ submitting: true, error: null });
    try {
      await promptsApi.cancel(prompt.id);
      get().remove(prompt.id);
      get().addResolved(resolvedRecord(prompt, { cancelled: true }));
      return true;
    } catch (error) {
      set({ error: `Couldn't cancel: ${error instanceof Error ? error.message : String(error)}` });
      void get().recover();
      return false;
    } finally {
      set({ submitting: false });
    }
  },

  recover: async () => {
    const session = get().session;
    if (!session) return;
    try {
      const response = await promptsApi.listPending();
      if (get().session !== session) return;
      get().setQueue(response.pending || []);
    } catch {
      // Keep the current queue during a transient network failure.
    }
  },
}));

/** The prompt shown now (the head of the queue, or the one navigated to). */
export function useActivePrompt(): { prompt: PendingPrompt | null; index: number; total: number } {
  const queue = usePromptStore(s => s.queue);
  const index = usePromptStore(s => s.index);
  return { prompt: queue[index] || queue[0] || null, index: queue[index] ? index : 0, total: queue.length };
}

/** Keep the queue in sync with the server (mount once). */
export function usePromptSync(): void {
  const isConnected = useConnectionStore(s => s.isConnected);
  const session = useConnectionStore(s => s.activeSessionName);
  const yolo = useConnectionStore(s => s.yolo);
  const queue = usePromptStore(s => s.queue);
  const autoApproving = useRef(new Set<string>());
  const announced = useRef<string | null>(null);

  useEffect(() => {
    const store = usePromptStore.getState();
    store.reset(isConnected ? session : null);
    if (!isConnected || !session) return undefined;
    void store.recover();
    const sub = subscribeToEvents({
      onMessage: event => {
        const s = usePromptStore.getState();
        if (event.kind === 'prompt') {
          const prompt = asPendingPrompt(event);
          if (prompt) s.merge([prompt]);
          return;
        }
        if ((event.kind === 'prompt_resolved' || event.kind === 'prompt_cancelled') && typeof event.id === 'string') {
          const known = s.queue.find(p => p.id === event.id);
          s.remove(event.id);
          // Server-side auto-approvals (YOLO) carry `auto: true`; answers a person gave elsewhere
          // simply drop the card.
          if (event.kind === 'prompt_resolved' && event.auto) {
            const record = resolvedRecord(
              { id: event.id, shape: 'tool_approval', tool_name: String(event.tool_name || known?.tool_name || ''), tool_args: known?.tool_args },
              { auto: true, answer: 'approved' },
            );
            s.addResolved({ ...record, target: String(event.target || record.target) });
          }
        }
      },
      onOpen: () => { void usePromptStore.getState().recover(); },
    }, { sessionName: session });
    return () => sub.close();
  }, [isConnected, session]);

  // Auto-approve answers queued tool approvals (and one already waiting when it was turned on).
  useEffect(() => {
    if (!yolo || !isConnected || !session) return;
    for (const prompt of queue) {
      if (prompt.shape !== 'tool_approval' || prompt.approval_policy === 'always_human' || prompt.requires_desktop || autoApproving.current.has(prompt.id)) continue;
      autoApproving.current.add(prompt.id);
      void promptsApi.answer(prompt.id, { approved: true, remember: false })
        .then(() => {
          const s = usePromptStore.getState();
          s.remove(prompt.id);
          s.addResolved(resolvedRecord(prompt, { auto: true, answer: 'approved' }));
        })
        .catch(() => usePromptStore.getState().recover())
        .finally(() => autoApproving.current.delete(prompt.id));
    }
  }, [isConnected, queue, session, yolo]);

  // A new prompt is announced; focus is never moved to Approve.
  const head = queue[0];
  useEffect(() => {
    if (!head || announced.current === head.id) return;
    announced.current = head.id;
    AccessibilityInfo.announceForAccessibility(announcement(head));
  }, [head]);
}

/** Answer/cancel helpers bound to the store. */
export function usePromptActions() {
  const answer = usePromptStore(s => s.answer);
  const cancel = usePromptStore(s => s.cancel);
  return { answer: useCallback(answer, [answer]), cancel: useCallback(cancel, [cancel]) };
}
