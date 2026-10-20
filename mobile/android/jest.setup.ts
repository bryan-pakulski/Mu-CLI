// Mock AsyncStorage
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));
// RN Modal renders in a separate native window; under jest (test env) its
// children are not mounted, so BottomSheet/BottomSheet-backed tests see an
// empty tree. Mock Modal to a plain View that always renders its children.
jest.mock('react-native/Libraries/Modal/Modal', () => {
  const React = require('react');
  const { View } = require('react-native');
  return ({ children, testID, visible }: { children?: React.ReactNode; testID?: string; visible?: boolean }) =>
    visible === false ? null : React.createElement(View, { testID }, children);
});

// react-native-safe-area-context native components render nothing under jest;
// replace with pass-through views so SafeAreaModal/SafeAreaView trees mount.
jest.mock('react-native-safe-area-context', () => {
  const React = require('react');
  const { View } = require('react-native');
  const PassThrough = ({ children, ...rest }: { children?: React.ReactNode; [k: string]: unknown }) =>
    React.createElement(View, rest, children);
  return {
    SafeAreaProvider: PassThrough,
    SafeAreaView: PassThrough,
    SafeAreaConsumer: ({ children }: { children?: React.ReactNode }) => children ?? null,
    SafeAreaInsetsContext: {
      Provider: ({ children }: { children?: React.ReactNode }) => children ?? null,
      Consumer: ({ children }: { children?: (v: unknown) => React.ReactNode }) => children?.({ top: 0, bottom: 0, left: 0, right: 0 }) ?? null,
    },
    useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
    useSafeAreaFrame: () => ({ x: 0, y: 0, width: 390, height: 844 }),
    initialWindowMetrics: {
      frame: { x: 0, y: 0, width: 0, height: 0 },
      insets: { top: 0, bottom: 0, left: 0, right: 0 },
    },
    withSafeAreaInsets: (C: unknown) => C,
  };
});

// expo-secure-store: an in-memory keychain (device tokens, api/auth.ts).
jest.mock('expo-secure-store', () => {
  const store = new Map<string, string>();
  return {
    __store: store,
    getItemAsync: jest.fn(async (key: string) => (store.has(key) ? store.get(key)! : null)),
    setItemAsync: jest.fn(async (key: string, value: string) => { store.set(key, value); }),
    deleteItemAsync: jest.fn(async (key: string) => { store.delete(key); }),
  };
});

// expo-camera: the QR scanner renders a plain view; tests fire onBarcodeScanned themselves.
jest.mock('expo-camera', () => {
  const React = require('react');
  const { View } = require('react-native');
  return {
    CameraView: (props: Record<string, unknown>) => React.createElement(View, { testID: 'camera-view', ...props }),
    useCameraPermissions: () => [{ granted: true, canAskAgain: true }, jest.fn(async () => ({ granted: true }))],
  };
});

// expo-av: recording is stubbed (the mic records nothing under jest).
jest.mock('expo-av', () => {
  class Recording {
    prepareToRecordAsync = jest.fn(async () => undefined);
    startAsync = jest.fn(async () => undefined);
    stopAndUnloadAsync = jest.fn(async () => undefined);
    getURI = jest.fn(() => 'file:///tmp/recording.m4a');
    getStatusAsync = jest.fn(async () => ({ isRecording: false, durationMillis: 0 }));
    setOnRecordingStatusUpdate = jest.fn();
  }
  return {
    Audio: {
      Recording,
      requestPermissionsAsync: jest.fn(async () => ({ granted: true })),
      getPermissionsAsync: jest.fn(async () => ({ granted: true })),
      setAudioModeAsync: jest.fn(async () => undefined),
      RecordingOptionsPresets: { HIGH_QUALITY: {} },
    },
  };
});

// expo-sharing: the share sheet (session files downloaded with the device token).
jest.mock('expo-sharing', () => ({
  isAvailableAsync: jest.fn(async () => true),
  shareAsync: jest.fn(async () => undefined),
}));
