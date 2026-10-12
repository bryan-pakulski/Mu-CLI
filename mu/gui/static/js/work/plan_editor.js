/* The browser plan editor uses the same structured document as the job core. */
(() => {
    'use strict';
    const sections = { summary: 'Summary', approach: 'Approach', acceptance: 'Acceptance criteria',
        checks: 'Checks', files: 'Files likely touched', risks: 'Risks / out of scope' };
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    function render(doc) {
        const lines = ['---', `version: ${doc.version || 1}`, `author: ${doc.author || 'human'}`, `based_on: ${doc.based_on || 'null'}`, '---'];
        for (const [key, title] of Object.entries(sections)) {
            lines.push(`## ${title}`);
            if (key === 'summary') lines.push(doc.summary || '');
            else (doc[key] || []).forEach((v, i) => lines.push(key === 'approach' ? `${i + 1}. ${v}` :
                key === 'acceptance' ? `- [${v.id}] ${v.text}` : key === 'checks' ? `- suite: ${v.suite}   cmd: ${v.cmd}` : `- ${v}`));
            lines.push('');
        }
        return lines.join('\n').trimEnd() + '\n';
    }
    function parse(text) {
        const doc = { summary: '', approach: [], acceptance: [], checks: [], files: [], risks: [], version: 1, author: 'human', based_on: null };
        let key = '', front = false;
        String(text).split(/\r?\n/).forEach((line, i) => {
            if (i === 0 && line.trim() === '---') { front = true; return; }
            if (front) {
                if (line.trim() === '---') front = false;
                else {
                    const [name, ...rest] = line.split(':'); const value = rest.join(':').trim();
                    if (name === 'version') doc.version = Number(value) || 1;
                    if (name === 'author') doc.author = value;
                    if (name === 'based_on') doc.based_on = Number(value) || null;
                }
                return;
            }
            if (line.startsWith('## ')) { key = Object.keys(sections).find(k => sections[k].toLowerCase() === line.slice(3).trim().toLowerCase()) || ''; return; }
            if (!key || !line.trim()) return;
            const value = line.replace(/^\s*(?:\d+[.)]|[-*])\s+/, '').trim();
            if (key === 'summary') doc.summary += (doc.summary ? '\n' : '') + line;
            else if (key === 'acceptance') { const m = value.match(/^\[([\w.-]+)\]\s*(.*)$/); doc.acceptance.push({ id: m ? m[1] : `AC-${doc.acceptance.length + 1}`, text: m ? m[2] : value }); }
            else if (key === 'checks') { const m = value.match(/^suite:\s*(\S+)\s+cmd:\s*(.+)$/); if (m) doc.checks.push({ suite: m[1], cmd: m[2] }); }
            else doc[key].push(value);
        });
        return doc;
    }
    function preview(text) {
        // Escape first: plan content may contain repository-controlled HTML.
        if (!window.marked) return `<pre>${escape(text)}</pre>`;
        const template = document.createElement('template');
        template.innerHTML = window.marked.parse(escape(text), { breaks:true });
        const allowed = new Set(['P','BR','HR','H1','H2','H3','H4','H5','H6','UL','OL','LI','STRONG','EM','DEL','PRE','CODE','BLOCKQUOTE','TABLE','THEAD','TBODY','TR','TH','TD']);
        template.content.querySelectorAll('*').forEach(el => {
            if (!allowed.has(el.tagName)) el.replaceWith(document.createTextNode(el.textContent));
            else [...el.attributes].forEach(attr => el.removeAttribute(attr.name));
        });
        return template.innerHTML;
    }
    function mount(container, plan, actions, send, onSaved = () => {}) {
        let doc = plan.doc || parse(plan.markdown || '');
        let markdown = plan.draft ?? plan.markdown ?? render(doc);
        if (plan.draft) doc = parse(plan.draft);
        let dirty = Boolean(plan.draft), revision = 0, savedRevision = -1, mode = 'structured', busy = false;
        const editable = actions.some(a => a.id === 'savePlanDraft' && a.enabled);
        const action = id => actions.find(a => a.id === id);
        const message = text => { const el = container.querySelector('[data-plan-status]'); if (el) el.textContent = text; };
        function changed() {
            dirty = true; revision += 1; markdown = mode === 'markdown' ? container.querySelector('[data-markdown]').value : render(doc);
            const preview = container.querySelector('[data-plan-preview]'); if (preview) preview.innerHTML = window.MuJobPlan.preview(markdown);
            const approve = container.querySelector('[data-plan-approve]'); if (approve) approve.disabled = true;
            message('Unsaved edits — save a version before approving.');
        }
        function draw() {
            container.innerHTML = `<div class="job-plan-toolbar"><button data-mode="structured">Structured</button><button data-mode="markdown">Markdown</button><span>Version ${plan.version || 0}${plan.approved_version ? ` · approved v${plan.approved_version}` : ''}</span></div>
                <div data-fields></div><p role="status" data-plan-status></p>
                ${plan.diff_from_previous ? `<details open><summary>Changes from previous version</summary><pre class="work-diff">${escape(plan.diff_from_previous)}</pre></details>` : ''}
                ${editable ? '<div class="work-action-row"><button data-plan-save>Save draft</button><button data-plan-approve>Approve plan</button></div><label>Re-plan feedback<textarea data-feedback rows="2"></textarea></label><button data-plan-reject>Request re-plan</button>' : '<p>Approved plans are read-only.</p>'}`;
            container.querySelectorAll('[data-mode]').forEach(button => { button.onclick = () => {
                if (mode === 'markdown') doc = parse(markdown);
                mode = button.dataset.mode; draw();
            }; });
            const fields = container.querySelector('[data-fields]');
            if (mode === 'markdown') {
                fields.innerHTML = `<div class="job-plan-split"><label>Markdown<textarea data-markdown rows="24" ${editable ? '' : 'readonly'}>${escape(markdown)}</textarea></label><div><h4>Live preview</h4><div data-plan-preview>${preview(markdown)}</div></div></div>`;
                fields.querySelector('textarea').oninput = changed;
            } else {
                fields.innerHTML = `<label>Summary<textarea data-summary ${editable ? '' : 'readonly'}>${escape(doc.summary)}</textarea></label>`;
                fields.querySelector('[data-summary]').oninput = e => { doc.summary = e.target.value; changed(); };
                for (const key of ['approach', 'acceptance', 'checks', 'files', 'risks']) {
                    const section = document.createElement('section'); section.className = 'job-plan-section';
                    section.innerHTML = `<h4>${sections[key]}</h4>`;
                    (doc[key] || []).forEach((item, i) => {
                        const row = document.createElement('div'); row.className = 'job-plan-row';
                        const keys = key === 'acceptance' ? ['id', 'text'] : key === 'checks' ? ['suite', 'cmd'] : ['value'];
                        row.innerHTML = keys.map(k => `<input aria-label="${escape(sections[key])} ${k}" value="${escape(k === 'value' ? item : item[k])}" ${editable ? '' : 'readonly'}>`).join('') +
                            (editable ? '<button data-up aria-label="Move up">↑</button><button data-down aria-label="Move down">↓</button><button data-remove aria-label="Remove">×</button>' : '');
                        row.querySelectorAll('input').forEach((input, j) => { input.oninput = e => { if (keys[j] === 'value') doc[key][i] = e.target.value; else doc[key][i][keys[j]] = e.target.value; changed(); }; });
                        if (editable) {
                            row.querySelector('[data-remove]').onclick = () => { doc[key].splice(i, 1); changed(); draw(); };
                            for (const [name, delta] of [['up', -1], ['down', 1]]) row.querySelector(`[data-${name}]`).onclick = () => {
                                const next = i + delta; if (next < 0 || next >= doc[key].length) return;
                                [doc[key][i], doc[key][next]] = [doc[key][next], doc[key][i]]; changed(); draw();
                            };
                        }
                        section.append(row);
                    });
                    if (editable) { const add = document.createElement('button'); add.textContent = '+ Add'; add.onclick = () => {
                        doc[key].push(key === 'acceptance' ? { id: `AC-${Date.now()}`, text: '' } : key === 'checks' ? { suite: `check-${Date.now()}`, cmd: '' } : ''); changed(); draw();
                    }; section.append(add); }
                    fields.append(section);
                }
            }
            if (editable) {
                container.querySelector('[data-plan-approve]').disabled = dirty || !action('approvePlan')?.enabled;
                container.querySelector('[data-plan-save]').onclick = () => perform('savePlanDraft', { plan: markdown, version: plan.version });
                container.querySelector('[data-plan-approve]').onclick = () => perform('approvePlan', { version: plan.version });
                container.querySelector('[data-plan-reject]').onclick = () => perform('rejectPlan', { feedback: container.querySelector('[data-feedback]').value });
            }
            if (dirty) message('Unsaved edits — save a version before approving.');
        }
        async function perform(type, args) {
            if (busy) return; busy = true;
            const current = revision;
            try {
                const result = await send(type, args);
                if (result.data?.version) plan.version = result.data.version;
                dirty = revision !== current;
                await onSaved();
                if (dirty) message('Version saved; newer edits remain unsaved.');
            }
            catch (error) { message(error.message); }
            finally { busy = false; }
        }
        const timer = setInterval(async () => {
            if (!editable || !dirty || busy || savedRevision === revision) return;
            const current = revision; busy = true;
            try { await send('savePlanDraft', { plan: markdown, autosave: true, version: plan.version }); savedRevision = current; message('Draft autosaved. Save a version before approving.'); }
            catch (error) { message(`Autosave failed: ${error.message}`); }
            finally { busy = false; }
        }, 5000);
        const beforeUnload = e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
        window.addEventListener('beforeunload', beforeUnload);
        draw();
        return { isDirty: () => dirty, canLeave: () => !dirty || window.confirm('Leave this plan with unsaved edits?'),
            destroy: () => { clearInterval(timer); window.removeEventListener('beforeunload', beforeUnload); } };
    }
    const api = { parse, render, preview, mount };
    if (typeof window !== 'undefined') window.MuJobPlan = api;
    if (typeof module !== 'undefined') module.exports = api;
})();
