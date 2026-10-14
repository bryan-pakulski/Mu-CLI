import { inspectorApi } from './inspector';
import { providersApi } from './providers';

/** Composer effort picker. "" = provider default; levels are mapped per provider server-side. */
export const DEFAULT_EFFORT_LEVELS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

const LABELS: Record<string, string> = {
  '': 'Default',
  none: 'None',
  minimal: 'Minimal',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  xhigh: 'XHigh',
  max: 'Max',
};

const HINTS: Record<string, string> = {
  '': 'Provider default',
  none: 'No extra reasoning (fastest)',
  minimal: 'Barely any reasoning',
  low: 'Quick turns, light thinking',
  medium: 'Balanced',
  high: 'Thorough reasoning',
  xhigh: 'Very thorough (supported models)',
  max: 'Maximum reasoning (slowest)',
};

export function effortLabel(level: string | null | undefined): string {
  const key = String(level || '');
  return LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

export function effortHint(level: string | null | undefined): string {
  return HINTS[String(level || '')] ?? '';
}

/** Picker options: Default first, then the server's levels (or the built-in list). */
export function effortOptions(levels?: string[] | null): string[] {
  const list = (levels && levels.length ? levels : DEFAULT_EFFORT_LEVELS).filter(Boolean);
  return ['', ...list.filter((level, index) => list.indexOf(level) === index)];
}

export const effortApi = {
  async get(): Promise<{ effort: string; levels: string[] }> {
    const current = await providersApi.getCurrent();
    return { effort: String(current.effort || ''), levels: effortOptions(current.effort_levels) };
  },
  /** "" resets to the provider default (DELETE); anything else is set on the session. */
  async set(level: string, sessionName?: string): Promise<string> {
    if (!level) {
      await inspectorApi.unsetVariable('reasoning_effort', sessionName);
      return '';
    }
    const response = await inspectorApi.setVariable('reasoning_effort', level, sessionName);
    return String(response.value ?? level);
  },
};
