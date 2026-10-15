import React from 'react';
import { AppState } from 'react-native';
import { useFonts } from 'expo-font';
import * as SplashScreen from 'expo-splash-screen';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from './src/theme/ThemeContext';
import { AppNavigator } from './src/navigation/AppNavigator';
import { PromptHost } from './src/components/PromptHost';
import { AtmosphericBackground } from './src/components/AtmosphericBackground';
import { refreshPendingMutations, useConnectionStore } from './src/store/connection';

// Hold the native splash (Paper canvas) until the bundled Wash fonts load,
// so the first frame never flashes the platform font.
SplashScreen.preventAutoHideAsync().catch(() => undefined);

export default function App() {
  const [fontsLoaded, fontError] = useFonts({
    Inter: require('./assets/fonts/InterVariable.ttf'),
    JetBrainsMono: require('./assets/fonts/JetBrainsMono-Variable.ttf'),
  });
  const fontsReady = fontsLoaded || !!fontError;
  const loadFromStorage = useConnectionStore((s) => s.loadFromStorage);
  const autoReconnect = useConnectionStore((s) => s.autoReconnect);

  React.useEffect(() => {
    // MUCLI_MOBILE_RECONNECT_YOLO_V1: foreground health recovery. Android may
    // resume after Wi-Fi/VPN is available without remounting the application.
    let disposed = false;
    let hydrated = false;

    void (async () => {
      await loadFromStorage();
      // Round-33b F6: cold-start hydration MUST run between storage load and
      // the first reconnect/replay — refreshPendingMutations() derives the
      // conflict banner + pending-mutation badge from the durable queue
      // (rebuilding each session's conflict map, F7). Without this call the
      // hydration helper existed but was never invoked by the app, so a
      // restarted client showed no banner over a still-blocked queue and
      // replayPending() could not attribute parked ids on the first pass.
      await refreshPendingMutations();
      hydrated = true;
      if (!disposed) await autoReconnect();
    })();

    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active' && hydrated && !disposed) void autoReconnect();
    });

    return () => {
      disposed = true;
      subscription.remove();
    };
  }, [loadFromStorage, autoReconnect]);

  React.useEffect(() => {
    // A font failure still releases the splash: the app falls back to the
    // platform font rather than hanging on launch.
    if (fontsReady) SplashScreen.hideAsync().catch(() => undefined);
  }, [fontsReady]);

  if (!fontsReady) return null;

  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <AtmosphericBackground>
          <AppNavigator />
          <PromptHost />
        </AtmosphericBackground>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
