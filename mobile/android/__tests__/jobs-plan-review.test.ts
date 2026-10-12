import {
  missingStageModels,
  planApprovalBody,
  planReviewDraft,
  splitLines,
} from '../src/api/jobs';

describe('missingStageModels', () => {
  it('reports every stage when nothing is set', () => {
    expect(missingStageModels(undefined)).toEqual(['plan', 'implement', 'review']);
  });

  it('needs both provider and model per stage', () => {
    expect(
      missingStageModels({
        plan: { provider: 'anthropic', model: 'opus' },
        implement: { provider: 'openai' },
        review: { provider: ' ', model: 'sonnet' },
      }),
    ).toEqual(['implement', 'review']);
  });
});

describe('planReviewDraft', () => {
  it('prefers job fields over plan and context', () => {
    const draft = planReviewDraft(
      {
        acceptance_criteria: ['job criterion'],
        validation_commands: [],
        metadata: { plan: { summary: 'S', steps: ['a', 'b'], validation_commands: ['pytest -q'] } },
      },
      { validation_commands: ['ctx cmd'] },
    );
    expect(draft.steps).toEqual(['a', 'b']);
    expect(draft.criteria).toEqual(['job criterion']);
    expect(draft.commands).toEqual(['ctx cmd']);
  });

  it('falls back to attention context plan', () => {
    const draft = planReviewDraft(
      { acceptance_criteria: [], validation_commands: [] },
      { plan: { steps: ['x'], acceptance_criteria: ['from plan'] } },
    );
    expect(draft.steps).toEqual(['x']);
    expect(draft.criteria).toEqual(['from plan']);
  });
});

describe('planApprovalBody', () => {
  it('splits edited lines and drops blanks', () => {
    expect(splitLines(' a \n\n b\r\n')).toEqual(['a', 'b']);
    expect(planApprovalBody('c1\n\nc2', 'pytest -q\n', ' ok ')).toEqual({
      decision: 'approve',
      detail: 'ok',
      value: { acceptance_criteria: ['c1', 'c2'], validation_commands: ['pytest -q'] },
    });
  });
});
