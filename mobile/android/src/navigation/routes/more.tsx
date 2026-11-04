// App screens reached from the top bar's Options menu (TopBar appOptions): Settings (Appearance,
// Areas, Devices), Connection, Provider and model, and pairing.

import React from 'react';
import { SettingsScreen } from '../../screens/SettingsScreen';
import { ConnectionScreen } from '../../screens/ConnectionScreen';
import { ProvidersScreen } from '../../screens/ProvidersScreen';
import { PairScreen } from '../../screens/PairScreen';
import type { ScreenSpec } from '../types';

export type MoreParams = {
  Settings: undefined;
  Connection: undefined;
  Providers: undefined;
  Pair: { url?: string; code?: string } | undefined;
};


export const moreScreens: ScreenSpec[] = [
  // Each draws its own PageHeader (with the back link), so the navigator header stays hidden.
  { name: 'Settings', component: SettingsScreen, options: { title: 'Settings', headerShown: false } },
  { name: 'Connection', component: ConnectionScreen, options: { title: 'Connection', headerShown: false } },
  { name: 'Providers', component: ProvidersScreen, options: { title: 'Provider and model', headerShown: false } },
  { name: 'Pair', component: PairScreen, options: { title: 'Pair with mucli', headerShown: false } },
];
