// The app shell (mobile_parity.md §3): six tabs in the web rail's order, each with its
// destination (accent + watercolour), unique stack routes, and mucli:// deep links that mirror
// the web's hash routes.
import { getStateFromPath } from '@react-navigation/native';
import { SCREENS, TABS, TAB_DESTINATION, linking } from '../../src/navigation/AppNavigator';
import { TAB_ROUTES } from '../../src/navigation/nav';
import { destinationArt } from '../../src/ui/Glass';

it('has the six destinations in the web rail order', () => {
  expect(TABS.map(t => t.name)).toEqual(['Chat', 'Work', 'Orchestration', 'Environments', 'Insights', 'More']);
  expect(TAB_DESTINATION).toEqual({
    Chat: 'chat', Work: 'work', Orchestration: 'orchestration', Environments: 'environments', Insights: 'insights', More: 'settings',
  });
  expect([...TAB_ROUTES].sort()).toEqual(TABS.map(t => t.name).sort());
  expect(destinationArt(TAB_DESTINATION.Environments)).toBe('meadow');
});

it('registers every stack route once and never shadows a tab', () => {
  const names = SCREENS.map(s => s.name);
  expect(new Set(names).size).toBe(names.length);
  for (const tab of TABS) expect(names).not.toContain(tab.name);
  // Retired screens stay retired.
  for (const gone of ['History', 'Audio', 'Workspace', 'WorkspaceCategory', 'Placeholder']) expect(names).not.toContain(gone);
});

const route = (path: string) => {
  const state = getStateFromPath(path, linking.config as never) as { routes: Array<{ name: string; params?: Record<string, unknown>; state?: unknown }> };
  return state.routes[state.routes.length - 1];
};

it('opens pairing links with the code and address', () => {
  const r = route('pair?code=K7QM3XRT&url=http%3A%2F%2F192.168.1.20%3A30311');
  expect(r.name).toBe('Pair');
  expect(r.params).toMatchObject({ code: 'K7QM3XRT', url: 'http://192.168.1.20:30311' });
});

it('opens jobs, runs and nodes by id', () => {
  expect(route('work/job/j-42')).toMatchObject({ name: 'JobDetail', params: { jobId: 'j-42' } });
  expect(route('orchestration/run/r-7')).toMatchObject({ name: 'OrchRun', params: { runId: 'r-7' } });
  expect(route('orchestration/node/n-1')).toMatchObject({ name: 'OrchNode', params: { nodeId: 'n-1' } });
  expect(route('orchestration/tool/t-1/3')).toMatchObject({ name: 'OrchTool', params: { toolId: 't-1', version: 3 } });
  const tabs = getStateFromPath('work/tickets', linking.config as never) as any;
  expect(tabs.routes[0].state.routes[0]).toMatchObject({ name: 'Work', params: { tab: 'tickets' } });
  expect(route('environments/env/dev')).toMatchObject({ name: 'EnvironmentDetail', params: { name: 'dev' } });
});
