// Insights (documentation/plans/mobile_parity.md §6 Insights): Runs, Jobs and Costs as header tabs
// of the tab's page. A job's analysis opens Work's JobAnalysis screen.

import React from 'react';
import { InsightsScreen } from '../../screens/insights/InsightsScreen';
import { Stage } from '../../ui/Glass';
import type { ScreenSpec, TabSpec } from '../types';

export type InsightsParams = Record<never, never>;

const InsightsTab = (props: any) => <Stage><InsightsScreen {...props} /></Stage>;

export const insightsTab: TabSpec = { name: 'Insights', component: InsightsTab, label: 'Insights', icon: 'activity', destination: 'insights', headerShown: false };

export const insightsScreens: ScreenSpec[] = [];
