// The runbook editor's pure model, ported from the web (mu/gui/static/js/orchestration/runbooks.js,
// "pure model"). A spec survives form → spec unchanged: unknown keys ride along in `raw`, and
// untouched input rows give back their property exactly. __tests__/orchestration/runbook-model
// runs the web's functions on the same inputs and expects identical results — change both together.

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;

const clone = <T>(v: T): T => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
const STEP_FIELDS = ['name', 'tool', 'params', 'when', 'on_failure', 'nodes'] as const;
const TOP_FIELDS = ['id', 'title', 'summary', 'inputs', 'steps'];

export type StepModel = {
  name: string; tool: string; params: Json; when: string; on_failure: string; nodes: Json; raw: Record<string, Json>;
};
export type RunbookModel = {
  raw: Record<string, Json>; id: string; title: string; summary: string; inputs: Json; steps: StepModel[];
};
export type InputRow = { name: string; raw: Record<string, Json>; required: boolean; edits: Record<string, string> };
export type RowField = 'type' | 'default' | 'enum' | 'pattern' | 'maxLength' | 'description';
export const ROW_FIELDS: RowField[] = ['type', 'default', 'enum', 'pattern', 'maxLength', 'description'];

export function stepToModel(s: Json): StepModel {
  const raw = s && typeof s === 'object' ? clone(s) : {};
  return {
    name: raw.name ?? '', tool: raw.tool ?? '', params: raw.params && typeof raw.params === 'object' ? raw.params : (raw.params ?? {}),
    when: raw.when ?? '', on_failure: raw.on_failure ?? 'stop', nodes: raw.nodes ?? '', raw,
  };
}

export function specToModel(spec: Json): RunbookModel {
  const raw = spec && typeof spec === 'object' ? clone(spec) : {};
  return {
    raw, id: raw.id ?? '', title: raw.title ?? '', summary: raw.summary ?? '',
    inputs: raw.inputs === undefined ? undefined : raw.inputs,
    steps: Array.isArray(raw.steps) ? raw.steps.map(stepToModel) : [],
  };
}

const isEmptyObject = (v: Json) => v && typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length;

function stepToSpec(step: StepModel): Record<string, Json> {
  const raw = step.raw || {};
  const out: Record<string, Json> = {};
  Object.keys(raw).forEach(k => { out[k] = (STEP_FIELDS as readonly string[]).includes(k) ? clone((step as Json)[k]) : clone(raw[k]); });
  STEP_FIELDS.forEach(k => {
    if (k in out) return;
    const v = (step as Json)[k];
    if (k === 'name' || k === 'tool') { if (v !== '' && v != null) out[k] = v; return; }
    if (k === 'params') { if (v && !isEmptyObject(v)) out[k] = clone(v); return; }
    if (k === 'on_failure') { if (v && v !== 'stop') out[k] = v; return; }
    if (v !== '' && v != null) out[k] = v;
  });
  return out;
}

export function modelToSpec(model: RunbookModel): Record<string, Json> {
  const raw = model.raw || {};
  const out: Record<string, Json> = {};
  const value = (k: string) => (k === 'steps' ? model.steps.map(stepToSpec) : clone((model as Json)[k]));
  Object.keys(raw).forEach(k => { out[k] = TOP_FIELDS.includes(k) ? value(k) : clone(raw[k]); });
  if (!('id' in out)) out.id = model.id;
  if (!('title' in out) && model.title) out.title = model.title;
  if (!('summary' in out) && model.summary) out.summary = model.summary;
  if (!('inputs' in out) && model.inputs !== undefined) out.inputs = clone(model.inputs);
  if (!('steps' in out)) out.steps = value('steps');
  Object.keys(out).forEach(k => { if (out[k] === undefined) delete out[k]; });
  return out;
}

export function inputsToRows(schema: Json): InputRow[] {
  const props = (schema && schema.properties) || {};
  const req = new Set<string>((schema && schema.required) || []);
  return Object.entries(props).map(([name, p]) => ({ name, raw: clone(p as Json) || {}, required: req.has(name), edits: {} }));
}

export function fieldText(row: InputRow, f: RowField): string {
  if (f in row.edits) return row.edits[f];
  const p = row.raw || {};
  switch (f) {
    case 'type': return Array.isArray(p.type) ? p.type.join(',') : (p.type || '');
    case 'default': return 'default' in p ? (typeof p.default === 'string' ? p.default : JSON.stringify(p.default)) : '';
    case 'enum': return Array.isArray(p.enum) ? p.enum.map((v: Json) => (typeof v === 'string' ? v : JSON.stringify(v))).join(', ') : '';
    case 'pattern': return p.pattern || '';
    case 'maxLength': return p.maxLength != null ? String(p.maxLength) : '';
    case 'description': return p.description || '';
    default: return '';
  }
}

function coerce(text: string, type: string): Json {
  const t = String(text).trim();
  if (type === 'integer' || type === 'number') { const n = Number(t); return t !== '' && !Number.isNaN(n) ? n : t; }
  if (type === 'boolean') { if (t === 'true') return true; if (t === 'false') return false; return t; }
  if (type === 'string') return String(text);
  try { return JSON.parse(t); } catch { return String(text); }
}

function propOf(row: InputRow): Record<string, Json> {
  const p = clone(row.raw || {}) || {};
  const type = 'type' in row.edits ? row.edits.type : (Array.isArray(p.type) ? '' : (p.type || ''));
  Object.entries(row.edits).forEach(([f, v]) => {
    const empty = String(v).trim() === '';
    if (f === 'type') { if (empty) delete p.type; else p.type = v; }
    else if (f === 'default') { if (empty) delete p.default; else p.default = coerce(v, type); }
    else if (f === 'enum') { if (empty) delete p.enum; else p.enum = String(v).split(',').map(s => s.trim()).filter(Boolean).map(x => coerce(x, type)); }
    else if (f === 'maxLength') { if (empty) delete p.maxLength; else { const n = Number(v); p.maxLength = Number.isNaN(n) ? v : n; } }
    else if (empty) delete p[f];
    else p[f] = v;
  });
  return p;
}

export function rowsToInputs(rows: InputRow[], base?: Json): Record<string, Json> {
  const out = base ? clone(base) : { type: 'object', properties: {}, additionalProperties: false };
  const named = rows.filter(r => r.name);
  if (named.length || !base || 'properties' in base) {
    const props: Record<string, Json> = {};
    named.forEach(r => { props[r.name] = propOf(r); });
    out.properties = props;
  }
  const req = named.filter(r => r.required).map(r => r.name);
  const same = base && Array.isArray(base.required) && base.required.length === req.length && base.required.every((n: string) => req.includes(n));
  if (same) out.required = clone(base.required);
  else if (req.length || (base && 'required' in base)) out.required = req;
  else delete out.required;
  return out;
}

/* ------------------------------------------------------------ problems (web runbooks.js helpers) */

/** The step index a problem path points into (`steps[2].params.x` → 2), or null for the runbook. */
export function stepOf(path: string | null | undefined): number | null {
  const m = /^steps\[(\d+)\]/.exec(path || '');
  return m ? Number(m[1]) : null;
}
export const errorsOf = <T extends { severity?: string }>(problems: T[] | null | undefined) => (problems || []).filter(p => (p.severity || 'error') === 'error');
export const warningsOf = <T extends { severity?: string }>(problems: T[] | null | undefined) => (problems || []).filter(p => p.severity === 'warning');

/** A dotted lowercase id like web.safe-deploy (the web's new-runbook check). */
export const RUNBOOK_ID = /^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+$/;
