// New session on mobile: a workspace folder that doesn't exist yet is created by the server
// (`create_workspace`) instead of failing; the path field says so.
import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

jest.mock('../../src/api/providers', () => ({
  providersApi: {
    list: jest.fn(async () => ({ providers: [{ name: 'openai', configured: true }] })),
    listModels: jest.fn(async () => ({ models: ['gpt-x'] })),
  },
}));
jest.mock('../../src/api/containers', () => ({ containersApi: { list: jest.fn(async () => ({ containers: [], templates: [] })) } }));
jest.mock('../../src/api/sessions', () => ({
  sessionsApi: {
    getContainerDefaults: jest.fn(async () => ({ dockerfile: '', egress_allow: [], egress_deny: [] })),
    suggestWorkspaces: jest.fn(async (path: string) => ({ query: path, resolved_path: path, exists: false, suggestions: [] })),
    create: jest.fn(async () => ({})),
  },
}));
jest.mock('../../src/api/client', () => ({
  ...jest.requireActual('../../src/api/client'),
  api: { post: jest.fn(async () => ({})), get: jest.fn() },
}));

import { sessionsApi } from '../../src/api/sessions';
import { NewSessionSheet } from '../../src/components/NewSessionSheet';

const sessions = sessionsApi as unknown as Record<string, jest.Mock>;

it('asks the server to create a missing workspace folder', async () => {
  const onCreated = jest.fn();
  const screen = render(<NewSessionSheet visible onClose={jest.fn()} onCreated={onCreated} />);
  fireEvent.changeText(screen.getByPlaceholderText('project-research'), 'fresh');
  fireEvent.press(screen.getByText('Continue'));
  await screen.findByText('gpt-x');
  fireEvent.press(screen.getByText('Continue'));
  const field = screen.getByPlaceholderText('Browse a workspace folder');
  fireEvent(field, 'focus');
  fireEvent.changeText(field, '/home/me/src/new-project');
  await screen.findByText('New folder: /home/me/src/new-project is created with the session.');
  fireEvent.press(screen.getByText('Continue'));
  fireEvent.press(await screen.findByText('Create and load'));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ name: 'fresh', provider: 'openai', model: 'gpt-x' }));
  const [name, provider, model, workspace, options] = sessions.create.mock.calls[0];
  expect([name, provider, model, workspace]).toEqual(['fresh', 'openai', 'gpt-x', '/home/me/src/new-project']);
  expect(options).toMatchObject({ sessionType: 'workspace', createWorkspace: true });
});

it('sends create_workspace in the create request', async () => {
  const { sessionsApi: realApi } = jest.requireActual('../../src/api/sessions');
  const { api } = jest.requireMock('../../src/api/client') as { api: { post: jest.Mock } };
  await realApi.create('fresh', 'openai', 'gpt-x', '/home/me/new', { sessionType: 'workspace', createWorkspace: true });
  expect(api.post).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({ workspace: '/home/me/new', create_workspace: true }));
  api.post.mockClear();
  await realApi.create('chatty', 'openai', 'gpt-x', undefined, { sessionType: 'chat' });
  expect(api.post.mock.calls[0][1].create_workspace).toBeUndefined();
});
