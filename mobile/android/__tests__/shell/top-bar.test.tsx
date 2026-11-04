// The tab shell's fixed, compact top bar (src/navigation/TopBar.tsx): the focused page's title, the
// destinations as icons with badges, and one Options dropdown holding the page's own actions then
// the app's. Side panels (src/components/SlidePanel.tsx) open with an edge swipe that they follow.
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';
import { Text } from 'react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { TopBar } from '../../src/navigation/TopBar';
import { TABS } from '../../src/navigation/AppNavigator';
import { useBarPage } from '../../src/store/barPage';
import { edgeSwipeSide } from '../../src/components/EdgeSwipeView';
import { SlidePanel, settlesOpen, type SlidePanelHandle } from '../../src/components/SlidePanel';

const mockNavigateTo = jest.fn();
jest.mock('../../src/navigation/nav', () => ({ navigateTo: (...a: unknown[]) => mockNavigateTo(...a) }));

const wrap = (ui: React.ReactElement) => render(<ThemeProvider statusBar={false}>{ui}</ThemeProvider>);
const state = { index: 0, routes: TABS.map(t => ({ key: `${t.name}-k`, name: t.name })) } as never;
const nav = () => ({ emit: jest.fn(() => ({ defaultPrevented: false })), navigate: jest.fn() });

beforeEach(() => act(() => { useBarPage.setState({ owner: null, page: null }); }));

it('switches destination from an icon and shows what needs you', () => {
  const navigation = nav();
  const ui = wrap(<TopBar state={state} navigation={navigation as never} tabs={TABS} badges={{ Work: { count: 3, summary: '3 jobs need you' } }} />);
  expect(ui.getByLabelText('Work, 3 jobs need you')).toBeTruthy();
  fireEvent.press(ui.getByTestId('tab-work'));
  expect(navigation.navigate).toHaveBeenCalledWith('Work');
});

it('shows the focused page title in the bar, falling back to the tab label', () => {
  const ui = wrap(<TopBar state={state} navigation={nav() as never} tabs={TABS} badges={{}} />);
  expect(ui.getByTestId('top-bar-title').props.children).toBe('Chat');
  act(() => { useBarPage.getState().register('p', { title: 'my-long-session-name', items: [] }); });
  expect(ui.getByTestId('top-bar-title').props.children).toBe('my-long-session-name');
  act(() => { useBarPage.getState().register('p', null); });
  expect(ui.getByTestId('top-bar-title').props.children).toBe('Chat');
});

it('opens Options as a dropdown under the bar: the page options, then the app options', () => {
  const sessionSettings = jest.fn();
  act(() => { useBarPage.getState().register('p', { title: 'mucli', items: [{ key: 'session', label: 'Session settings', onPress: sessionSettings }] }); });
  const ui = wrap(<TopBar state={state} navigation={nav() as never} tabs={TABS} badges={{}} />);
  fireEvent.press(ui.getByTestId('top-bar-options'));
  const menu = ui.getByTestId('options-menu');
  const style = Object.assign({}, ...[].concat(menu.props.style).flat(Infinity).filter(Boolean));
  expect(style.position).toBe('absolute');
  expect(style.bottom).toBeUndefined();
  for (const label of ['Session settings', 'App settings', 'Provider and model', 'Connection', 'Pairing']) expect(ui.getByText(label)).toBeTruthy();
  // The side panels open by swipe, so the menu never repeats them.
  expect(ui.queryByText('Conversations')).toBeNull();
  expect(ui.queryByText('Workspace')).toBeNull();
  fireEvent.press(ui.getByText('Provider and model'));
  expect(mockNavigateTo).toHaveBeenCalledWith('Providers');
  expect(ui.queryByTestId('options-menu')).toBeNull();
});

it('claims only a sideways drag that starts at an edge', () => {
  const w = 400, edge = 32;
  expect(edgeSwipeSide(5, 20, 4, w, edge, 12)).toBe('left');
  expect(edgeSwipeSide(395, -20, 4, w, edge, 12)).toBe('right');
  // A tap, a mostly vertical drag, or one starting mid-screen is never claimed.
  expect(edgeSwipeSide(395, 0, 0, w, edge, 12)).toBeNull();
  expect(edgeSwipeSide(395, -20, 40, w, edge, 12)).toBeNull();
  expect(edgeSwipeSide(200, -80, 4, w, edge, 12)).toBeNull();
});

it('settles a released panel open past a third of its width or on a fling', () => {
  expect(settlesOpen(0.5, 0)).toBe(true);
  expect(settlesOpen(0.2, 0)).toBe(false);
  expect(settlesOpen(0.1, 0.8)).toBe(true);
  expect(settlesOpen(0.9, -0.8)).toBe(false);
});

it('appears as soon as an edge drag starts and opens when released far enough', () => {
  const ref = React.createRef<SlidePanelHandle>();
  const onOpen = jest.fn();
  const ui = wrap(
    <SlidePanel ref={ref} side="left" visible={false} onClose={jest.fn()} onOpen={onOpen} label="Conversations" testID="panel">
      <Text>inside</Text>
    </SlidePanel>,
  );
  expect(ui.queryByTestId('panel')).toBeNull();
  act(() => { ref.current?.drag(40); });
  expect(ui.getByText('inside')).toBeTruthy();
  act(() => { ref.current?.release(300, 0); });
  expect(onOpen).toHaveBeenCalled();
});

it('closes from its backdrop', () => {
  const onClose = jest.fn();
  const ui = wrap(
    <SlidePanel side="right" visible onClose={onClose} label="Workspace" testID="panel"><Text>inside</Text></SlidePanel>,
  );
  // The panel is the accessibility modal, so screen readers skip the backdrop; a tap still closes it.
  fireEvent.press(ui.getByTestId('panel-backdrop', { includeHiddenElements: true }));
  expect(onClose).toHaveBeenCalled();
});
