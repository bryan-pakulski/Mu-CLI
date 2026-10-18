import { type IconName } from '../ui';

export type WorkspaceScreenName =
  | 'Modes'
  | 'Teacher'
  | 'Feature'
  | 'Research'
  | 'Security'
  | 'Loop'
  | 'Debug'
  | 'Memory'
  | 'Files'
  | 'Skills'
  | 'SystemPrompts'
  | 'History'
  | 'Threads'
  | 'Prompts'
  | 'Traces'
  | 'Audio'
  | 'Providers'
  | 'Connection'
  | 'Shell';

export type WorkspaceCategoryId = 'workflows' | 'context' | 'review' | 'runtime';

export type WorkspaceItem = {
  screen: WorkspaceScreenName;
  title: string;
  description: string;
  icon: IconName;
};

export type WorkspaceCategory = {
  id: WorkspaceCategoryId;
  title: string;
  description: string;
  icon: IconName;
  items: readonly WorkspaceItem[];
};

export const WORKSPACE_CATEGORIES: readonly WorkspaceCategory[] = [
  {
    id: 'workflows',
    title: 'Create & run',
    description: 'Choose how MuCLI approaches the task.',
    icon: 'zap',
    items: [
      { screen: 'Modes', title: 'Modes', description: 'Select the active agent strategy.', icon: 'sliders-horizontal' },
      { screen: 'Teacher', title: 'Teacher', description: 'Build structured, source-grounded lessons.', icon: 'graduation-cap' },
      { screen: 'Feature', title: 'Feature plans', description: 'Break larger changes into approvable stages.', icon: 'layers' },
      { screen: 'Research', title: 'Research', description: 'Investigate a topic without editing files.', icon: 'telescope' },
      { screen: 'Security', title: 'Security', description: 'Run a verified security review workflow.', icon: 'shield-check' },
      { screen: 'Loop', title: 'Loop', description: 'Manage long-running autonomous work.', icon: 'repeat' },
      { screen: 'Debug', title: 'Debug', description: 'Work through reproduce, locate, and fix.', icon: 'bug' },
    ],
  },
  {
    id: 'context',
    title: 'Context',
    description: 'Control what MuCLI can see and remember.',
    icon: 'folder-open',
    items: [
      { screen: 'Memory', title: 'Memory', description: 'Inspect saved context and scratchpad state.', icon: 'layers' },
      { screen: 'Files', title: 'Files', description: 'Browse attached workspace files.', icon: 'folder' },
      { screen: 'Skills', title: 'Skills', description: 'Review and manage agent extensions.', icon: 'sparkles' },
      { screen: 'SystemPrompts', title: 'System prompts', description: 'Inspect the active instruction layers.', icon: 'file-text' },
    ],
  },
  {
    id: 'review',
    title: 'Review',
    description: 'See decisions, activity, and pending input.',
    icon: 'activity',
    items: [
      { screen: 'Threads', title: 'Agent threads', description: 'Review peer conversations, ownership, and coordination activity.', icon: 'git-branch' },
      { screen: 'History', title: 'History', description: 'Review earlier conversation turns.', icon: 'history' },
      { screen: 'Prompts', title: 'Pending prompts', description: 'Answer requests waiting for your input.', icon: 'message-circle' },
      { screen: 'Traces', title: 'Traces', description: 'Inspect run telemetry and context growth.', icon: 'activity' },
      { screen: 'Audio', title: 'Audio', description: 'Manage audio input and transcription.', icon: 'mic' },
    ],
  },
  {
    id: 'runtime',
    title: 'Runtime',
    description: 'Configure the model and server connection.',
    icon: 'settings',
    items: [
      { screen: 'Providers', title: 'Providers', description: 'Choose a provider and model.', icon: 'server' },
      { screen: 'Connection', title: 'Connection', description: 'Configure and test the MuCLI server.', icon: 'wifi' },
      { screen: 'Shell', title: 'Shell', description: 'Open an interactive terminal into the session container.', icon: 'terminal' },
    ],
  },
] as const;

export function getWorkspaceCategory(id: WorkspaceCategoryId): WorkspaceCategory {
  return WORKSPACE_CATEGORIES.find(category => category.id === id) ?? WORKSPACE_CATEGORIES[0];
}
