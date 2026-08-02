import React from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from './src/theme/ThemeContext';
import { AppNavigator } from './src/navigation/AppNavigator';
import { PromptHost } from './src/components/PromptHost';
import { useConnectionStore } from './src/store/connection';

export default function App() {
  const loadFromStorage = useConnectionStore((s) => s.loadFromStorage);
  const autoReconnect = useConnectionStore((s) => s.autoReconnect);

  React.useEffect(() => {
    (async () => {
      await loadFromStorage();
      // After restoring persisted state, verify the server is still
      // reachable. If Android killed the app mid-agent and the host is
      // gone, clear isConnected so the user gets ConnectionPrompt.
      // If the host is still up, isConnected stays true and the app
      // drops straight into chat — no manual reconnect needed.
      await autoReconnect();
    })();
  }, [loadFromStorage, autoReconnect]);

  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <>
          <AppNavigator />
          <PromptHost />
        </>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}