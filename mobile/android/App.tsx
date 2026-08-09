import React from 'react';
import { AppState } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from './src/theme/ThemeContext';
import { AppNavigator } from './src/navigation/AppNavigator';
import { PromptHost } from './src/components/PromptHost';
import { AtmosphericBackground } from './src/components/AtmosphericBackground';
import { useConnectionStore } from './src/store/connection';

export default function App() {
  const loadFromStorage = useConnectionStore((s) => s.loadFromStorage);
  const autoReconnect = useConnectionStore((s) => s.autoReconnect);

  React.useEffect(() => {
    // MUCLI_MOBILE_RECONNECT_YOLO_V1: foreground health recovery. Android may
    // resume after Wi-Fi/VPN is available without remounting the application.
    let disposed = false;
    let hydrated = false;

    void (async () => {
      await loadFromStorage();
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
