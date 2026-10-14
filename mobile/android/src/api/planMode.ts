import { inspectorApi, type InspectorVariableGroup } from './inspector';
import { useConnectionStore } from '../store/connection';

/**
 * Plan mode (`session.variables.plan_mode`) is the session's read-only switch:
 * while it is on, the server's pre-tool hook blocks every write-side tool and
 * returns `plan_mode_blocked`. The phone mirrors it in the connection store so
 * the header badge, composer strip and placeholder can show it.
 */
export const PLAN_MODE_KEY = 'plan_mode';
export const PLAN_MODE_TOOLTIP =
  'Plan mode: read-only. File edits, shell writes and other write tools are blocked.';

export function planModeFromGroups(groups: InspectorVariableGroup[] | undefined | null): boolean | null {
  for (const group of groups || []) {
    const variable = (group.variables || []).find(item => item.key === PLAN_MODE_KEY);
    if (variable) return variable.value === true || variable.value === 'true';
  }
  return null;
}

/** Re-read plan mode for a session; keeps the last value on transient errors. */
export async function refreshPlanMode(
  sessionName: string | null | undefined = useConnectionStore.getState().activeSessionName,
): Promise<boolean | null> {
  if (!sessionName) return null;
  try {
    const response = await inspectorApi.getVariables(sessionName);
    const value = planModeFromGroups(response?.groups);
    if (value === null) return null;
    // Ignore answers that arrive after the user switched sessions.
    if (useConnectionStore.getState().activeSessionName === sessionName) {
      useConnectionStore.getState().setPlanMode(value);
    }
    return value;
  } catch {
    return null;
  }
}

/** POST /api/variables/plan_mode — same effect as `/plan on|off`. */
export async function setPlanModeRemote(
  next: boolean,
  sessionName: string | null | undefined = useConnectionStore.getState().activeSessionName,
): Promise<boolean> {
  if (!sessionName) throw new Error('No active session');
  const response = await inspectorApi.setVariable(PLAN_MODE_KEY, next, sessionName);
  const value = response?.value === true || response?.value === 'true';
  if (useConnectionStore.getState().activeSessionName === sessionName) {
    useConnectionStore.getState().setPlanMode(value);
  }
  return value;
}

/** True when a tool result is the plan-mode refusal envelope. */
export function isPlanModeBlocked(result: unknown): boolean {
  if (result == null) return false;
  if (typeof result === 'string') return result.includes('plan_mode_blocked');
  if (typeof result === 'object') {
    if ((result as { error_code?: unknown }).error_code === 'plan_mode_blocked') return true;
    try {
      return JSON.stringify(result).includes('plan_mode_blocked');
    } catch {
      return false;
    }
  }
  return false;
}
