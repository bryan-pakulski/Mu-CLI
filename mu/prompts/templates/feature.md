---
name: feature
version: 2
description: Feature plan loop — draft, user approval, evidence-backed tasks, review.
---
WORKFLOW (Feature plan) — the plan engine is the only plan; the user owns approval and completion.

1. **Propose**: understand the request (read code in parallel, `search_memory`), then `propose_feature_plan(name, goal, phases → tasks)`. Every task has an objective, actions and exit criteria; give a criterion a `check` command when one can prove it. Show the user the plan and ask them to approve it (/feature approve or the panel). Do not implement before approval; edits are refused until then. Change it with `revise_feature_plan` — changing an approved plan needs re-approval.
2. **Execute, one task at a time**: `start_task(id)` → implement in small steps → meet each criterion with evidence:
   - `check_criterion(task, criterion)` runs its declared check;
   - `cite_evidence(task, criterion, runs=[r<n>])` cites a passing run (e.g. a `bash` test run), or `file` + `justification` for criteria that aren't commands.
   Then `complete_task(id)` — refused until every criterion has evidence.
3. **Review** each completed task: `review_task(id, summary, issues)` against its objective and criteria; it needs a passing check after your last edit. Issues are `follow_up` (a new task) or `blocking` (a blocker).
4. **Finish**: `run_feature_suite(command)` after the last change, then `request_feature_completion` — the user marks it complete.

`feature_status` shows the plan. Blocked on the user → `block_task(id, summary, needs)` (or `raise_blocker`) and stop. Save durable decisions with `save_memory`; delegate research-heavy side quests with `spawn_agent`.
