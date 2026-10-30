---
name: loop
version: 3
description: Loop mission — increments with evidence until done, paused or out of budget.
---
WORKFLOW (Loop mission) — the user owns the mission (goal, done-when criteria, budget) and starts, pauses and stops it. A message they send while no mission is live starts one with that message as its goal; if there is still no mission, ask them to start one (`/loop start <goal>`). Never set or change the goal yourself.

Each increment:
1. **Re-orient**: the mission state rides with this message; `loop_status` for detail; `search_memory` for prior findings.
2. **Pick** the next backlog item (`todo_write` / `todo_set_status`; one in progress).
3. **Gather** in parallel (`retrieve_relevant_context`, `read_file`, `search_for_string`), then **act** in small, testable steps; isolate risky or research-heavy side quests with `spawn_agent`.
4. **Verify** with a real run (`bash` tests, linters, smoke checks). A backlog item is done only with a passing check run as evidence: `todo_set_status(id, "done", evidence=["r<n>"])`.
5. **Record** `loop_increment(objective, result, evidence, decision, next)` — this is the timeline the user reads. A failed increment opens a remediation task.
6. **Stop conditions**: `loop_check_done` checks the done-when criteria; when all pass the mission is done.

Blocked on credentials, a decision or the environment → `raise_blocker` with the exact request and stop; never stall silently. Save durable findings with `save_memory` and in-flight notes with `save_scratchpad`.
