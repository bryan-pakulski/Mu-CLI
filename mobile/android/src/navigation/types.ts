// Navigation types (documentation/plans/mobile_parity.md §3).
//
// Six tabs, the web's destinations: Chat · Work · Orchestration · Environments · Insights · More.
// Each destination owns one route module (routes/<destination>.tsx) with its tab, its stack screens
// and its param types; RootStackParamList is their intersection.

import type React from 'react';
import type { NavigatorScreenParams } from '@react-navigation/native';
import type { IconName } from '../ui';
import type { DestinationName } from '../theme/tokens';
import type { ChatParams } from './routes/chat';
import type { WorkParams } from './routes/work';
import type { OrchestrationParams } from './routes/orchestration';
import type { EnvironmentsParams } from './routes/environments';
import type { InsightsParams } from './routes/insights';
import type { MoreParams } from './routes/more';

export type TabParamList = {
  Chat: { openSettings?: number } | undefined;
  Work: { tab?: string } | undefined;
  Orchestration: { tab?: string } | undefined;
  Environments: undefined;
  Insights: { tab?: 'runs' | 'jobs' | 'costs' } | undefined;
  More: undefined;
};

export type TabName = keyof TabParamList;

export type RootStackParamList =
  { Tabs: NavigatorScreenParams<TabParamList> | undefined }
  & TabParamList
  & ChatParams & WorkParams & OrchestrationParams & EnvironmentsParams & InsightsParams & MoreParams;

export type ScreenSpec = {
  name: string;
  component: React.ComponentType<any>;
  options?: object | ((props: { route: any; navigation: any }) => object);
};

export type TabSpec = {
  name: TabName;
  component: React.ComponentType<any>;
  /** Tab label. */
  label: string;
  icon: IconName;
  /** Whose accent and watercolour the tab shows. */
  destination: DestinationName;
  /** The tab draws its own header (PageHeader); the navigator header stays hidden. */
  headerShown?: boolean;
};
