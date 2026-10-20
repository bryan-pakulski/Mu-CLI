// The app's navigation handle, for code outside a screen (notifications, deep links, sheets).

import React from 'react';
import type { NavigationContainerRef } from '@react-navigation/native';
import type { RootStackParamList, TabName } from './types';

export const navRef = React.createRef<NavigationContainerRef<RootStackParamList>>();

export const TAB_ROUTES = new Set<string>(['Chat', 'Work', 'Orchestration', 'Environments', 'Insights', 'More']);

/** Navigate from anywhere: tab routes go through the tab shell. */
export function navigateTo(name: string, params?: object) {
  const nav = navRef.current;
  if (!nav) return;
  const go = nav.navigate as unknown as (n: string, p?: object) => void;
  if (TAB_ROUTES.has(name)) go('Tabs', { screen: name as TabName, params });
  else go(name, params);
}
