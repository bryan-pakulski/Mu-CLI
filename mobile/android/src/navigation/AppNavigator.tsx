import React, { useState, useRef } from 'react';
import { NavigationContainer, DarkTheme, DefaultTheme, NavigationContainerRef } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { SafeAreaView } from 'react-native-safe-area-context';
import { View, StyleSheet } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

import { Header, type ViewPanel } from '../components/Header';
import { SessionsDrawer } from '../components/SessionsDrawer';
import { InspectorDrawer } from '../components/InspectorDrawer';

import { ChatScreen } from '../screens/ChatScreen';
import { MemoryScreen } from '../screens/MemoryScreen';
import { FilesScreen } from '../screens/FilesScreen';
import { SkillsScreen } from '../screens/SkillsScreen';
import { AudioScreen } from '../screens/AudioScreen';
import { TracesScreen } from '../screens/TracesScreen';
import { ProvidersScreen } from '../screens/ProvidersScreen';
import { ConnectionScreen } from '../screens/ConnectionScreen';
import { ModesScreen } from '../screens/ModesScreen';
import { PromptsScreen } from '../screens/PromptsScreen';
import { SystemPromptsScreen } from '../screens/SystemPromptsScreen';
import { TeacherScreen } from '../screens/TeacherScreen';
import { FeatureScreen } from '../screens/FeatureScreen';
import { ResearchScreen } from '../screens/ResearchScreen';
import { SecurityScreen } from '../screens/SecurityScreen';
import { LoopScreen } from '../screens/LoopScreen';
import { DebugScreen } from '../screens/DebugScreen';
import { HistoryScreen } from '../screens/HistoryScreen';

export type RootStackParamList = {
  Chat: undefined;
  Teacher: undefined;
  Feature: undefined;
  Research: undefined;
  Security: undefined;
  Loop: undefined;
  Debug: undefined;
  History: undefined;
  SystemPrompts: undefined;
  Memory: undefined;
  Files: undefined;
  Skills: undefined;
  Audio: undefined;
  Traces: undefined;
  Providers: undefined;
  Connection: undefined;
  Modes: undefined;
  Prompts: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const navRef = React.createRef<NavigationContainerRef<RootStackParamList>>();

const VIEW_TO_SCREEN: Record<string, keyof RootStackParamList> = {
  teacher: 'Teacher',
  feature: 'Feature',
  research: 'Research',
  security: 'Security',
  loop: 'Loop',
  debug: 'Debug',
  history: 'History',
  systemPrompts: 'SystemPrompts',
  memory: 'Memory',
  files: 'Files',
  skills: 'Skills',
  audio: 'Audio',
  traces: 'Traces',
  providers: 'Providers',
  connection: 'Connection',
  modes: 'Modes',
  prompts: 'Prompts',
};

const PANEL_SCREENS: { name: keyof RootStackParamList; component: React.ComponentType }[] = [
  { name: 'Teacher', component: TeacherScreen },
  { name: 'Feature', component: FeatureScreen },
  { name: 'Research', component: ResearchScreen },
  { name: 'Security', component: SecurityScreen },
  { name: 'Loop', component: LoopScreen },
  { name: 'Debug', component: DebugScreen },
  { name: 'History', component: HistoryScreen },
  { name: 'SystemPrompts', component: SystemPromptsScreen },
  { name: 'Memory', component: MemoryScreen },
  { name: 'Files', component: FilesScreen },
  { name: 'Skills', component: SkillsScreen },
  { name: 'Audio', component: AudioScreen },
  { name: 'Traces', component: TracesScreen },
  { name: 'Providers', component: ProvidersScreen },
  { name: 'Connection', component: ConnectionScreen },
  { name: 'Modes', component: ModesScreen },
  { name: 'Prompts', component: PromptsScreen },
];

function ChatScreenWithChrome() {
  const [activeView, setActiveView] = useState<ViewPanel>('chat');
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  const handleViewChange = (view: ViewPanel) => {
    if (view === 'chat') {
      setActiveView('chat');
      return;
    }
    const screen = VIEW_TO_SCREEN[view];
    if (screen) {
      setActiveView(view);
      navRef.current?.navigate(screen);
    }
  };

  return (
    <View style={{ flex: 1 }}>
      <Header
        activeView={activeView}
        onViewChange={handleViewChange}
        onOpenSessions={() => setSessionsOpen(true)}
        onOpenInspector={() => setInspectorOpen(true)}
      />
      <ChatScreen />
      <SessionsDrawer visible={sessionsOpen} onClose={() => setSessionsOpen(false)} />
      <InspectorDrawer visible={inspectorOpen} onClose={() => setInspectorOpen(false)} />
    </View>
  );
}

export function AppNavigator() {
  const { colors, isDark } = useTheme();

  const navTheme = isDark ? DarkTheme : DefaultTheme;
  navTheme.colors.background = colors.bg;
  navTheme.colors.card = colors.bgLift;
  navTheme.colors.border = colors.border;
  navTheme.colors.text = colors.text;

  return (
    <NavigationContainer ref={navRef} theme={navTheme}>
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.bgLift },
          headerTintColor: colors.text,
          headerShadowVisible: false,
          headerBackTitle: 'Back',
        }}
      >
        <Stack.Screen name="Chat" component={ChatScreenWithChrome} options={{ headerShown: false }} />
        {PANEL_SCREENS.map(({ name, component: Comp }) => (
          <Stack.Screen key={name} name={name} component={Comp} options={{ title: name }} />
        ))}
      </Stack.Navigator>
    </NavigationContainer>
  );
}