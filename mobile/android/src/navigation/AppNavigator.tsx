// The app shell (documentation/plans/mobile_parity.md §3): five destinations — the web rail's — in a
// top bar (TopBar: icons plus one Options menu) over a root stack. Each destination owns a route module (routes/<destination>.tsx); this file
// only assembles them. Switching tab sets the destination, whose accent marks the tab and whose
// watercolour cross-fades behind the app (ThemeContext / ui/Glass).

import React, { useEffect, useState } from 'react';
import { DarkTheme, DefaultTheme, NavigationContainer, type LinkingOptions } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator, type BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, fontWeights, type DestinationName } from '../theme/tokens';
import { useConnectionStore } from '../store/connection';
import { useOrchBadge } from '../store/orchBadge';
import { useWorkBadge } from '../store/workBadge';
import { NeedsYouNotifier } from '../components/NeedsYouNotifier';
import { OrchestrationNotifier } from '../components/OrchestrationNotifier';
import { Banner, ToastHost, textStyle } from '../ui';
import { GlassChrome, glassStyle } from '../ui/Glass';
import { TopBarContext } from '../ui/Mu';
import { TopBar, type TabBadges } from './TopBar';
import { useBarPage } from '../store/barPage';
import { navRef, navigateTo } from './nav';
import { useNotificationTaps } from './notifications';
import type { RootStackParamList, ScreenSpec, TabParamList, TabSpec } from './types';
import { chatScreens, chatTab } from './routes/chat';
import { workScreens, workTab } from './routes/work';
import { orchestrationScreens, orchestrationTab } from './routes/orchestration';
import { environmentsScreens, environmentsTab } from './routes/environments';
import { insightsScreens, insightsTab } from './routes/insights';
import { moreScreens } from './routes/more';

export type { RootStackParamList, TabParamList } from './types';
export { navigateTo, TAB_ROUTES } from './nav';

export const TABS: TabSpec[] = [chatTab, workTab, orchestrationTab, environmentsTab, insightsTab];
export const SCREENS: ScreenSpec[] = [...chatScreens, ...workScreens, ...orchestrationScreens, ...environmentsScreens, ...insightsScreens, ...moreScreens];
export const TAB_DESTINATION: Record<string, DestinationName> = Object.fromEntries(TABS.map(t => [t.name, t.destination]));

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<TabParamList>();

export const linking: LinkingOptions<RootStackParamList> = {
  prefixes: ['mucli://'],
  config: {
    screens: {
      Pair: 'pair',
      JobDetail: 'work/job/:jobId',
      OrchRun: 'orchestration/run/:runId',
      OrchNode: 'orchestration/node/:nodeId',
      OrchTool: { path: 'orchestration/tool/:toolId/:version', parse: { version: Number } },
      OrchRunbook: { path: 'orchestration/runbook/:runbookId/:version', parse: { version: Number } },
      EnvironmentDetail: 'environments/env/:name',
      TemplateDetail: 'environments/template/:name',
      Shell: 'environments/terminal/:container',
      Tabs: { screens: { Chat: 'chat', Work: 'work/:tab?', Orchestration: 'orchestration/:tab?', Environments: 'environments', Insights: 'insights/:tab?' } },
    },
  },
};

const HeaderGlass = () => <GlassChrome level={1} edges={false} style={{ flex: 1 }} />;

/** Shown over every tab while the server answers but this phone isn't paired (§7a). */
function PairingBanner() {
  const { isConnected, pairingRequired } = useConnectionStore();
  if (!isConnected || !pairingRequired) return null;
  return (
    <View style={{ paddingHorizontal: 12, paddingTop: 8 }}>
      <Banner tone="attention" title="This phone isn't paired" action={{ label: 'Pair', onPress: () => navigateTo('Pair'), testID: 'pairing-banner-pair' }} testID="pairing-banner">
        Pair it from Settings → Devices on the computer to sign in.
      </Banner>
    </View>
  );
}

/** Hands the tab navigator's state to the top bar (drawn above the tabs, not below them). */
function TabStateBridge({ onState, ...props }: BottomTabBarProps & { onState: (p: BottomTabBarProps) => void }) {
  useEffect(() => { onState(props as BottomTabBarProps); }, [props.state]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

/** Tab pages hand their title and options to the top bar through this (PageHeader). */
const TOP_BAR = { registerPage: useBarPage.getState().register };

function MainTabs() {
  const { colors, setDestination } = useTheme();
  const orchCount = useOrchBadge(state => state.count);
  const orchSummary = useOrchBadge(state => state.summary);
  const workCount = useWorkBadge(state => state.count);
  const workSummary = useWorkBadge(state => state.summary);
  const badges: TabBadges = {
    Work: { count: workCount, summary: workSummary },
    Orchestration: { count: orchCount, summary: orchSummary },
  };
  const [bar, setBar] = useState<BottomTabBarProps | null>(null);
  return (
    <TopBarContext.Provider value={TOP_BAR}>
      <View style={{ flex: 1 }}>
        {bar ? <TopBar state={bar.state} navigation={bar.navigation} tabs={TABS} badges={badges} /> : null}
        <PairingBanner />
        <Tab.Navigator
          sceneContainerStyle={{ backgroundColor: 'transparent' }}
          tabBar={props => <TabStateBridge {...props} onState={setBar} />}
          screenListeners={({ route }) => ({ focus: () => { setDestination(TAB_DESTINATION[route.name] || 'chat'); } })}
          screenOptions={({ route }) => {
            const spec = TABS.find(t => t.name === route.name) as TabSpec;
            return {
              headerShown: spec.headerShown !== false,
              title: spec.label,
              headerStyle: { backgroundColor: 'transparent' },
              headerBackground: HeaderGlass,
              headerTintColor: colors.ink,
              headerShadowVisible: false,
              headerTitleStyle: { ...textStyle('title3'), color: colors.plum },
            };
          }}
        >
          {TABS.map(spec => <Tab.Screen key={spec.name} name={spec.name} component={spec.component} />)}
        </Tab.Navigator>
      </View>
    </TopBarContext.Provider>
  );
}

export function AppNavigator() {
  const { colors, isDark, flat, glass } = useTheme();
  useNotificationTaps();
  const baseTheme = isDark ? DarkTheme : DefaultTheme;
  // The navigator is transparent so the watercolour behind it shows; screens sit on the stage.
  const stage = glassStyle(1, colors, { flat: true, solid: flat, strength: glass / 100 });
  const headerFill = glassStyle(2, colors, { flat: true, solid: flat, strength: glass / 100 });
  const navTheme = {
    ...baseTheme,
    colors: { ...baseTheme.colors, background: 'transparent', card: 'transparent', border: colors.hairline, text: colors.ink, primary: colors.accent.ink },
  };
  return (
    <ToastHost>
      <NeedsYouNotifier />
      <OrchestrationNotifier />
      <NavigationContainer ref={navRef} theme={navTheme} linking={linking}>
        <Stack.Navigator
          screenOptions={{
            animation: 'slide_from_right',
            contentStyle: stage,
            headerStyle: { backgroundColor: headerFill.backgroundColor as string },
            headerTintColor: colors.ink,
            headerShadowVisible: false,
            headerBackTitle: '',
            headerTitleStyle: { fontSize: fontSizes.md, fontWeight: fontWeights.semibold },
          }}
        >
          <Stack.Screen name="Tabs" component={MainTabs} options={{ headerShown: false, contentStyle: { backgroundColor: 'transparent' } }} />
          {SCREENS.map(spec => (
            <Stack.Screen key={spec.name} name={spec.name as keyof RootStackParamList} component={spec.component} options={spec.options as any} />
          ))}
        </Stack.Navigator>
      </NavigationContainer>
    </ToastHost>
  );
}
