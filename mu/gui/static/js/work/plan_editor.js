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
        // Front matter (version/author/based_on) is metadata, shown as the version label instead.
        text = String(text ?? '').replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');
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
    /* planos plan editing: markdown with a live side-by-side preview (structured rows as an
     * alternative view of the same document), Cancel / Save plan, ctrl+s, 5 s autosave of drafts.
     * Approve and re-plan live in the job's action bar; approving is blocked while edits are unsaved. */
    function mount(container, plan, actions, send, onSaved = () => {}, { onDirty = () => {}, onCancel = () => {} } = {}) {
        let doc = plan.doc || parse(plan.markdown || '');
        let markdown = plan.draft ?? plan.markdown ?? render(doc);
        if (plan.draft) doc = parse(plan.draft);
        let dirty = Boolean(plan.draft), revision = 0, savedRevision = -1, mode = 'markdown', busy = false;
        const editable = actions.some(a => a.id === 'savePlanDraft' && a.enabled);
        const confirmLeave = (text, opts) => window.MuJobUI?.confirm ? window.MuJobUI.confirm(text, opts) : Promise.resolve(window.confirm(text));
        const message = text => { const el = container.querySelector('[data-plan-status]'); if (el) el.textContent = text; };
        const setDirty = value => { const was = dirty; dirty = value; if (was !== dirty) onDirty(dirty); };
        function changed() {
            revision += 1; markdown = mode === 'markdown' ? container.querySelector('[data-markdown]').value : render(doc);
            const out = container.querySelector('[data-plan-preview]'); if (out) out.innerHTML = window.MuJobPlan.preview(markdown);
            const save = container.querySelector('[data-plan-save]'); if (save) save.disabled = false;
            setDirty(true);
            message('Unsaved edits — save the plan before approving.');
        }
        function draw() {
            container.innerHTML = `<div class="work-stack-tight">
                <div class="work-toolbar"><strong>Editing plan · v${plan.version || 0}</strong><span class="work-meta">Save, then approve from the action bar to implement your version.</span><span class="work-spacer"></span>
                    <div class="w-segmented" data-size="sm" role="group" aria-label="Editor view"><button type="button" class="w-segmented__option" data-mode="markdown" aria-pressed="${mode === 'markdown'}">Markdown</button><button type="button" class="w-segmented__option" data-mode="structured" aria-pressed="${mode === 'structured'}">Structured</button></div>
                    <button type="button" class="w-btn" data-variant="secondary" data-plan-cancel>Cancel</button>
                    ${editable ? `<button type="button" class="w-btn" data-variant="primary" data-plan-save ${dirty ? '' : 'disabled'}>Save plan <kbd class="w-keycap" aria-hidden="true">ctrl+s</kbd></button>` : ''}</div>
                <p role="status" class="work-meta" data-tone="attention" data-plan-status></p>
                <div data-fields></div></div>`;
            container.querySelectorAll('[data-mode]').forEach(button => { button.onclick = () => {
                if (mode === 'markdown') doc = parse(markdown);
                mode = button.dataset.mode; draw();
            }; });
            const fields = container.querySelector('[data-fields]');
            if (mode === 'markdown') {
                fields.innerHTML = `<div class="work-editor"><label class="w-sr-only" for="work-plan-markdown">Plan markdown</label><textarea id="work-plan-markdown" data-markdown spellcheck="true" ${editable ? '' : 'readonly'}>${escape(markdown)}</textarea><div class="work-preview w-markdown work-markdown" aria-label="Preview" data-plan-preview>${preview(markdown)}</div></div>`;
                fields.querySelector('textarea').oninput = changed;
            } else {
                fields.innerHTML = `<div class="work-plan-structured"><label class="w-field"><span class="w-field__label">Summary</span><textarea data-summary ${editable ? '' : 'readonly'}>${escape(doc.summary)}</textarea></label></div>`;
                const wrap = fields.firstElementChild;
                fields.querySelector('[data-summary]').oninput = e => { doc.summary = e.target.value; changed(); };
                for (const key of ['approach', 'acceptance', 'checks', 'files', 'risks']) {
                    const section = document.createElement('section'); section.className = 'work-stack-tight';
                    section.innerHTML = `<h3>${sections[key]}</h3>`;
                    (doc[key] || []).forEach((item, i) => {
                        const row = document.createElement('div'); row.className = 'work-plan-row';
                        const keys = key === 'acceptance' ? ['id', 'text'] : key === 'checks' ? ['suite', 'cmd'] : ['value'];
                        row.innerHTML = keys.map(k => `<input type="text" aria-label="${escape(sections[key])} ${k}" value="${escape(k === 'value' ? item : item[k])}" ${editable ? '' : 'readonly'}>`).join('') +
                            (editable ? '<button type="button" class="w-btn" data-variant="icon" data-size="sm" data-up aria-label="Move up" title="Move up">↑</button><button type="button" class="w-btn" data-variant="icon" data-size="sm" data-down aria-label="Move down" title="Move down">↓</button><button type="button" class="w-btn" data-variant="icon" data-size="sm" data-tone="danger" data-remove aria-label="Remove" title="Remove">×</button>' : '');
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
                    if (editable) { const add = document.createElement('button'); add.type = 'button'; add.className = 'w-btn work-plan-add'; add.dataset.variant = 'secondary'; add.dataset.size = 'sm'; add.textContent = '+ Add'; add.onclick = () => {
                        doc[key].push(key === 'acceptance' ? { id: `AC-${Date.now()}`, text: '' } : key === 'checks' ? { suite: `check-${Date.now()}`, cmd: '' } : ''); changed(); draw();
                    }; section.append(add); }
                    wrap.append(section);
                }
            }
            container.querySelector('[data-plan-cancel]').onclick = async () => {
                if (dirty && !await confirmLeave('Discard your plan edits?', { title: 'Discard edits', okLabel: 'Discard', tone: 'danger' })) return;
                setDirty(false); destroy(); onCancel();
            };
            if (editable) container.querySelector('[data-plan-save]').onclick = () => perform('savePlanDraft', { plan: markdown, version: plan.version });
            container.querySelector('[data-fields]').addEventListener('keydown', e => { if (e.key === 's' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (editable && dirty) perform('savePlanDraft', { plan: markdown, version: plan.version }); } });
            if (dirty) message('Unsaved edits — save the plan before approving.');
        }
        async function perform(type, args) {
            if (busy) return; busy = true;
            const current = revision;
            const save = container.querySelector('[data-plan-save]'); if (save) { save.disabled = true; save.firstChild.textContent = 'Saving… '; }
            try {
                const result = await send(type, args);
                if (result.data?.version) plan.version = result.data.version;
                setDirty(revision !== current);
                window.MuJobUI?.toast?.(`Plan saved as v${plan.version}`, 'ok');
                await onSaved();
                if (dirty) message('Version saved; newer edits remain unsaved.');
            }
            catch (error) { message(error.message); if (save) { save.disabled = false; save.firstChild.textContent = 'Save plan '; } }
            finally { busy = false; }
        }
        const timer = setInterval(async () => {
            if (!editable || !dirty || busy || savedRevision === revision) return;
            const current = revision; busy = true;
            try { await send('savePlanDraft', { plan: markdown, autosave: true, version: plan.version }); savedRevision = current; message('Draft autosaved. Save the plan before approving.'); }
            catch (error) { message(`Autosave failed: ${error.message}`); }
            finally { busy = false; }
        }, 5000);
        const beforeUnload = e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
        window.addEventListener('beforeunload', beforeUnload);
        function destroy() { clearInterval(timer); window.removeEventListener('beforeunload', beforeUnload); }
        draw();
        return { isDirty: () => dirty, canLeave: () => !dirty || window.confirm('Leave this plan with unsaved edits?'), destroy };
    }
    const api = { parse, render, preview, mount };
    if (typeof window !== 'undefined') window.MuJobPlan = api;
    if (typeof module !== 'undefined') module.exports = api;
})();
