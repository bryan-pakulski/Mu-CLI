import React from 'react';
import { act, fireEvent, render, waitFor, within } from '@testing-library/react-native';
import { ApiError } from '../src/api/client';
import { formatRuleValue, groupDigits, jobsApi, rulesPatch, type JobRuleField, type JobRules } from '../src/api/jobs';
import { JobRulesForm, TOOLCHAIN_POLL_MS, parseRuleDraft } from '../src/components/JobRulesForm';
import { ToastHost } from '../src/components/planos';

jest.mock('../src/api/jobs', () => ({
  ...jest.requireActual('../src/api/jobs'),
  jobsApi: { getSettings: jest.fn(), saveRules: jest.fn(), toolchains: jest.fn(), buildToolchain: jest.fn() },
}));

const api = jobsApi as unknown as Record<string, jest.Mock>;

const RULES: JobRules = {
  schema: [
    { id: 'budget', title: 'Model budget', hint: 'Per pipeline stage, per run.', fields: [
      { path: 'sandbox.max_tokens', label: 'Token budget per stage', kind: 'int', default: 50_000_000, hint: 'Tokens a single stage run may use.', min: 1, max: 10_000_000_000, unit: 'tokens' },
      { path: 'sandbox.count_cache_reads', label: 'Count cache reads', kind: 'bool', default: false, hint: 'Off: only new tokens count.' },
    ] },
    { id: 'timeouts', title: 'Stage time limits', hint: 'Wall-clock limit.', fields: [
      { path: 'sandbox.stage_timeout_s.implement', label: 'Implement', kind: 'int', default: 3600, hint: '', min: 1, max: 86_400, unit: 'seconds' },
    ] },
    { id: 'sandbox', title: 'Sandbox', hint: 'The container.', fields: [
      { path: 'sandbox.memory', label: 'Memory limit', kind: 'text', default: '8g', hint: 'Docker memory value.', pattern: '[1-9][0-9]*[kmgKMG]?' },
      { path: 'sandbox.egress_extra', label: 'Extra egress domains', kind: 'list', default: [], hint: 'Domains.', item_pattern: '.*' },
    ] },
    { id: 'checks', title: 'Checks', hint: 'Host-run verification.', fields: [
      { path: 'checks.suites', label: 'Required check suites', kind: 'suites', default: [], hint: 'Added to every job.' },
    ] },
    { id: 'publish', title: 'Push', hint: 'What push does.', fields: [
      { path: 'publish.mode', label: 'Publish mode', kind: 'select', default: '', hint: 'Used when a job does not set its own mode.', options: [['', 'Per job (default)'], ['gh', 'GitHub CLI']] },
    ] },
  ],
  values: {
    'sandbox.max_tokens': 50_000_000, 'sandbox.count_cache_reads': false, 'sandbox.stage_timeout_s.implement': 7200,
    'sandbox.memory': '8g', 'sandbox.egress_extra': [], 'checks.suites': [], 'publish.mode': '',
  },
  overrides: { 'sandbox.stage_timeout_s.implement': 7200 },
};

const settings = (rules: JobRules = RULES) => ({ stage_models: {}, plan_approval: true, missing: [], rules });

function renderForm() {
  return render(<ToastHost><JobRulesForm active /></ToastHost>);
}

beforeEach(() => {
  jest.clearAllMocks();
  api.getSettings.mockResolvedValue(settings());
  api.toolchains.mockResolvedValue({ toolchains: [] });
});

describe('rule helpers', () => {
  it('builds a nested patch from dotted paths', () => {
    expect(rulesPatch({ 'sandbox.stage_timeout_s.implement': 7200, 'sandbox.max_tokens': 5, 'publish.mode': null }))
      .toEqual({ sandbox: { stage_timeout_s: { implement: 7200 }, max_tokens: 5 }, publish: { mode: null } });
  });

  it('formats values per kind', () => {
    expect(groupDigits(50_000_000)).toBe('50,000,000');
    expect(formatRuleValue(RULES.schema[0].fields[0], 50_000_000)).toBe('50,000,000 tokens');
    expect(formatRuleValue(RULES.schema[4].fields[0], '')).toBe('Per job (default)');
  });

  it('parses drafts by kind', () => {
    const [tokens] = RULES.schema[0].fields;
    expect(parseRuleDraft(tokens, '1,000')).toEqual({ value: 1000 });
    expect(parseRuleDraft(tokens, '1.5')).toEqual({ error: 'Token budget per stage must be a whole number' });
    expect(parseRuleDraft(RULES.schema[2].fields[1], 'pypi.org\n *.npmjs.org ,')).toEqual({ value: ['pypi.org', '*.npmjs.org'] });
  });
});

describe('JobRulesForm', () => {
  it('renders one panel per schema section with hints, defaults and set markers', async () => {
    const screen = renderForm();
    await screen.findByText('Run rules');
    for (const title of ['Model budget', 'Stage time limits', 'Sandbox', 'Checks', 'Push']) {
      expect(screen.getByText(title)).toBeTruthy();
    }
    expect(screen.getByText('Per pipeline stage, per run.')).toBeTruthy();
    expect(screen.getByText('default: 50,000,000 tokens')).toBeTruthy();
    expect(screen.getByTestId('rule-set-sandbox.stage_timeout_s.implement')).toBeTruthy();
    expect(screen.getByLabelText('Reset Implement')).toBeTruthy();
    expect(screen.queryByLabelText('Reset Token budget per stage')).toBeNull();
    expect(screen.getByLabelText('Implement').props.value).toBe('7200');
  });

  it('edits a field, shows the dirty indicator and saves a nested payload', async () => {
    const saved = { ...RULES, values: { ...RULES.values, 'sandbox.max_tokens': 1_000_000 }, overrides: { ...RULES.overrides, 'sandbox.max_tokens': 1_000_000 } };
    api.saveRules.mockResolvedValue(settings(saved));
    const screen = renderForm();
    fireEvent.changeText(await screen.findByLabelText('Token budget per stage'), '1,000,000');
    fireEvent(screen.getByTestId('rule-sandbox.count_cache_reads'), 'valueChange', true);
    expect(screen.getByText('2 unsaved')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Save Model budget'));
    await waitFor(() => expect(api.saveRules).toHaveBeenCalledWith({ sandbox: { max_tokens: 1_000_000, count_cache_reads: true } }));
    await waitFor(() => expect(screen.queryByText('2 unsaved')).toBeNull());
    expect(screen.getByTestId('rule-set-sandbox.max_tokens')).toBeTruthy();
    expect(await screen.findByText('Model budget saved')).toBeTruthy();
  });

  it('saves lists, suites and selects in their JSON shapes', async () => {
    api.saveRules.mockResolvedValue(settings());
    const screen = renderForm();
    fireEvent.changeText(await screen.findByLabelText('Extra egress domains'), 'pypi.org\n*.npmjs.org');
    fireEvent.press(screen.getByLabelText('Save Sandbox'));
    await waitFor(() => expect(api.saveRules).toHaveBeenLastCalledWith({ sandbox: { egress_extra: ['pypi.org', '*.npmjs.org'] } }));

    fireEvent.press(screen.getByText('Add suite'));
    fireEvent.changeText(screen.getByLabelText('Suite 1 name'), 'unit');
    fireEvent.changeText(screen.getByLabelText('Suite 1 command'), 'pytest -q');
    fireEvent.press(screen.getByLabelText('Save Checks'));
    await waitFor(() => expect(api.saveRules).toHaveBeenLastCalledWith({ checks: { suites: [{ name: 'unit', command: 'pytest -q' }] } }));

    fireEvent.press(within(screen.getByTestId('rule-publish.mode')).getByLabelText('GitHub CLI'));
    fireEvent.press(screen.getByLabelText('Save Push'));
    await waitFor(() => expect(api.saveRules).toHaveBeenLastCalledWith({ publish: { mode: 'gh' } }));
  });

  it('reset sends null for the path', async () => {
    const after = { ...RULES, values: { ...RULES.values, 'sandbox.stage_timeout_s.implement': 3600 }, overrides: {} };
    api.saveRules.mockResolvedValue(settings(after));
    const screen = renderForm();
    fireEvent.press(await screen.findByLabelText('Reset Implement'));
    await waitFor(() => expect(api.saveRules).toHaveBeenCalledWith({ sandbox: { stage_timeout_s: { implement: null } } }));
    await waitFor(() => expect(screen.queryByLabelText('Reset Implement')).toBeNull());
    expect(screen.getByText('default: 3,600 seconds')).toBeTruthy();
  });

  it('shows a server 400 inline on the matching field', async () => {
    api.saveRules.mockRejectedValue(new ApiError(400, 'Memory limit is not valid'));
    const screen = renderForm();
    fireEvent.changeText(await screen.findByLabelText('Memory limit'), 'lots');
    fireEvent.press(screen.getByLabelText('Save Sandbox'));
    const alert = await screen.findByText('Memory limit is not valid');
    expect(within(screen.getByTestId('rule-sandbox.memory')).getByText('Memory limit is not valid')).toBe(alert);
    expect(screen.getByText('1 unsaved')).toBeTruthy();
  });

  it('blocks a save locally when a number does not parse', async () => {
    const screen = renderForm();
    fireEvent.changeText(await screen.findByLabelText('Token budget per stage'), 'abc');
    fireEvent.press(screen.getByLabelText('Save Model budget'));
    expect(await screen.findByText('Token budget per stage must be a number')).toBeTruthy();
    expect(api.saveRules).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------ rows + toolchains */

const TOOLCHAINS: JobRuleField = {
  path: 'sandbox.toolchains', label: 'Repository toolchain images', kind: 'rows', default: [], hint: 'Base image = an image with the toolchain.',
  columns: [
    { key: 'repository', label: 'Repository path', placeholder: '/home/me/src/project', required: true },
    { key: 'base_image', label: 'Base image', placeholder: 'rust:1.88-bookworm', required: true, pattern: '[\\w./:@-]+' },
  ],
};
const ROW = { repository: '/src/app', base_image: 'rust:1.88' };

function rulesWithToolchains(rows: Array<Record<string, string>> = []): JobRules {
  return {
    schema: [...RULES.schema, { id: 'toolchains', title: 'Toolchains', hint: 'Per-repository build environments.', fields: [TOOLCHAINS] }],
    values: { ...RULES.values, 'sandbox.toolchains': rows },
    overrides: rows.length ? { ...RULES.overrides, 'sandbox.toolchains': rows } : RULES.overrides,
  };
}

describe('rows rules', () => {
  it('formats and parses rows by column', () => {
    expect(formatRuleValue(TOOLCHAINS, [])).toBe('none');
    expect(formatRuleValue(TOOLCHAINS, [ROW, { repository: '/b', base_image: 'node:22' }])).toBe('/src/app · rust:1.88; /b · node:22');
    expect(parseRuleDraft(TOOLCHAINS, [{ repository: ' /src/app ', base_image: 'rust:1.88 ' }, { repository: '', base_image: '' }]))
      .toEqual({ value: [ROW] });
    expect(parseRuleDraft(TOOLCHAINS, [{ repository: '/src/app', base_image: '' }]))
      .toEqual({ error: 'Repository toolchain images: Base image is required' });
    expect(parseRuleDraft(TOOLCHAINS, [{ repository: '/src/app', base_image: 'rust 1.88' }]))
      .toEqual({ error: "Repository toolchain images: Base image 'rust 1.88' is not valid" });
  });

  it('adds a row, edits its cells and saves the list of objects', async () => {
    api.getSettings.mockResolvedValue(settings(rulesWithToolchains()));
    api.saveRules.mockResolvedValue(settings(rulesWithToolchains([ROW])));
    const screen = renderForm();
    expect(await screen.findByTestId('toolchain-images-empty')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Add Repository toolchain images row'));
    expect(screen.getByText('1 unsaved')).toBeTruthy();
    fireEvent.changeText(screen.getByLabelText('Repository path 1'), '/src/app');
    expect(screen.getByText('Base image is required')).toBeTruthy();
    fireEvent.changeText(screen.getByLabelText('Base image 1'), 'rust 1.88');
    expect(screen.getByText("Base image 'rust 1.88' is not valid")).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Save Toolchains'));
    expect(await screen.findByText("Repository toolchain images: Base image 'rust 1.88' is not valid")).toBeTruthy();
    expect(api.saveRules).not.toHaveBeenCalled();

    fireEvent.changeText(screen.getByLabelText('Base image 1'), 'rust:1.88');
    expect(screen.queryByText("Base image 'rust 1.88' is not valid")).toBeNull();
    fireEvent.press(screen.getByLabelText('Save Toolchains'));
    await waitFor(() => expect(api.saveRules).toHaveBeenCalledWith({ sandbox: { toolchains: [ROW] } }));
    expect(await screen.findByText('Toolchains saved')).toBeTruthy();
    expect(screen.getByTestId('rule-set-sandbox.toolchains')).toBeTruthy();
    await waitFor(() => expect(api.toolchains).toHaveBeenCalledTimes(2)); // re-read after the rows changed
  });

  it('removes a saved row and saves an empty list', async () => {
    api.getSettings.mockResolvedValue(settings(rulesWithToolchains([ROW])));
    api.saveRules.mockResolvedValue(settings(rulesWithToolchains()));
    const screen = renderForm();
    expect((await screen.findByLabelText('Repository path 1')).props.value).toBe('/src/app');
    expect(screen.getByLabelText('Base image 1').props.value).toBe('rust:1.88');
    fireEvent.press(screen.getByLabelText('Remove row 1'));
    expect(screen.queryByLabelText('Repository path 1')).toBeNull();
    fireEvent.press(screen.getByLabelText('Save Toolchains'));
    await waitFor(() => expect(api.saveRules).toHaveBeenCalledWith({ sandbox: { toolchains: [] } }));
  });

  it('shows a server 400 for rows inline on the field', async () => {
    api.getSettings.mockResolvedValue(settings(rulesWithToolchains()));
    api.saveRules.mockRejectedValue(new ApiError(400, 'Repository toolchain images: Repository path is required'));
    const screen = renderForm();
    fireEvent.press(await screen.findByLabelText('Add Repository toolchain images row'));
    fireEvent.changeText(screen.getByLabelText('Repository path 1'), '/src/app');
    fireEvent.changeText(screen.getByLabelText('Base image 1'), 'rust:1.88');
    fireEvent.press(screen.getByLabelText('Save Toolchains'));
    const alert = await screen.findByText('Repository toolchain images: Repository path is required');
    expect(within(screen.getByTestId('rule-sandbox.toolchains')).getByText('Repository toolchain images: Repository path is required')).toBe(alert);
  });
});

describe('toolchain images', () => {
  afterEach(() => jest.useRealTimers());

  it('shows status, builds, polls every 3s while building and toggles the build log', async () => {
    jest.useFakeTimers();
    api.getSettings.mockResolvedValue(settings(rulesWithToolchains([ROW])));
    api.toolchains
      .mockResolvedValueOnce({ toolchains: [{ ...ROW, tag: 'mucli-job-toolchain:rust', status: 'missing' }] })
      .mockResolvedValueOnce({ toolchains: [{ ...ROW, status: 'building', log_tail: 'Probing rust:1.88 …\nStep 2/7 : RUN apt-get\n' }] })
      .mockResolvedValue({ toolchains: [{ ...ROW, status: 'built', log_tail: 'Built\n' }] });
    api.buildToolchain.mockResolvedValue({ ...ROW, status: 'building' });
    const screen = renderForm();
    const images = within(await screen.findByTestId('toolchain-images'));
    expect(images.getByLabelText('not built')).toBeTruthy();
    expect(images.getByText('/src/app ← rust:1.88')).toBeTruthy();

    fireEvent.press(images.getByLabelText('Build rust:1.88'));
    await waitFor(() => expect(api.buildToolchain).toHaveBeenCalledWith('rust:1.88'));
    expect(await screen.findByLabelText('building')).toBeTruthy();
    expect(screen.queryByLabelText('Build rust:1.88')).toBeNull();
    expect(api.toolchains).toHaveBeenCalledTimes(2);

    fireEvent.press(screen.getByLabelText('Show build log for rust:1.88'));
    expect(screen.getByTestId('toolchain-log-rust:1.88').props.children).toContain('Step 2/7 : RUN apt-get');
    fireEvent.press(screen.getByLabelText('Hide build log for rust:1.88'));
    expect(screen.queryByTestId('toolchain-log-rust:1.88')).toBeNull();

    await act(async () => { jest.advanceTimersByTime(TOOLCHAIN_POLL_MS); });
    expect(await screen.findByLabelText('built')).toBeTruthy();
    expect(api.toolchains).toHaveBeenCalledTimes(3);
    expect(screen.getByLabelText('Rebuild rust:1.88')).toBeTruthy();
    await act(async () => { jest.advanceTimersByTime(TOOLCHAIN_POLL_MS * 3); });
    expect(api.toolchains).toHaveBeenCalledTimes(3); // no polling once nothing is building
  });

  it('shows a failed build with its error and a build error toast', async () => {
    api.getSettings.mockResolvedValue(settings(rulesWithToolchains([ROW])));
    api.toolchains.mockResolvedValue({ toolchains: [{ ...ROW, status: 'failed', error: 'docker build failed', log_tail: 'E: boom\n' }] });
    api.buildToolchain.mockRejectedValue(new ApiError(400, 'Add this base image to Settings → Toolchains first'));
    const screen = renderForm();
    expect(await screen.findByLabelText('failed')).toBeTruthy();
    expect(screen.getByText('docker build failed')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Build rust:1.88'));
    expect(await screen.findByText('Add this base image to Settings → Toolchains first')).toBeTruthy();
  });
});
