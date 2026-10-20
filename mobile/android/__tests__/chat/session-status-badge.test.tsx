import React from 'react';
import { render } from '@testing-library/react-native';
import { SessionStatusBadge, sessionState, sortSessions } from '../../src/components/SessionStatusBadge';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider>{children}</ThemeProvider>
);

describe('sessionState', () => {
  it('prefers client-side active over server flags', () => {
    expect(sessionState({ is_busy: true, is_loaded: true }, true)).toBe('active');
    expect(sessionState({ is_current: true })).toBe('active');
  });
  it('ranks working above loaded, null for saved', () => {
    expect(sessionState({ is_busy: true, is_loaded: true })).toBe('working');
    expect(sessionState({ is_loaded: true })).toBe('loaded');
    expect(sessionState({})).toBeNull();
    expect(sessionState({ is_current: false, is_loaded: false, is_busy: false })).toBeNull();
  });
});

describe('sortSessions', () => {
  it('orders active → working → loaded → saved, stable within rank', () => {
    const input = [
      { name: 'saved-a' },
      { name: 'loaded-a', is_loaded: true },
      { name: 'saved-b' },
      { name: 'busy', is_loaded: true, is_busy: true },
      { name: 'loaded-b', is_loaded: true },
      { name: 'focus', is_loaded: true },
    ];
    expect(sortSessions(input, 'focus').map(s => s.name)).toEqual([
      'focus', 'busy', 'loaded-a', 'loaded-b', 'saved-a', 'saved-b',
    ]);
  });
  it('does not mutate input', () => {
    const input = [{ name: 'b', is_loaded: true }, { name: 'a' }];
    sortSessions(input, null);
    expect(input.map(s => s.name)).toEqual(['b', 'a']);
  });
});

describe('SessionStatusBadge', () => {
  it('renders Active when the row is the focused session', () => {
    const { getByText, getByTestId } = render(
      <SessionStatusBadge session={{ name: 'x', is_loaded: true }} active />,
      { wrapper },
    );
    expect(getByText('Active')).toBeTruthy();
    expect(getByTestId('session-state-active').props.accessibilityLabel).toBe('Session active');
  });
  it('renders Working for busy sessions and Loaded for idle loaded ones', () => {
    const busy = render(<SessionStatusBadge session={{ is_busy: true, is_loaded: true }} />, { wrapper });
    expect(busy.getByText('Working')).toBeTruthy();
    const loaded = render(<SessionStatusBadge session={{ is_loaded: true }} />, { wrapper });
    expect(loaded.getByText('Loaded')).toBeTruthy();
  });
  it('renders nothing for saved sessions', () => {
    const { toJSON } = render(<SessionStatusBadge session={{ name: 'saved' }} />, { wrapper });
    expect(toJSON()).toBeNull();
  });
});
