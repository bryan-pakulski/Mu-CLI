/* The JSON Schema subset of mu/orchestration/node/schema.py, mirrored for
 * validation as you type (documentation/plans/runbook_editor.md §4.1).
 *
 * validate(value, schema, path) returns the same error strings as the Python
 * validate(); tests/test_orch_web_pickers.py runs both on one generated case
 * list so the two cannot drift. The server (planRun, validateRunbook) stays
 * the authority: this only gives earlier feedback.
 */
(() => {
    'use strict';
    const TYPES = ['string', 'integer', 'number', 'boolean', 'array', 'object', 'null'];

    function isType(value, name) {
        switch (name) {
        case 'string': return typeof value === 'string';
        case 'integer': return typeof value === 'number' && Number.isInteger(value);
        case 'number': return typeof value === 'number' && Number.isFinite(value);
        case 'boolean': return typeof value === 'boolean';
        case 'array': return Array.isArray(value);
        case 'object': return value !== null && typeof value === 'object' && !Array.isArray(value);
        case 'null': return value === null;
        default: return false;
        }
    }

    /* Python's repr() for the values a schema holds (strings, numbers, booleans, None, lists, dicts). */
    function pyRepr(v) {
        if (v === null || v === undefined) return 'None';
        if (v === true) return 'True';
        if (v === false) return 'False';
        if (typeof v === 'number') return String(v);
        if (typeof v === 'string') {
            const quote = v.includes("'") && !v.includes('"') ? '"' : "'";
            const body = v.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/\t/g, '\\t').replace(/\r/g, '\\r');
            return quote + (quote === "'" ? body.replace(/'/g, "\\'") : body) + quote;
        }
        if (Array.isArray(v)) return `[${v.map(pyRepr).join(', ')}]`;
        return `{${Object.entries(v).map(([k, x]) => `${pyRepr(k)}: ${pyRepr(x)}`).join(', ')}}`;
    }
    const num = n => String(n);
    const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);

    function validate(value, schema, path = '$') {
        const errors = [];
        if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return [`${path}: invalid schema`];
        if (schema.type !== undefined && schema.type !== null) {
            const kinds = Array.isArray(schema.type) ? schema.type : [schema.type];
            if (!kinds.some(k => isType(value, k))) return [`${path}: expected ${kinds.join(' or ')}`];
        }
        if ('const' in schema && !equal(value, schema.const)) errors.push(`${path}: must equal ${pyRepr(schema.const)}`);
        if ('enum' in schema && !(schema.enum || []).some(x => equal(x, value))) errors.push(`${path}: must be one of ${pyRepr(schema.enum || [])}`);
        if (isType(value, 'number')) {
            if ('minimum' in schema && value < schema.minimum) errors.push(`${path}: must be >= ${num(schema.minimum)}`);
            if ('maximum' in schema && value > schema.maximum) errors.push(`${path}: must be <= ${num(schema.maximum)}`);
            if ('exclusiveMinimum' in schema && value <= schema.exclusiveMinimum) errors.push(`${path}: must be > ${num(schema.exclusiveMinimum)}`);
            if ('exclusiveMaximum' in schema && value >= schema.exclusiveMaximum) errors.push(`${path}: must be < ${num(schema.exclusiveMaximum)}`);
        }
        if (typeof value === 'string') {
            const len = [...value].length;
            if ('minLength' in schema && len < schema.minLength) errors.push(`${path}: shorter than ${schema.minLength}`);
            if ('maxLength' in schema && len > schema.maxLength) errors.push(`${path}: longer than ${schema.maxLength}`);
            if ('pattern' in schema) {
                let re = null;
                try { re = new RegExp(String(schema.pattern), 'u'); } catch (_) {
                    try { re = new RegExp(String(schema.pattern)); } catch (__) { errors.push(`${path}: invalid pattern`); }
                }
                if (re && !re.test(value)) errors.push(`${path}: does not match ${pyRepr(String(schema.pattern))}`);
            }
        }
        if (Array.isArray(value)) {
            if ('minItems' in schema && value.length < schema.minItems) errors.push(`${path}: fewer than ${schema.minItems} items`);
            if ('maxItems' in schema && value.length > schema.maxItems) errors.push(`${path}: more than ${schema.maxItems} items`);
            if (schema.uniqueItems) {
                const seen = new Set();
                for (const item of value) {
                    const key = JSON.stringify(item);
                    if (seen.has(key)) { errors.push(`${path}: items must be unique`); break; }
                    seen.add(key);
                }
            }
            if (schema.items && typeof schema.items === 'object' && !Array.isArray(schema.items)) {
                value.forEach((item, i) => errors.push(...validate(item, schema.items, `${path}[${i}]`)));
            }
        }
        if (isType(value, 'object')) {
            const props = schema.properties || {};
            (schema.required || []).forEach(name => { if (!(name in value)) errors.push(`${path}.${name}: required`); });
            const extra = 'additionalProperties' in schema ? schema.additionalProperties : true;
            Object.entries(value).forEach(([name, item]) => {
                if (name in props) errors.push(...validate(item, props[name], `${path}.${name}`));
                else if (extra === false) errors.push(`${path}.${name}: not allowed`);
                else if (extra && typeof extra === 'object') errors.push(...validate(item, extra, `${path}.${name}`));
            });
        }
        return errors;
    }

    function applyDefaults(value, schema) {
        if (!isType(value, 'object') || !schema || typeof schema !== 'object') return value;
        const out = { ...value };
        Object.entries(schema.properties || {}).forEach(([name, sub]) => {
            if (!(name in out) && sub && typeof sub === 'object' && 'default' in sub) out[name] = JSON.parse(JSON.stringify(sub.default));
        });
        return out;
    }

    /* The control a property gets in a generated form. */
    function kindOf(prop) {
        const p = prop || {};
        if (Array.isArray(p.enum)) return 'enum';
        const type = Array.isArray(p.type) ? p.type.find(t => t !== 'null') : p.type;
        if (type === 'boolean') return 'boolean';
        if (type === 'integer' || type === 'number') return type;
        if (type === 'array' && (!p.items || p.items.type === 'string' || p.items.type === undefined)) return 'list';
        if (type === 'string' || !type) return 'string';
        return 'json';
    }

    const api = { validate, applyDefaults, isType, kindOf, pyRepr, TYPES };
    if (typeof window !== 'undefined') window.OrchSchema = api;
    if (typeof module !== 'undefined') module.exports = api;
})();
