// Feature components. UI primitives live in src/ui (the Wash kit).
export { CodeBlock } from './CodeBlock';
export { ConflictBanner } from './ConflictBanner';
export { ModeWorkspaceIntro, useModeWorkspaceView, workspaceState, workspaceTabs } from './ModeWorkspace';
export {
  SessionStatusBadge,
  sessionState,
  sortSessions,
  SESSION_STATE_LABEL,
  type SessionState,
  type SessionStateSource,
  type SessionStatusBadgeProps,
} from './SessionStatusBadge';
