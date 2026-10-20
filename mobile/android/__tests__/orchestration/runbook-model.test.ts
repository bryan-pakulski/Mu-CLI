// The runbook editor model is the web's (mu/gui/static/js/orchestration/runbooks.js "pure model")
// ported to TS. Both run on the same specs and must agree exactly: a spec survives form → spec,
// unknown keys ride along, untouched inputs give back their property, edits coerce the same way.
import fs from 'fs';
import path from 'path';
import * as ts from '../../src/screens/orchestration/runbookModel';

const WEB = path.resolve(__dirname, '../../../../mu/gui/static/js/orchestration/runbooks.js');

function loadWeb(): typeof ts {
  const mod = { exports: {} as Record<string, unknown> };
  // The file is an IIFE that exports its pure functions through `module` when there is no page.
  new Function('module', 'window', fs.readFileSync(WEB, 'utf8'))(mod, undefined);
  return mod.exports as unknown as typeof ts;
}
const web = loadWeb();

const fixture = require('../fixtures/server/orchestration/runbook.json');

const SPECS: Array<Record<string, unknown>> = [
  fixture.spec,
  { id: 'web.deploy', title: 'Deploy', steps: [] },
  { id: 'a.b', title: 'Extra keys', owner: 'ops', x_meta: { k: [1, 2] }, steps: [{ name: 's1', tool: 'sys.disk@1', retries: 2, params: {} }] },
  {
    id: 'web.safe', title: 'Safe', summary: 'two steps',
    inputs: { type: 'object', properties: { days: { type: 'integer', default: 7 }, tag: { type: 'string', enum: ['a', 'b'] } }, required: ['days'], additionalProperties: false },
    steps: [
      { name: 'check', tool: 'sys.disk-usage@1', params: { path: '/' }, when: '', on_failure: 'stop', nodes: '' },
      { name: 'clean', tool: 'ops.clean@2', params: { days: '${{ inputs.days }}' }, when: 'steps.check.outputs.used > 80', on_failure: 'rollback', nodes: 'role=web' },
    ],
  },
  { steps: 'not a list' },
  {},
];

describe('runbook model parity with the web', () => {
  it('turns specs into the same models and back into the same specs', () => {
    for (const spec of SPECS) {
      const a = ts.specToModel(spec);
      const b = web.specToModel(spec);
      expect(a).toEqual(b);
      expect(ts.modelToSpec(a)).toEqual(web.modelToSpec(b));
    }
  });

  it('round-trips a spec through the form unchanged', () => {
    const spec = SPECS[3];
    expect(ts.modelToSpec(ts.specToModel(spec))).toEqual(spec);
    expect(ts.modelToSpec(ts.specToModel(SPECS[2]))).toEqual(SPECS[2]);
  });

  it('applies form edits identically', () => {
    const edit = (m: ts.RunbookModel) => {
      m.title = 'Renamed';
      m.steps[0].on_failure = 'continue';
      m.steps[1].params = {};
      m.steps[1].when = '';
      m.steps.push(m.steps[0] && { ...m.steps[0], raw: {}, name: 'third', tool: 'x.y@1' });
      return m;
    };
    expect(ts.modelToSpec(edit(ts.specToModel(SPECS[3])))).toEqual(web.modelToSpec(edit(web.specToModel(SPECS[3]))));
  });

  it('reads and writes the inputs table identically, including untouched rows', () => {
    const schema = (SPECS[3].inputs as Record<string, unknown>);
    const a = ts.inputsToRows(schema);
    const b = web.inputsToRows(schema);
    expect(a).toEqual(b);
    for (const f of ts.ROW_FIELDS) expect(a.map(r => ts.fieldText(r, f))).toEqual(b.map(r => web.fieldText(r, f)));
    expect(ts.rowsToInputs(a, schema)).toEqual(schema);
    const edits = (rows: ts.InputRow[]) => {
      rows[0].edits = { default: '14', maxLength: '3', description: '' };
      rows[1].edits = { enum: 'a, b, c', type: 'string' };
      rows[1].required = true;
      rows.push({ name: 'flag', raw: {}, required: false, edits: { type: 'boolean', default: 'true' } });
      rows.push({ name: '', raw: {}, required: true, edits: {} });
      return rows;
    };
    expect(ts.rowsToInputs(edits(ts.inputsToRows(schema)), schema)).toEqual(web.rowsToInputs(edits(web.inputsToRows(schema)), schema));
    expect(ts.rowsToInputs([], undefined)).toEqual(web.rowsToInputs([], undefined));
  });

  it('points problems at their step', () => {
    expect(ts.stepOf('steps[3].params.days')).toBe(3);
    expect(ts.stepOf('inputs.properties.days')).toBeNull();
    const problems = fixture.problem_details;
    expect(ts.errorsOf<{ path: string; severity?: string }>(problems).map(p => ts.stepOf(p.path))).toEqual([1]);
  });
});
