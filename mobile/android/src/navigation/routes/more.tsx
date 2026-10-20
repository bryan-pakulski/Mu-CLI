// More (documentation/plans/mobile_parity.md §3): Settings (Appearance, Areas, Devices),
// Connection and Providers, and pairing.

import React from 'react';
import { MoreScreen } from '../../screens/MoreScreen';
import { SettingsScreen } from '../../screens/SettingsScreen';
import { ConnectionScreen } from '../../screens/ConnectionScreen';
import { ProvidersScreen } from '../../screens/ProvidersScreen';
import { PairScreen } from '../../screens/PairScreen';
import { Stage } from '../../ui/Glass';
import type { ScreenSpec, TabSpec } from '../types';

export type MoreParams = {
  Settings: undefined;
  Connection: undefined;
  Providers: undefined;
  Pair: { url?: string; code?: string } | undefined;
};

const MoreTab = (props: any) => <Stage><MoreScreen {...props} /></Stage>;

export const moreTab: TabSpec = { name: 'More', component: MoreTab, label: 'More', icon: 'more-horizontal', destination: 'settings', headerShown: false };

export const moreScreens: ScreenSpec[] = [
  // Each draws its own PageHeader (with the back link), so the navigator header stays hidden.
  { name: 'Settings', component: SettingsScreen, options: { title: 'Settings', headerShown: false } },
  { name: 'Connection', component: ConnectionScreen, options: { title: 'Connection', headerShown: false } },
  { name: 'Providers', component: ProvidersScreen, options: { title: 'Provider and model', headerShown: false } },
  { name: 'Pair', component: PairScreen, options: { title: 'Pair with mucli', headerShown: false } },
];
