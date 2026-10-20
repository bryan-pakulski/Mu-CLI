/**
 * Sheets and dialogs never unmount a visible RN Modal. On Android, unmounting a Modal while it is
 * visible can leave its native window on screen: the stale sheet stays up and swallows every touch.
 * That froze the app (a resumed job's "continue" sheet stayed up; sessions could not be switched).
 * Components keep the Modal mounted and toggle `visible`; RN dismisses the window natively.
 */
import fs from 'fs';
import path from 'path';
import React from 'react';
import { Text } from 'react-native';
import { render } from '@testing-library/react-native';

const mockModalLog: Array<{ event: 'mount' | 'unmount' | 'update'; visible: boolean }> = [];
jest.mock('react-native/Libraries/Modal/Modal', () => {
  const React = require('react');
  const { View } = require('react-native');
  return function MockModal({ children, visible, testID }: { children?: React.ReactNode; visible?: boolean; testID?: string }) {
    const visibleRef = React.useRef(visible);
    visibleRef.current = visible;
    React.useEffect(() => {
      mockModalLog.push({ event: 'mount', visible: !!visible });
      return () => mockModalLog.push({ event: 'unmount', visible: !!visibleRef.current });
    }, []);
    React.useEffect(() => { mockModalLog.push({ event: 'update', visible: !!visible }); }, [visible]);
    return visible === false ? null : React.createElement(View, { testID }, children);
  };
});

import { Dialog, Sheet } from '../src/ui/Sheet';
import { ThemeProvider } from '../src/theme/ThemeContext';

const wrap = (node: React.ReactNode) => <ThemeProvider>{node}</ThemeProvider>;

beforeEach(() => { mockModalLog.length = 0; });

it.each([
  ['Sheet', (visible: boolean) => <Sheet visible={visible} onClose={() => undefined} title="Resume"><Text>guidance</Text></Sheet>],
  ['full Sheet', (visible: boolean) => <Sheet full visible={visible} onClose={() => undefined} title="Edit"><Text>x</Text></Sheet>],
  ['Dialog', (visible: boolean) => <Dialog visible={visible} onClose={() => undefined} title="Allow?"><Text>x</Text></Dialog>],
])('%s closes by toggling visible, never by unmounting a visible Modal', (_name, make) => {
  const tree = render(wrap(make(true)));
  expect(tree.queryByText(/guidance|x/)).toBeTruthy();
  tree.rerender(wrap(make(false)));
  expect(mockModalLog.some(e => e.event === 'unmount')).toBe(false);
  expect(mockModalLog[mockModalLog.length - 1]).toEqual({ event: 'update', visible: false });
  tree.unmount(); // unmounting while hidden is fine
  expect(mockModalLog.filter(e => e.event === 'unmount' && e.visible)).toEqual([]);
});

it('no component hard-codes a visible Sheet/Dialog that it mounts conditionally', () => {
  const walk = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap(d =>
    d.isDirectory() ? walk(path.join(dir, d.name)) : /\.tsx$/.test(d.name) ? [path.join(dir, d.name)] : []);
  const offenders = walk(path.join(__dirname, '..', 'src')).filter(f => {
    const text = fs.readFileSync(f, 'utf8');
    return /<(Sheet|Dialog|ModalFrame)\b[^>]*?\svisible(\s|\/?>)/.test(text) || /if \(!visible\) return null;/.test(text);
  });
  expect(offenders).toEqual([]);
});
