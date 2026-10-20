// Work screens rendered against view models captured from the real server
// (scripts/fixtures/capture_work.py → __tests__/fixtures/server/work/).
import React from 'react';
import { act, fireEvent, render, waitFor, within } from '@testing-library/react-native';

import home from '../fixtures/server/work/home.json';
import planReview from '../fixtures/server/work/job-plan-review.json';
import checked from '../fixtures/server/work/job-checked.json';
import listing from '../fixtures/server/work/artifacts.json';
import previewLog from '../fixtures/server/work/preview-log.json';
import previewJunit from '../fixtures/server/work/preview-junit.json';
import previewPcap from '../fixtures/server/work/preview-pcap.json';
import previewCsv from '../fixtures/server/work/preview-csv.json';
import previewJson from '../fixtures/server/work/preview-json.json';
import { jobCoreApi } from '../../src/api/jobCore';
import { checkArtifactsApi } from '../../src/api/checkArtifacts';
import { JobDetailScreen } from '../../src/screens/JobDetailScreen';
import { WorkScreen } from '../../src/screens/WorkScreen';
import { ArtifactBrowser } from '../../src/ui/ArtifactViewer';

jest.mock('../../src/api/jobCore', () => ({
  ...jest.requireActual('../../src/api/jobCore'),
  jobCoreApi: { home: jest.fn(), job: jest.fn(), command: jest.fn(), status: jest.fn(() => Promise.resolve({ agents: 1, checks: 0, needs_you: 1, engine: 'up' })), markViewed: jest.fn() },
  subscribeToJobChanges: jest.fn(() => ({ close: jest.fn() })),
}));
jest.mock('../../src/api/checkArtifacts', () => ({
  ...jest.requireActual('../../src/api/checkArtifacts'),
  checkArtifactsApi: { listing: jest.fn(), preview: jest.fn() },
}));
jest.mock('../../src/api/jobs', () => ({
  ...jest.requireActual('../../src/api/jobs'),
  jobsApi: { getSettings: jest.fn(() => Promise.resolve({ stage_models: {}, missing: [] })), sessions: jest.fn(() => new Promise(() => {})), diagnostics: jest.fn(() => new Promise(() => {})), attempts: jest.fn(() => Promise.resolve({ attempts: [] })) },
}));
jest.mock('../../src/api/setup', () => ({ ...jest.requireActual('../../src/api/setup'), setupApi: { get: jest.fn(() => Promise.resolve({ completed_at: 1 })) } }));
jest.mock('../../src/components/JobStageModelsSheet', () => ({ JobStageModelsSheet: () => null }));

const core = jobCoreApi as unknown as Record<string, jest.Mock>;
const arts = checkArtifactsApi as unknown as Record<string, jest.Mock>;
const nav = () => ({ setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()), navigate: jest.fn(), goBack: jest.fn(), dispatch: jest.fn() });
const PREVIEWS: Record<string, unknown> = Object.fromEntries([previewLog, previewJunit, previewPcap, previewCsv, previewJson].map(p => [p.path, p]));

beforeEach(() => {
  jest.clearAllMocks();
  arts.listing.mockResolvedValue(listing);
  arts.preview.mockImplementation((_job: string, _run: number, path: string) => Promise.resolve(PREVIEWS[path]));
});

it('the Now board pins the job waiting on its plan with its decisive action, and lists the running one', async () => {
  core.home.mockResolvedValue(home);
  const navigation = nav();
  const screen = render(<WorkScreen route={{} as any} navigation={navigation as any} />);
  const needs = await screen.findByTestId('now-needs-you');
  expect(within(needs).getByText('Add the widget', { exact: false })).toBeTruthy();
  const waiting = home.needs_you[0];
  fireEvent.press(within(needs).getByTestId(`needs-you-${waiting.id}-approvePlan`));
  expect(navigation.navigate).toHaveBeenCalledWith('JobDetail', { jobId: waiting.id, action: 'approvePlan' });
  expect(within(screen.getByTestId('now-running')).getByText('Fix the login race', { exact: false })).toBeTruthy();
  screen.unmount();
});

it('a job waiting on its plan opens on Plan with Approve plan as the one primary action', async () => {
  core.job.mockResolvedValue(planReview);
  const screen = render(<JobDetailScreen route={{ params: { jobId: planReview.id } } as any} navigation={nav() as any} />);
  await screen.findByTestId('job-header');
  expect(screen.getByTestId('job-page-header-tabs-plan').props.accessibilityState).toMatchObject({ selected: true });
  expect(screen.getByTestId('job-action-approvePlan')).toBeTruthy();
  // Hidden actions (raiseLimit, environments) never reach the bar.
  fireEvent.press(screen.getByTestId('job-action-more'));
  expect(screen.queryByText('Raise limit')).toBeNull();
  screen.unmount();
});

it('a checked job shows its notes and browses the run’s artifacts by kind', async () => {
  core.job.mockResolvedValue(checked);
  const screen = render(<JobDetailScreen route={{ params: { jobId: checked.id } } as any} navigation={nav() as any} />);
  await screen.findByTestId('job-header');
  expect(within(screen.getByTestId('note-e16')).getByText('1 of 1 checks failed.')).toBeTruthy();
  fireEvent.press(screen.getByTestId('note-e16-tab'));
  const run = checked.checks.runs[0].run;
  const browser = await screen.findByTestId(`artifacts-run-${run}`);
  expect(arts.listing).toHaveBeenCalledWith(checked.id, run);
  expect(within(browser).getByText('Suite output', { exact: false })).toBeTruthy();
  expect(within(browser).getByText('reports/junit.xml')).toBeTruthy();
  screen.unmount();
});

describe('ArtifactBrowser on the captured run', () => {
  const open = async (name: string) => {
    const screen = render(<ArtifactBrowser jobId="j" run={1} />);
    const file = listing.files.find(f => f.name === name)!;
    fireEvent.press(await screen.findByTestId(`artifacts-file-${file.path}`));
    await screen.findByTestId('artifact-viewer');
    return screen;
  };

  it('a log: counts lines, keeps only errors and warnings, finds text', async () => {
    const screen = await open('test.log');
    expect(within(screen.getByTestId('artifact-count')).getByText(/6 lines/)).toBeTruthy();
    fireEvent.press(screen.getByTestId('artifact-problems'));
    expect(within(screen.getByTestId('artifact-count')).getByText('3 of 6 lines')).toBeTruthy();
    expect(screen.getByLabelText('error line 3: ERROR boom')).toBeTruthy();
    expect(screen.getByLabelText('warn line 2: WARNING slow fixture')).toBeTruthy();
    fireEvent.changeText(screen.getByTestId('artifact-find'), 'boom');
    expect(within(screen.getByTestId('artifact-count')).getByText('1 of 6 lines')).toBeTruthy();
  });

  it('JUnit: failures first, then every test', async () => {
    const screen = await open('reports/junit.xml');
    const junit = screen.getByTestId('artifact-junit');
    expect(within(junit).getByText('test_bad')).toBeTruthy();
    expect(within(junit).queryByText('test_ok')).toBeNull();
    fireEvent.press(within(junit).getByText('All'));
    expect(within(screen.getByTestId('artifact-junit')).getByText('test_ok')).toBeTruthy();
    fireEvent.press(screen.getByTestId('artifact-mode-text'));
    expect(screen.getByTestId('artifact-text')).toBeTruthy();
  });

  it('a packet capture: packets by protocol, and conversations', async () => {
    const screen = await open('capture.pcap');
    const packets = screen.getByTestId('artifact-packets');
    expect(within(packets).getByText('query example.com')).toBeTruthy();
    fireEvent.press(screen.getByTestId('artifact-proto-TCP'));
    expect(within(screen.getByTestId('artifact-packets')).queryByText('query example.com')).toBeNull();
    fireEvent.press(screen.getByTestId('artifact-mode-talks'));
    expect(within(screen.getByTestId('artifact-talks')).getAllByText(/10\.0\.0\.2/).length).toBeGreaterThan(0);
  });

  it('CSV as a table, JSON as text', async () => {
    let screen = await open('timings.csv');
    expect(within(screen.getByTestId('artifact-table')).getByText('test_bad')).toBeTruthy();
    screen.unmount();
    screen = await open('coverage.json');
    expect(within(screen.getByTestId('artifact-text')).getByText(/"total": 81.5/)).toBeTruthy();
  });

  it('an HTML report is shown as source only, never rendered', async () => {
    arts.listing.mockResolvedValue({ ...listing, files: [{ path: 'artifacts/suite-1/report.html', name: 'report.html', kind: 'html', bytes: 40, label: 'suite-1', suites: ['check1'] }] });
    arts.preview.mockResolvedValue({ path: 'artifacts/suite-1/report.html', kind: 'html', bytes: 40, text: '<script>alert(1)</script><p>ok</p>' });
    const screen = render(<ArtifactBrowser jobId="j" run={1} />);
    fireEvent.press(await screen.findByTestId('artifacts-file-artifacts/suite-1/report.html'));
    expect(await screen.findByText(/aren't rendered on the phone/)).toBeTruthy();
    expect(within(screen.getByTestId('artifact-text')).getByText('<script>alert(1)</script><p>ok</p>')).toBeTruthy();
    expect(screen.queryByTestId('artifact-mode-html')).toBeNull();
  });

  it('says so when a run kept nothing', async () => {
    arts.listing.mockRejectedValue(new Error('not found'));
    const screen = render(<ArtifactBrowser jobId="j" run={9} />);
    expect(await screen.findByText(/No artifacts for run 9/)).toBeTruthy();
    await act(async () => undefined);
  });
});
