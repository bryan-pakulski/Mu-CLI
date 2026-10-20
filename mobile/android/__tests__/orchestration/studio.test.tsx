// Studio on the phone: a tool draft is edited and saved (updateDraft), linted after saving, tested on
// a canary (proposeTest) and submitted for review with a headline; a runbook draft is edited in the
// step form with the server's problems at their field, saved with base_digest so someone else's
// change is never overwritten; and the catalog's actions open the right dialogs.
import React from 'react';
import { act, fireEvent, render, waitFor, within } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: { tool: jest.fn(), runbook: jest.fn(), tools: jest.fn(), nodes: jest.fn(), toolResults: jest.fn(), command: jest.fn() },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));

import { ApiError } from '../../src/api/client';
import { orchestrationApi } from '../../src/api/orchestration';
import { OrchToolEditorScreen, draftChanges } from '../../src/screens/orchestration/ToolEditorScreen';
import { OrchRunbookEditorScreen, groupProblems } from '../../src/screens/orchestration/RunbookEditorScreen';
import { OrchToolScreen } from '../../src/screens/orchestration/ToolScreen';
import { NewRunbookSheet, StudioView, stageSteps } from '../../src/screens/orchestration/StudioView';
import { specToModel } from '../../src/screens/orchestration/runbookModel';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const fx = (name: string) => JSON.parse(JSON.stringify(require(`../fixtures/server/orchestration/${name}.json`)));
const navigation = { navigate: jest.fn(), goBack: jest.fn(), replace: jest.fn(), push: jest.fn() };
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);
const ok = (data: Record<string, unknown> = {}) => ({ ok: true, command: 'x', data });

beforeEach(() => {
  jest.clearAllMocks();
  api.tool.mockResolvedValue(fx('tool_draft'));
  api.runbook.mockResolvedValue(fx('runbook'));
  api.tools.mockResolvedValue(fx('tools'));
  api.nodes.mockResolvedValue(fx('nodes'));
  api.command.mockImplementation(async (type: string) => (type === 'validateRunbook' ? ok(fx('validate_runbook')) : ok()));
});

describe('tool drafts', () => {
  it('lists open drafts with their discovery progress', async () => {
    const draft = fx('tool_draft');
    expect(stageSteps(draft).map(s => s.status)).toEqual(['active', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending']);
    const ui = wrap(<StudioView openToolEditor={jest.fn()} openRunbookEditor={jest.fn()} openTool={jest.fn()} openRunbook={jest.fn()} />);
    expect(await ui.findByTestId(`orch-draft-${draft.ref}`)).toBeTruthy();
    expect(within(ui.getByTestId('orch-studio-runbooks')).getByText('Safe check')).toBeTruthy();
  });

  it('saves only what changed: the manifest and new files', () => {
    expect(draftChanges('t@1', null, { 'a.sh': 'x' }, new Set(), new Set())).toEqual({ ref: 't@1' });
    expect(draftChanges('t@1', 'id: t', { 'a.sh': 'x', 'b.py': 'y' }, new Set(['b.py']), new Set(['old.sh'])))
      .toEqual({ ref: 't@1', manifest: 'id: t', files: { 'b.py': 'y' }, remove_files: ['old.sh'] });
  });

  it('edits the manifest and adds a file, then saves before linting', async () => {
    const draft = fx('tool_draft');
    const ui = wrap(<OrchToolEditorScreen route={{ params: { toolId: draft.tool_id, version: draft.version } }} navigation={navigation} />);
    fireEvent.changeText(await ui.findByTestId('orch-manifest'), 'id: demo.rotate\ntitle: Rotate\n');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-file-add')); });
    fireEvent.changeText(ui.getByTestId('orch-file-name'), 'rotate.sh');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-file-dialog-add')); });
    fireEvent.changeText(ui.getByTestId('orch-file-rotate.sh'), '#!/bin/sh\necho hi\n');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-tool-editor-actions-more')); });
    await act(async () => { fireEvent.press(ui.getByText('Run lint')); });
    await waitFor(() => expect(api.command).toHaveBeenCalledWith('lintDraft', { ref: draft.ref }));
    const calls = api.command.mock.calls.map(c => c[0]);
    expect(calls.indexOf('updateDraft')).toBeLessThan(calls.indexOf('lintDraft'));
    expect(api.command).toHaveBeenCalledWith('updateDraft', { ref: draft.ref, manifest: 'id: demo.rotate\ntitle: Rotate\n', files: { 'rotate.sh': '#!/bin/sh\necho hi\n' } });
  });

  it('refuses a file name with a path in it', async () => {
    const draft = fx('tool_draft');
    const ui = wrap(<OrchToolEditorScreen route={{ params: { toolId: draft.tool_id, version: draft.version } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-file-add')); });
    fireEvent.changeText(ui.getByTestId('orch-file-name'), '../etc/passwd');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-file-dialog-add')); });
    expect(ui.queryByTestId('orch-file-../etc/passwd')).toBeNull();
  });

  it('tests a draft on a canary node, then opens the test run', async () => {
    const draft = fx('tool_draft');
    const canary = fx('nodes').nodes.find((n: any) => n.canary);
    api.command.mockResolvedValue(ok({ run: { id: 'r_test', state: 'proposed' } }));
    const ui = wrap(<OrchToolEditorScreen route={{ params: { toolId: draft.tool_id, version: draft.version } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-tool-editor-actions-more')); });
    await act(async () => { fireEvent.press(ui.getByText('Test on canary')); });
    await ui.findByTestId(`orch-canary-${canary.id}`);
    await act(async () => { fireEvent.press(ui.getByTestId('orch-test-tool-go')); });
    expect(api.command).toHaveBeenCalledWith('proposeTest', expect.objectContaining({ ref: draft.ref, node_id: canary.id, test_name: '', params: {}, check_mode: !!draft.check_mode || draft.effect === 'read_only' }));
    expect(navigation.navigate).toHaveBeenCalledWith('OrchRun', { runId: 'r_test' });
  });

  it('submits for review only with a headline, sending the risks one per line', async () => {
    const draft = fx('tool_draft');
    const ui = wrap(<OrchToolEditorScreen route={{ params: { toolId: draft.tool_id, version: draft.version } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-tool-editor-actions-more')); });
    await act(async () => { fireEvent.press(ui.getByText('Submit for review')); });
    await act(async () => { fireEvent.press(ui.getByTestId('orch-submit-go')); });
    expect(ui.getByTestId('orch-submit-error')).toBeTruthy();
    fireEvent.changeText(ui.getByTestId('orch-submit-headline'), 'Rotate logs safely');
    fireEvent.changeText(ui.getByTestId('orch-submit-risks'), 'Runs as root\n\nReopens nginx');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-submit-go')); });
    expect(api.command).toHaveBeenCalledWith('submitReview', { ref: draft.ref, headline: 'Rotate logs safely', risks: ['Runs as root', 'Reopens nginx'], notes: '' });
  });

  it('a published tool offers Run, which reads the plan before proposing', async () => {
    api.tool.mockResolvedValue(fx('tool'));
    api.command.mockImplementation(async (type: string) => (type === 'planRun'
      ? ok({ plan: { ok: true, nodes: ['n1', 'n2'], blast_radius: '2 nodes in 2 batches', effect: 'read_only', decision: { level: 'allow', summary: 'allowed by rule' }, batches: [['n1'], ['n2']] } })
      : ok({ run: { id: 'r_new', state: 'running' } })));
    const ui = wrap(<OrchToolScreen route={{ params: { toolId: 'demo.echo', version: 1 } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-tool-actions-runTool')); });
    fireEvent.changeText(ui.getByTestId('orch-run-sheet-labels'), 'role=web');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-run-sheet-plan')); });
    expect(api.command).toHaveBeenCalledWith('planRun', { kind: 'tool', target: 'demo.echo@1', params: { message: 'hi' }, selector: { labels: 'role=web' }, check_mode: false });
    expect(ui.getByText('2 nodes in 2 batches')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-run-sheet-propose')); });
    expect(api.command).toHaveBeenCalledWith('proposeRun', expect.objectContaining({ kind: 'tool', target: 'demo.echo@1', selector: { labels: 'role=web' } }));
    expect(navigation.navigate).toHaveBeenCalledWith('OrchRun', { runId: 'r_new' });
  });
});

describe('runbook drafts', () => {
  const open = () => {
    const rb = fx('runbook');
    return { rb, ui: wrap(<OrchRunbookEditorScreen route={{ params: { runbookId: rb.runbook_id, version: rb.version } }} navigation={navigation} />) };
  };

  it('shows the server’s problems at their step and field, and says the draft can’t be submitted', async () => {
    const { rb, ui } = open();
    expect(await ui.findByText(/1 problem — this draft can’t be submitted/)).toBeTruthy();
    expect(within(ui.getByTestId('orch-rb-step-1')).getAllByText(/choose a pinned tool version/).length).toBeGreaterThan(0);
    expect(within(ui.getByTestId('orch-rb-step-0')).queryByText(/choose a pinned tool version/)).toBeNull();
    expect(groupProblems(fx('validate_runbook').problems, specToModel(rb.spec)).map(([g]) => g)).toEqual(['Step 2 · bad']);
  });

  it('checks edits on the server and saves with the digest it was loaded at', async () => {
    const { rb, ui } = open();
    await ui.findByTestId('orch-rb-step-1-tool');
    fireEvent.changeText(ui.getByTestId('orch-rb-step-1-tool'), 'demo.echo@1');
    await waitFor(() => expect(api.command).toHaveBeenCalledWith('validateRunbook', { spec: expect.objectContaining({ steps: expect.arrayContaining([expect.objectContaining({ name: 'bad', tool: 'demo.echo@1' })]) }) }), { timeout: 2000 });
    api.command.mockResolvedValue(ok({ digest: 'sha256:new', problems: [] }));
    await act(async () => { fireEvent.press(ui.getByTestId('orch-rb-save')); });
    expect(api.command).toHaveBeenCalledWith('saveRunbookDraft', { spec: expect.objectContaining({ id: rb.spec.id }), runbook_id: rb.runbook_id, base_digest: rb.digest });
  });

  it('never overwrites someone else’s change: a 409 offers Reload or Keep mine', async () => {
    const { ui } = open();
    fireEvent.changeText(await ui.findByTestId('orch-rb-title'), 'Safer check');
    api.command.mockImplementation(async (type: string) => {
      if (type === 'saveRunbookDraft') throw new ApiError(409, 'the draft changed', { detail: 'the draft changed', data: { conflict: true, digest: 'sha256:other' } });
      return ok(fx('validate_runbook'));
    });
    await act(async () => { fireEvent.press(ui.getByTestId('orch-rb-save')); });
    expect(ui.getByTestId('orch-rb-stale')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-rb-keep')); });
    api.command.mockClear();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-rb-save')); });
    expect(api.command).not.toHaveBeenCalledWith('saveRunbookDraft', expect.anything());
  });

  it('switches to YAML through the server, so both views describe one document', async () => {
    const { ui } = open();
    await ui.findByTestId('orch-rb-step-0');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-rb-tabs-yaml')); });
    expect(ui.getByTestId('orch-rb-yaml').props.value).toContain('id: web.safe-check');
  });

  it('creates a runbook only with a dotted lowercase id and a title', async () => {
    api.command.mockResolvedValue(ok({ ref: 'web.deploy@1' }));
    const onCreated = jest.fn();
    const ui = wrap(<NewRunbookSheet visible onClose={jest.fn()} onCreated={onCreated} />);
    fireEvent.changeText(ui.getByTestId('orch-new-runbook-id'), 'Deploy');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-new-runbook-create')); });
    expect(ui.getByTestId('orch-new-runbook-error')).toBeTruthy();
    fireEvent.changeText(ui.getByTestId('orch-new-runbook-id'), 'web.deploy');
    fireEvent.changeText(ui.getByTestId('orch-new-runbook-title'), 'Deploy');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-new-runbook-create')); });
    expect(api.command).toHaveBeenCalledWith('saveRunbookDraft', { spec: { id: 'web.deploy', title: 'Deploy', steps: [{ name: 'step1', tool: '' }] } });
    expect(onCreated).toHaveBeenCalledWith('web.deploy@1');
  });
});
