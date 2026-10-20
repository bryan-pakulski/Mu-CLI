// Environments (documentation/plans/mobile_parity.md §6 Environments): environments and templates,
// an environment's page, a template's page, the create / configure / clone form and the terminal.
// Every screen draws its own PageHeader (with a back link), so the native header stays hidden.

import React from 'react';
import { EnvironmentsScreen } from '../../screens/environments/EnvironmentsScreen';
import { EnvironmentDetailScreen } from '../../screens/environments/EnvironmentDetailScreen';
import { EnvironmentFormScreen, type EnvironmentFormMode } from '../../screens/environments/EnvironmentFormScreen';
import { TemplateDetailScreen } from '../../screens/environments/TemplateDetailScreen';
import { TerminalScreen } from '../../screens/environments/TerminalScreen';
import { Stage } from '../../ui/Glass';
import type { ScreenSpec, TabSpec } from '../types';

export type EnvironmentsParams = {
  EnvironmentDetail: { name: string };
  TemplateDetail: { name: string };
  EnvironmentForm: { mode: EnvironmentFormMode; name?: string; template?: string };
  /** The terminal; without a container it uses the active session's environment. */
  Shell: { container?: string } | undefined;
};

const EnvironmentsTab = (props: any) => <Stage><EnvironmentsScreen {...props} /></Stage>;

export const environmentsTab: TabSpec = { name: 'Environments', component: EnvironmentsTab, label: 'Environments', icon: 'box', destination: 'environments', headerShown: false };

const own = { headerShown: false };

export const environmentsScreens: ScreenSpec[] = [
  { name: 'EnvironmentDetail', component: EnvironmentDetailScreen, options: own },
  { name: 'TemplateDetail', component: TemplateDetailScreen, options: own },
  { name: 'EnvironmentForm', component: EnvironmentFormScreen, options: own },
  { name: 'Shell', component: TerminalScreen, options: own },
];
