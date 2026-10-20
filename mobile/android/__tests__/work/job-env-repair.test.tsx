// Environment repair (documentation/plans/job_templates.md §7): the Checks tab shows the latest
// repair attempt (outcome from the shared vocabulary, commands, error) and offers the template
// actions on the repair card, never in the action bar.
import React from 'react';
import { fireEvent, render, within } from '@testing-library/react-native';

import type { CheckRun, EnvironmentRepair, JobVM } from '../../src/api/jobCore';
import { BAR_HIDDEN } from '../../src/components/JobActions';
import { ChecksTab } from '../../src/components/JobTabs';
import { repairOutcome } from '../../src/theme/status';

const passed = { suite: 'unit', command: 'npx vitest run', passed: true, stdout: '1 passed', stderr: '', preexisting_failures: [] };
const run: CheckRun = { run: 2, passed: true, head_sha: 'abc', new_failures: 0, preexisting_failures: 0, suites: [passed], verified: true, unrunnable: 0, trigger: 'repair' };
const repair = (extra: Partial<EnvironmentRepair> = {}): EnvironmentRepair => ({
  attempt: 1, binding: 'node', source: 'template', template: 'node-build', outcome: 'repaired',
  commands: ['apt-get install -y jq', 'npm ci'], project_commands: ['npm ci'], image: 'mucli/job-abc-node-repair1',
  cost_usd: 0.42, error: '', reverted_files: [], ...extra,
});
const jobWith = (repairs: EnvironmentRepair[], actions: unknown[] = []) =>
  ({ id: 'j', state: 'review_done', actions, fix_attempts: 0, checks: { runs: [run], repairs } } as unknown as JobVM);

describe('environment repair (§7)', () => {
  it('maps every outcome through the shared vocabulary', () => {
    expect(repairOutcome('repaired')).toEqual({ status: 'done', label: 'environment repaired' });
    expect(repairOutcome('unresolved').status).toBe('unverified');
    expect(repairOutcome('failed')).toEqual({ status: 'failed', label: 'repair failed' });
    expect(repairOutcome('budget_exhausted').status).toBe('failed');
    expect(repairOutcome('running').status).toBe('active');
    expect(repairOutcome('mystery')).toEqual({ status: 'pending', label: 'mystery' });
  });

  it('shows "Environment repaired" with the commands in the Checks tab', () => {
    const screen = render(<ChecksTab job={jobWith([repair()])} invoke={jest.fn()} busy={false} />);
    const card = within(screen.getByTestId('checks-repair'));
    expect(card.getByText('Environment repaired')).toBeTruthy();
    expect(card.getByText('attempt 1 · node · $0.42')).toBeTruthy();
    expect(card.getByText('$ apt-get install -y jq')).toBeTruthy();
    expect(card.getByText('$ npm ci')).toBeTruthy();
    expect(screen.getByTestId('checks-repair-status').props.accessibilityLabel).toBe('environment repaired');
  });

  it('shows "Repair failed" with the error and that the job continued unverified', () => {
    const failed = repair({ attempt: 2, outcome: 'timed_out', commands: [], image: '', error: 'the repair reached its time limit (1200s)' });
    const screen = render(<ChecksTab job={jobWith([repair({ outcome: 'unresolved' }), failed])} invoke={jest.fn()} busy={false} />);
    const card = within(screen.getByTestId('checks-repair'));
    expect(card.getByText('Repair failed')).toBeTruthy();
    expect(card.getByText(/reached its time limit .* The job continued unverified\./)).toBeTruthy();
    expect(screen.getByTestId('checks-repair-status').props.accessibilityLabel).toBe('repair timed out');
  });

  it('renders nothing without repairs and keeps the template actions out of the action bar', () => {
    const screen = render(<ChecksTab job={jobWith([])} invoke={jest.fn()} busy={false} />);
    expect(screen.queryByTestId('checks-repair')).toBeNull();
    expect(BAR_HIDDEN.has('addRepairToTemplate') && BAR_HIDDEN.has('saveRepairedTemplate')).toBe(true);
  });

  it('offers the template actions on the repair card and runs them through the job commands', () => {
    const add = { id: 'addRepairToTemplate', label: 'Add to template setup', enabled: true };
    const save = { id: 'saveRepairedTemplate', label: 'Save repaired image as template', enabled: false, reason: 'the repaired image is gone' };
    const invoke = jest.fn();
    const screen = render(<ChecksTab job={jobWith([repair()], [add, save])} invoke={invoke} busy={false} />);
    fireEvent.press(screen.getByTestId('checks-repair-addRepairToTemplate'));
    expect(invoke).toHaveBeenCalledWith(add);
    // A disabled action stays visible with its reason (the web shows it too).
    expect(screen.getByTestId('checks-repair-saveRepairedTemplate').props.accessibilityHint).toBe('the repaired image is gone');
  });

  it('hides the template actions until there is a committed repair', () => {
    const none = { id: 'addRepairToTemplate', label: 'Add to template setup', enabled: false, reason: 'no committed environment repair' };
    const screen = render(<ChecksTab job={jobWith([repair({ outcome: 'failed' })], [none])} invoke={jest.fn()} busy={false} />);
    expect(screen.queryByTestId('checks-repair-actions')).toBeNull();
  });
});
