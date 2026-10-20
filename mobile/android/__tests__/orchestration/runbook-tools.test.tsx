// Runbook editor tools: the expression preview (validateRunbook with a node and sample inputs) and
// "Upgrade tools" (upgradeRunbookTools: per-step impact, then apply to the draft or start a new
// version from a published runbook).
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToastHost } from '../../src/ui';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: { command: jest.fn(), nodes: jest.fn() },
}));

import { orchestrationApi } from '../../src/api/orchestration';
import { ExpressionPreview, UpgradeToolsSheet, previewFacts, upgradeNotes, type UpgradeReport } from '../../src/screens/orchestration/runbookTools';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}><ToastHost>{node}</ToastHost></ThemeProvider>);
const style = (label: string) => ({ semantic: 'neutral', label });

const report: UpgradeReport = {
  spec: { title: 'Disk', steps: [{ name: 'disk', tool: 'sys.disk@2' }] },
  effect: { from: 'read_only', to: 'mutating', changed: true, from_style: style('read only'), to_style: style('changes things') },
  decision: { from: { level: 'allow' }, to: { level: 'confirm' } },
  steps: [{
    index: 0, name: 'disk', from: 'sys.disk@1', to: 'sys.disk@2', diff: '--- a/tool.yaml\n+++ b/tool.yaml\n@@ -1 +1 @@\n-version: 1\n+version: 2\n',
    effect: { from: 'read_only', to: 'mutating', changed: true, from_style: style('read only'), to_style: style('changes things') },
    rejected_params: [{ path: 'steps[0].params.days', message: 'not declared by sys.disk@2', severity: 'error' } as never],
    removed_outputs: ['free'], removed_referenced: [{ step: 'report', output: 'free' }], rollback: { from: '', to: 'undo.sh' },
  }],
};

beforeEach(() => {
  jest.clearAllMocks();
  api.nodes.mockResolvedValue({ nodes: [{ id: 'n1', name: 'web-1', status: 'active', labels: {} }] });
});

describe('expression preview', () => {
  it('words each step: where it runs, its when: value and its params, errors first', () => {
    expect(previewFacts({ name: 'a', runs_here: true, when: { value: true }, params: { days: 7 } }))
      .toEqual({ runs: 'runs here', when: 'when: true', params: '{"days":7}', failed: false });
    expect(previewFacts({ name: 'b', runs_here: false }).runs).toBe("doesn't run here (node selector)");
    expect(previewFacts({ name: 'c', runs_here: true, when: { error: 'unknown name x' } })).toMatchObject({ when: 'unknown name x', failed: true });
  });

  it('asks the server with the chosen node and typed sample inputs, and lists the steps', async () => {
    api.command.mockResolvedValue({ ok: true, data: { preview: { steps: [{ name: 'disk', runs_here: true, params: { days: 7 } }] } } });
    const spec = { steps: [{ name: 'disk', tool: 'sys.disk@1', params: { days: '${{ inputs.days }}' } }] };
    const inputs = { type: 'object', properties: { days: { type: 'integer', default: 3 } } };
    const ui = wrap(<ExpressionPreview spec={() => spec} inputs={inputs} />);
    await act(async () => undefined);
    fireEvent.changeText(ui.getByTestId('orch-rb-preview-inputs-days'), '7');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-rb-preview-run')); });
    expect(api.command).toHaveBeenCalledWith('validateRunbook', { spec, preview: { node_id: '', inputs: { days: 7 } } });
    expect(ui.getByTestId('orch-rb-preview-step-0')).toBeTruthy();
    expect(ui.getByText('runs here · always · params {"days":7}')).toBeTruthy();
  });

  it('says to fix the YAML when the server has no preview, and refuses bad sample inputs', async () => {
    api.command.mockResolvedValue({ ok: true, data: { preview: null } });
    const ui = wrap(<ExpressionPreview spec={() => 'steps: ['} inputs={{ type: 'object', properties: { days: { type: 'integer' } } }} />);
    await act(async () => undefined);
    fireEvent.changeText(ui.getByTestId('orch-rb-preview-inputs-days'), 'many');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-rb-preview-run')); });
    expect(api.command).not.toHaveBeenCalled();
    expect(ui.getByTestId('orch-rb-preview-error')).toBeTruthy();
    fireEvent.changeText(ui.getByTestId('orch-rb-preview-inputs-days'), '2');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-rb-preview-run')); });
    expect(ui.getByText('✗ Fix the YAML first.')).toBeTruthy();
  });
});

describe('upgrade tools', () => {
  it('explains what each upgrade changes', () => {
    expect(upgradeNotes(report.steps[0])).toEqual([
      { text: 'The effect changes: read only → changes things', problem: false },
      { text: 'steps[0].params.days: not declared by sys.disk@2', problem: true },
      { text: 'Step report uses output free, which sys.disk@2 no longer declares', problem: true },
      { text: 'Rollback added: undo.sh', problem: false },
    ]);
  });

  it('applies the chosen upgrades to a draft without saving', async () => {
    api.command.mockImplementation(async (_cmd: string, args: Record<string, unknown>) => ({ ok: true, data: args.steps ? { ...report, spec: { upgraded: true } } : report }));
    const onApplied = jest.fn();
    const onClose = jest.fn();
    const ui = wrap(<UpgradeToolsSheet visible spec="yaml" runbookRef="disk@2" published={false} onClose={onClose} onApplied={onApplied} />);
    await act(async () => undefined);
    expect(ui.getByText(/Your access decision: Allowed → Needs approval/)).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-upgrade-apply')); });
    expect(api.command).toHaveBeenLastCalledWith('upgradeRunbookTools', { spec: 'yaml', steps: [0] });
    expect(onApplied).toHaveBeenCalledWith({ upgraded: true });
    expect(api.command).not.toHaveBeenCalledWith('saveRunbookDraft', expect.anything());
  });

  it('starts a new version from a published runbook', async () => {
    api.command.mockImplementation(async (cmd: string, args: Record<string, unknown>) => {
      if (cmd === 'newRunbookVersion') return { ok: true, data: { ref: 'disk@3' } };
      return { ok: true, data: args.steps ? { ...report, spec: { upgraded: true } } : report };
    });
    const onNewVersion = jest.fn();
    const ui = wrap(<UpgradeToolsSheet visible spec={{ steps: [] }} runbookRef="disk@2" published onClose={jest.fn()} onNewVersion={onNewVersion} />);
    await act(async () => undefined);
    await act(async () => { fireEvent.press(ui.getByTestId('orch-upgrade-apply')); });
    expect(api.command).toHaveBeenCalledWith('newRunbookVersion', { ref: 'disk@2', spec: { upgraded: true } });
    expect(onNewVersion).toHaveBeenCalledWith('disk@3');
  });

  it('needs at least one step, and closes when every tool is already current', async () => {
    api.command.mockResolvedValue({ ok: true, data: report });
    const ui = wrap(<UpgradeToolsSheet visible spec="x" runbookRef="disk@2" published={false} onClose={jest.fn()} />);
    await act(async () => undefined);
    fireEvent(ui.getByTestId('orch-upgrade-pick-0'), 'press');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-upgrade-apply')); });
    expect(ui.getByText('Select at least one step.')).toBeTruthy();

    api.command.mockResolvedValue({ ok: true, data: { ...report, steps: [] } });
    const onClose = jest.fn();
    wrap(<UpgradeToolsSheet visible spec="x" runbookRef="disk@2" published={false} onClose={onClose} />);
    await act(async () => undefined);
    expect(onClose).toHaveBeenCalled();
  });
});
