// Per-mode panel models on the shared shell (documentation/plans/modes_v2.md §5.3). Pure
// functions over each mode's GET /api/<mode>/state payload, loadable without Alpine (node tests:
// tests/gui/frontend/mode-models.test.cjs). Each model returns plain data; the Alpine stores in
// app.js bind it and own the fetches and actions.
//
//   model.sentence(state)  → state-sentence parts (MuModes.sentence drops zeros)
//   model.now(state)       → Now rows {id, kind, status, glyph, title, sub, actions:[{id, label, primary?}]}
//                            in the order to act (MuModes.orderNow)
//   model.primary(state)   → {id, label} | null — the mode's one decisive verb
//   model.footWord(state)  → the composer foot's one-word state ("reproduced", "t3 of 9", "paused")
//   plus record rows and escaped sheet markup per mode.
(function (root) {
    'use strict';

    const M = () => root.MuModes;
    const esc = v => M().esc(v);
    const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

    function blockerRows(blockers) {
        return (blockers || []).map(b => {
            const s = M().vstyle('blocker', 'open');
            return {
                id: `blocker:${b.id}`, kind: 'blocker', status: s.status, glyph: s.glyph,
                title: b.summary, sub: `Needs: ${b.needs || 'your answer'}`, ref: b.id,
                actions: [{ id: 'resolve', label: 'Answer', primary: true }],
            };
        });
    }

    const security = {
        tabs: ['Risk', 'Findings', 'Coverage', 'Report'],
        sentence(state) {
            const r = state && state.report;
            if (!r) return [];
            const c = r.coverage || {};
            const verified = (state.findings || []).filter(f => f.proof_verified).length;
            return [M().vstyle('security_scan', r.status).label,
                { text: `${c.reviewed || 0} of ${c.total || 0} surface items reviewed`, keep: true },
                plural(verified, 'finding verified', 'findings verified')];
        },
        now(state) {
            if (!state || state.plan_mode) return [];
            return M().orderNow((state.findings || []).filter(f => !['approved', 'refuted'].includes(f.status)).map(f => {
                const ready = f.proof_verified && f.remediation_verified && f.severity_confirmed;
                if (!ready) return null;
                const st = M().vstyle('security_finding', 'remediation_verified');
                return { id: f.finding_id, ref: f.finding_id, kind: 'decision', status: st.status, glyph: st.glyph,
                    title: f.title, sub: `${f.cvss_score} ${f.severity} · proof and fix verified`,
                    actions: [{ id: 'approve', label: 'Approve', primary: true }, { id: 'refute', label: 'Refute…' }] };
            }).filter(Boolean));
        },
        primary(state) {
            return state && state.report && state.report.scope && Object.keys(state.report.scope).length && !state.plan_mode
                ? { id: 'report', label: 'Generate report' } : null;
        },
        footWord(state) { return state && state.report ? M().vstyle('security_scan', state.report.status).label : ''; },
        findingRow(f) {
            const st = M().vstyle('security_finding', f.status);
            return { id: f.finding_id, title: f.title, status: st.status, glyph: st.glyph,
                sub: `${f.cvss_score || 0} ${f.severity} · ${f.cwe || ''} · ${st.label}` };
        },
        findingSheet(f) {
            const facts = [['Exploit path', f.exploit_path], ['Affected', (f.affected_paths || []).join(', ')],
                ['CVSS 3.1', f.cvss_vector], ['Rationale', f.severity_rationale], ['Demonstrated impact', f.demonstrated_impact],
                ['PoC command', f.proof_command], ['Proof result', f.proof_notes], ['Fix', f.remediation_description], ['Fix result', f.remediation_notes]];
            const runs = (f.runs || []).map(r => `<li class="w-row"><div class="w-row__main"><span class="w-row__title">${esc(r.purpose)} ${esc(r.id)} · ${esc(r.runner)}</span><span class="w-row__sub">${esc(r.detail)}</span></div></li>`).join('');
            return `<dl class="w-facts">${facts.filter(x => x[1]).map(([k,v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl><ul class="w-rows">${runs}</ul>`;
        },
    };

    // ------------------------------------------------------------------ debug

    const DEBUG_LADDER = ['open', 'reproduced', 'root_caused', 'fixed', 'verified'];

    const debug = {
        tabs: ['Case', 'Hypotheses', 'Runs', 'Root causes'],
        sentence(state) {
            const c = state && state.case;
            if (!c) return [];
            const st = M().vstyle('debug_case', c.status);
            return [c.title, st.label, plural((c.hypotheses || []).length, 'hypothesis', 'hypotheses'),
                plural((c.suspects || []).length, 'suspect')];
        },
        stepper(c) {
            if (!c) return [];
            const rung = DEBUG_LADDER.indexOf(c.status);
            const finished = c.status === 'closed';
            return DEBUG_LADDER.map((step, i) => {
                let status = 'pending';
                if (finished || (rung >= 0 && i < rung)) status = 'done';
                else if (i === rung) status = step === 'verified' ? 'waiting' : 'active';
                if (c.status === 'unreproducible') status = i === 0 ? 'skipped' : 'pending';
                const vocab = (root.WASH && root.WASH.vocabulary && root.WASH.vocabulary.status) || {};
                return { id: step, status, glyph: (vocab[status] || {}).glyph || '○', label: M().vstyle('debug_case', step).label };
            });
        },
        now(state) {
            const rows = blockerRows(state && state.blockers);
            const c = state && state.case;
            if (c && c.status === 'verified') {
                const s = M().vstyle('debug_case', 'verified');
                rows.push({ id: 'close', kind: 'decision', status: 'waiting', glyph: '◆',
                    title: 'Close case?', sub: c.regression_test ? 'Verified, with a regression test.'
                        : `${s.label}; no regression test recorded yet.`,
                    actions: [{ id: 'close', label: 'Close case', primary: true }] });
            }
            return M().orderNow(rows);
        },
        primary(state) {
            return state && state.case && state.case.status === 'verified' ? { id: 'close', label: 'Close case' } : null;
        },
        footWord(state) {
            return state && state.case ? M().vstyle('debug_case', state.case.status).label : '';
        },
        hypothesisRow(h) {
            const s = M().vstyle('hypothesis', h.status);
            const n = (h.evidence || []).length;
            return { id: h.id, status: s.status, glyph: s.glyph, title: h.statement,
                sub: [s.label, n ? plural(n, 'piece of evidence', 'pieces of evidence') : ''].filter(Boolean).join(' · ') };
        },
        hypothesisSheet(h) {
            const items = (h.evidence || []).map(e => {
                const stance = e.stance === 'refutes' ? M().vstyle('hypothesis', 'refuted') : M().vstyle('hypothesis', 'supported');
                return `<li class="w-row"><span class="w-sign" data-status="${esc(stance.status)}" aria-hidden="true">${esc(stance.glyph)}</span>
                    <div class="w-row__main"><span class="w-row__title">${esc(e.kind)} <span class="mono">${esc(e.ref || '')}</span></span>
                    <span class="w-row__sub">${esc(e.stance)}${e.note ? ` · ${esc(e.note)}` : ''}</span></div></li>`;
            }).join('');
            return `<dl class="w-facts"><dt>Status</dt><dd>${M().sign('hypothesis', h.status)} ${esc(M().vstyle('hypothesis', h.status).label)}</dd></dl>
                <ul class="w-rows">${items || '<li class="w-group__empty">No evidence yet.</li>'}</ul>
                <details class="w-disclose"><summary>Details</summary><dl class="w-facts"><dt>Id</dt><dd class="mono">${esc(h.id)}</dd></dl></details>`;
        },
        suspectLabel(s) {
            return s.line ? `${s.path}:${s.line}` : s.path;
        },
    };

    // ------------------------------------------------------------------ feature

    function featureCounts(plan) {
        const live = (plan.tasks || []).filter(t => t.status !== 'archived');
        return {
            total: live.length,
            done: live.filter(t => t.status === 'completed').length,
            blocked: live.filter(t => t.status === 'blocked').length,
            unreviewed: live.filter(t => t.status === 'completed' && !t.reviewed).length,
        };
    }

    function criterionKey(c) {
        if (!c.met) return 'unmet';
        const last = (c.evidence || [])[c.evidence.length - 1] || {};
        return last.kind === 'attested' ? 'attested' : 'met';
    }

    const feature = {
        tabs: ['Plan', 'Tasks', 'Criteria', 'Reviews'],
        sentence(state) {
            const plan = state && state.plan;
            if (!plan) return [];
            const n = featureCounts(plan);
            if (plan.status === 'draft') {
                return [{ text: 'Draft', tone: 'attention' }, plural((plan.phases_meta || []).length, 'phase'), plural(n.total, 'task')];
            }
            if (plan.status === 'review') return ['Review', n.unreviewed ? `${n.unreviewed} to review` : ''];
            if (plan.status === 'done') return ['Done', plural(n.total, 'task')];
            const cur = (plan.tasks || []).find(t => t.id === plan.current_task);
            return [cur ? `t${cur.id} of ${n.total}` : `${n.done} of ${n.total} done`,
                n.blocked ? { text: `${n.blocked} blocked`, tone: 'attention' } : ''];
        },
        now(state) {
            const plan = state && state.plan;
            const rows = blockerRows(state && state.blockers);
            if (!plan) return rows;
            if (plan.status === 'draft') {
                const n = featureCounts(plan);
                rows.push({ id: 'approve', kind: 'decision', status: 'waiting', glyph: '◆', title: 'Approve plan',
                    sub: `${plural((plan.phases_meta || []).length, 'phase')} · ${plural(n.total, 'task')} · revision ${plan.revision}`,
                    actions: [{ id: 'approve', label: 'Approve and start', primary: true }, { id: 'changes', label: 'Request changes' }] });
            }
            for (const p of (plan.diff_proposals || []).filter(d => d.status === 'pending')) {
                rows.push({ id: `diff:${p.id}`, kind: 'decision', status: 'waiting', glyph: '◆', title: `Diff for t${p.task_id}`,
                    sub: p.rationale || 'A proposed change waits for your decision.', ref: p.id,
                    actions: [{ id: 'diff-approve', label: 'Approve', primary: true }, { id: 'diff-reject', label: 'Reject' }] });
            }
            if (plan.completion_requested && !(plan.completion_gaps || []).length && plan.status !== 'done') {
                rows.push({ id: 'complete', kind: 'review', status: 'waiting', glyph: '◆', title: 'Mark feature complete?',
                    sub: 'Every task is complete and reviewed; the full suite passed.',
                    actions: [{ id: 'complete', label: 'Mark complete', primary: true }] });
            }
            return M().orderNow(rows);
        },
        primary(state) {
            const plan = state && state.plan;
            if (!plan) return null;
            if (plan.status === 'draft') return { id: 'approve', label: 'Approve plan' };
            if (plan.completion_requested && !(plan.completion_gaps || []).length && plan.status !== 'done') {
                return { id: 'complete', label: 'Mark complete' };
            }
            return null;
        },
        footWord(state) {
            const plan = state && state.plan;
            if (!plan) return '';
            if (plan.status === 'draft') return 'draft';
            if (plan.status === 'review') return 'review';
            if (plan.status === 'done') return 'done';
            const n = featureCounts(plan);
            const cur = plan.current_task || plan.next_task;
            return cur ? `t${cur} of ${n.total}` : `${n.done} of ${n.total}`;
        },
        phases(plan) {
            const phases = [...(plan && plan.phases_meta || [])].sort((a, b) => (a.order || a.id) - (b.order || b.id));
            return phases.map(p => ({ ...p, tasks: (plan.tasks || []).filter(t => t.phase_id === p.id) }));
        },
        taskRow(t) {
            const s = M().vstyle('feature_task', t.status);
            const met = (t.criteria || []).filter(c => c.met).length;
            return { id: t.id, status: s.status, glyph: s.glyph, title: `t${t.id} ${t.title}`,
                sub: [s.label, `${met} of ${(t.criteria || []).length} criteria`, t.reviewed ? 'reviewed' : '',
                    t.override ? 'override' : ''].filter(Boolean).join(' · ') };
        },
        criterionKey,
        taskSheet(t, reviews) {
            const crits = (t.criteria || []).map(c => {
                const k = criterionKey(c);
                const s = M().vstyle('criterion', k);
                const ev = (c.evidence || []).map(e => `${esc(e.kind)} ${e.ref ? `<span class="mono">${esc(e.ref)}</span>` : ''}${e.justification ? ` — ${esc(e.justification)}` : ''}`).join('; ');
                return `<li class="w-row"><span class="w-sign" data-status="${esc(s.status)}" aria-hidden="true">${esc(s.glyph)}</span>
                    <div class="w-row__main"><span class="w-row__title">${esc(c.id)} ${esc(c.text)}</span>
                    <span class="w-row__sub">${esc(s.label)}${ev ? ` · ${ev}` : ''}${c.check ? ` · check <span class="mono">${esc(c.check.command)}</span>` : ''}</span></div></li>`;
            }).join('');
            const revs = (reviews || []).filter(r => r.task_id === t.id).map(r =>
                `<li class="w-row"><div class="w-row__main"><span class="w-row__title">${esc(r.summary)}</span>
                 <span class="w-row__sub">check <span class="mono">${esc(r.check_run)}</span>${(r.issues || []).length ? ` · ${esc(plural(r.issues.length, 'issue'))}` : ''}</span></div></li>`).join('');
            const override = t.override ? `<dt>Override</dt><dd data-tone="attention">${esc(t.override.actor)}: ${esc(t.override.reason)}</dd>` : '';
            return `<dl class="w-facts"><dt>Status</dt><dd>${M().sign('feature_task', t.status)} ${esc(M().vstyle('feature_task', t.status).label)}</dd>
                <dt>Objective</dt><dd>${esc(t.objective || '—')}</dd>${override}</dl>
                <h3 class="w-group__title">Exit criteria</h3><ul class="w-rows">${crits}</ul>
                ${t.diff_stat ? `<h3 class="w-group__title">Changes</h3><pre class="w-code__pre mono">${esc(t.diff_stat)}</pre>` : ''}
                ${revs ? `<h3 class="w-group__title">Reviews</h3><ul class="w-rows">${revs}</ul>` : ''}
                <details class="w-disclose"><summary>Details</summary><dl class="w-facts"><dt>Task</dt><dd class="mono">t${esc(t.id)}</dd>
                <dt>Start tree</dt><dd class="mono">${esc(t.start_tree || '—')}</dd></dl></details>`;
        },
    };

    // ------------------------------------------------------------------ loop

    function money(v) { return `$${Number(v || 0).toFixed(2)}`; }

    function timeLeft(mission) {
        const cap = mission.budget && mission.budget.max_runtime_s;
        if (!cap) return '';
        const left = Math.max(0, cap - Number(mission.spent && mission.spent.runtime_s || 0));
        const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60);
        return h ? `${h}h ${m}m left` : `${m}m left`;
    }

    const loop = {
        tabs: ['Mission', 'Timeline', 'Backlog'],
        sentence(state) {
            const m = state && state.mission;
            if (!m) return [];
            const s = M().vstyle('loop_mission', m.status);
            const head = m.status_reason && m.status !== 'running' ? `${s.label} — ${m.status_reason}` : s.label;
            const tone = ['paused', 'blocked'].includes(m.status) ? 'attention' : undefined;
            const cost = m.budget && m.budget.max_cost_usd ? `${money(m.spent && m.spent.cost_usd)} of ${money(m.budget.max_cost_usd)}` : '';
            return [{ text: head, tone, keep: true }, m.increments && m.increments.length ? `increment ${m.increments.length}` : '',
                cost, timeLeft(m)];
        },
        meter(state) {
            const b = state && state.budget_meter;
            if (!b) return null;
            const kind = String(b.kind || '');
            const label = kind.startsWith('cost') ? `${money(b.used)} of ${money(b.cap)}`
                : kind.startsWith('runtime') ? `${Math.round(b.used / 60)} of ${Math.round(b.cap / 60)} min`
                    : `${b.used} of ${b.cap} ${kind}`;
            return { ratio: b.ratio, label };
        },
        now(state) {
            const rows = blockerRows(state && state.blockers);
            const m = state && state.mission;
            if (m && m.status === 'paused' && /budget/.test(m.status_reason || '')) {
                rows.push({ id: 'budget', kind: 'budget', status: 'waiting', glyph: '◆', title: 'Budget reached',
                    sub: m.status_reason, actions: [{ id: 'budget', label: 'Raise budget', primary: true }, { id: 'stop', label: 'Finish' }] });
            }
            if (m && m.status === 'done') {
                rows.push({ id: 'done', kind: 'review', status: 'waiting', glyph: '◆', title: 'Mission done — extend or finish?',
                    sub: m.status_reason || 'Every done-when criterion is met.',
                    actions: [{ id: 'resume', label: 'Extend', primary: true }, { id: 'stop', label: 'Finish' }] });
            }
            return M().orderNow(rows);
        },
        primary(state) {
            const m = state && state.mission;
            if (!m || ['stopped'].includes(m.status)) return { id: 'start', label: 'Start a mission' };
            if (m.status === 'running') return { id: 'pause', label: 'Pause' };
            if (['paused', 'blocked'].includes(m.status)) return { id: 'resume', label: 'Resume' };
            return null;
        },
        footWord(state) {
            const m = state && state.mission;
            return m ? M().vstyle('loop_mission', m.status).label : 'no mission';
        },
        incrementRow(inc) {
            const s = M().vstyle('loop_increment', inc.result);
            return { id: inc.n, status: s.status, glyph: s.glyph, title: `${inc.n} ${inc.objective}`,
                sub: [s.label, (inc.evidence || []).length ? (inc.evidence || []).join(', ') : '', inc.next ? `next: ${inc.next}` : '']
                    .filter(Boolean).join(' · '), when: M().relTime(inc.at) };
        },
        criterionRow(c) {
            const k = c.met ? (c.attested_by ? 'attested' : 'met') : 'unmet';
            const s = M().vstyle('criterion', k);
            return { id: c.id, status: s.status, glyph: s.glyph, title: c.text,
                sub: [s.label, c.check ? c.check.command : '', (c.evidence || []).join(', ')].filter(Boolean).join(' · ') };
        },
        incrementSheet(inc) {
            const list = items => (items || []).map(x => `<li>${esc(x)}</li>`).join('');
            return `<dl class="w-facts"><dt>Result</dt><dd>${M().sign('loop_increment', inc.result)} ${esc(M().vstyle('loop_increment', inc.result).label)}</dd>
                <dt>Decision</dt><dd>${esc(inc.decision || '—')}</dd><dt>Next</dt><dd>${esc(inc.next || '—')}</dd>
                ${inc.work_item ? `<dt>Work item</dt><dd class="mono">${esc(inc.work_item)}</dd>` : ''}
                ${inc.remediation_item ? `<dt>Remediation</dt><dd class="mono">${esc(inc.remediation_item)}</dd>` : ''}</dl>
                ${(inc.actions || []).length ? `<h3 class="w-group__title">Actions</h3><ul>${list(inc.actions)}</ul>` : ''}
                ${(inc.delegations || []).length ? `<h3 class="w-group__title">Delegated</h3><ul>${list(inc.delegations)}</ul>` : ''}`;
        },
    };

    // ------------------------------------------------------------------ teacher

    function currentModuleIndex(course) {
        const lesson = (course.lessons || []).find(l => l.lesson_id === course.current_lesson_id);
        if (!lesson) return 0;
        return (course.modules || []).findIndex(m => m.module_id === lesson.module_id) + 1;
    }

    function lessonGateLine(l) {
        const g = l.gate || {};
        if (l.status === 'completed') return `${M().vstyle('teacher_lesson', 'completed').label}${l.mastery_pct != null ? ` ${l.mastery_pct}%` : ''}`;
        if ((l.order_blockers || []).length) return 'waits for earlier lessons';
        if (['presenting', 'lecturing'].includes(l.status)) {
            if (g.skip) return 'lecture skipped (known)';
            if (!g.concluded) return `comprehension ${g.assessed || 0} of ${g.required || 0}`;
            if (!g.transcript) return 'needs transcript';
            return 'ready for the exercise';
        }
        return M().vstyle('teacher_lesson', l.status).label;
    }

    const teacher = {
        tabs: ['Path', 'Lesson', 'Mastery', 'Reviews'],
        sentence(state) {
            const c = state && state.course;
            if (!c) return [];
            if (c.status === 'curriculum_proposed') return [{ text: 'Curriculum to approve', tone: 'attention' }, plural((c.lessons || []).length, 'lesson')];
            if (c.status === 'diagnosing') return ['Diagnostic'];
            const due = (c.due_reviews || []).length;
            const mi = currentModuleIndex(c);
            return [mi ? `Module ${mi} of ${(c.modules || []).length}` : M().vstyle('teacher_course', c.status).label,
                c.current_lesson_id ? `lesson ${c.current_lesson_id}` : '',
                due ? { text: `${plural(due, 'review')} due`, tone: 'attention' } : ''];
        },
        now(state) {
            const c = state && state.course;
            const rows = blockerRows(state && state.blockers);
            if (!c) return rows;
            if (c.status === 'curriculum_proposed') {
                rows.push({ id: 'approve', kind: 'decision', status: 'waiting', glyph: '◆', title: 'Approve curriculum',
                    sub: `${plural((c.modules || []).length, 'module')} · ${plural((c.lessons || []).length, 'lesson')} · your consent`,
                    actions: [{ id: 'approve', label: 'Approve curriculum', primary: true }] });
            }
            for (const r of c.due_reviews || []) {
                rows.push({ id: `review:${r.review_id}`, kind: 'review', status: 'waiting', glyph: '◆',
                    title: `Review: ${r.objective || r.source_lesson_id}`, sub: `Due ${M().relTime(r.due_at)} ago`, ref: r.review_id,
                    actions: [{ id: 'start-review', label: 'Start review', primary: true }] });
            }
            const cur = (c.assignments || []).find(a => a.assignment_id === c.current_assignment_id);
            if (cur && cur.status === 'presented') {
                rows.push({ id: `exercise:${cur.assignment_id}`, kind: 'review', status: 'waiting', glyph: '◆',
                    title: 'Exercise waiting for you', sub: cur.prompt, ref: cur.assignment_id,
                    actions: [{ id: 'open-exercise', label: 'Open exercise', primary: true }] });
            }
            return M().orderNow(rows);
        },
        primary(state) {
            const c = state && state.course;
            return c && c.status === 'curriculum_proposed' ? { id: 'approve', label: 'Approve curriculum' } : null;
        },
        footWord(state) {
            const c = state && state.course;
            if (!c) return '';
            if (c.status === 'curriculum_proposed') return 'awaiting approval';
            return c.current_lesson_id ? `lesson ${c.current_lesson_id}` : M().vstyle('teacher_course', c.status).label;
        },
        path(course) {
            const modules = [...(course && course.modules || [])].sort((a, b) => a.order - b.order);
            return modules.map(m => ({ ...m, lessons: (course.lessons || []).filter(l => l.module_id === m.module_id) }));
        },
        lessonRow(l) {
            const s = M().vstyle('teacher_lesson', l.status);
            return { id: l.lesson_id, status: s.status, glyph: s.glyph, title: l.title, sub: lessonGateLine(l) };
        },
        lessonGateLine,
        masteryRow(r) {
            const pct = r.mastery_pct;
            const status = pct == null ? 'pending' : (pct >= 70 ? 'done' : 'unverified');
            const glyph = (M().vstyle('comprehension', pct == null ? '' : pct >= 70 ? 'on_track' : 'partial')).glyph;
            return { id: `${r.lesson_id}:${r.objective}`, status, glyph, title: r.objective,
                sub: pct == null ? 'not assessed yet' : `${pct}% · ${plural(r.attempts, 'attempt')}` };
        },
        reviewRow(r, now) {
            const due = r.due_at && r.due_at <= (now || Date.now() / 1000);
            const s = M().vstyle('teacher_review', r.status === 'pending' ? (due ? 'due' : 'scheduled') : r.status);
            const when = r.due_at ? new Date(r.due_at * 1000).toISOString().slice(0, 10) : '';
            return { id: r.review_id, status: s.status, glyph: s.glyph, title: r.objective || r.source_lesson_id,
                sub: [s.label, when, r.score_pct != null ? `${r.score_pct}%` : ''].filter(Boolean).join(' · ') };
        },
        checkRow(k) {
            const s = k.signal ? M().vstyle('comprehension', k.signal) : M().vstyle('teacher_review', 'scheduled');
            return { id: k.check_id, status: k.signal ? s.status : 'pending', glyph: s.glyph, title: k.question,
                sub: [k.answer != null ? `answer: ${k.answer}` : 'waiting for the answer', k.signal ? s.label : ''].filter(Boolean).join(' · ') };
        },
        gradeSheet(a) {
            const g = a.grade;
            if (!g) return '<p class="w-group__empty">Not graded yet.</p>';
            const rubric = (g.rubric_breakdown || []).map(r =>
                `<dt>${esc(r.criterion)} ×${esc(r.weight)}</dt><dd>${esc(r.score_pct != null ? r.score_pct + '%' : '—')}${r.note ? ` · ${esc(r.note)}` : ''}</dd>`).join('');
            const state = g.status === 'awaiting_judgement' ? 'awaiting judgement' : `${g.score_pct}% · ${g.passed ? 'passed' : 'not passed'}`;
            return `<dl class="w-facts"><dt>Grade</dt><dd>${esc(state)}</dd>${rubric}</dl><p>${esc(g.feedback || '')}</p>`;
        },
    };

    // ------------------------------------------------------------------ research

    const research = {
        tabs: ['Questions', 'Claims', 'Sources', 'Citations'],
        sentence(state) {
            const qs = (state && state.questions) || [];
            if (!qs.length && !((state && state.source_count) || 0)) return [];
            const contested = qs.filter(q => q.status === 'contested').length;
            const read = ((state && state.coverage && state.coverage.sources) || {});
            return [plural(qs.length, 'question'), contested ? { text: `${contested} contested`, tone: 'attention' } : '',
                plural(((state && state.claims) || []).length, 'claim'),
                `${(read.read || 0) + (read.cited || 0)} sources read`];
        },
        now(state) { return M().orderNow(blockerRows(state && state.blockers)); },
        primary() { return null; },
        footWord(state) {
            const qs = (state && state.questions) || [];
            return qs.length ? plural(qs.length, 'question') : '';
        },
        sourceSheet(src, claims) {
            const st = M().vstyle('research_source_state', src.state);
            const quotes = [];
            for (const c of claims || []) {
                for (const e of c.evidence || []) {
                    if (e.citation_id === src.id) quotes.push({ claim: c, e });
                }
            }
            const citing = quotes.map(({ claim, e }) =>
                `<li class="w-row"><span class="w-sign" data-status="${esc(M().vstyle('research_stance', e.stance).status)}" aria-hidden="true">${esc(M().vstyle('research_stance', e.stance).glyph)}</span>
                 <div class="w-row__main"><blockquote class="rs-evidence__quote">${esc(e.excerpt)}</blockquote>
                 <span class="w-row__sub">${esc(claim.id)} · ${esc(claim.text)}</span></div></li>`).join('');
            const cred = src.state === 'candidate' ? '' :
                `<dt>Credibility</dt><dd>${esc(Math.round((src.credibility_score || 0) * 100))}%${src.assessed ? '' : ' (prior, not assessed)'}${src.metadata && src.metadata.assessment_rationale ? ` · ${esc(src.metadata.assessment_rationale)}` : ''}</dd>`;
            return `<dl class="w-facts"><dt>State</dt><dd>${M().sign('research_source_state', src.state)} ${esc(st.label)}${src.id ? ` · [^${esc(src.id)}]` : ''}</dd>
                <dt>Origin</dt><dd>${esc(src.origin || '')}</dd>${cred}
                ${(src.authors || []).length ? `<dt>Authors</dt><dd>${esc(src.authors.join(', '))}</dd>` : ''}
                ${src.date ? `<dt>Published</dt><dd>${esc(src.date)}</dd>` : ''}</dl>
                <p><a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a></p>
                <h3 class="w-group__title">Quoted in claims</h3><ul class="w-rows">${citing || '<li class="w-group__empty">Not cited yet.</li>'}</ul>
                ${src.snippet ? `<details class="w-disclose"><summary>Snippet</summary><p>${esc(src.snippet)}</p></details>` : ''}
                <details class="w-disclose"><summary>Details</summary><dl class="w-facts"><dt>Type</dt><dd>${esc(src.source_type)}</dd>
                <dt>URL</dt><dd class="mono">${esc(src.url)}</dd><dt>Accessed</dt><dd>${esc(src.accessed_date || '')}</dd></dl></details>`;
        },
        // [^n] in assistant prose → links that open the source sheet (not inside code).
        linkFootnotes(html) {
            return String(html || '').split(/(<code[\s\S]*?<\/code>|<pre[\s\S]*?<\/pre>)/).map((part, i) => (i % 2 ? part
                : part.replace(/\[\^(\d+)\]/g, (_m, n) => `<a href="#" class="mode-cite" data-cite="${n}">[^${n}]</a>`))).join('');
        },
    };

    // ------------------------------------------------------------------ default (turn receipt)

    const receipt = {
        line(r) {
            if (!r) return '';
            const files = (r.files || []).length;
            if (r.status === 'no_changes') {
                const checks = (r.checks || []).length;
                return checks ? `no files changed · ${(r.checks || []).filter(c => c.passed).length} of ${checks} checks passed` : '';
            }
            return `${plural(files, 'file')} changed · ${M().vstyle('turn_receipt', r.status).label}`;
        },
        mark(r) {
            return M().vstyle('turn_receipt', (r && r.status) || 'no_changes');
        },
        sheet(r) {
            const files = (r.files || []).map(f => `<li class="mono">${esc(f)}</li>`).join('');
            const checks = (r.checks || []).map(c => {
                const s = M().vstyle('check_run', c.passed ? 'passed' : 'failed');
                return `<li class="w-row"><span class="w-sign" data-status="${esc(s.status)}" aria-hidden="true">${esc(s.glyph)}</span>
                    <div class="w-row__main"><span class="w-row__title mono">${esc(c.command)}</span>
                    <span class="w-row__sub">${esc(s.label)}${c.after_edit ? '' : ' · before the last edit'}</span></div></li>`;
            }).join('');
            return `<h3 class="w-group__title">Files</h3><ul>${files || '<li>None</li>'}</ul>
                <h3 class="w-group__title">Checks</h3><ul class="w-rows">${checks || '<li class="w-group__empty">No check ran.</li>'}</ul>`;
        },
        planRows(items) {
            return (items || []).filter(i => !['done', 'dropped'].includes(i.status)).map(i => {
                const s = M().vstyle('work_item', i.status);
                return { id: i.id, status: s.status, glyph: s.glyph, title: i.title, sub: s.label };
            });
        },
    };

    root.MuModeModels = { security, debug, feature, loop, teacher, research, receipt, blockerRows };
})(typeof window !== 'undefined' ? window : globalThis);
