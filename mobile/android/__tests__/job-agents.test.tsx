import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

import {
  CODEX_IMPLEMENT_WARNING, agentProblem, agentsApi, defaultModel, draftsFromDefaults, stageAgentsLabel, stageAgentsPayload,
  type AgentsStatus,
} from '../src/api/agents';
import { api } from '../src/api/client';
import { AgentsSettings, StageAgentsField } from '../src/components/JobAgents';
import { ToastHost } from '../src/components/planos';

jest.mock('../src/api/client', () => ({ api: { get: jest.fn(), put: jest.fn() } }));

const STATUS: AgentsStatus = {
  mucli: { agent: 'mucli', label: 'mucli', ready: true, problems: [], warnings: [] },
  claude: { agent: 'claude', label: 'Claude Code', ready: true, problems: [], warnings: ['glibc image needed'],
    binary: '/home/me/.local/bin/claude', version: '2.1.299 (Claude Code)', credential: 'api_key', credential_source: 'ANTHROPIC_API_KEY',
    default_models: { plan: 'claude-opus-5-5', implement: 'claude-sonnet-5-5', review: 'claude-opus-5-5' },
    models: { plan: 'claude-opus-5-5', implement: 'claude-sonnet-5-5', review: 'claude-opus-5-5' } },
  codex: { agent: 'codex', label: 'Codex', ready: false, problems: ['Codex is not installed'], warnings: [],
    default_models: { plan: '', implement: '', review: '' }, models: { plan: '', implement: '', review: '' } },
  defaults: { plan: 'claude', implement: 'mucli', review: 'mucli' },
  secrets: { CLAUDE_CODE_OAUTH_TOKEN: '', ANTHROPIC_API_KEY: 'environment', OPENAI_API_KEY: '' },
  setup: { claude: ['Install Claude Code'], codex: ['Install Codex then run codex login'] },
};

beforeEach(() => jest.clearAllMocks());

describe('agent helpers', () => {
  it('omits stage_agents when nothing differs from the defaults', () => {
    const drafts = draftsFromDefaults(STATUS.defaults);
    expect(drafts.plan.agent).toBe('claude');
    expect(stageAgentsPayload(drafts, STATUS.defaults)).toBeNull();
  });

  it('sends every stage once one is changed, with models only for external agents', () => {
    const drafts = draftsFromDefaults(STATUS.defaults);
    drafts.implement = { agent: 'codex', model: ' gpt-5.5 ', touched: true };
    drafts.review = { agent: 'mucli', model: 'ignored', touched: true };
    expect(stageAgentsPayload(drafts, STATUS.defaults)).toEqual({
      plan: { agent: 'claude' }, implement: { agent: 'codex', model: 'gpt-5.5' }, review: { agent: 'mucli' } });
  });

  it('flags not-ready agents and resolves placeholder models', () => {
    expect(agentProblem(STATUS, 'codex')).toBe('Codex is not installed');
    expect(agentProblem(STATUS, 'claude')).toBe('');
    expect(agentProblem(STATUS, 'mucli')).toBe('');
    expect(defaultModel(STATUS, 'claude', 'implement')).toBe('claude-sonnet-5-5');
    expect(defaultModel(STATUS, 'mucli', 'plan')).toBe('');
  });

  it('labels the job header only when an external agent is used', () => {
    const mucli = { agent: 'mucli', model: '', effort: '', source: 'settings', label: 'mucli' };
    expect(stageAgentsLabel({ plan: mucli, implement: mucli, review: mucli })).toBeNull();
    expect(stageAgentsLabel({ plan: { ...mucli, agent: 'claude', label: 'Claude Code', model: 'claude-opus-5-5' }, implement: mucli, review: mucli }))
      .toBe('plan Claude Code claude-opus-5-5 · implement mucli · review mucli');
  });

  it('calls the agents endpoints', async () => {
    (api.get as jest.Mock).mockResolvedValue(STATUS);
    (api.put as jest.Mock).mockResolvedValue(STATUS);
    await agentsApi.status();
    await agentsApi.saveSecret('CLAUDE_CODE_OAUTH_TOKEN', 'tok');
    expect((api.get as jest.Mock).mock.calls[0][0]).toBe('/api/jobs/agents');
    expect(api.put).toHaveBeenCalledWith('/api/jobs/agents/secret', { key: 'CLAUDE_CODE_OAUTH_TOKEN', value: 'tok' });
  });
});

describe('StageAgentsField', () => {
  it('switches a stage agent, shows its problem and the Codex implement warning', () => {
    const onChange = jest.fn();
    const value = draftsFromDefaults(STATUS.defaults);
    const screen = render(<StageAgentsField status={STATUS} value={value} onChange={onChange} />);
    expect(screen.getByTestId('stage-agent-model-plan').props.placeholder).toBe('claude-opus-5-5');
    fireEvent.press(screen.getAllByLabelText('Codex')[1]);
    expect(onChange.mock.calls[0][0].implement).toMatchObject({ agent: 'codex', touched: true });
    screen.rerender(<StageAgentsField status={STATUS} value={onChange.mock.calls[0][0]} onChange={onChange} />);
    expect(screen.getByText('Codex is not installed')).toBeTruthy();
    expect(screen.getByText(CODEX_IMPLEMENT_WARNING)).toBeTruthy();
  });
});

describe('AgentsSettings', () => {
  it('shows status, setup steps and saves the setup token without echoing it', async () => {
    (api.get as jest.Mock).mockResolvedValue(STATUS);
    (api.put as jest.Mock).mockResolvedValue({ ...STATUS, secrets: { ...STATUS.secrets, CLAUDE_CODE_OAUTH_TOKEN: 'secrets.env' } });
    const screen = render(<ToastHost><AgentsSettings active /></ToastHost>);
    await waitFor(() => expect(screen.getByTestId('agent-status-claude')).toBeTruthy());
    expect(screen.getByText(/Claude Code · ready/)).toBeTruthy();
    expect(screen.getByText(/Codex · not ready/)).toBeTruthy();
    expect(screen.getByText('→ Install Codex then run codex login')).toBeTruthy();
    expect(screen.queryByText('→ Install Claude Code')).toBeNull();
    const input = screen.getByTestId('claude-token-input');
    expect(input.props.secureTextEntry).toBe(true);
    fireEvent.changeText(input, 'sk-ant-oat-secret');
    fireEvent.press(screen.getByTestId('claude-token-save'));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/jobs/agents/secret', { key: 'CLAUDE_CODE_OAUTH_TOKEN', value: 'sk-ant-oat-secret' }));
    await waitFor(() => expect(screen.getByTestId('claude-token-clear')).toBeTruthy());
    expect(screen.getByTestId('claude-token-input').props.value).toBe('');
    expect(screen.queryByText(/sk-ant-oat-secret/)).toBeNull();
  });
});
