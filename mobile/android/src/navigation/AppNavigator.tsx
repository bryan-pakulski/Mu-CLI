import React, { useCallback, useEffect, useState } from 'react';
import { NavigationContainer, DarkTheme, DefaultTheme, NavigationContainerRef } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import type { WorkspaceCategoryId } from './workspace';

import { ConnectionPrompt } from '../components/ConnectionPrompt';
import { ContainerManagerSheet } from '../components/ContainerManagerSheet';
import { EdgeSwipeView } from '../components/EdgeSwipeView';
import { ModeDrawer } from '../components/ModeDrawer';
import { ModernHeader } from '../components/ModernHeader';
import { SessionStartPrompt } from '../components/SessionStartPrompt';
import { SwipeSessionsDrawer } from '../components/SwipeSessionsDrawer';
import { sessionsApi } from '../api/sessions';
import { useConnectionStore } from '../store/connection';

import { ChatScreen } from '../screens/ChatScreen';
import { WorkspaceScreen } from '../screens/WorkspaceScreen';
import { WorkspaceCategoryScreen } from '../screens/WorkspaceCategoryScreen';
import { MemoryScreen } from '../screens/MemoryScreen';
import { FilesScreen } from '../screens/FilesScreen';
import { SkillsScreen } from '../screens/SkillsScreen';
import { AudioScreen } from '../screens/AudioScreen';
import { SessionTraceScreenV2 } from '../screens/SessionTraceScreenV2';
import { ProvidersScreen } from '../screens/ProvidersScreen';
import { ConnectionScreen } from '../screens/ConnectionScreen';
import { ModesScreen } from '../screens/ModesScreen';
import { PromptsScreen } from '../screens/PromptsScreen';
import { SystemPromptsScreen } from '../screens/SystemPromptsScreen';
import { TeacherScreen } from '../screens/TeacherScreen';
import { FeatureExplorerScreen } from '../screens/FeatureExplorerScreen';
import { ResearchScreen } from '../screens/ResearchScreen';
import { SecurityScreen } from '../screens/SecurityScreen';
import { LoopScreen } from '../screens/LoopScreen';
import { DebugScreen } from '../screens/DebugScreen';
import { HistoryScreen } from '../screens/HistoryScreen';
import { ShellScreen } from '../screens/ShellScreen';
import { ArtifactsScreen } from '../screens/ArtifactsScreen';

export type RootStackParamList = {
  Chat: undefined;
  Workspace: undefined;
  WorkspaceCategory: { categoryId: WorkspaceCategoryId; title: string };
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
  Shell: undefined;
  Artifacts: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const navRef = React.createRef<NavigationContainerRef<RootStackParamList>>();

const PANEL_SCREENS: {
  name: Exclude<keyof RootStackParamList, 'Chat' | 'Workspace' | 'WorkspaceCategory'>;
  title: string;
  component: React.ComponentType;
}[] = [
  { name: 'Teacher', title: 'Teacher', component: TeacherScreen },
  { name: 'Feature', title: 'Feature plans', component: FeatureExplorerScreen },
  { name: 'Research', title: 'Research', component: ResearchScreen },
  { name: 'Security', title: 'Security', component: SecurityScreen },
  { name: 'Loop', title: 'Loop', component: LoopScreen },
  { name: 'Debug', title: 'Debug', component: DebugScreen },
  { name: 'History', title: 'History', component: HistoryScreen },
  { name: 'SystemPrompts', title: 'System prompts', component: SystemPromptsScreen },
  { name: 'Memory', title: 'Memory', component: MemoryScreen },
  { name: 'Files', title: 'Files', component: FilesScreen },
  { name: 'Skills', title: 'Skills', component: SkillsScreen },
  { name: 'Audio', title: 'Audio', component: AudioScreen },
  { name: 'Traces', title: 'Session trace', component: SessionTraceScreenV2 },
  { name: 'Providers', title: 'Providers', component: ProvidersScreen },
  { name: 'Connection', title: 'Connection', component: ConnectionScreen },
  { name: 'Modes', title: 'Modes', component: ModesScreen },
  { name: 'Prompts', title: 'Pending prompts', component: PromptsScreen },
  { name: 'Shell', title: 'Shell', component: ShellScreen },
  { name: 'Artifacts', title: 'Session files', component: ArtifactsScreen },
];

function ChatScreenWithChrome() {
  const isConnected = useConnectionStore(state => state.isConnected);
  const baseUrl = useConnectionStore(state => state.baseUrl);
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const setActiveSession = useConnectionStore(state => state.setActiveSession);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);
  const [containersOpen, setContainersOpen] = useState(false);
  const [createRequestToken, setCreateRequestToken] = useState(0);

  const openSessions = useCallback(() => setSessionsOpen(true), []);
  const openMode = useCallback(() => {
    if (activeSessionName) setModeOpen(true);
  }, [activeSessionName]);
  const createSession = useCallback(() => {
    setSessionsOpen(false);
    setCreateRequestToken(value => value + 1);
  }, []);

  useEffect(() => {
    if (!isConnected) return;
    const controller = new AbortController();
    sessionsApi.list({ signal: controller.signal, timeoutMs: 8_000 })
      .then(response => {
        if (controller.signal.aborted) return;
        const selected = useConnectionStore.getState().activeSessionName;
        // Keep a valid user-selected session. Bootstrap from server focus only
        // when mobile has no usable selection; do not run a focus tug-of-war.
        if (selected && response.loaded.includes(selected)) return;
        const current = response.current && response.loaded.includes(response.current)
          ? response.current
          : response.loaded[0] || null;
        setActiveSession(current);
      })
      .catch(() => {
        // The connection screen owns transport errors. A timeout must not
        // replace the navigator or clear a previously usable session.
      });
    return () => controller.abort();
  }, [baseUrl, isConnected, setActiveSession]);

  return (
    <EdgeSwipeView onSwipeFromLeft={openSessions} onSwipeFromRight={openMode}>
      <View style={{ flex: 1 }}>
        <ModernHeader
          onOpenSessions={openSessions}
          onOpenWorkspace={() => activeSessionName ? navRef.current?.navigate('Workspace') : openSessions()}
          onOpenTraces={() => activeSessionName ? navRef.current?.navigate('Traces') : openSessions()}
          onOpenConnection={() => navRef.current?.navigate('Connection')}
          onOpenModes={() => activeSessionName ? navRef.current?.navigate('Modes') : openSessions()}
          onOpenProviders={() => navRef.current?.navigate('Providers')}
          onOpenArtifacts={() => activeSessionName ? navRef.current?.navigate('Artifacts') : openSessions()}
          onOpenContainers={() => { /* MUCLI_MOBILE_CONTAINER_MENU_V1: active-session access. */ setContainersOpen(true); }}
        />
        {!isConnected ? (
          <ConnectionPrompt onConnect={() => navRef.current?.navigate('Connection')} />
        ) : activeSessionName ? (
          <ChatScreen />
        ) : (
          <SessionStartPrompt
            onLoadSession={openSessions}
            onCreateSession={createSession}
            onManageContainers={() => setContainersOpen(true)}
          />
        )}
        <SwipeSessionsDrawer
          visible={sessionsOpen}
          onClose={() => setSessionsOpen(false)}
          createRequestToken={createRequestToken}
        />
        <ContainerManagerSheet visible={containersOpen} onClose={() => setContainersOpen(false)} />
        <ModeDrawer
          visible={Boolean(activeSessionName) && modeOpen}
          onClose={() => setModeOpen(false)}
          onOpenModes={() => navRef.current?.navigate('Modes')}
        />
      </View>
    </EdgeSwipeView>
  );
}

export function AppNavigator() {
  const { colors, isDark } = useTheme();
  const baseTheme = isDark ? DarkTheme : DefaultTheme;
  const navTheme = {
    ...baseTheme,
    colors: {
      ...baseTheme.colors,
      background: colors.bg,
      card: colors.bg,
      border: colors.border,
      text: colors.text,
      primary: colors.accent,
    },
  };

  return (
    <NavigationContainer ref={navRef} theme={navTheme}>
      <Stack.Navigator
        screenOptions={{
          animation: 'slide_from_right',
          contentStyle: { backgroundColor: colors.bg },
          headerStyle: { backgroundColor: colors.bg },
          headerTintColor: colors.text,
          headerShadowVisible: false,
          headerBackTitle: '',
          headerTitleStyle: { fontSize: 17, fontWeight: '600' },
        }}
      >
        <Stack.Screen name="Chat" component={ChatScreenWithChrome} options={{ headerShown: false }} />
        <Stack.Screen name="Workspace" component={WorkspaceScreen} options={{ title: 'Workspace' }} />
        <Stack.Screen
          name="WorkspaceCategory"
          component={WorkspaceCategoryScreen}
          options={({ route }: { route: { params: RootStackParamList['WorkspaceCategory'] } }) => ({ title: route.params.title })}
        />
        {PANEL_SCREENS.map(({ name, title, component: Comp }) => (
          <Stack.Screen key={name} name={name} component={Comp} options={{ title }} />
        ))}
      </Stack.Navigator>
    </NavigationContainer>
  );
}