import { useCallback, useEffect, useRef, useState } from 'react';
import { chatApi } from '../api/chat';
import { sessionsApi, type SessionHistoryTurn } from '../api/sessions';
import { subscribeToEvents, type SSESubscription } from '../api/sse';
import type { ArtifactDescriptor } from '../api/artifacts';

const SESSION_POLL_MS = 2500;

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'visualization';
  text: string;
  turnId?: string;
  streaming?: boolean;
  origin?: 'history' | 'local' | 'stream';
  artifact?: ArtifactDescriptor;
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

function historyToMessages(turns: SessionHistoryTurn[]): ChatMessage[] {
  return turns.flatMap(turn => {
    if (turn.role !== 'user' && turn.role !== 'assistant') return [];
    const messages: ChatMessage[] = [];
    let pendingText: string[] = [];
    let partIndex = 0;
    const flushText = () => {
      const text = pendingText.join('\n\n').trim();
      pendingText = [];
      if (!text) return;
      messages.push({
        id: `history-${turn.index}-${partIndex++}`,
        role: turn.role as 'user' | 'assistant',
        text,
        streaming: false,
        origin: 'history',
      });
    };

    for (const part of turn.parts) {
      if (part.type === 'text' && typeof part.text === 'string') {
        pendingText.push(String(part.text));
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
    return messages;
  });
}

function eventBelongsToSession(event: StreamEvent, activeSessionName: string | null): boolean {
  const eventSession = typeof event.session_name === 'string' ? event.session_name : null;
  return !eventSession || !activeSessionName || eventSession === activeSessionName;
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

  const subscriptionRef = useRef<SSESubscription | null>(null);
  const messageIdRef = useRef(0);
  const messagesRef = useRef<ChatMessage[]>([]);
  const busyRef = useRef(false);
  const sseConnectedRef = useRef(false);
  const lastSessionRef = useRef<string | null>(null);
  const historyHydratedRef = useRef<string | null>(null);
  const historyRequestRef = useRef<{ sessionName: string; promise: Promise<void> } | null>(null);
  const externalWriteAtRef = useRef(0);
  const completionProbeRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Fallback: if history_refresh doesn't arrive within 3s after
  // turn_complete, force a history reload. Safety net for server
  // bugs or network issues that drop the event.
  const historyFallbackRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  const appendUserMessage = useCallback((text: string, origin: 'local' | 'stream') => {
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

    const request = (async () => {
      try {
        const response = await sessionsApi.getHistory(activeSessionName);
        if (lastSessionRef.current !== activeSessionName) return;
        const historyMessages = historyToMessages(response.turns || []);
        setMessages(current => {
          const hasLiveContent = current.some(message => message.origin !== 'history' || message.streaming);
          if (preserveLive && busyRef.current && hasLiveContent) return current;
          const unchanged = current.length === historyMessages.length
            && current.every((message, index) => {
              const next = historyMessages[index];
              if (message.role === 'visualization' || next.role === 'visualization') {
                return message.role === next.role
                  && message.artifact?.artifact_id === next.artifact?.artifact_id
                  && message.artifact?.height === next.artifact?.height
                  && message.origin === 'history';
              }
              return message.role === next.role
                && message.text === next.text
                && !message.streaming
                && message.origin === 'history';
            });
          return unchanged ? current : historyMessages;
        });
        historyHydratedRef.current = activeSessionName;
        setError(null);
      } catch (historyError) {
        if (messagesRef.current.length === 0) {
          setError(`Could not load conversation: ${String(historyError)}`);
        }
      } finally {
        if (initialLoad) setHistoryLoading(false);
      }
    })();

    historyRequestRef.current = { sessionName: activeSessionName, promise: request };
    try {
      await request;
    } finally {
      if (historyRequestRef.current?.promise === request) historyRequestRef.current = null;
    }
  }, [activeSessionName]);

  const syncSessionState = useCallback(async () => {
    if (!activeSessionName) return;
    try {
      const response = await sessionsApi.getActive(activeSessionName) as ActiveSessionState;
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
        // When SSE is connected, skip loadHistory on busy→not-busy
        // transitions. The server emits a `history_refresh` SSE event
        // after persisting the session, which triggers the reload at
        // the right time. Calling loadHistory here races the server
        // save and replaces live messages with stale history — the
        // view-flash / missing-final-output bug.
        if (wasBusy && sseConnectedRef.current) {
          // SSE will deliver history_refresh; skip.
        } else if (sawExternalWrite || historyHydratedRef.current !== activeSessionName) {
          await loadHistory(false);
        }
      }
    } catch {
      if (busyRef.current) {
        setStreaming(true);
        setWaitingForFirstToken(true);
        setActivityLabel('Reconnecting');
      }
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
      appendUserMessage(String(event.text || ''), 'stream');
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
          const index = current.findIndex(message =>
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
          if (index < 0) return [...current, next];
          const updated = [...current];
          updated[index] = next;
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
    const sessionChanged = lastSessionRef.current !== activeSessionName;
    lastSessionRef.current = activeSessionName;
    if (sessionChanged) {
      setMessages([]);
      setError(null);
      setActivityLabel('Thinking');
      busyRef.current = false;
      historyHydratedRef.current = null;
      externalWriteAtRef.current = 0;
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
    });

    void syncSessionState();
    const poll = setInterval(() => {
      // Skip polling while SSE is connected and streaming — the event stream
      // already drives state updates. Polling during active streaming causes
      // unnecessary network calls and can trigger redundant history loads.
      if (sseConnectedRef.current && busyRef.current) return;
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
    };
  }, [activeSessionName, handleEvent, loadHistory, reconnectKey, syncSessionState]);

  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !activeSessionName || busyRef.current) return;

    setError(null);
    busyRef.current = true;
    setStreaming(true);
    setWaitingForFirstToken(true);
    setActivityLabel(sseConnectedRef.current ? 'Thinking' : 'Connecting');
    appendUserMessage(trimmed, 'local');

    try {
      await chatApi.send(trimmed, activeSessionName);
    } catch (sendError) {
      setError(String(sendError));
      await syncSessionState();
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
    streaming,
    waitingForFirstToken,
    historyLoading,
    activityLabel,
    sseConnected,
    error,
    artifactRevision,
    sendMessage,
    stop,
    retry,
  };
}
