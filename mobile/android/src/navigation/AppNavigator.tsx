import React, { useCallback, useEffect, useState } from 'react';
import { NavigationContainer, DarkTheme, DefaultTheme, NavigationContainerRef, type NavigatorScreenParams } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
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

import { ChatScreenProduct } from '../screens/ChatScreenProduct';
import { WorkspaceScreen } from '../screens/WorkspaceScreen';
import { WorkspaceCategoryScreen } from '../screens/WorkspaceCategoryScreen';
import { WorkScreen } from '../screens/WorkScreen';
import { JobDetailScreen } from '../screens/JobDetailScreen';
import { JobAnalysisScreen } from '../screens/JobAnalysisScreen';
import { JobsListScreen } from '../screens/JobsListScreen';
import { JobActivityScreen } from '../screens/JobActivityScreen';
import { JobBoardsScreen } from '../screens/JobBoardsScreen';
import { JobHistoryScreen } from '../screens/JobHistoryScreen';
import { JobSettingsScreen } from '../screens/JobSettingsScreen';
import { JobSetupScreen } from '../screens/JobSetupScreen';
import { JobDoctorScreen } from '../screens/JobDoctorScreen';
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
import { ThreadsScreen } from '../screens/ThreadsScreen';
import { ShellScreen } from '../screens/ShellScreen';
import { ArtifactsScreen } from '../screens/ArtifactsScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { MoreScreen } from '../screens/MoreScreen';
import { fontSizes, fontWeights, radii } from '../theme/tokens';
import { NeedsYouNotifier } from '../components/NeedsYouNotifier';
import { Icon, Pressable, Text, ToastHost, textStyle, type IconName } from '../ui';
import { GlassChrome, Stage, glassStyle, routeAmbient, useAmbientController, useGlass } from '../ui/Glass';

/** Bottom tabs (G§8.3): Chat, Work, Workspace, More. */
export type TabParamList = {
  Chat: undefined;
  Work: undefined;
  Workspace: undefined;
  More: undefined;
};

export type RootStackParamList = {
  /** The tab shell; navigate with `navigate('Tabs', { screen: 'Work' })`. */
  Tabs: NavigatorScreenParams<TabParamList> | undefined;
  Settings: undefined;
  // Tab routes, typed here so their screens share the root param list.
  Chat: undefined;
  Work: undefined;
  JobDetail: { jobId: string; tab?: string; action?: string };
  JobAnalysis: { jobId: string };
  JobsList: undefined;
  JobActivity: undefined;
  JobBoards: undefined;
  JobHistory: undefined;
  JobSettings: undefined;
  /** Setup with its two tabs: the checklist (default) and Doctor. */
  JobSetup: { tab?: 'checklist' | 'doctor' } | undefined;
  JobDoctor: undefined;
  Workspace: undefined;
  WorkspaceCategory: { categoryId: WorkspaceCategoryId; title: string };
  Teacher: undefined;
  Feature: undefined;
  Research: undefined;
  Security: undefined;
  Loop: undefined;
  Debug: undefined;
  History: undefined;
  Threads: undefined;
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

type JobsPageRoute = 'JobsList' | 'JobActivity' | 'JobBoards' | 'JobHistory' | 'JobSettings';

/** Wash jobs pages (CONTRACT §6: full screens, not dialogs). */
const JOBS_PAGE_SCREENS: { name: JobsPageRoute; title: string; component: React.ComponentType<any> }[] = [
  { name: 'JobsList', title: 'Jobs', component: JobsListScreen },
  { name: 'JobActivity', title: 'Activity', component: JobActivityScreen },
  { name: 'JobBoards', title: 'Tickets', component: JobBoardsScreen },
  { name: 'JobHistory', title: 'Job history', component: JobHistoryScreen },
  { name: 'JobSettings', title: 'Job settings', component: JobSettingsScreen },
];

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<TabParamList>();

/** Route names that live in the tab shell rather than the root stack. */
export const TAB_ROUTES = new Set<string>(['Chat', 'Work', 'Workspace', 'More']);

/** Navigate from anywhere: tab routes go through the tab shell. */
export function navigateTo(name: string, params?: object) {
  const nav = navRef.current;
  if (!nav) return;
  if (TAB_ROUTES.has(name)) (nav.navigate as unknown as (n: string, p?: object) => void)('Tabs', { screen: name });
  else (nav.navigate as unknown as (n: string, p?: object) => void)(name, params);
}
const navRef = React.createRef<NavigationContainerRef<RootStackParamList>>();

const PANEL_SCREENS: {
  name: Exclude<keyof RootStackParamList, 'Tabs' | 'Settings' | 'Chat' | 'Work' | 'JobDetail' | 'JobAnalysis' | 'JobSetup' | 'JobDoctor' | 'Workspace' | 'WorkspaceCategory' | JobsPageRoute>;
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
  { name: 'Threads', title: 'Agent conversations', component: ThreadsScreen },
  { name: 'SystemPrompts', title: 'System prompts', component: SystemPromptsScreen },
  { name: 'Memory', title: 'Memory Center', component: MemoryScreen },
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
        if (selected && response.loaded.includes(selected)) return;
        const current = response.current && response.loaded.includes(response.current)
          ? response.current
          : response.loaded[0] || null;
        setActiveSession(current);
      })
      .catch(() => {
        // The connection screen owns transport errors.
      });
    return () => controller.abort();
  }, [baseUrl, isConnected, setActiveSession]);

  return (
    <EdgeSwipeView onSwipeFromLeft={openSessions} onSwipeFromRight={openMode}>
      <View style={{ flex: 1, backgroundColor: 'transparent' }}>
        <ModernHeader
          onOpenSessions={openSessions}
          onOpenWork={() => isConnected ? navigateTo('Work') : navigateTo('Connection')}
          onOpenTraces={() => activeSessionName ? navigateTo('Traces') : openSessions()}
          onOpenConnection={() => navigateTo('Connection')}
          onOpenSettings={() => navigateTo('Settings')}
          onOpenContext={() => navigateTo('Memory')}
        />
        <Stage>
          {!isConnected ? (
            <ConnectionPrompt onConnect={() => navigateTo('Connection')} />
          ) : activeSessionName ? (
            <ChatScreenProduct />
          ) : (
            <SessionStartPrompt
              onLoadSession={openSessions}
              onCreateSession={createSession}
              onManageContainers={() => setContainersOpen(true)}
            />
          )}
        </Stage>
        <SwipeSessionsDrawer
          visible={sessionsOpen}
          onClose={() => setSessionsOpen(false)}
          createRequestToken={createRequestToken}
        />
        <ContainerManagerSheet visible={containersOpen} onClose={() => setContainersOpen(false)} />
        <ModeDrawer
          visible={Boolean(activeSessionName) && modeOpen}
          onClose={() => setModeOpen(false)}
          onOpenModes={() => navigateTo('Modes')}
        />
      </View>
    </EdgeSwipeView>
  );
}

const TAB_ICONS: Record<keyof TabParamList, IconName> = {
  Chat: 'message-circle',
  Work: 'briefcase',
  Workspace: 'layout-dashboard',
  More: 'more-horizontal',
};

/** Tab screens other than Chat sit on a full-bleed glass stage (Chat draws its own below the header). */
function staged<P extends object>(Screen: React.ComponentType<P>) {
  const StagedScreen = (props: P) => <Stage><Screen {...props} /></Stage>;
  StagedScreen.displayName = `Staged(${Screen.displayName || Screen.name || 'Screen'})`;
  return StagedScreen;
}

const WorkTab = staged(WorkScreen);
const WorkspaceTab = staged(WorkspaceScreen);
const MoreTab = staged(MoreScreen);

/** Glass chrome behind the tab bar and tab headers (G§3.3 level 1, blurred). */
const TabBarGlass = () => <GlassChrome level={1} edges={false} style={{ flex: 1 }} testID="tab-bar-glass" />;
const HeaderGlass = () => <GlassChrome level={1} edges={false} style={{ flex: 1 }} />;

/** Tab bar item (G§9.4 M): 24 icon, 12 label; active = accent icon, ink label, wash pill behind the icon. */
function TabIcon({ name, focused }: { name: keyof TabParamList; focused: boolean }) {
  const { colors } = useTheme();
  return (
    <View style={{ width: 56, height: 28, borderRadius: radii.pill, alignItems: 'center', justifyContent: 'center', backgroundColor: focused ? colors.accent.wash : 'transparent' }}>
      <Icon name={TAB_ICONS[name]} size="lg" color={focused ? colors.accent.ink : colors.ink3} />
    </View>
  );
}

function MainTabs() {
  const { colors } = useTheme();
  const glass = useGlass();
  return (
    <Tab.Navigator
      sceneContainerStyle={{ backgroundColor: 'transparent' }}
      screenOptions={({ route }) => ({
        tabBarIcon: ({ focused }) => <TabIcon name={route.name} focused={focused} />,
        tabBarActiveTintColor: colors.ink,
        tabBarInactiveTintColor: colors.ink3,
        tabBarLabelStyle: { ...textStyle('caption'), marginBottom: 2 },
        tabBarStyle: { backgroundColor: 'transparent', borderTopColor: glass.edge, minHeight: 64, paddingTop: 6, elevation: 0 },
        tabBarBackground: TabBarGlass,
        tabBarItemStyle: { minHeight: 44 },
        tabBarHideOnKeyboard: true,
        headerStyle: { backgroundColor: 'transparent' },
        headerBackground: HeaderGlass,
        headerTintColor: colors.ink,
        headerShadowVisible: false,
        headerTitleStyle: { ...textStyle('title3'), color: colors.plum },
      })}
    >
      <Tab.Screen name="Chat" component={ChatScreenWithChrome} options={{ headerShown: false, tabBarAccessibilityLabel: 'Chat', tabBarTestID: 'tab-chat' }} />
      <Tab.Screen name="Work" component={WorkTab} options={{ title: 'Engineering work', tabBarLabel: 'Work', tabBarAccessibilityLabel: 'Work', tabBarTestID: 'tab-work' }} />
      <Tab.Screen name="Workspace" component={WorkspaceTab} options={{ title: 'Workspace', tabBarAccessibilityLabel: 'Workspace', tabBarTestID: 'tab-workspace' }} />
      <Tab.Screen name="More" component={MoreTab} options={{ title: 'More', tabBarAccessibilityLabel: 'More', tabBarTestID: 'tab-more' }} />
    </Tab.Navigator>
  );
}

/** The deepest focused route name (tabs included) for the ambient wash. */
function focusedRouteName(state: { index?: number; routes: Array<{ name: string; state?: unknown }> } | undefined): string | null {
  if (!state || !state.routes?.length) return null;
  const route = state.routes[state.index ?? state.routes.length - 1];
  const nested = route.state as typeof state | undefined;
  return (nested ? focusedRouteName(nested) : null) || route.name;
}

export function AppNavigator() {
  const { colors, isDark, reduceDecoration } = useTheme();
  const { setRouteArt } = useAmbientController();
  const baseTheme = isDark ? DarkTheme : DefaultTheme;
  // Glass (G§3.3): the navigator is transparent so the ambient wash behind it
  // shows through; screens sit on a level-1 glass stage, headers on glass.
  const stage = glassStyle(1, colors, { flat: true, solid: reduceDecoration });
  const headerGlass = glassStyle(2, colors, { flat: true, solid: reduceDecoration });
  const syncAmbient = useCallback(() => {
    const state = navRef.current?.getRootState() as Parameters<typeof focusedRouteName>[0];
    setRouteArt(routeAmbient(focusedRouteName(state) || 'Chat'));
  }, [setRouteArt]);
  const navTheme = {
    ...baseTheme,
    colors: {
      ...baseTheme.colors,
      background: 'transparent',
      card: 'transparent',
      border: colors.hairline,
      text: colors.ink,
      primary: colors.accent.ink,
    },
  };

  return (
    <ToastHost>
    <NeedsYouNotifier />
    <NavigationContainer ref={navRef} theme={navTheme} onReady={syncAmbient} onStateChange={syncAmbient}>
      <Stack.Navigator
        screenOptions={{
          animation: 'slide_from_right',
          contentStyle: stage,
          headerStyle: { backgroundColor: headerGlass.backgroundColor as string },
          headerTintColor: colors.ink,
          headerShadowVisible: false,
          headerBackTitle: '',
          headerTitleStyle: { fontSize: fontSizes.md, fontWeight: fontWeights.semibold },
        }}
      >
        <Stack.Screen name="Tabs" component={MainTabs} options={{ headerShown: false, contentStyle: { backgroundColor: 'transparent' } }} />
        <Stack.Screen name="Settings" component={SettingsScreen} options={{ title: 'Settings' }} />
        <Stack.Screen
          name="JobDetail"
          component={JobDetailScreen}
          options={({ navigation, route }) => ({
            title: 'Job review',
            headerRight: () => (
              <Pressable accessibilityRole="button" accessibilityLabel="Analyze job performance" onPress={() => navigation.navigate('JobAnalysis', { jobId: route.params.jobId })} hitSlop={14} style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1, minHeight: 44, justifyContent: 'center' })}>
                <Text style={{ color: colors.accent.ink, fontSize: fontSizes.xs, fontWeight: fontWeights.semibold }}>Analyze</Text>
              </Pressable>
            ),
          })}
        />
        <Stack.Screen name="JobAnalysis" component={JobAnalysisScreen} options={{ title: 'Job performance' }} />
        <Stack.Screen name="JobSetup" component={JobSetupScreen} options={{ title: 'Jobs setup' }} />
        <Stack.Screen name="JobDoctor" component={JobDoctorScreen} options={{ title: 'Jobs doctor' }} />
        {JOBS_PAGE_SCREENS.map(({ name, title, component }) => (
          // Jobs-mode tabs: switched in place (switchJobsPage) with a fade, not a history slide.
          <Stack.Screen key={name} name={name} component={component} options={{ title, animation: 'fade' }} />
        ))}
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
    </ToastHost>
  );
}
