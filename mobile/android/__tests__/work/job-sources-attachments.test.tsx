// New job sheet parity with the web (mu/jobs/sources.py, mu/jobs/attachments.py): the Source switch
// (folder / Settings → Repositories / session), attached files, and saving a job's attachments.
import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

jest.mock('expo-document-picker', () => ({ getDocumentAsync: jest.fn() }));
jest.mock('expo-file-system', () => ({
  cacheDirectory: 'file:///cache/',
  EncodingType: { Base64: 'base64' },
  readAsStringAsync: jest.fn(async () => 'IyBzcGVj'),
  makeDirectoryAsync: jest.fn(async () => undefined),
  downloadAsync: jest.fn(async () => ({ status: 200 })),
  deleteAsync: jest.fn(async () => undefined),
  StorageAccessFramework: {
    requestDirectoryPermissionsAsync: jest.fn(async () => ({ granted: true, directoryUri: 'content://tree/Downloads' })),
    createFileAsync: jest.fn(async () => 'content://tree/Downloads/spec.md'),
    writeAsStringAsync: jest.fn(async () => undefined),
  },
}));
jest.mock('../../src/api/jobs', () => ({
  ...jest.requireActual('../../src/api/jobs'),
  jobsApi: { sessions: jest.fn(), getSettings: jest.fn(), create: jest.fn(), importTicket: jest.fn() },
}));
jest.mock('../../src/api/attachments', () => ({ attachmentsApi: { list: jest.fn() } }));
jest.mock('../../src/api/artifacts', () => ({ artifactsApi: { list: jest.fn() } }));

import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system';
import { configuredRepositories, jobsApi, type JobRules, type JobSessionOption } from '../../src/api/jobs';
import { attachmentsApi } from '../../src/api/attachments';
import { artifactsApi } from '../../src/api/artifacts';
import { saveJobAttachment, saveJobPatch } from '../../src/api/artifactDownload';
import { MAX_JOB_ATTACHMENT_BYTES, pickJobFiles } from '../../src/api/jobAttachments';
import { saveToken, resetAuthCache } from '../../src/api/auth';
import { useConnectionStore } from '../../src/store/connection';
import { NewJobSheet, extraSourcePayload } from '../../src/components/NewJobSheet';

const jobs = jobsApi as unknown as Record<string, jest.Mock>;
const picker = DocumentPicker as unknown as Record<string, jest.Mock>;
const fs = FileSystem as unknown as Record<string, jest.Mock>;

const S = (name: string, extra: Partial<JobSessionOption> = {}): JobSessionOption => ({
  name, workspace: `/work/${name}`, workspaces: [`/work/${name}`], provider: 'openai', model: 'gpt-x', agent_mode: 'default',
  yolo: false, session_type: 'workspace', loaded: false, current: false, updated_at: 1, ...extra,
});
const SESSIONS = [S('beta', { current: true }), S('alpha'), S('bare', { workspace: '', workspaces: [] })];
const RULES = { schema: [], overrides: {}, values: { 'jobs.repositories': [
  { name: 'backend', url: 'git@github.com:acme/backend.git', branch: 'main' },
  { name: '', url: '/ignored' },
] } } as unknown as JobRules;

beforeEach(() => {
  jest.clearAllMocks();
  jobs.sessions.mockResolvedValue({ current: 'beta', sessions: SESSIONS });
  jobs.getSettings.mockResolvedValue({ stage_models: {}, plan_approval: true, rules: RULES });
  jobs.create.mockResolvedValue({ job: { id: 'new1' } });
  (attachmentsApi.list as jest.Mock).mockResolvedValue({ attachments: [{ attachment_id: 'a1', name: 'trace.log', size: 4 }] });
  (artifactsApi.list as jest.Mock).mockResolvedValue({ artifacts: [{ artifact_id: 'r1', name: 'chart.html', size: 9, kind: 'visualization' }] });
});

const open = () => {
  const onCreated = jest.fn();
  const screen = render(<NewJobSheet visible sessionName="beta" provider={null} model={null} onClose={jest.fn()} onCreated={onCreated} />);
  return { screen, onCreated };
};

it('reads the repository list from Settings, dropping incomplete rows', () => {
  expect(configuredRepositories(RULES)).toEqual([{ name: 'backend', url: 'git@github.com:acme/backend.git', branch: 'main' }]);
  expect(configuredRepositories(undefined)).toEqual([]);
});

it('a configured repository is sent as a source by name', async () => {
  const { screen, onCreated } = open();
  fireEvent.press(await screen.findByTestId('job-source-repository'));
  await waitFor(() => expect(screen.getByTestId('job-source-hint').props.children).toMatch(/Clones git@github.com:acme\/backend.git, starting from main/));
  fireEvent.changeText(screen.getByTestId('job-title'), 'Fix login');
  fireEvent.press(screen.getByTestId('job-create-submit'));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 'new1' }));
  const body = jobs.create.mock.calls[0][0];
  expect(body.source).toEqual({ kind: 'repository', name: 'backend' });
  expect(body.workspace_root).toBeUndefined();
  expect(body.repository).toBeUndefined();
  expect(body.session_name).toBeUndefined();
});

it('a session source sends the session, the conversation choice and the session files picked', async () => {
  const { screen, onCreated } = open();
  fireEvent.press(await screen.findByTestId('job-source-session'));
  // Sessions without a workspace can't be a job's source.
  await screen.findByTestId('job-session-alpha');
  expect(screen.queryByTestId('job-session-bare')).toBeNull();
  fireEvent.press(screen.getByTestId('job-session-alpha'));
  fireEvent.press(screen.getByTestId('job-session-conversation'));
  fireEvent.press(screen.getByTestId('job-attach-session'));
  fireEvent.press(await screen.findByTestId('job-session-file-trace.log'));
  fireEvent.press(screen.getByTestId('job-session-file-chart.html'));
  expect(attachmentsApi.list).toHaveBeenCalledWith('alpha');
  expect(screen.getByTestId('job-attachment-trace.log')).toBeTruthy();
  fireEvent.changeText(screen.getByTestId('job-title'), 'Use the session');
  fireEvent.press(screen.getByTestId('job-create-submit'));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const body = jobs.create.mock.calls[0][0];
  expect(body.source).toEqual({ kind: 'session', session: 'alpha', include_conversation: true });
  expect(body.attachments).toEqual([{ session: 'alpha', attachment_id: 'a1' }, { session: 'alpha', artifact_id: 'r1' }]);
});

it('phone files travel base64-encoded and can be removed before queueing', async () => {
  picker.getDocumentAsync.mockResolvedValue({ canceled: false, assets: [
    { uri: 'file:///spec.md', name: 'spec.md', size: 6, mimeType: 'text/markdown' },
    { uri: 'file:///huge.bin', name: 'huge.bin', size: MAX_JOB_ATTACHMENT_BYTES + 1 },
    { uri: 'file:///notes.txt', name: 'notes.txt', size: 3 },
  ] });
  const { screen, onCreated } = open();
  await waitFor(() => expect(screen.getByLabelText('Workspace').props.value).toBe('/work/beta'));
  fireEvent.press(screen.getByTestId('job-attach-files'));
  await screen.findByTestId('job-attachment-spec.md');
  expect(screen.getByTestId('job-attach-error').props.children).toMatch(/huge\.bin/);
  fireEvent.press(screen.getByTestId('job-attachment-remove-notes.txt'));
  fireEvent.changeText(screen.getByTestId('job-title'), 'Read the spec');
  fireEvent.press(screen.getByTestId('job-create-submit'));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const body = jobs.create.mock.calls[0][0];
  // Folder source: the workspace path and its matched session, as before.
  expect(body.workspace_root).toBe('/work/beta');
  expect(body.session_name).toBe('beta');
  expect(body.source).toBeUndefined();
  expect(body.attachments).toEqual([{ name: 'spec.md', content_base64: 'IyBzcGVj', mime_type: 'text/markdown' }]);
});

it('stops reading files once the attachment limit is reached', async () => {
  picker.getDocumentAsync.mockResolvedValue({ canceled: false, assets: [
    { uri: 'file:///a', name: 'a', size: 1 }, { uri: 'file:///b', name: 'b', size: 1 }] });
  const out = await pickJobFiles(1);
  expect(out.added.map(a => a.name)).toEqual(['a']);
  expect(out.skipped).toEqual(['b']);
  expect(fs.readAsStringAsync).toHaveBeenCalledTimes(1);
});

it('saves a job attachment with the device token', async () => {
  resetAuthCache();
  await saveToken('http://10.0.0.2:30311', 'tok-9');
  useConnectionStore.setState({ baseUrl: 'http://10.0.0.2:30311' });
  expect(await saveJobAttachment('job 1', { name: 'spec v2.md', size: 6 })).toEqual({ saved: true, name: 'spec_v2.md' });
  const [url, , opts] = fs.downloadAsync.mock.calls[0];
  expect(url).toBe('http://10.0.0.2:30311/api/jobs/job%201/attachments/spec%20v2.md');
  expect(opts.headers.Authorization).toBe('Bearer tok-9');
});

it("saves a folder job's patch with the device token", async () => {
  resetAuthCache();
  await saveToken('http://10.0.0.2:30311', 'tok-9');
  useConnectionStore.setState({ baseUrl: 'http://10.0.0.2:30311' });
  expect(await saveJobPatch('0123456789abcdef')).toEqual({ saved: true, name: 'job-0123456789ab.patch' });
  const [url, , opts] = fs.downloadAsync.mock.calls[0];
  expect(url).toBe('http://10.0.0.2:30311/api/jobs/0123456789abcdef/patch');
  expect(opts.headers.Authorization).toBe('Bearer tok-9');
});

it('other sources are sent as `sources`: a Settings repository by name, a URL, a folder and a session', async () => {
  const { screen, onCreated } = open();
  await waitFor(() => expect(screen.getByLabelText('Workspace').props.value).toBe('/work/beta'));
  fireEvent.press(screen.getByTestId('job-source-add'));
  fireEvent.changeText(screen.getByTestId('job-extra-source-0-value'), '/work/shared-lib');
  fireEvent.press(screen.getByTestId('job-source-add'));
  fireEvent.press(screen.getByTestId('job-extra-source-1-repository'));
  fireEvent.changeText(screen.getByTestId('job-extra-source-1-value'), 'backend');
  fireEvent.press(screen.getByTestId('job-source-add'));
  fireEvent.press(screen.getByTestId('job-extra-source-2-session'));
  fireEvent.press(screen.getByTestId('job-source-add'));
  fireEvent.press(screen.getByTestId('job-extra-source-3-remove'));
  fireEvent.changeText(screen.getByTestId('job-title'), 'Change three repos');
  fireEvent.press(screen.getByTestId('job-create-submit'));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  expect(jobs.create.mock.calls[0][0].sources).toEqual([
    { kind: 'folder', path: '/work/shared-lib' },
    { kind: 'repository', name: 'backend' },
    { kind: 'session', session: 'beta' },
  ]);
  expect(extraSourcePayload([{ key: 'a', kind: 'repository', value: 'git@host:o/r.git' }, { key: 'b', kind: 'folder', value: '  ' }],
    configuredRepositories(RULES))).toEqual([{ kind: 'repository', url: 'git@host:o/r.git' }]);
});
