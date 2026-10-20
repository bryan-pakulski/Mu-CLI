// The workspace panel (the web's ⋯ panels beside the chat; mobile_parity.md §3 Chat): the active
// mode's panel first, then the session's Context, Files, Session files, Skills, System prompts and
// agent conversations, then every mode panel. Each opens as a pushed screen.

import React from 'react';
import { View } from 'react-native';
import { spacing } from '../../theme/tokens';
import { BoardSection, Row } from '../../ui/Mu';
import { Sheet } from '../../ui/Sheet';

export type PanelRoute = 'Teacher' | 'Feature' | 'Research' | 'Security' | 'Loop' | 'Debug' | 'Memory' | 'Files' | 'Artifacts' | 'Skills' | 'SystemPrompts' | 'Threads' | 'Modes';

export const MODE_PANELS: Array<{ mode: string; route: PanelRoute; title: string; sub: string }> = [
  { mode: 'feature', route: 'Feature', title: 'Feature plans', sub: 'Phases, tasks and progress' },
  { mode: 'research', route: 'Research', title: 'Research', sub: 'Sources, notes and the bibliography' },
  { mode: 'teacher', route: 'Teacher', title: 'Teacher', sub: 'Curriculum, lessons and grades' },
  { mode: 'security', route: 'Security', title: 'Security', sub: 'Findings and scope' },
  { mode: 'loop', route: 'Loop', title: 'Loop', sub: 'Autonomous runs and their checks' },
  { mode: 'debug', route: 'Debug', title: 'Debug', sub: 'Hypotheses, evidence and fixes' },
];

export const WORKSPACE_PANELS: Array<{ route: PanelRoute; title: string; sub: string }> = [
  { route: 'Memory', title: 'Context', sub: 'Layers, memory and what the model sees' },
  { route: 'Files', title: 'Files', sub: 'Read, edit and create workspace files' },
  { route: 'Artifacts', title: 'Session files', sub: 'Visualizations, model artifacts and uploads' },
  { route: 'Skills', title: 'Skills', sub: 'Skills available to this session' },
  { route: 'SystemPrompts', title: 'System prompts', sub: 'The prompts and your overrides' },
  { route: 'Threads', title: 'Agent conversations', sub: 'Threads and their coordination activity' },
];

export function WorkspacePanelSheet({ visible, onClose, activeMode, onOpen }: {
  visible: boolean;
  onClose: () => void;
  activeMode: string | null;
  onOpen: (route: PanelRoute) => void;
}) {
  const current = MODE_PANELS.find(p => p.mode === activeMode);
  const go = (route: PanelRoute) => () => { onClose(); onOpen(route); };
  return (
    <Sheet visible={visible} onClose={onClose} title="Workspace" testID="workspace-panel">
      <View style={{ gap: spacing.sm }}>
        {current ? (
          <BoardSection title="This mode">
            <Row testID={`panel-${current.route}`} status="active" title={current.title} sub={current.sub} onPress={go(current.route)} />
          </BoardSection>
        ) : null}
        <BoardSection title="Session">
          {WORKSPACE_PANELS.map(p => <Row key={p.route} testID={`panel-${p.route}`} title={p.title} sub={p.sub} onPress={go(p.route)} />)}
        </BoardSection>
        <BoardSection title="Mode panels">
          {MODE_PANELS.filter(p => p !== current).map(p => <Row key={p.route} testID={`panel-${p.route}`} title={p.title} sub={p.sub} onPress={go(p.route)} />)}
          <Row testID="panel-Modes" title="All modes" sub="What each mode does, and switch" onPress={go('Modes')} />
        </BoardSection>
      </View>
    </Sheet>
  );
}
