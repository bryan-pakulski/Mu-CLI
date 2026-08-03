import { useCallback, useEffect, useRef, useState } from 'react';
import { chatApi } from '../api/chat';
import { sessionsApi, type SessionHistoryTurn } from '../api/sessions';
import { subscribeToEvents, type SSESubscription } from '../api/sse';
import type { ArtifactDescriptor } from '../api/artifacts';
import type { AttachmentDescriptor } from '../api/attachments';

const SESSION_POLL_MS = 5000;
// Initial window size — the latest N turns to load on session open.
// Older turns are loaded on demand via loadOlderHistory() when the user
// scrolls near the top (sliding window). FlatList virtualization keeps
// only a few cells mounted, so the array can grow without OOM.
// Initial window size — the latest N turns to load on session open.
// Kept small to avoid OOM on mid-range Android; older turns load on demand
// via loadOlderHistory() (sliding window) when the user scrolls up.
const MOBILE_HISTORY_TURN_LIMIT = 20;
const MOBILE_HISTORY_PAGE_SIZE = 20;

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'visualization' | 'collapse';
  text: string;
  turnId?: string;
  streaming?: boolean;
  origin?: 'history' | 'local' | 'stream';
  artifact?: ArtifactDescriptor;
  attachments?: AttachmentDescriptor[];
  childTurns?: ChatMessage[];
  collapseCount?: number;
  collapseElapsed?: string;
  collapseTokens?: string;
  collapseOpen?: boolean;
  collapseGroupKey?: string;
}

type StreamEvent = { kind: string; [key: string]: unknown };
type ActiveSessionState = { active?: boolean; is_busy?: boolean; external_active?: boolean; external_last_at?: number };

function asVisualization(value: unknown): ArtifactDescriptor | null {
  if (!value || typeof value !== 'object') return null;
  const artifact = value as ArtifactDescriptor;
  return artifact.kind === 'visualization' && typeof artifact.artifact_id === 'string'
    ? artifact
    : null;
}

export function historyToMessages(turns: SessionHistoryTurn[]): ChatMessage[] {
  // MUCLI_MOBILE_VISUALIZATION_HISTORY_V1: tool-result turns can contain durable visualization
  // descriptors even when their role is `tool`. Preserve those cards while
  // continuing to render ordinary text only for user/assistant turns.
  return turns.flatMap(turn => {
    const messageRole = turn.role === 'user' || turn.role === 'assistant'
      ? turn.role as 'user' | 'assistant'
      : null;
    const messages: ChatMessage[] = [];
    let pendingText: string[] = [];
    const pendingAttachments: AttachmentDescriptor[] = [];
    let partIndex = 0;
    const flushText = () => {
      const text = pendingText.join('\n\n').trim();
      pendingText = [];
      if (!messageRole || !text) return;
      messages.push({
        id: `history-${turn.index}-${partIndex++}`,
        role: messageRole,
        text,
        streaming: false,
        origin: 'history',
        attachments: messageRole === 'user' ? [...pendingAttachments] : undefined,
      });
    };

    for (const part of turn.parts) {
      if (part.type === 'text' && typeof part.text === 'string') {
        if (messageRole) pendingText.push(String(part.text));
        continue;
      }
      if (
        messageRole === 'user'
        && part.type === 'attachment'
        && part.attachment
        && typeof part.attachment === 'object'
      ) {
        pendingAttachments.push(part.attachment as AttachmentDescriptor);
        continue;
      }
      const artifact = asVisualization(part.artifact);
      if (!artifact) continue;
      flushText();
      messages.push({
        id: `visualization-${artifact.artifact_id}`,
        role: 'visualization',
        text: '',
        streaming: false,
        origin: 'history',
        artifact,
      });
    }
    flushText();
    if (messageRole === 'user' && pendingAttachments.length > 0 && messages.length === 0) {
      messages.push({
        id: `history-${turn.index}-attachments`,
        role: 'user',
        text: '',
        streaming: false,
        origin: 'history',
        attachments: [...pendingAttachments],
      });
    }
    return messages;
  });
}

function eventBelongsToSession(event: StreamEvent, activeSessionName: string | null): boolean {
  const eventSession = typeof event.session_name === 'string' ? event.session_name : null;
  return !eventSession || !activeSessionName || eventSession === activeSessionName;
}

// Collapse every completed exchange independently. Everything after the user
// message and before the final non-streaming assistant response belongs to
// the compact interim section, including visualization cards.
function flattenCollapsedMessages(messages: ChatMessage[]): ChatMessage[] {
  const flattened: ChatMessage[] = [];
  for (const message of messages) {
    if (message.role === 'collapse') {
      flattened.push(...flattenCollapsedMessages(message.childTurns || []));
    } else {
      flattened.push(message);
    }
  }
  return flattened;
}

function collapseGroupKey(user: ChatMessage, finalResponse: ChatMessage): string {
  return JSON.stringify([user.text, finalResponse.text]);
}

function groupIntermediateTurns(
  messages: ChatMessage[],
  previous: ChatMessage[] = messages,
): ChatMessage[] {
  const source = flattenCollapsedMessages(messages);
  if (source.length < 3) return source;

  const previousOpen = new Map<string, boolean>();
  for (const message of previous) {
    if (message.role === 'collapse' && message.collapseGroupKey) {
      previousOpen.set(message.collapseGroupKey, Boolean(message.collapseOpen));
    }
  }

  const grouped: ChatMessage[] = [];
  let index = 0;
  while (index < source.length) {
    const userMessage = source[index];
    if (userMessage.role !== 'user') {
      grouped.push(userMessage);
      index += 1;
      continue;
    }

    grouped.push(userMessage);
    let nextUserIndex = index + 1;
    while (nextUserIndex < source.length && source[nextUserIndex].role !== 'user') {
      nextUserIndex += 1;
    }

    const exchange = source.slice(index + 1, nextUserIndex);
    let finalOffset = -1;
    for (let offset = exchange.length - 1; offset >= 0; offset -= 1) {
      const candidate = exchange[offset];
      if (candidate.role === 'assistant' && !candidate.streaming) {
        finalOffset = offset;
        break;
      }
    }

    if (finalOffset > 0) {
      const childTurns = exchange.slice(0, finalOffset);
      const finalResponse = exchange[finalOffset];
      const groupKey = collapseGroupKey(userMessage, finalResponse);
      grouped.push({
        id: `collapse-${userMessage.id}-${finalResponse.id}`,
        role: 'collapse',
        text: '',
        childTurns,
        collapseCount: childTurns.length,
        collapseElapsed: '',
        collapseTokens: '',
        collapseOpen: previousOpen.get(groupKey) || false,
        collapseGroupKey: groupKey,
      });
      grouped.push(finalResponse, ...exchange.slice(finalOffset + 1));
    } else {
      grouped.push(...exchange);
    }

    index = nextUserIndex;
  }
  return grouped;
}

export function useChatSession(activeSessionName: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [waitingForFirstToken, setWaitingForFirstToken] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [activityLabel, setActivityLabel] = useState('Thinking');
  const [sseConnected, setSseConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reconnectKey, setReconnectKey] = useState(0);
  const [artifactRevision, setArtifactRevision] = useState(0);
  // MUCLI_SLIDING_WINDOW_V1: track whether older turns exist on the server
  // and whether a backward-pagination request is in flight. Mobile ChatScreen
  // triggers loadOlderHistory when the user scrolls near the top.
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const subscriptionRef = useRef<SSESubscription | null>(null);
  const messageIdRef = useRef(0);
  const messagesRef = useRef<ChatMessage[]>([]);
  const busyRef = useRef(false);
  const sseConnectedRef = useRef(false);
  const lastSessionRef = useRef<string | null>(null);
  const historyHydratedRef = useRef<string | null>(null);
  const historyRequestRef = useRef<{ sessionName: string; promise: Promise<void> } | null>(null);
  const historyAbortRef = useRef<AbortController | null>(null);
  const stateAbortRef = useRef<AbortController | null>(null);
  const externalWriteAtRef = useRef(0);
  const completionProbeRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Fallback: if history_refresh doesn't arrive within 3s after
  // turn_complete, force a history reload. Safety net for server
  // bugs or network issues that drop the event.
  const historyFallbackRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // MUCLI_SLIDING_WINDOW_V1: the absolute server index of the oldest turn
  // currently in the messages array. Used as the `before_index` cursor for
  // backward pagination. Reset on session change / full reload.
  const oldestLoadedIndexRef = useRef<number | null>(null);
  const loadingOlderRef = useRef(false);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    sseConnectedRef.current = sseConnected;
  }, [sseConnected]);

  const nextId = useCallback((prefix: string) => {
    messageIdRef.current += 1;
    return `${prefix}-${Date.now().toString(36)}-${messageIdRef.current}`;
  }, []);

  const appendUserMessage = useCallback((text: string, origin: 'local' | 'stream', attachments: AttachmentDescriptor[] = []) => {
    setMessages(current => {
      const last = current[current.length - 1];
      if (origin === 'stream' && last?.role === 'user' && last.text === text && last.origin === 'local') {
        const updated = [...current];
        updated[updated.length - 1] = { ...last, origin: 'stream' };
        return updated;
      }
      return [...current, {
        id: nextId('user'),
        role: 'user',
        text,
        streaming: false,
        origin,
        attachments,
      }];
    });
  }, [nextId]);

  const appendAssistantDelta = useCallback((turnId: string, delta: string) => {
    if (!delta) return;
    const safeTurnId = turnId || 'active-turn';
    setMessages(current => {
      const index = current.findIndex(message => message.role === 'assistant' && message.turnId === safeTurnId);
      if (index >= 0) {
        const updated = [...current];
        updated[index] = {
          ...updated[index],
          text: updated[index].text + delta,
          streaming: true,
          origin: 'stream',
        };
        return updated;
      }
      return [...current, {
        id: nextId('assistant'),
        role: 'assistant',
        text: delta,
        turnId: safeTurnId,
        streaming: true,
        origin: 'stream',
      }];
    });
  }, [nextId]);

  const finalizeAssistant = useCallback((turnId: string) => {
    setMessages(current => current.map(message =>
      message.role === 'assistant' && message.turnId === turnId
        ? { ...message, streaming: false }
        : message,
    ));
  }, []);

  const loadHistory = useCallback(async (preserveLive = true) => {
    if (!activeSessionName) {
      historyAbortRef.current?.abort();
      historyAbortRef.current = null;
      historyHydratedRef.current = null;
      setMessages([]);
      setHistoryLoading(false);
      return;
    }

    const inFlight = historyRequestRef.current;
    if (inFlight?.sessionName === activeSessionName) {
      await inFlight.promise;
      return;
    }

    const initialLoad = historyHydratedRef.current !== activeSessionName;
    if (initialLoad) setHistoryLoading(true);

    historyAbortRef.current?.abort();
    const controller = new AbortController();
    historyAbortRef.current = controller;
    const request = (async () => {
      try {
        const response = await sessionsApi.getHistory(activeSessionName, {
          signal: controller.signal,
          timeoutMs: 15_000,
          limitTurns: MOBILE_HISTORY_TURN_LIMIT,
          // Visualization cards are collapsed until opened, so hydrate every
          // durable visualization descriptor for parity with the web client.
        });
        // Yield once before converting/rendering history so a navigation press
        // already queued by React Native is handled first.
        await new Promise<void>(resolve => setTimeout(resolve, 0));
        if (controller.signal.aborted || lastSessionRef.current !== activeSessionName) return;
        const historyMessages = historyToMessages(response.turns || []);
        // Track the oldest loaded turn index for backward pagination.
        const oldestIdx = response.start_index ?? null;
        oldestLoadedIndexRef.current = oldestIdx;
        setHasMore(response.has_more ?? false);
        setMessages(current => {
          const hasLiveContent = current.some(message => message.origin !== 'history' || message.streaming);
          if (preserveLive && busyRef.current && hasLiveContent) return current;
          // Preserve existing message IDs where content matches so FlatList
          // keyExtractor returns stable keys → no cell remount → no scroll
          // jump. When a history_refresh arrives after a turn, the live
          // messages (origin: 'stream', id: 'assistant-xxx') are replaced by
          // history messages (id: 'history-N-N'). Without ID reuse, every
          // key changes → FlatList unmounts/remounts all cells → visible
          // "jumping up and down" effect.
          const currentFlat = flattenCollapsedMessages(current);
          const merged = historyMessages.map((next, index) => {
            const prev = currentFlat[index];
            if (!prev) return next;
            const contentMatches = prev.role === next.role
              && prev.text === next.text
              && (next.role === 'visualization'
                ? prev.artifact?.artifact_id === next.artifact?.artifact_id
                : true);
            if (contentMatches) return { ...next, id: prev.id };
            return next;
          });
          return groupIntermediateTurns(merged, current);
        });
        historyHydratedRef.current = activeSessionName;
        setError(null);
      } catch (historyError) {
        if (controller.signal.aborted) return;
        if (messagesRef.current.length === 0) {
          setError(`Could not load conversation: ${String(historyError)}`);
        }
      } finally {
        if (historyAbortRef.current === controller) historyAbortRef.current = null;
        if (initialLoad && lastSessionRef.current === activeSessionName) {
          setHistoryLoading(false);
        }
      }
    })();

    historyRequestRef.current = { sessionName: activeSessionName, promise: request };
    try {
      await request;
    } finally {
      if (historyRequestRef.current?.promise === request) historyRequestRef.current = null;
    }
  }, [activeSessionName]);

  // MUCLI_SLIDING_WINDOW_V1: load older turns and prepend them to the
  // messages array. Called by ChatScreen when the user scrolls near the top.
  // Uses `before_index` = oldestLoadedIndexRef to request the page of turns
  // immediately older than what's currently loaded. The server returns
  // has_more + start_index so we can update the cursor.
  const loadOlderHistory = useCallback(async (): Promise<number> => {
    if (!activeSessionName || loadingOlderRef.current) return 0;
    const cursor = oldestLoadedIndexRef.current;
    if (cursor === null || cursor <= 0 || !hasMore) return 0;

    loadingOlderRef.current = true;
    setLoadingOlder(true);
    try {
      const response = await sessionsApi.getHistory(activeSessionName, {
        timeoutMs: 15_000,
        limitTurns: MOBILE_HISTORY_PAGE_SIZE,
        beforeIndex: cursor,
      });
      if (lastSessionRef.current !== activeSessionName) return 0;
      const olderMessages = historyToMessages(response.turns || []);
      const olderCount = olderMessages.length;
      const newOldest = response.start_index ?? null;
      oldestLoadedIndexRef.current = newOldest;
      setHasMore(response.has_more ?? false);
      if (olderCount > 0) {
        setMessages(current => groupIntermediateTurns(
          [...olderMessages, ...flattenCollapsedMessages(current)],
          current,
        ));
      }
      return olderCount;
    } catch {
      return 0;
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [activeSessionName, hasMore]);

  const syncSessionState = useCallback(async () => {
    if (!activeSessionName) return;
    stateAbortRef.current?.abort();
    const controller = new AbortController();
    stateAbortRef.current = controller;
    try {
      const response = await sessionsApi.getActive(activeSessionName, {
        signal: controller.signal,
        timeoutMs: 8_000,
      }) as ActiveSessionState;
      if (controller.signal.aborted || lastSessionRef.current !== activeSessionName) return;
      // `external_active` is a short cross-process write pulse, not evidence
      // that an agent turn is still running. Only the server-owned busy event
      // controls the mobile generating state.
      const busy = Boolean(response.active && response.is_busy);
      const externalWriteAt = Number(response.external_last_at || 0);
      const sawExternalWrite = externalWriteAt > externalWriteAtRef.current;
      if (sawExternalWrite) externalWriteAtRef.current = externalWriteAt;
      const wasBusy = busyRef.current;
      busyRef.current = busy;
      setStreaming(busy);

      if (busy) {
        const hasStreamingText = messagesRef.current.some(message =>
          message.role === 'assistant' && message.streaming && message.text.trim().length > 0,
        );
        if (!hasStreamingText) setWaitingForFirstToken(true);
        if (!sseConnectedRef.current) setActivityLabel('Reconnecting');
      } else {
        setWaitingForFirstToken(false);
        setActivityLabel('Thinking');
        if (wasBusy && sseConnectedRef.current) {
          // SSE will deliver history_refresh; skip.
        } else if (sawExternalWrite || historyHydratedRef.current !== activeSessionName) {
          await loadHistory(false);
        }
      }
    } catch {
      if (controller.signal.aborted) return;
      if (busyRef.current) {
        setStreaming(true);
        setWaitingForFirstToken(true);
        setActivityLabel('Reconnecting');
      }
    } finally {
      if (stateAbortRef.current === controller) stateAbortRef.current = null;
    }
  }, [activeSessionName, loadHistory]);

  const handleEvent = useCallback((event: StreamEvent) => {
    if (!eventBelongsToSession(event, activeSessionName)) return;
    const kind = event.kind;

    if (kind === 'hello') {
      const busyNames = Array.isArray(event.busy) ? event.busy.map(String) : [];
      const busy = Boolean(activeSessionName && busyNames.includes(activeSessionName));
      busyRef.current = busy;
      setStreaming(busy);
      setWaitingForFirstToken(busy);
      if (!busy && historyHydratedRef.current !== activeSessionName) void loadHistory(false);
      return;
    }

    if (kind === 'user_message') {
      const attachments = Array.isArray(event.attachments)
        ? event.attachments.filter(value => value && typeof value === 'object') as AttachmentDescriptor[]
        : [];
      appendUserMessage(String(event.text || ''), 'stream', attachments);
      return;
    }

    if (kind === 'assistant_start') {
      busyRef.current = true;
      setStreaming(true);
      setWaitingForFirstToken(true);
      setActivityLabel('Generating');
      return;
    }

    if (kind === 'assistant_delta') {
      busyRef.current = true;
      setStreaming(true);
      setWaitingForFirstToken(false);
      setActivityLabel('Generating');
      appendAssistantDelta(String(event.turn_id || 'active-turn'), String(event.text || ''));
      return;
    }

    if (kind === 'thinking_delta') {
      busyRef.current = true;
      setStreaming(true);
      setWaitingForFirstToken(true);
      setActivityLabel('Thinking');
      return;
    }

    if (kind === 'tool_call') {
      busyRef.current = true;
      setStreaming(true);
      setWaitingForFirstToken(true);
      const toolName = typeof event.tool_name === 'string' ? event.tool_name : '';
      setActivityLabel(toolName ? `Running ${toolName}` : 'Running tool');
      return;
    }

    if (kind === 'tool_result') {
      busyRef.current = true;
      setStreaming(true);
      setWaitingForFirstToken(true);
      setActivityLabel('Thinking');
      return;
    }

    if (kind === 'assistant_end') {
      finalizeAssistant(String(event.turn_id || 'active-turn'));
      if (busyRef.current) {
        setWaitingForFirstToken(true);
        setActivityLabel('Finishing');
        if (completionProbeRef.current) clearTimeout(completionProbeRef.current);
        completionProbeRef.current = setTimeout(() => {
          void syncSessionState();
        }, 500);
      }
      return;
    }

    if (kind === 'prompt') {
      busyRef.current = true;
      setStreaming(true);
      setWaitingForFirstToken(true);
      setActivityLabel('Waiting for approval');
      return;
    }

    if (kind === 'prompt_resolved' || kind === 'prompt_cancelled') {
      if (busyRef.current) {
        setWaitingForFirstToken(true);
        setActivityLabel('Generating');
      }
      return;
    }

    if (kind === 'turn_complete') {
      if (completionProbeRef.current) {
        clearTimeout(completionProbeRef.current);
        completionProbeRef.current = null;
      }
      busyRef.current = false;
      setStreaming(false);
      setWaitingForFirstToken(false);
      setActivityLabel('Thinking');
      const result = event.result && typeof event.result === 'object'
        ? event.result as Record<string, unknown>
        : null;
      if (result?.status === 'error' && result.error) {
        setError(String(result.error));
      }
      setMessages(current => groupIntermediateTurns(current, current));
      setArtifactRevision(value => value + 1);
      // Do NOT call loadHistory here. The server may not have persisted
      // the final assistant message yet, so loadHistory would replace
      // the live-streamed message with stale history (missing the last
      // turn) — causing the screen to flash and the final output to
      // vanish. Instead, wait for the `history_refresh` SSE event,
      // which the server emits after the session is safely persisted.
      // Safety net: if history_refresh doesn't arrive in 3s, force reload.
      if (historyFallbackRef.current) clearTimeout(historyFallbackRef.current);
      historyFallbackRef.current = setTimeout(() => {
        historyFallbackRef.current = null;
        void loadHistory(false);
      }, 3000);
      return;
    }

    if (kind === 'artifact_created') {
      const artifact = asVisualization(event.artifact);
      if (artifact) {
        setMessages(current => {
          const updated = [...current];
          const existing = updated.findIndex(message =>
            message.role === 'visualization'
            && message.artifact?.artifact_id === artifact.artifact_id,
          );
          const next: ChatMessage = {
            id: `visualization-${artifact.artifact_id}`,
            role: 'visualization',
            text: '',
            streaming: false,
            origin: 'stream',
            artifact,
          };
          if (existing >= 0) {
            updated[existing] = next;
            return updated;
          }

          // MUCLI_VISUALIZATION_TIMELINE_V2: close the current assistant segment
          // at the artifact boundary. Future deltas create a new segment after
          // the visualization instead of mutating text above it.
          let insertAt = updated.length;
          for (let index = updated.length - 1; index >= 0; index -= 1) {
            const message = updated[index];
            if (message.role === 'assistant' && message.streaming) {
              updated[index] = {
                ...message,
                id: `${message.id}-segment-${artifact.artifact_id}`,
                turnId: `${message.turnId || 'active-turn'}-segment-${artifact.artifact_id}`,
                streaming: false,
              };
              insertAt = index + 1;
              break;
            }
          }
          updated.splice(insertAt, 0, next);
          return updated;
        });
      }
      setArtifactRevision(value => value + 1);
      return;
    }

    if (kind === 'history_refresh') {
      if (historyFallbackRef.current) {
        clearTimeout(historyFallbackRef.current);
        historyFallbackRef.current = null;
      }
      setArtifactRevision(value => value + 1);
      if (!busyRef.current) void loadHistory(false);
      return;
    }

    if (kind === 'session_updated') {
      if (!busyRef.current) void loadHistory(false);
      return;
    }

    if (kind === 'error') {
      if (completionProbeRef.current) {
        clearTimeout(completionProbeRef.current);
        completionProbeRef.current = null;
      }
      busyRef.current = false;
      setStreaming(false);
      setWaitingForFirstToken(false);
      setError(String(event.text || 'Agent error'));
      // Same as turn_complete: skip loadHistory when SSE connected.
      // The server will emit history_refresh after persisting.
      if (!sseConnectedRef.current) void loadHistory(false);
    }
  }, [activeSessionName, appendAssistantDelta, appendUserMessage, finalizeAssistant, loadHistory, syncSessionState]);

  useEffect(() => {
    subscriptionRef.current?.close();
    subscriptionRef.current = null;
    historyAbortRef.current?.abort();
    historyAbortRef.current = null;
    historyRequestRef.current = null;
    stateAbortRef.current?.abort();
    stateAbortRef.current = null;
    const sessionChanged = lastSessionRef.current !== activeSessionName;
    lastSessionRef.current = activeSessionName;
    if (sessionChanged) {
      setMessages([]);
      setError(null);
      setActivityLabel('Thinking');
      busyRef.current = false;
      historyHydratedRef.current = null;
      externalWriteAtRef.current = 0;
      oldestLoadedIndexRef.current = null;
      loadingOlderRef.current = false;
      setHasMore(false);
      setLoadingOlder(false);
      setArtifactRevision(value => value + 1);
      if (completionProbeRef.current) {
        clearTimeout(completionProbeRef.current);
        completionProbeRef.current = null;
      }
      if (historyFallbackRef.current) {
        clearTimeout(historyFallbackRef.current);
        historyFallbackRef.current = null;
      }
    }
    setSseConnected(false);

    if (!activeSessionName) {
      setHistoryLoading(false);
      setStreaming(false);
      setWaitingForFirstToken(false);
      return undefined;
    }

    void loadHistory(false);
    subscriptionRef.current = subscribeToEvents({
      onOpen: () => {
        setSseConnected(true);
        void syncSessionState();
      },
      onMessage: handleEvent,
      onError: () => {
        setSseConnected(false);
        if (busyRef.current) {
          setStreaming(true);
          setWaitingForFirstToken(true);
          setActivityLabel('Reconnecting');
        }
      },
      onClose: () => setSseConnected(false),
    }, { sessionName: activeSessionName });

    void syncSessionState();
    const poll = setInterval(() => {
      // A connected event stream already carries busy, completion, prompt,
      // artifact, and external session updates. Poll only as a disconnected
      // recovery path so mobile does not hammer the host while an agent runs.
      if (sseConnectedRef.current) return;
      void syncSessionState();
    }, SESSION_POLL_MS);

    return () => {
      clearInterval(poll);
      if (completionProbeRef.current) {
        clearTimeout(completionProbeRef.current);
        completionProbeRef.current = null;
      }
      if (historyFallbackRef.current) {
        clearTimeout(historyFallbackRef.current);
        historyFallbackRef.current = null;
      }
      subscriptionRef.current?.close();
      subscriptionRef.current = null;
      historyAbortRef.current?.abort();
      historyAbortRef.current = null;
      stateAbortRef.current?.abort();
      stateAbortRef.current = null;
    };
  }, [activeSessionName, handleEvent, loadHistory, reconnectKey, syncSessionState]);

  const sendMessage = useCallback(async (text: string, attachments: AttachmentDescriptor[] = []) => {
    let trimmed = text.trim();
    if (!trimmed && attachments.length > 0) trimmed = 'Please review the attached document(s).';
    if (!trimmed || !activeSessionName || busyRef.current) return false;

    setError(null);
    busyRef.current = true;
    setStreaming(true);
    setWaitingForFirstToken(true);
    setActivityLabel(sseConnectedRef.current ? 'Thinking' : 'Connecting');
    appendUserMessage(trimmed, 'local', attachments);

    try {
      await chatApi.send(trimmed, activeSessionName, attachments.map(item => item.attachment_id));
      return true;
    } catch (sendError) {
      setError(String(sendError));
      await syncSessionState();
      return false;
    }
  }, [activeSessionName, appendUserMessage, syncSessionState]);

  const stop = useCallback(async () => {
    if (!activeSessionName) return;
    setActivityLabel('Stopping');
    try {
      await chatApi.interrupt(activeSessionName);
    } catch (stopError) {
      setError(String(stopError));
    }
    await syncSessionState();
  }, [activeSessionName, syncSessionState]);

  const retry = useCallback(() => {
    setError(null);
    setReconnectKey(value => value + 1);
    void loadHistory(false);
    void syncSessionState();
  }, [loadHistory, syncSessionState]);

  return {
    messages,
    setMessages,
    streaming,
    waitingForFirstToken,
    historyLoading,
    activityLabel,
    sseConnected,
    error,
    artifactRevision,
    hasMore,
    loadingOlder,
    sendMessage,
    stop,
    retry,
    loadOlderHistory,
  };
}
