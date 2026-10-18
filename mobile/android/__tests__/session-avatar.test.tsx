// Session avatars: monogram and pigment rules match the web (tests/test_session_avatars.py
// pins the same table against static/js/app.js), so a session looks the same everywhere.
import React from 'react';
import { render } from '@testing-library/react-native';
import { SessionAvatar, sessionMonogram, sessionPigment } from '../src/ui';
import { ThemeProvider } from '../src/theme/ThemeContext';

const TABLE: Array<[string, string, string]> = [
  ['Harvest-House', 'HH', 'rose'], ['codex', 'Co', 'apricot'], ['general', 'Ge', 'lagoon'],
  ['pakulski.dev', 'PD', 'plum'], ['rtdetr', 'Rt', 'meadow'], ['wavebathy', 'Wa', 'lilac'],
  ['myCoolApp', 'MC', 'rose'], ['', '?', 'lagoon'],
];

describe('session avatar', () => {
  it.each(TABLE)('%s → %s on %s', (name, mono, pigment) => {
    expect(sessionMonogram(name)).toBe(mono);
    expect(sessionPigment(name)).toBe(pigment);
  });

  it('renders the monogram and stays out of the accessibility tree', () => {
    const { getByText, toJSON } = render(<ThemeProvider><SessionAvatar name="rsq-cloud" type="container" /></ThemeProvider>);
    expect(getByText('RC', { includeHiddenElements: true })).toBeTruthy();
    expect(JSON.stringify(toJSON())).toContain('no-hide-descendants');
  });
});
