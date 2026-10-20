// Orchestration (documentation/plans/mobile_parity.md §6 Orchestration): the tab (Now, Runs, Nodes,
// Catalog, Studio, Settings, and Ask — the fleet chat), and the pushed screens: a run, a node, a tool
// or runbook version, and the Studio editors. Every pushed screen draws its own PageHeader.

import React from 'react';
import { OrchestrationScreen } from '../../screens/orchestration/OrchestrationScreen';
import { OrchRunScreen } from '../../screens/orchestration/RunScreen';
import { OrchNodeScreen } from '../../screens/orchestration/NodeScreen';
import { OrchToolScreen } from '../../screens/orchestration/ToolScreen';
import { OrchRunbookScreen } from '../../screens/orchestration/RunbookScreen';
import { OrchToolEditorScreen } from '../../screens/orchestration/ToolEditorScreen';
import { OrchRunbookEditorScreen } from '../../screens/orchestration/RunbookEditorScreen';
import { Stage } from '../../ui/Glass';
import type { ScreenSpec, TabSpec } from '../types';

export type OrchestrationParams = {
  OrchRun: { runId: string; tab?: 'nodes' | 'plan' | 'results' | 'notes' };
  OrchNode: { nodeId: string };
  OrchTool: { toolId: string; version: number };
  OrchRunbook: { runbookId: string; version: number };
  OrchToolEditor: { toolId: string; version: number };
  OrchRunbookEditor: { runbookId: string; version: number };
};

const OrchestrationTab = (props: any) => <Stage><OrchestrationScreen {...props} /></Stage>;
const own = { headerShown: false };

export const orchestrationTab: TabSpec = { name: 'Orchestration', component: OrchestrationTab, label: 'Orchestration', icon: 'server', destination: 'orchestration', headerShown: false };

export const orchestrationScreens: ScreenSpec[] = [
  { name: 'OrchRun', component: OrchRunScreen, options: own },
  { name: 'OrchNode', component: OrchNodeScreen, options: own },
  { name: 'OrchTool', component: OrchToolScreen, options: own },
  { name: 'OrchRunbook', component: OrchRunbookScreen, options: own },
  { name: 'OrchToolEditor', component: OrchToolEditorScreen, options: own },
  { name: 'OrchRunbookEditor', component: OrchRunbookEditorScreen, options: own },
];
