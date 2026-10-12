/* Job layouts consume core VMs, commands and part-scoped changes. */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const money = value => `$${Number(value || 0).toFixed(2)}`;
    const elapsed = value => `${Math.floor(Number(value || 0) / 60)}m ${Math.floor(Number(value || 0) % 60)}s`;
    const tabs = ['plan', 'session', 'diff', 'review', 'checks', 'costs'];
    const labels = {plan:'Plan',session:'Session',diff:'Diff',review:'Review',checks:'Checks',costs:'Cost & audit'};
    const stepTabs = {plan:'plan',approve:'plan',implement:'session',checks:'checks',review:'review',push:'costs'};
    function create({ api, toast, boardEl, detailEl, totalEl }) {
        let home = null, job = null, selectedId = '', tab = 'plan', editor = null, source = null, fetchNumber = 0;
        let stateFilter = '', repoFilter = '', sessionStage = '', splitDiff = false;
        const selectedFindings = new Set();
        const viewed = new Set();
        const signatures = new Map();
        function buttons(actions, selection = null) {
            return (actions || []).filter(a => !selection || selection.includes(a.id)).map(a =>
                `<button data-command="${esc(a.id)}" class="${a.primary ? 'work-primary' : a.danger ? 'work-danger' : 'work-action'}" ${a.enabled ? '' : 'disabled'} title="${esc(a.reason || a.label)}">${esc(a.label)}${a.shortcut ? `<kbd>${esc(a.shortcut)}</kbd>` : ''}</button>`).join('');
        }
        function bindCommands(root) {
            root.querySelectorAll('[data-command]').forEach(b => { b.onclick = () => invoke(b.dataset.command); });
        }
        async function send(type, args = {}) {
            return api('/api/jobs/cmd', { method:'POST', body:{ type, job_id:selectedId, args } });
        }
        async function invoke(type) {
            const action = job?.actions?.find(a => a.id === type);
            if (!action?.enabled) return toast(action?.reason || 'Action unavailable', true);
            if (['approvePlan','savePlanDraft','rejectPlan'].includes(type)) { selectTab('plan'); return; }
            let args = {};
            if (['delete', 'discard'].includes(type) && !window.confirm(`${action.label}: ${job.title}? Running jobs are cancelled first and their worktree is removed. Git branch is preserved.`)) return;
            if (type === 'reprompt') {
                if (!selectedFindings.size) { selectTab('review'); return toast('Select findings to include in the re-prompt.'); }
                const guidance = window.prompt('Optional guidance for the implementer', ''); if (guidance === null) return;
                args = { finding_ids: [...selectedFindings], guidance };
            }
            if (type === 'resume') { const guidance = window.prompt('Guidance for the resumed stage', ''); if (guidance === null) return; args = {guidance}; }
            if (type === 'message') { selectTab('session'); detailEl.querySelector('[data-message]')?.focus(); return; }
            if (type === 'markFindings') { selectTab('review'); return; }
            if (type === 'push' && action.args?.requires_override) { const reason = window.prompt('Reason for overriding the blocking review'); if (!reason?.trim()) return; args.override_reason = reason; }
            try {
                const result = await send(type, args); toast('Job updated.');
                if (type === 'delete') { editor?.destroy(); editor = null; job = null; selectedId = ''; delete detailEl.dataset.jobId; detailEl.innerHTML = '<p>Select a job.</p>'; }
                else await refreshParts(result.parts);
                await refreshHome();
            } catch (error) { toast(error.message, true); }
        }
        function renderHome() {
            if (!home) return;
            totalEl.textContent = home.jobs.length;
            if (!boardEl.querySelector('[data-job-filters]')) {
                boardEl.innerHTML = '<div data-job-filters class="job-filters"><label>State<select data-state-filter><option value="">All states</option></select></label><label>Repository<select data-repo-filter><option value="">All repositories</option></select></label></div><div data-dashboard></div>';
                boardEl.querySelector('[data-state-filter]').onchange = e => { stateFilter = e.target.value; renderHome(); };
                boardEl.querySelector('[data-repo-filter]').onchange = e => { repoFilter = e.target.value; renderHome(); };
            }
            for (const [selector, field, current, all] of [['state','state',stateFilter,'All states'],['repo','repository',repoFilter,'All repositories']]) {
                const select = boardEl.querySelector(`[data-${selector}-filter]`);
                select.innerHTML = `<option value="">${all}</option>` + [...new Set(home.jobs.map(j => j[field]))].sort().map(v => `<option value="${esc(v)}">${esc(v?.replaceAll('_',' '))}</option>`).join(''); select.value = current;
            }
            const filtered = rows => rows.filter(j => (!stateFilter || j.state === stateFilter) && (!repoFilter || j.repository === repoFilter));
            const card = row => `<article class="work-job-card ${row.id === selectedId ? 'is-selected' : ''}"><button class="job-open" data-job-id="${esc(row.id)}"><strong>${esc(row.key ? row.key + ' · ' : '')}${esc(row.title)}</strong><span>${esc(row.state.replaceAll('_',' '))} · ${money(row.cost_usd)}</span><span class="job-mini" aria-label="Pipeline">${esc(row.mini)}</span></button>${row.attention_detail ? `<p>${esc(row.attention_detail)}</p>` : ''}<div class="work-action-row">${(row.actions || []).filter(a => a.primary).map(a => `<button data-open-action="${esc(row.id)}" ${a.enabled ? '' : 'disabled'} title="${esc(a.reason)}">${esc(a.label)}</button>`).join('')}</div></article>`;
            boardEl.querySelector('[data-dashboard]').innerHTML = [['needs_you','Needs you'],['running','Running now']].map(([key, label]) =>
                `<section class="work-board-section"><header class="work-board-section-head">${label}<span>${filtered(home[key]).length}</span></header>${filtered(home[key]).map(card).join('') || '<p class="work-muted">None</p>'}</section>`).join('') +
                `<section><h3>Jobs</h3><div class="job-table-scroll"><table class="job-table"><thead><tr><th>Job</th><th>Pipeline</th><th>State / verdict</th><th>Cost</th><th>Age</th></tr></thead><tbody>${filtered(home.jobs).map(row => `<tr><td><button data-job-id="${esc(row.id)}">${esc(row.key || row.id.slice(0,8))}<br>${esc(row.title)}</button></td><td class="job-mini">${esc(row.mini)}</td><td>${esc(row.state.replaceAll('_',' '))}<br>${esc(row.verdict.replaceAll('_',' '))}</td><td>${money(row.cost_usd)}</td><td>${elapsed(Date.now()/1000-row.created_at)}</td></tr>`).join('')}</tbody></table></div></section>`;
            boardEl.querySelectorAll('[data-job-id]').forEach(b => { b.onclick = () => select(b.dataset.jobId); });
            boardEl.querySelectorAll('[data-open-action]').forEach(b => { b.onclick = async () => { if (await select(b.dataset.openAction) !== true) return; const primary = job?.actions.find(a => a.primary && a.enabled); if (primary) await invoke(primary.id); }; });
        }
        async function refreshHome() { home = await api('/api/jobs/vm/home'); renderHome(); return home; }
        function shell() {
            detailEl.innerHTML = '<article class="work-detail job-vm-detail"><header data-job-header></header><nav class="job-pipeline" aria-label="Job pipeline" data-pipeline></nav><div class="work-action-row job-actions" data-actions></div><nav class="job-tabs" role="tablist">' + tabs.map(t => `<button role="tab" data-tab="${t}" aria-selected="${t === tab}">${labels[t]}</button>`).join('') + '</nav>' + tabs.map(t => `<section role="tabpanel" data-panel="${t}" ${t === tab ? '' : 'hidden'}></section>`).join('') + '<aside data-side-rail class="job-side-rail"></aside></article>';
            detailEl.querySelectorAll('[data-tab]').forEach(b => { b.onclick = () => selectTab(b.dataset.tab); });
        }
        function renderState() {
            detailEl.querySelector('[data-job-header]').innerHTML = `<div class="work-detail-header-top"><div><span class="work-status">${esc(job.state.replaceAll('_',' '))}</span><h2>${esc(job.key ? job.key + ' · ' : '')}${esc(job.title)}</h2></div><strong>${money(job.cost_usd)}</strong></div><p class="work-muted">${esc(job.branch || '')} · ${esc(job.worktree || job.repository)}</p>${job.attention_detail ? `<p class="work-attention-banner">${esc(job.attention_detail)}</p>` : ''}`;
            detailEl.querySelector('[data-pipeline]').innerHTML = (job.pipeline || job.steps || []).map(s => `<button data-step="${esc(s.id)}" data-status="${esc(s.status)}"><span class="job-step-glyph">${esc(s.glyph)}</span><strong>${esc(s.label)}</strong><small>${elapsed(s.elapsed)} · ${money(s.cost_usd)} · ${s.runs} run${s.runs === 1 ? '' : 's'}</small>${s.detail ? `<small>${esc(s.detail)}</small>` : ''}</button>`).join('');
            detailEl.querySelectorAll('[data-step]').forEach(b => { b.onclick = () => selectTab(stepTabs[b.dataset.step]); });
            const header = detailEl.querySelector('[data-job-header]');
            header.insertAdjacentHTML('beforeend', `<details><summary>Stage models</summary>${Object.entries(job.stage_models || {}).map(([s,m])=>`<p>${esc(s)} · ${esc(m.provider)} · ${esc(m.model)}</p>`).join('')}<button data-stage-models>Job settings</button></details>`);
            header.querySelector('[data-stage-models]').onclick=()=>window.muOpenStageModels?.();
            const rail = detailEl.querySelector('[data-side-rail]');
            const latest = job.checks?.runs?.slice(-1)[0];
            rail.innerHTML = `<h4>Pending actions</h4>${buttons(job.actions.filter(a => a.primary))}<h4>Latest checks</h4><p>${latest ? (latest.passed ? '✓ Passed' : '✗ Failed') + ` · ${latest.new_failures || 0} new failures` : 'No checks yet'}</p>`;
            bindCommands(rail);
            const bar = detailEl.querySelector('[data-actions]'); bar.innerHTML = buttons(job.actions, ['approvePlan','resume','retry','recheck','reprompt','push','markMerged','discard','delete']); bindCommands(bar);
        }
        function renderPlan(panel) {
            if (editor?.isDirty()) return;
            editor?.destroy(); editor = null;
            const plan = job.plan || {};
            panel.innerHTML = `<label>Version history<select data-plan-version>${(plan.versions || []).map(v => `<option value="${v}" ${v === plan.version ? 'selected' : ''}>v${v}${v === plan.approved_version ? ' · approved' : ''}</option>`).join('')}</select></label><div data-editor></div>`;
            const acts = plan.version === plan.latest_version ? job.actions : [];
            editor = window.MuJobPlan.mount(panel.querySelector('[data-editor]'), plan, acts, send, () => refreshParts(['plan','state']));
            panel.querySelector('[data-plan-version]').onchange = async e => {
                if (!editor.canLeave()) return;
                const response = await api(`/api/jobs/vm/job/${encodeURIComponent(selectedId)}?parts=plan&plan_version=${e.target.value}`);
                editor.destroy(); editor = null; job.plan = response.plan; renderPlan(panel);
            };
        }
        function renderReview(panel) {
            const report = job.review;
            if (!report) { panel.innerHTML = '<p>The review report will appear here.</p>'; return; }
            const findings = [...report.findings].sort((a,b) => ['blocker','major','minor','nit'].indexOf(a.severity)-['blocker','major','minor','nit'].indexOf(b.severity));
            const action = job.actions.find(a => a.id === 'markFindings');
            panel.innerHTML = `<header class="job-report-header"><span class="job-verdict">${esc(report.verdict.replaceAll('_',' '))}</span><meter min="0" max="100" value="${Number(report.merge_readiness)}">${report.merge_readiness}</meter><strong>${report.merge_readiness}/100</strong></header><p>${esc(report.summary)}</p><p>${Object.entries(report.counts || {}).map(([severity,count]) => `${count} ${severity}`).join(' · ')}</p><h3>Plan coverage</h3><table class="job-table"><thead><tr><th>Criterion</th><th>Coverage</th><th>Evidence</th></tr></thead><tbody>${(report.plan_coverage || []).map(c => `<tr><td>${esc(c.criterion_id)}</td><td>${esc(c.status)}</td><td>${esc(c.evidence)}</td></tr>`).join('')}</tbody></table><h3>Findings</h3>${findings.map(f => `<article class="job-finding" id="finding-${esc(f.id)}" data-severity="${esc(f.severity)}"><label><input type="checkbox" data-finding="${esc(f.id)}" ${selectedFindings.has(f.id) ? 'checked' : ''}>${esc(f.id)} · ${esc(f.severity)} · <strong>${esc(f.title)}</strong></label><button data-anchor="${esc(f.id)}">${esc(f.file || 'No file anchor')}${f.line_start ? ':' + f.line_start : ''}</button><p>${esc(f.detail)}</p>${f.suggestion ? `<p>${esc(f.suggestion)}</p>` : ''}<label>Disposition<select data-disposition="${esc(f.id)}" ${action?.enabled ? '' : 'disabled'}>${['open','accepted','dismissed','fixed'].map(d => `<option ${d === f.disposition ? 'selected' : ''}>${d}</option>`).join('')}</select></label></article>`).join('')}<div class="work-action-row">${buttons(job.actions,['reprompt','push','recheck'])}</div>`;
            bindCommands(panel);
            panel.querySelectorAll('[data-finding]').forEach(c => { c.onchange = () => c.checked ? selectedFindings.add(c.dataset.finding) : selectedFindings.delete(c.dataset.finding); });
            panel.querySelectorAll('[data-disposition]').forEach(c => { c.onchange = async () => { try { await send('markFindings',{ids:[c.dataset.disposition],disposition:c.value}); await refreshParts(['review','state']); } catch (e) { toast(e.message,true); } }; });
            panel.querySelectorAll('[data-anchor]').forEach(b => { b.onclick = () => { const f = findings.find(x => x.id === b.dataset.anchor); selectTab('diff'); setTimeout(() => detailEl.querySelector(`[data-line-key="${CSS.escape((f.file || '') + ':' + f.line_start)}"]`)?.scrollIntoView({block:'center'}), 0); }; });
        }
        function renderDiff(panel) {
            const patch = job.diff?.patch || '';
            const files = patch.split(/(?=^diff --git )/m).filter(Boolean);
            panel.innerHTML = `<div class="job-diff-toolbar"><button data-diff-mode>${splitDiff ? 'Unified view' : 'Split view'}</button></div><div class="job-diff-layout"><nav data-file-tree></nav><div class="job-diff-files ${splitDiff ? 'is-split' : ''}" data-diff-files></div></div>`;
            panel.querySelector('[data-diff-mode]').onclick = () => { splitDiff = !splitDiff; renderDiff(panel); };
            const tree = panel.querySelector('[data-file-tree]'), body = panel.querySelector('[data-diff-files]');
            files.forEach((block, index) => {
                const file = block.match(/^\+\+\+ b\/(.*)$/m)?.[1] || block.match(/^--- a\/(.*)$/m)?.[1] || 'Diff';
                const section = document.createElement('section'); section.id = `diff-file-${index}`;
                const link = document.createElement('button'); link.textContent = file; link.onclick = () => section.scrollIntoView({block:'start'}); tree.append(link);
                let lineNo = 0;
                section.innerHTML = `<h4><label><input type="checkbox" data-viewed ${viewed.has(file) ? 'checked' : ''}>Viewed</label> ${esc(file)}</h4><div class="job-patch">${block.split('\n').map(line => {
                    const hunk = line.match(/^@@ -\d+(?:,\d+)? \+(\d+)/); if (hunk) lineNo = Number(hunk[1]) - 1;
                    const content = !line.startsWith('+++') && !line.startsWith('---') && /^[ +\-]/.test(line);
                    if (content && !line.startsWith('-')) lineNo += 1;
                    const findings = (job.review?.findings || []).filter(f => f.file === file && f.line_start === lineNo);
                    return `<div class="job-diff-line ${line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : ''}" data-line-key="${esc(file + ':' + lineNo)}"><span>${content ? lineNo : ''}</span><code>${esc(line)}</code>${findings.map(f => `<button data-diff-finding="${esc(f.id)}" title="${esc(f.title)}">◆ ${esc(f.severity)}</button>`).join('')}</div>`;
                }).join('')}</div>`;
                section.querySelector('[data-viewed]').onchange = e => e.target.checked ? viewed.add(file) : viewed.delete(file);
                body.append(section);
            });
            if (!files.length) body.textContent = 'No committed diff yet.';
            panel.querySelectorAll('[data-diff-finding]').forEach(b => { b.onclick = () => { selectTab('review'); document.getElementById(`finding-${b.dataset.diffFinding}`)?.scrollIntoView({block:'center'}); }; });
        }
        function renderChecks(panel) {
            const runs = job.checks?.runs || [];
            panel.innerHTML = `<h3>Checks</h3>${runs.slice().reverse().map(run => `<article class="job-check-run"><h4>Run ${run.run} · ${run.passed ? '✓ Passed' : '✗ Failed'} · ${esc((run.head_sha || '').slice(0,12))}</h4><p>${run.new_failures || 0} new failures · ${run.preexisting_failures || 0} preexisting</p>${(run.suites || []).map(s => { const counts = s.junit || {passed:s.passed ? 1:0,failed:s.passed ? 0:1,skipped:0,total:1}; return `<details><summary>${s.passed ? '✓' : '✗'} ${esc(s.suite)} ${s.preexisting_failures?.length ? '<span class="job-preexisting">preexisting</span>' : ''}<span class="job-check-bar"><i style="flex:${counts.passed}" class="passed"></i><i style="flex:${counts.failed}" class="failed"></i><i style="flex:${counts.skipped}" class="skipped"></i></span>${counts.passed}/${counts.total} passed</summary><code>${esc(s.command)}</code><pre class="work-diff">${esc(String(s.stdout || '') + '\n' + String(s.stderr || '')).replace(/\u001b\[[0-9;]*m/g,'')}</pre></details>`; }).join('')}</article>`).join('') || '<p>No checks have completed.</p>'}<div class="work-action-row">${buttons(job.actions,['recheck'])}</div>`;
            bindCommands(panel);
        }
        function renderSession(panel) {
            const data = job.session || {};
            const messages = sessionStage ? (data.stages?.[sessionStage] || []).map(m => ({role:m.role,text:(m.parts || []).filter(p => p.type === 'text').map(p => p.text).join('\n')})) :
                (data.events || []).map(e => ({role:e.payload?.event || e.event_type,text:e.payload?.payload?.text || e.payload?.payload?.preview || e.payload?.payload?.tool_name || e.payload?.text || e.reason}));
            const text = panel.querySelector('[data-message]')?.value || '';
            panel.innerHTML = `<label>Stage<select data-session-stage><option value="">Live stage</option>${['plan','implement','review'].map(s => `<option value="${s}" ${s === sessionStage ? 'selected' : ''}>${s}</option>`).join('')}</select></label><div class="job-transcript">${messages.map(m => `<details ${String(m.role).includes('tool') ? '' : 'open'}><summary>${esc(m.role)}</summary><pre>${esc(m.text)}</pre></details>`).join('') || '<p>No stage activity yet.</p>'}</div><label>Message the stage<textarea data-message rows="3">${esc(text)}</textarea></label><button data-send-message ${job.actions.find(a => a.id === 'message')?.enabled ? '' : 'disabled'}>Send message</button><div class="work-action-row">${buttons(job.actions,['resume','restartStage'])}</div>`;
            panel.querySelector('[data-session-stage]').onchange = e => { sessionStage = e.target.value; renderSession(panel); };
            panel.querySelector('[data-send-message]').onclick = async () => { try { await send('message',{text:panel.querySelector('[data-message]').value}); panel.querySelector('[data-message]').value = ''; } catch(e) {toast(e.message,true);} };
            bindCommands(panel);
        }
        function renderCosts(panel) {
            const costs = job.costs || {};
            panel.innerHTML = `<h3>Cost & audit · ${money(costs.total_usd)}</h3><table class="job-table">${Object.entries(costs.by_stage || {}).map(([s,c]) => `<tr><th>${esc(s)}</th><td>${money(c)}</td></tr>`).join('')}</table>${(costs.events || []).slice().reverse().map(e => `<details><summary>${esc(new Date(e.created_at*1000).toLocaleString())} · ${esc(e.event_type)} · ${esc(e.reason)}</summary><pre>${esc(JSON.stringify(e.payload,null,2))}</pre></details>`).join('')}`;
        }
        const renderers = {plan:renderPlan,session:renderSession,diff:renderDiff,review:renderReview,checks:renderChecks,costs:renderCosts};
        function renderPart(part, force=false) {
            if (!tabs.includes(part)) return;
            const signature = JSON.stringify([job[part], part === 'diff' ? job.review : null, job.actions]);
            if (!force && signatures.get(part) === signature) return;
            if (part === 'plan' && editor?.isDirty()) return;
            const panel = detailEl.querySelector(`[data-panel="${part}"]`);
            renderers[part](panel); signatures.set(part,signature);
        }
        function selectTab(next) {
            tab = next;
            detailEl.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected',String(b.dataset.tab === tab)));
            detailEl.querySelectorAll('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== tab; });
            renderPart(tab);
        }
        async function refreshParts(parts=tabs) {
            if (!selectedId) return;
            const id = selectedId;
            const response = await api(`/api/jobs/vm/job/${encodeURIComponent(id)}?parts=${encodeURIComponent([...new Set(parts)].join(','))}`);
            if (id !== selectedId) return;
            job = {...job,...response}; renderState();
            for (const part of parts) if (part === tab || part === 'plan' && editor) renderPart(part);
        }
        async function select(id) {
            if (!id) return;
            if (id !== selectedId && editor && !editor.canLeave()) return;
            const request = ++fetchNumber;
            const next = await api(`/api/jobs/vm/job/${encodeURIComponent(id)}`);
            if (request !== fetchNumber) return;
            if (selectedId !== id) { editor?.destroy(); editor=null; selectedFindings.clear(); signatures.clear(); tab='plan'; selectedId=id; shell(); }
            job=next; detailEl.dataset.jobId=id; renderState(); renderPart(tab); renderHome(); return true;
        }
        function palette() {
            if (!job) return;
            const dialog = document.createElement('dialog'); dialog.className='job-palette';
            dialog.innerHTML=`<form method="dialog"><button aria-label="Close">×</button></form><h3>Job commands</h3><div>${buttons(job.actions)}</div><ul>${job.actions.filter(a=>!a.enabled).map(a=>`<li>${esc(a.label)}: ${esc(a.reason)}</li>`).join('')}</ul>`;
            document.body.append(dialog); dialog.querySelectorAll('[data-command]').forEach(b => { b.onclick=()=> { dialog.close(); invoke(b.dataset.command); }; }); dialog.onclose=()=>dialog.remove(); dialog.showModal();
        }
        let goKey = false;
        window.addEventListener('keydown', e => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase()==='k') { e.preventDefault(); palette(); return; }
            if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '') || !job) return;
            if (goKey && e.key==='d') { goKey=false; selectTab('diff'); return; }
            goKey=e.key==='g';
            const action=job.actions.find(a=>a.shortcut===e.key && a.enabled); if(action) invoke(action.id);
        });
        async function start() {
            await refreshHome(); const initial=home.needs_you[0] || home.running[0] || home.jobs[0]; if(initial) await select(initial.id);
            source=new EventSource('/api/jobs/events');
            source.onmessage=async event=> { try { const change=JSON.parse(event.data); if(change.scope==='home') await refreshHome(); if(change.scope==='job' && change.id===selectedId) await refreshParts(change.parts); } catch(e) {toast(e.message,true);} };
            source.addEventListener('hello',()=> { refreshHome().catch(()=>{}); if(selectedId) refreshParts().catch(()=>{}); });
            window.addEventListener('beforeunload',()=>source.close());
        }
        return {start,select,refreshHome,refreshParts,selectedId:()=>selectedId};
    }
    window.MuJobWork={create};
})();
