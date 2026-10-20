// The Orchestration tab (web view.js): the page header — title, the fleet's state sentence, text tabs
// Now · Runs · Nodes · Catalog, Studio and Settings under ⋯, and Ask (the fleet chat) — over one view
// at a time. A run, node, tool or runbook opens as its own screen. Live via /api/orchestration/events.
// Route param `tab` opens a view directly (Settings → Areas sends {tab: 'settings'}).

import React, { useEffect, useState } from 'react';
import { View } from 'react-native';
import { orchestrationApi, type OrchOverview } from '../../api/orchestration';
import { EmptyState, IconButton, PageHeader, type HeaderTab, type StatePart } from '../../ui';
import { FleetChatSheet } from './AskSheets';
import { CatalogView } from './CatalogView';
import { NodesView } from './NodesView';
import { NowView } from './NowView';
import { RunsView } from './RunsView';
import { SettingsView } from './SettingsView';
import { StudioView } from './StudioView';
import { plural } from './parts';
import { useOrchData } from './useOrchData';

export const ORCH_TABS = ['now', 'runs', 'nodes', 'catalog', 'studio', 'settings'] as const;
export type OrchTab = typeof ORCH_TABS[number];
const MAIN: HeaderTab[] = [{ key: 'now', label: 'Now' }, { key: 'runs', label: 'Runs' }, { key: 'nodes', label: 'Nodes' }, { key: 'catalog', label: 'Catalog' }];
const MORE: Record<string, string> = { studio: 'Studio', settings: 'Settings' };

/** The fleet in one sentence (web view.js stateParts): nodes, offline / connecting, tools, drafts, dispatch. */
export function fleetState(o: OrchOverview | null): StatePart[] {
  if (!o) return [];
  const parts: StatePart[] = [];
  const n = o.nodes || { total: 0, by_status: {} };
  const by = n.by_status || {};
  const offline = by.offline || 0;
  if (!n.total) parts.push({ text: 'No nodes yet' });
  else if (!offline && (by.active || 0) === n.total) parts.push({ text: `${plural(n.total, 'node')}, all online` });
  else {
    parts.push({ text: plural(n.total, 'node') });
    if (offline) parts.push({ text: `${offline} offline`, tone: 'danger' });
    if (by.connecting) parts.push({ text: `${by.connecting} connecting`, tone: 'quiet' });
  }
  if (o.attention?.total) parts.push({ text: `${o.attention.total} need${o.attention.total === 1 ? 's' : ''} you`, tone: 'attention' });
  if (o.catalog?.published) parts.push({ text: plural(o.catalog.published, 'tool') });
  if (o.catalog?.drafts) parts.push({ text: plural(o.catalog.drafts, 'draft') });
  parts.push(o.attention?.dispatch_paused ? { text: 'dispatch paused', tone: 'danger' } : { text: 'dispatching', tone: 'quiet' });
  return parts;
}

export function OrchestrationScreen({ navigation, route }: { navigation: any; route?: { params?: { tab?: string } } }) {
  const [tab, setTab] = useState<OrchTab>('now');
  const [ask, setAsk] = useState(false);
  const wanted = route?.params?.tab;
  useEffect(() => { if (wanted && (ORCH_TABS as readonly string[]).includes(wanted)) setTab(wanted as OrchTab); }, [wanted]);
  const { data: overview, missing, reload } = useOrchData(() => orchestrationApi.overview(), ['node', 'tool', 'run']);

  const openRun = (runId: string) => navigation.navigate('OrchRun', { runId });
  const openNode = (nodeId: string) => navigation.navigate('OrchNode', { nodeId });
  const openTool = (toolId: string, version: number | string) => navigation.navigate('OrchTool', { toolId, version: Number(version) });
  const openRunbook = (runbookId: string, version: number | string) => navigation.navigate('OrchRunbook', { runbookId, version: Number(version) });
  const openToolEditor = (toolId: string, version: number) => navigation.navigate('OrchToolEditor', { toolId, version });
  const openRunbookEditor = (runbookId: string, version: number) => navigation.navigate('OrchRunbookEditor', { runbookId, version });

  if (missing) {
    return <EmptyState icon="server" title="Orchestration is not available" message="This server has no orchestration API. Update mucli on the computer running the GUI." testID="orch-missing" />;
  }
  const tabs = MORE[tab] ? [...MAIN, { key: tab, label: MORE[tab] }] : MAIN;
  return (
    <View style={{ flex: 1 }} testID="orch-screen">
      <PageHeader testID="orch-head" title="Orchestration" state={fleetState(overview)} tabs={tabs} tab={tab} onTab={k => setTab(k as OrchTab)}
        right={<IconButton icon="message-circle" label="Ask about the fleet" onPress={() => setAsk(true)} testID="orch-ask" />}
        menu={[
          { key: 'studio', label: 'Studio', icon: 'pencil', onPress: () => setTab('studio') },
          { key: 'settings', label: 'Settings', icon: 'settings', onPress: () => setTab('settings') },
        ]} />
      <View style={{ flex: 1 }}>
        {tab === 'now' ? <NowView overview={overview} onChanged={() => { void reload(); }} openRun={openRun} openNode={openNode} openTool={openTool} openRunbook={openRunbook} openTab={k => setTab(k as OrchTab)} /> : null}
        {tab === 'runs' ? <RunsView openRun={openRun} /> : null}
        {tab === 'nodes' ? <NodesView openNode={openNode} openRun={openRun} /> : null}
        {tab === 'catalog' ? <CatalogView openTool={openTool} openRunbook={openRunbook} /> : null}
        {tab === 'studio' ? <StudioView openToolEditor={openToolEditor} openRunbookEditor={openRunbookEditor} openTool={openTool} openRunbook={openRunbook} /> : null}
        {tab === 'settings' ? <SettingsView /> : null}
      </View>
      <FleetChatSheet visible={ask} onClose={() => setAsk(false)} />
    </View>
  );
}
