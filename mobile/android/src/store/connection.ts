import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = '@mucli/connection';

export interface ConnectionState {
  baseUrl: string;
  activeSessionName: string | null;
  activeProvider: string | null;
  activeModel: string | null;
  isConnected: boolean;
  yolo: boolean;
  setBaseUrl: (url: string) => void;
  setActiveSession: (name: string | null) => void;
  setActiveProviderModel: (provider: string | null, model: string | null) => void;
  setConnected: (connected: boolean) => void;
  setYolo: (yolo: boolean) => void;
  loadFromStorage: () => Promise<void>;
  saveToStorage: () => Promise<void>;
  /** Best-effort background reconnection after cold start. */
  autoReconnect: () => Promise<void>;
}

export const useConnectionStore = create<ConnectionState>((set, get) => ({
  baseUrl: 'http://192.168.20.14:30311',
  activeSessionName: null,
  activeProvider: null,
  activeModel: null,
  isConnected: false,
  yolo: false,

  setBaseUrl: (url: string) => {
    set({ baseUrl: url.replace(/\/$/, '') });
    get().saveToStorage();
  },

  setActiveSession: (name: string | null) => {
    set({ activeSessionName: name });
    get().saveToStorage();
  },

  setActiveProviderModel: (provider: string | null, model: string | null) => {
    set({ activeProvider: provider, activeModel: model });
    get().saveToStorage();
  },

  setConnected: (connected: boolean) => {
    set({ isConnected: connected });
    get().saveToStorage();
  },

  setYolo: (yolo: boolean) => {
    set({ yolo });
    get().saveToStorage();
  },

  loadFromStorage: async () => {
    try {
      const raw = await AsyncStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        set({
          baseUrl: (parsed.baseUrl && !parsed.baseUrl.includes('localhost') ? parsed.baseUrl : 'http://192.168.20.14:30311'),
          activeSessionName: parsed.activeSessionName || null,
          activeProvider: parsed.activeProvider || null,
          activeModel: parsed.activeModel || null,
          isConnected: parsed.isConnected || false,
          yolo: parsed.yolo || false,
        });
      }
    } catch {
      // AsyncStorage not available or corrupt — keep defaults
    }
  },

  saveToStorage: async () => {
    try {
      const state = get();
      await AsyncStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          baseUrl: state.baseUrl,
          activeSessionName: state.activeSessionName,
          activeProvider: state.activeProvider,
          activeModel: state.activeModel,
          isConnected: state.isConnected,
          yolo: state.yolo,
        }),
      );
    } catch {
      // Silently fail — persistence is best-effort
    }
  },

  autoReconnect: async () => {
    // Cold-start recovery: Android kills the app under memory pressure
    // while an agent runs. isConnected is persisted so the UI does not
    // bounce to ConnectionPrompt every restart. Verify the server is
    // still reachable with retries; clear the flag if it is gone.
    const state = get();
    if (!state.isConnected) return;

    const url = state.baseUrl + '/healthz';
    const MAX_ATTEMPTS = 3;
    const BACKOFF_MS = 1_500;

    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      try {
        const resp = await fetch(url, { method: 'GET', signal: controller.signal });
        if (resp.ok) return; // server reachable — keep isConnected
      } catch {
        // try again
      } finally {
        clearTimeout(timeout);
      }
      if (attempt < MAX_ATTEMPTS) {
        await new Promise(resolve => setTimeout(resolve, BACKOFF_MS * attempt));
      }
    }

    // All attempts failed — server unreachable. Clear flag so the user
    // sees the ConnectionPrompt and can reconfigure if the host moved.
    set({ isConnected: false });
    get().saveToStorage();
  },
}));