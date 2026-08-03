import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = '@mucli/connection';
let reconnectInFlight: Promise<void> | null = null;
let storageWriteQueue: Promise<void> = Promise.resolve();

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
    // MUCLI_MOBILE_RECONNECT_YOLO_V1: serialize snapshots so rapid toggles and
    // connection changes cannot finish AsyncStorage writes out of order.
    const state = get();
    const snapshot = JSON.stringify({
      baseUrl: state.baseUrl,
      activeSessionName: state.activeSessionName,
      activeProvider: state.activeProvider,
      activeModel: state.activeModel,
      isConnected: state.isConnected,
      yolo: state.yolo,
    });
    storageWriteQueue = storageWriteQueue
      .catch(() => undefined)
      .then(() => AsyncStorage.setItem(STORAGE_KEY, snapshot));
    try {
      await storageWriteQueue;
    } catch {
      // Persistence is best-effort. Keep the in-memory connection usable.
    }
  },

  autoReconnect: async () => {
    // MUCLI_MOBILE_RECONNECT_YOLO_V1: non-destructive reconnect. A temporary
    // Wi-Fi/VPN/background outage must not erase a known-good remote host or
    // eject the user from a server-side session that is still running.
    if (reconnectInFlight) return reconnectInFlight;

    const task = (async () => {
      const state = get();
      const baseUrl = state.baseUrl.replace(/\/$/, '');
      if (!baseUrl) return;

      const url = baseUrl + '/healthz';
      const MAX_ATTEMPTS = 3;
      const BACKOFF_MS = 1_500;

      for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10_000);
        try {
          const resp = await fetch(url, { method: 'GET', signal: controller.signal });
          if (resp.ok) {
            if (!get().isConnected) {
              set({ isConnected: true });
              await get().saveToStorage();
            }
            return;
          }
        } catch {
          // Retry. Existing connection state remains intact on failure.
        } finally {
          clearTimeout(timeout);
        }
        if (attempt < MAX_ATTEMPTS) {
          await new Promise(resolve => setTimeout(resolve, BACKOFF_MS * attempt));
        }
      }
      // Keep last-known connection state. SSE and foreground health probes will
      // recover automatically when the network becomes reachable again.
    })();

    reconnectInFlight = task;
    try {
      await task;
    } finally {
      if (reconnectInFlight === task) reconnectInFlight = null;
    }
  },
}));