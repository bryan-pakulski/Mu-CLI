import React from 'react';
import { NavigationContainer, DarkTheme, DefaultTheme } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';

import { Header } from '../components/Header';

import { ChatScreen } from '../screens/ChatScreen';
import { SessionsScreen } from '../screens/SessionsScreen';
import { SessionDetailScreen } from '../screens/SessionDetailScreen';
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
import { InspectorScreen } from '../screens/InspectorScreen';
import { TeacherScreen } from '../screens/TeacherScreen';
import { FeatureScreen } from '../screens/FeatureScreen';
import { ResearchScreen } from '../screens/ResearchScreen';
import { SecurityScreen } from '../screens/SecurityScreen';
import { LoopScreen } from '../screens/LoopScreen';
import { DebugScreen } from '../screens/DebugScreen';
import { HistoryScreen } from '../screens/HistoryScreen';

import type { BottomTabNavigationProp } from '@react-navigation/bottom-tabs';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';

// ── Types ──────────────────────────────────────────────────────────
export type ChatStackParamList = {
  Chat: undefined;
  Modes: undefined;
  Prompts: undefined;
  SystemPrompts: undefined;
  Inspector: undefined;
  Teacher: undefined;
  Feature: undefined;
  Research: undefined;
  Security: undefined;
  Loop: undefined;
  Debug: undefined;
  History: undefined;
};

export type SessionsStackParamList = {
  Sessions: undefined;
  SessionDetail: undefined;
};

export type ToolsStackParamList = {
  Memory: undefined;
  Files: undefined;
  Skills: undefined;
  Audio: undefined;
};

export type RunStackParamList = {
  Traces: undefined;
};

export type DataStackParamList = {
  Providers: undefined;
  Connection: undefined;
};

export type RootTabParamList = {
  Chat: undefined;
  Sessions: undefined;
  Tools: undefined;
  Run: undefined;
  Data: undefined;
};

// ── Stack navigators per tab ───────────────────────────────────────
const ChatStack = createNativeStackNavigator<ChatStackParamList>();
const SessionsStack = createNativeStackNavigator<SessionsStackParamList>();
const ToolsStack = createNativeStackNavigator<ToolsStackParamList>();
const RunStack = createNativeStackNavigator<RunStackParamList>();
const DataStack = createNativeStackNavigator<DataStackParamList>();
const Tab = createBottomTabNavigator<RootTabParamList>();

function ChatStackNavigator() {
  return (
    <ChatStack.Navigator>
      <ChatStack.Screen name="Chat" component={ChatScreen} options={{ headerShown: false }} />
      <ChatStack.Screen name="Modes" component={ModesScreen} />
      <ChatStack.Screen name="Prompts" component={PromptsScreen} />
      <ChatStack.Screen name="SystemPrompts" component={SystemPromptsScreen} />
      <ChatStack.Screen name="Inspector" component={InspectorScreen} />
      <ChatStack.Screen name="Teacher" component={TeacherScreen} />
      <ChatStack.Screen name="Feature" component={FeatureScreen} />
      <ChatStack.Screen name="Research" component={ResearchScreen} />
      <ChatStack.Screen name="Security" component={SecurityScreen} />
      <ChatStack.Screen name="Loop" component={LoopScreen} />
      <ChatStack.Screen name="Debug" component={DebugScreen} />
      <ChatStack.Screen name="History" component={HistoryScreen} />
    </ChatStack.Navigator>
  );
}

function SessionsStackNavigator() {
  return (
    <SessionsStack.Navigator>
      <SessionsStack.Screen name="Sessions" component={SessionsScreen} options={{ headerShown: false }} />
      <SessionsStack.Screen name="SessionDetail" component={SessionDetailScreen} />
    </SessionsStack.Navigator>
  );
}

function ToolsStackNavigator() {
  return (
    <ToolsStack.Navigator>
      <ToolsStack.Screen name="Memory" component={MemoryScreen} options={{ headerShown: false }} />
      <ToolsStack.Screen name="Files" component={FilesScreen} />
      <ToolsStack.Screen name="Skills" component={SkillsScreen} />
      <ToolsStack.Screen name="Audio" component={AudioScreen} />
    </ToolsStack.Navigator>
  );
}

function RunStackNavigator() {
  return (
    <RunStack.Navigator>
      <RunStack.Screen name="Traces" component={TracesScreen} options={{ headerShown: false }} />
    </RunStack.Navigator>
  );
}

function DataStackNavigator() {
  return (
    <DataStack.Navigator>
      <DataStack.Screen name="Providers" component={ProvidersScreen} options={{ headerShown: false }} />
      <DataStack.Screen name="Connection" component={ConnectionScreen} />
    </DataStack.Navigator>
  );
}

// ── Tab icons ──────────────────────────────────────────────────────
const tabIcons: Record<keyof RootTabParamList, { icon: keyof typeof Ionicons.glyphMap; label: string }> = {
  Chat: { icon: 'chatbubble-outline', label: 'Chat' },
  Sessions: { icon: 'folder-outline', label: 'Sessions' },
  Tools: { icon: 'construct-outline', label: 'Tools' },
  Run: { icon: 'analytics-outline', label: 'Run' },
  Data: { icon: 'settings-outline', label: 'Data' },
};

// ── App Navigator ──────────────────────────────────────────────────
export function AppNavigator() {
  const { colors, isDark } = useTheme();

  const navTheme = isDark ? DarkTheme : DefaultTheme;
  navTheme.colors.background = colors.bg;
  navTheme.colors.card = colors.bgLift;
  navTheme.colors.border = colors.border;
  navTheme.colors.text = colors.text;

  return (
    <NavigationContainer theme={navTheme}>
      <Header />
      <Tab.Navigator
        screenOptions={({ route }) => ({
          tabBarIcon: ({ color, size }) => {
            const icon = tabIcons[route.name as keyof RootTabParamList]?.icon;
            return <Ionicons name={icon || 'circle-outline'} size={size} color={color} />;
          },
          tabBarActiveTintColor: colors.accent,
          tabBarInactiveTintColor: colors.textDim,
          tabBarStyle: { backgroundColor: colors.bgLift, borderTopColor: colors.border },
          headerShown: false,
        })}
      >
        <Tab.Screen name="Chat" component={ChatStackNavigator} />
        <Tab.Screen name="Sessions" component={SessionsStackNavigator} />
        <Tab.Screen name="Tools" component={ToolsStackNavigator} />
        <Tab.Screen name="Run" component={RunStackNavigator} />
        <Tab.Screen name="Data" component={DataStackNavigator} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}