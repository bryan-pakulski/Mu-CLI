# Teacher Mode

A one-on-one tutoring engine. The agent runs a diagnostic up front to
learn how the user learns (modality, pace, jargon tolerance, motivation,
analogy anchors, voice), proposes a curriculum the learner approves,
teaches each lesson with recorded comprehension checks, and proves each
lesson with a verified exercise. Generic, off-the-shelf teaching is
treated as a failure mode: every explanation, example and exercise
should reflect the learner in front of the agent.

Switch in via `/mode teacher`. Course management is independent of the
mode: `/teach new <subject>` works from any mode and creates a course;
`/mode teacher` is what lets the agent drive lessons against it.

The method lives in the engine (`mu/teacher/engine.py` for the course
model, `mu/teacher/gates.py` for the rules), not in the prompt. Every
surface (agent tools, the GUI panel, `/teach`, mobile) goes through the
same gates, and decisions that belong to the learner are not agent
tools.

For non-teaching workflows see [feature](feature_plan_engine.md) for
phased implementation work and [security](security_mode.md) for audits.

## State and context

- The course lives on disk in `course.json`; the session keeps only a
  pointer record (course id, path, revision) and loads the course
  through a cached loader. Course files load tolerantly: unknown keys
  are ignored and missing ones default, so older course files keep
  loading.
- Each turn the engine builds a short **mode capsule** that rides in the
  request-only TURN CONTEXT (never saved to history): the course and its
  status, the current lesson with its lecture gate state (checks
  assessed, comprehension, transcript present), answered checks still to
  assess, due reviews, and a one-line LEARNER PROFILE summary. With no
  active course it lists what earlier courses learned about this
  learner. There is no learner-profile block in the system prompt.
- When the agent needs something only the learner can give, it calls
  the core `raise_blocker` tool (shared by every mode). The learner's
  next message, the panel or `/blocker` resolves it.

## Gates

| Step | Rule |
| --- | --- |
| Curriculum | `propose_curriculum` needs a recorded diagnostic. On a course already proposed or in progress it makes a new **revision** that keeps every lesson with history (started, or with assignments), and clears the approval. |
| Approval | Only the learner approves: the panel's *Approve curriculum*, `/teach approve`, or mobile. There is no approval tool; an agent actor is refused. Approval records actor, time and revision. |
| Lesson order | `start_lesson` needs an approved curriculum and respects order: earlier lessons in the module completed, earlier modules completed with `complete_module`. The learner can start out of order with a reason (`/teach start <id> --override <why>`, the panel's *Start anyway…*); the override is recorded as an event. |
| Assignment | `assign_exercise` is refused until the lecture has concluded (or was skipped for a known concept) **and** the lecture transcript exists. Remediation and reviews skip this gate. |
| Verifier | For code kinds the verifier runs against the starting files first and must **fail** there, or the assignment is refused. |
| Advance | `decide_next(advance)` is refused unless the lesson's latest grade passed. |
| Module | `complete_module` needs every lesson completed and module mastery at or above the module's threshold (default 70%). |
| Finish | `finalize_course` needs every lesson passed, or a learner-confirmed early finish (`/teach finish --early <why>`, the panel's *Finish early…*), which is recorded. |

## Lecture tracking

Lectures are tracked with explicit checks, not an LLM classifier.

- `teach_check(question, kind, options?, objective?)` records a
  comprehension check. `kind="choice"` shows the learner the picker
  (`ask_user_choice`) and records their pick exactly. `kind="open"`:
  the agent asks it and ends its message; the learner's **next chat
  message is attached** to the latest unanswered open check
  automatically.
- `assess_response(check_id, signal, note)` records the tutor's read of
  the answer: `on_track` (100), `partial` (50) or `confused` (0). It is
  refused until the check has an answer.
- The engine concludes the lecture itself once at least
  `min_lecture_checks` (default 2) checks are assessed and the mean
  comprehension reaches `lecture_comprehension_threshold` (default 60%).
  Partial and confused checks are kept as the lesson's lecture gaps.
- `skip_lecture(lesson_id, concept, reason)` skips the lecture only for
  a concept the diagnostic marks known (strengths, anchors or
  background); the reason is recorded.
- **Inline options nudge.** If an assistant message lists answer
  options inline (`A) … B) …`, detected by a regex, no LLM), the agent
  gets one nudge to re-ask the check through `teach_check(kind="choice")`.

The legacy LLM lecture watcher (`mu/teacher/watcher.py`, classifying
every message into lecture turns) runs only when the `teacher_watcher`
variable is on (default off). With it on, it replaces the automatic
answer attachment.

## Engine tools

| Tool | Role |
| --- | --- |
| `create_course` | Open a course (status `diagnosing`). Returns what earlier courses learned about this learner from the Memory Ledger. |
| `record_diagnostic` | Save the learner profile from the diagnostic. Required before `propose_curriculum`. Re-running merges: lists are unioned, given scalars replace. |
| `update_learner_profile` | Mid-course refinement with an `observation`; lists merge, scalars overwrite, and the observation is kept in `calibration_history`. |
| `propose_curriculum` | Propose modules → lessons, each lesson with 1–4 learning objectives. Status becomes `curriculum_proposed`. |
| `start_lesson` | Begin a lesson (order gate above). Lists due reviews to handle first. |
| `teach_check` / `assess_response` | Comprehension checks and their assessment (above). |
| `skip_lecture` | Known-concept skip (above). |
| `write_lecture_transcript` | Write `lessons/<id>/lecture.md`. Required before `assign_exercise`. |
| `register_exercise_file` | Write an illustrative, ungraded file under `lessons/<id>/exercises/`. |
| `teach_sources` | Attach research-ledger citation numbers to a lesson (see Sources). |
| `assign_exercise` | Create the assignment; writes `artifact_files` to `work/` and runs the verifier pre-check. Pass `review_id` for a due review. Approval-gated; the preview shows the verifier command. |
| `submit_assignment` | Record the learner's submission (answers, or a note that the `work/` files are edited). |
| `grade_assignment` | Grade a submission (see Grading). Approval-gated; the preview shows the command that will run. |
| `decide_next` | `advance` (only after a pass) or `remediate` (re-teach, then a new exercise; the lecture reopens). |
| `record_dialog_turn` / `close_dialog` | Drive and close a `socratic-dialog` assignment; `close_dialog` enforces `min_turns` and `required_concepts`. |
| `get_course_state` | Status, current lesson and its gate state, objective mastery, due and upcoming reviews. |
| `complete_module` | Module gate above. |
| `finalize_course` | Course gate above; writes the report card and saves the learner model. |
| `get_due_reviews` | Reviews due now, oldest first. |
| `complete_review` | Close a due review with its graded review exercise. |

There is no `approve_curriculum`, `raise_teacher_blocker` or
`schedule_review` tool: approval is the learner's, blockers use the
core `raise_blocker`, and reviews are scheduled by the engine.

## Workflow

1. **Diagnostic** (8–15 questions, 2–3 at a time): `create_course`,
   then prior experience and adjacent fields (analogy anchors), goal
   and motivation, modality, pace, jargon tolerance, voice, and
   something they bounced off before and why. `record_diagnostic`.
2. **Curriculum:** read sources for factual lessons, then
   `propose_curriculum` (3–8 modules, 2–6 lessons each). The agent
   shows it and asks the learner to approve.
3. **Each lesson**, in order:
   1. `start_lesson`; handle due reviews first.
   2. A ≤3-sentence brief, then one chunk per message, matched to the
      profile, ending with one `teach_check`; then `assess_response`
      and react. The lecture concludes on its own.
   3. `register_exercise_file` examples and `write_lecture_transcript`.
   4. `assign_exercise` — the smallest exercise that proves the
      objectives.
   5. `grade_assignment` (or `close_dialog`), with honest feedback.
   6. `decide_next(advance | remediate)`.
4. After a module's lessons pass, `complete_module`.
5. **Reviews** when due: `assign_exercise(review_id=...)`, grade it,
   `complete_review`.
6. `finalize_course`.

## Grading

- **Code kinds** (`fix-broken-code`, `implement-from-scratch`,
  `command-output`): the engine runs `verify_cmd` through `run_check`
  in the assignment's `work/` directory (in the sandbox container when
  Docker is available, the host otherwise), with the same controls as
  `bash`. It passes when every `expected_markers` entry appears (or the
  exit code is 0 without markers) and no `forbidden_markers` appear.
  The run is kept as a check run linked from the grade.
- **Exact / regex kinds** (`predict-output`, `multiple-choice`,
  `fill-blank`) are compared literally or by regex.
- **Rubric kinds** (`short-answer`, `explain-trace`): the keyword gate
  runs first. Until the agent gives `rubric_scores` (a score per rubric
  criterion), the grade is **awaiting judgement**, its own status and
  not a 0% fail. With a rubric, every criterion must be scored and the
  total is weighted by each criterion's `weight`.
- `socratic-dialog` is graded by `close_dialog`.

## Mastery and reviews

- Assignments, checks and reviews map to a lesson's learning
  objectives. **Mastery per objective** is the best of the five most
  recent graded attempts, each decayed by age (half-life 45 days).
  Lesson, module and course mastery are means over objectives, and the
  panel, `complete_module` and the report card all use the same rule.
- **Spaced reviews** use an SM-2 schedule per objective with dates: the
  engine (re)schedules after each graded attempt (1 day, 6 days, then
  interval × ease; a low score resets to 1 day). A review is a re-graded
  exercise; `complete_review` records its score, which feeds mastery and
  schedules the next review. Due reviews appear in the mode capsule, at
  `start_lesson` and in the panel.

## Learner model across courses

`record_diagnostic`, `update_learner_profile` and `finalize_course`
save the profile to the Memory Ledger (personal scope, tagged
`learner-profile` and `course:<id>`). `create_course` and the empty-course
capsule pre-load what earlier courses learned.

## Sources

Lesson sources use the research ledger: the agent reads a source first
(`url_grounding`, `research_fetch`), then `teach_sources(lesson_id,
citation_ids)` attaches it. Citations of sources not read are refused.
The transcript cites them as `[^n]`, and `curriculum.md` lists them.

## Assignment kinds

| Kind | Verification | When to use |
| --- | --- | --- |
| `fix-broken-code` | exec + markers | Engine writes a file with a bug; learner edits. |
| `implement-from-scratch` | exec + markers | Engine writes a spec + test harness; learner writes the solution. |
| `command-output` | exec + markers | Learner runs a shell command; markers checked. |
| `predict-output` | exact / regex match | Learner predicts what code prints. |
| `multiple-choice` | exact match | Live quiz UI by default. |
| `fill-blank` | regex match | Live quiz UI by default. |
| `short-answer` | keyword gate + weighted rubric | Prose answer. |
| `explain-trace` | keyword gate + weighted rubric | Learner explains what code does. |
| `socratic-dialog` | dialog close | Back-and-forth Q&A for concepts; `min_turns` and `required_concepts` enforced. |

## Live quiz UI

For `multiple-choice` and `fill-blank` assignments, `assign_exercise`
launches the quiz picker (`session.ui.run_quiz`) at assignment time;
answers collected there are stored as the submission. The tool result
carries a `live_quiz_launch` envelope (`attempted`, `launched`,
`reason`). When no UI can run the quiz, `launched=false` with a reason,
and the agent asks the questions in chat instead; it must read this
field before narrating the outcome.

## Output artifacts

Everything lives under `<workspace>/courses/<course_id>/` (the current
working directory when no workspace is attached).

```
course.json              # full Course state (checks, grades, reviews, events)
curriculum.md            # syllabus with objectives and sources (regenerated)
report_card.md           # written by finalize_course
lessons/<id>/lecture.md  # authored lecture transcript
lessons/<id>/exercises/  # illustrative example files
assignments/
  <assignment_id>/
    prompt.md            # instructions
    work/                # engine-created artifacts; the verifier runs here
    grade.json           # Grade record
```

The lecture transcript is the one transcript; the recorded checks and
responses in `course.json` are the record of the lecture itself.

## Slash commands

`/teach` works from any mode. See [commands.md](commands.md).

| Command | Behavior |
| --- | --- |
| `/teach new <subject>` | Create a course in this workspace. Does not switch mode; run `/mode teacher` next. |
| `/teach list` | List courses in this workspace (also bare `/teach`). |
| `/teach load <id>` | Activate an existing course. |
| `/teach exit` / `unload` | Clear the active course (it stays on disk). |
| `/teach status` | Current module/lesson, progress, average score. |
| `/teach next` | Show the next pending lesson. |
| `/teach grades` | Table of every graded assignment. |
| `/teach curriculum` | Render the syllabus. |
| `/teach approve` | Approve the proposed curriculum (the learner's decision). |
| `/teach start <id> --override <why>` | Start a lesson out of order; the override is recorded. |
| `/teach finish --early <why>` | Allow finishing before every lesson passed; recorded. |
| `/teach delete <id>` | Delete a course (irreversible). |
| `/teach help` | Inline help. |

## Panel and mobile

The teacher panel uses the shared mode shell. Its state sentence reads
like *Module 2 of 4 · lesson 2.3 · 2 reviews due* (or *Curriculum to
approve*); tabs are Path · Lesson · Mastery · Reviews.

- **Now:** *Approve curriculum* (the primary action while a curriculum
  is proposed), due reviews (*Start review*), an exercise waiting for
  the learner, and open blockers.
- **Path:** modules → lessons with their status and gate state
  (*needs transcript*, *comprehension 2 of 3*), *Read* for the lecture
  transcript, *Start anyway…* for an out-of-order start with a reason,
  and *Finish early…*.
- **Lesson:** the current lesson's checks and responses, assignment and
  grade with per-criterion rubric scores; the lecture opens as a
  reading view in the expanded view.
- **Mastery:** objective rows with percentages. **Reviews:** due and
  upcoming reviews with dates.

Endpoints (`mu/gui/routers/teacher.py`): `GET /api/teacher/state`;
`POST /api/teacher/approve`, `POST /api/teacher/lessons/{id}/start`
(optional `override_reason`) and `POST /api/teacher/finish-early`
(`reason`), which call the same gates with the surface as actor; and
`GET /api/teacher/lessons/{id}/lecture`, `.../exercises` and
`.../exercises/{path}` for the lesson files (path traversal refused).

The Android teacher screen shows the path, mastery and reviews
read-only, with *Approve curriculum* and blocker resolution in Now.

## Plan-mode interaction

With `/plan on`, every teacher tool that changes the course or writes
files is blocked with the standard plan-mode envelope (`create_course`
through `finalize_course`, including `teach_check`, `assess_response`,
`skip_lecture`, `teach_sources`, `complete_review`,
`write_lecture_transcript` and `register_exercise_file`).
`get_course_state` and `get_due_reviews` stay available.
