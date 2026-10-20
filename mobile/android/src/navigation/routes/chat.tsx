// Chat (documentation/plans/mobile_parity.md §3, §6 Chat): the conversation, the sessions and
// threads drawer (menu button or the left edge), the workspace panel (⋯ or the right edge), and the
// session settings (⋯, the composer's model label, or Settings → Areas → Chat and workspace).
// The header carries the title only.

import React, { useCallback, useEffect, useState } from 'react';
import { View } from 'react-native';
import { ConnectionPrompt } from '../../components/ConnectionPrompt';
import { EdgeSwipeView } from '../../components/EdgeSwipeView';
import { SessionStartPrompt } from '../../components/SessionStartPrompt';
import { SessionsDrawer } from '../../components/chat/SessionsDrawer';
import { SessionSettingsSheet } from '../../components/chat/SessionSettingsSheet';
import { WorkspacePanelSheet } from '../../components/chat/WorkspacePanelSheet';
import { useModes } from '../../components/chat/useModes';
import { sessionsApi } from '../../api/sessions';
import { useConnectionStore } from '../../store/connection';
import { ChatScreen } from '../../screens/ChatScreen';
import { MemoryScreen } from '../../screens/MemoryScreen';
import { FilesScreen } from '../../screens/FilesScreen';
import { SkillsScreen } from '../../screens/SkillsScreen';
import { ModesScreen } from '../../screens/ModesScreen';
import { PromptsScreen } from '../../screens/PromptsScreen';
import { SystemPromptsScreen } from '../../screens/SystemPromptsScreen';
import { TeacherScreen } from '../../screens/TeacherScreen';
import { FeatureExplorerScreen } from '../../screens/FeatureExplorerScreen';
import { ResearchScreen } from '../../screens/ResearchScreen';
import { SecurityScreen } from '../../screens/SecurityScreen';
import { LoopScreen } from '../../screens/LoopScreen';
import { DebugScreen } from '../../screens/DebugScreen';
import { ThreadsScreen } from '../../screens/ThreadsScreen';
import { ArtifactsScreen } from '../../screens/ArtifactsScreen';
import { IconButton } from '../../ui/Button';
import { ChoiceRow } from '../../ui/Form';
import { Stage } from '../../ui/Glass';
import { PageHeader } from '../../ui/Mu';
import { Sheet } from '../../ui/Sheet';
import { navigateTo } from '../nav';
import type { ScreenSpec, TabSpec } from '../types';

export type ChatParams = {
  Teacher: undefined;
  Feature: undefined;
  Research: undefined;
  Security: undefined;
  Loop: undefined;
  Debug: undefined;
  Threads: undefined;
  SystemPrompts: undefined;
  Memory: undefined;
  Files: undefined;
  Skills: undefined;
  Modes: undefined;
  Prompts: undefined;
  Artifacts: undefined;
};

/** Params the Chat tab accepts (Settings → Areas opens the session settings). */
export type ChatTabParams = { openSettings?: number } | undefined;

function ChatTab({ route }: { route?: { params?: ChatTabParams } }) {
  const isConnected = useConnectionStore(state => state.isConnected);
  const baseUrl = useConnectionStore(state => state.baseUrl);
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const setActiveSession = useConnectionStore(state => state.setActiveSession);
  const modes = useModes(isConnected ? activeSessionName : null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);
  const [artifactsVisible, setArtifactsVisible] = useState(true);
  const [createRequestToken, setCreateRequestToken] = useState(0);

  const openSettings = useCallback(() => { void modes.reload(); setSettingsOpen(true); }, [modes]);
  const openModes = useCallback(() => { void modes.reload(); setModeOpen(true); }, [modes]);

  const openSettingsParam = route?.params?.openSettings;
  useEffect(() => { if (openSettingsParam) openSettings(); }, [openSettingsParam]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isConnected) return;
    const controller = new AbortController();
    sessionsApi.list({ signal: controller.signal, timeoutMs: 8_000 })
      .then(response => {
        if (controller.signal.aborted) return;
        const selected = useConnectionStore.getState().activeSessionName;
        if (selected && response.loaded.includes(selected)) return;
        const current = response.current && response.loaded.includes(response.current) ? response.current : response.loaded[0] || null;
        setActiveSession(current);
      })
      .catch(() => { /* the connection screen owns transport errors */ });
    return () => controller.abort();
  }, [baseUrl, isConnected, setActiveSession]);

  const menu = [
    { key: 'conversations', label: 'Conversations', icon: 'menu' as const, onPress: () => setDrawerOpen(true) },
    ...(activeSessionName ? [
      { key: 'workspace', label: 'Workspace', icon: 'panel-right' as const, onPress: () => setWorkspaceOpen(true) },
      { key: 'settings', label: 'Session settings', icon: 'sliders-horizontal' as const, onPress: openSettings },
      { key: 'artifacts', label: artifactsVisible ? 'Hide session files strip' : 'Show session files strip', icon: (artifactsVisible ? 'eye-off' : 'eye') as 'eye', onPress: () => setArtifactsVisible(v => !v) },
    ] : []),
    { key: 'runs', label: 'Runs and traces', icon: 'activity' as const, onPress: () => navigateTo('Insights') },
  ];

  return (
    <EdgeSwipeView onSwipeFromLeft={() => setDrawerOpen(true)} onSwipeFromRight={() => { if (activeSessionName) setWorkspaceOpen(true); }}>
      <View style={{ flex: 1, backgroundColor: 'transparent' }}>
        <PageHeader testID="chat-header" title={activeSessionName || 'Chat'} menu={menu}
          right={<IconButton icon="menu" label="Conversations" onPress={() => setDrawerOpen(true)} testID="chat-open-drawer" />} />
        <Stage>
          {!isConnected ? (
            <ConnectionPrompt onConnect={() => navigateTo('Connection')} />
          ) : activeSessionName ? (
            <ChatScreen mode={{ name: modes.current || 'default', label: modes.label }} onOpenModes={openModes} onOpenSettings={openSettings}
              onOpenContext={() => navigateTo('Memory')} artifactsVisible={artifactsVisible} />
          ) : (
            <SessionStartPrompt onLoadSession={() => setDrawerOpen(true)} onCreateSession={() => setCreateRequestToken(v => v + 1)} onManageContainers={() => navigateTo('Environments')} />
          )}
        </Stage>
        <SessionsDrawer visible={drawerOpen} onClose={() => setDrawerOpen(false)} createRequestToken={createRequestToken} />
        <WorkspacePanelSheet visible={workspaceOpen && !!activeSessionName} onClose={() => setWorkspaceOpen(false)} activeMode={modes.current}
          onOpen={panel => navigateTo(panel)} />
        <SessionSettingsSheet visible={settingsOpen && !!activeSessionName} onClose={() => setSettingsOpen(false)} modes={modes.modes} activeMode={modes.current}
          onSelectMode={name => { void modes.select(name); }} onOpenProviders={() => { setSettingsOpen(false); navigateTo('Providers'); }} />
        <Sheet visible={modeOpen && !!activeSessionName} onClose={() => setModeOpen(false)} title="Mode" testID="mode-sheet">
          <View style={{ gap: 4 }}>
            {modes.modes.map(mode => (
              <ChoiceRow key={mode.name} label={mode.display_name} description={mode.disabled ? 'Requires a workspace or container' : mode.description || undefined}
                selected={modes.current === mode.name} disabled={mode.disabled} testID={`mode-${mode.name}`}
                onPress={() => { void modes.select(mode.name).then(ok => { if (ok) setModeOpen(false); }); }} />
            ))}
          </View>
        </Sheet>
      </View>
    </EdgeSwipeView>
  );
}

export const chatTab: TabSpec = { name: 'Chat', component: ChatTab, label: 'Chat', icon: 'message-circle', destination: 'chat', headerShown: false };

// Every panel draws its own PageHeader (components/chat/PanelPage.tsx), so the navigator header stays hidden.
export const chatScreens: ScreenSpec[] = [
  { name: 'Teacher', component: TeacherScreen, options: { title: 'Teacher', headerShown: false } },
  { name: 'Feature', component: FeatureExplorerScreen, options: { title: 'Feature plans', headerShown: false } },
  { name: 'Research', component: ResearchScreen, options: { title: 'Research', headerShown: false } },
  { name: 'Security', component: SecurityScreen, options: { title: 'Security', headerShown: false } },
  { name: 'Loop', component: LoopScreen, options: { title: 'Loop', headerShown: false } },
  { name: 'Debug', component: DebugScreen, options: { title: 'Debug', headerShown: false } },
  { name: 'Threads', component: ThreadsScreen, options: { title: 'Agent conversations', headerShown: false } },
  { name: 'SystemPrompts', component: SystemPromptsScreen, options: { title: 'System prompts', headerShown: false } },
  { name: 'Memory', component: MemoryScreen, options: { title: 'Context', headerShown: false } },
  { name: 'Files', component: FilesScreen, options: { title: 'Files', headerShown: false } },
  { name: 'Skills', component: SkillsScreen, options: { title: 'Skills', headerShown: false } },
  { name: 'Modes', component: ModesScreen, options: { title: 'Modes', headerShown: false } },
  { name: 'Prompts', component: PromptsScreen, options: { title: 'Pending prompts', headerShown: false } },
  { name: 'Artifacts', component: ArtifactsScreen, options: { title: 'Session files', headerShown: false } },
];
