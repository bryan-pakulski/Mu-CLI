import React from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from './src/theme/ThemeContext';
import { AppNavigator } from './src/navigation/AppNavigator';
import { PromptHost } from './src/components/PromptHost';
import { useConnectionStore } from './src/store/connection';

export default function App() {
  const loadFromStorage = useConnectionStore((s) => s.loadFromStorage);

  React.useEffect(() => {
    loadFromStorage();
  }, [loadFromStorage]);

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