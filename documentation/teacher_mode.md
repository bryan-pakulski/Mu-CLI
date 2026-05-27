# Teacher Mode

A one-on-one tutoring engine. The agent runs an in-depth diagnostic up
front to learn how the user actually learns (modality, pace, jargon
tolerance, motivation, analogy anchors, personality), keeps that
`learner_profile` injected into every model turn, and continuously
refines it via `update_learner_profile` as the course progresses.
Generic, off-the-shelf teaching is treated as a failure mode — every
explanation, example, and exercise should reflect the specific learner
in front of the agent.

Switch in via `/mode teacher`. Course management is independent of the
mode: `/teach new <subject>` works from any mode and creates a course;
`/mode teacher` is what lets the agent actually drive lessons against
it.

## Architecture: watcher, not narrator

The agent teaches in chat. A watcher subsystem
(`mu/teacher/watcher.py`) classifies each assistant message and each
learner message into structured engine events
(`agent_explanation`, `agent_check`, `learner_response`,
`learner_question`) and writes them into the course transcript
automatically. The agent does NOT call any `record_lecture_turn`-style
tool — those tools were removed from the agent's surface to stop the
agent from fabricating a transcript that doesn't match what the user
actually saw.

Two consequences:

- **One explanation per assistant message.** The watcher records at
  most one `agent_explanation` per message. A second explanation in
  the same message is dropped and an "end the message after one check
  and wait for the learner" feedback note is surfaced. The agent can't
  bulldoze the lecture.
- **Multiple-choice always uses the interactive picker.** Discrete-
  option comprehension checks must go through `ask_user_choice`
  (in-lecture) or `assign_exercise(kind="multiple-choice", ...)`
  (graded). The watcher detects inline `A) … B) … C) …` patterns in
  assistant messages and surfaces feedback telling the agent to redo
  the check through the picker. Inline letter options bypass the TUI
  entirely and feel nothing like a tutor.
- **Comprehension signals come from real learner replies.** When the
  watcher classifies a learner_response with signal
  `on track | partial | confused`, those are derived from the actual
  user message — not numbers the agent typed. `conclude_lecture` is
  auto-fired by the watcher when the rolling comprehension score meets
  the lesson threshold.

The hard contract: a lesson is COMPLETE only when its assignment passes
verification. For code/quiz kinds the engine runs a verify command and
checks markers literally; for socratic-dialog kinds the engine enforces
`min_turns` and `required_concepts` coverage. `decide_next(advance)` is
refused unless the most recent grade passed — no "looks right to me"
inflation.

For non-teaching workflows see [feature](feature_plan_engine.md) for
phased implementation work and [security](security_mode.md) for audits.

## Engine tools

The teacher engine is the only source of truth for course progress.

| Tool | Role |
| --- | --- |
| `create_course` | Open a new course. Status begins as `diagnosing`. |
| `record_diagnostic` | Save the full learner profile after the upfront Q&A: strengths, gaps, goals, modality, pace, jargon_tolerance, motivation, background, personality, anchors, notes. Required before `propose_curriculum`. |
| `update_learner_profile` | Mid-course profile refinement. Call when an observed pattern (analogy lands, new stumbling block, pace needs adjustment, etc.) should change how subsequent lessons are taught. List fields are merged, scalars overwrite; every call appends to `calibration_history`. |

> **Note**: `present_concept`, `start_lecture`, `record_lecture_turn`,
> and `conclude_lecture` are no longer exposed to the agent. The
> watcher fires them internally based on the chat content. They remain
> as engine functions only.
| `propose_curriculum` | Replace the modules + lessons with a proposed curriculum. Flips status to `curriculum_proposed`. |
| `approve_curriculum` | Learner-side approval. Unlocks the lesson loop; refuses unless status is `curriculum_proposed`. |
| `start_lesson` | Set the current lesson and flip its status to `presenting`. |
| `present_concept` | Record the agent's ≤3-sentence intro for the lesson. |
| `start_lecture` | Enter the lecture / back-and-forth teaching phase. Optional `plan` outlines the chunks you'll cover. |
| `record_lecture_turn` | Append a turn to the lecture: `agent_explanation`, `agent_check`, or `learner_response`. |
| `conclude_lecture` | Close the lecture with a `comprehension_pct` and `gaps`. Refuses unless `min_lecture_checks` is met and the score clears the threshold. |
| `assign_exercise` | Create an assignment. Engine writes `artifact_files` to `work/` and persists the verification spec. |
| `submit_assignment` | Record the learner's submission payload (inline answer or notes referencing edited artifact files). |
| `grade_assignment` | Run the verifier. For exec_markers kinds runs `verify_cmd`; for MC/fill-blank with live UI launches the quiz Application; for rubric kinds requires `llm_rubric_score` after the keyword gate passes. |
| `decide_next` | Advance or remediate a graded lesson. Refuses `advance` unless the lesson's last grade passed. |
| `record_dialog_turn` | For `socratic-dialog` assignments — append one Q or A turn. |
| `close_dialog` | Finalize a socratic-dialog assignment with `mastery_pct`, `summary`, `gaps`. Refuses unless `min_turns` and `required_concepts` thresholds are met. |
| `get_course_state` | Snapshot: status, current module/lesson, metrics, latest grade. |
| `complete_module` | Mark a module complete. Refuses unless all its lessons are completed AND aggregate score ≥ `mastery_threshold`. |
| `finalize_course` | Write the report card, save a `user_skill:<subject>` memory, mark the course completed. |
| `raise_teacher_blocker` | Signal that the agent needs learner clarification before proceeding. |

## Phases

### Phase 1 — Deep diagnostic (8–15 questions, conversational)

This phase is the foundation of every subsequent lesson. Spend real
time here. Ask in small batches, not a wall of questions.

1. `create_course` with the subject.
2. **Prior experience** in the subject AND adjacent fields. Specific
   languages, tools, or domains they're already fluent in — these
   become analogy anchors.
3. **Motivation and goal.** Why are they learning this? What does
   "done" look like, specifically?
4. **Learning modality.** Analogies / hands-on / formal definitions /
   worked examples / visual / socratic — ask, but also infer from how
   they answer your earlier questions.
5. **Pace.** Deep mastery or get-functional-fast?
6. **Jargon tolerance.** Plain-English-first or precise-terms-up-front?
   Probe with one or two field-specific terms.
7. **Personality cues.** Terse vs chatty; pushback vs gentle; humor.
   Match their register.
8. **Known anchors.** Concepts they cite confidently in answers — log
   them; later lessons hang new material on these.
9. **Past stumbling blocks.** Something in this or an adjacent field
   they tried to learn and bounced off — and why. Highest-value
   single data point you'll capture.
10. `record_diagnostic` with EVERY field you have evidence for. Don't
    propose a curriculum until this is in — the engine refuses.

The profile is then auto-injected into the agent's system prompt on
every turn, and refined mid-course via `update_learner_profile` when
patterns emerge.

### Phase 2 — Curriculum proposal

1. `propose_curriculum` with 3–8 modules, each with 2–6 lessons.
2. Show the proposal to the learner. Wait for `approve_curriculum`.

### Phase 3 — Per-lesson loop

Repeat until the course is complete.

1. `start_lesson(next_lesson_id)`.
2. **Open the lesson with a ≤3-sentence concept brief in chat.** No
   tool call — just write the headline / hook to the learner.
3. **Cover the material in chat, one chunk per assistant message:**
   1. Write ONE explanation chunk (examples, definitions, snippets
      tuned to the learner's profile).
   2. Ask ONE comprehension check question at the end of the same
      message.
   3. **End the message.** Wait for the learner's reply.
   4. The watcher classifies the learner's reply into a
      `learner_response` with `comprehension_signal` (on track /
      partial / confused). React: re-explain gaps when partial or
      confused; move forward when on track.
   5. Mid-lecture interrupts (the learner asks a question instead of
      answering) are auto-classified as `learner_question`. Answer
      before returning to your planned chunk.
   The watcher auto-fires `start_lecture` on the first substantive
   explanation and auto-fires `conclude_lecture` when comprehension
   across recorded responses clears the lesson threshold. Skip
   chunked teaching ONLY when the diagnostic showed the learner
   already knows this concept (e.g. a C++ programmer learning C
   pointer syntax). Acknowledge in one line and go straight to (4).
4. `assign_exercise` — pick the SMALLEST exercise that proves the
   concept. Pick the kind to match the topic:
   - **code**: `fix-broken-code` (you write a broken file via
     `artifact_files`; learner edits) or `implement-from-scratch`.
   - **factual recall**: `multiple-choice` or `fill-blank` with
     `quiz_questions` — engine launches the live quiz UI automatically.
   - **theory / design / "why does X work this way"**:
     `socratic-dialog` — set `verification.min_turns` and
     `verification.required_concepts`, drive the dialog through
     `record_dialog_turn` (one call per agent question, one per learner
     answer). Close with `close_dialog`.
   - **read a trace**: `predict-output` or `explain-trace`.
5. Learner does the work. Call `submit_assignment` if they answered
   inline; for code edits the engine reads the work files at grade time.
6. `grade_assignment` (or `close_dialog` for socratic kinds).
7. Give specific, honest feedback. If they got 40%, say so.
8. `decide_next(advance | remediate)`. If `remediate`, do a *different*
   small exercise on the same concept — and if the failure was a
   *understanding* gap, re-enter the lecture phase (`start_lecture`
   is allowed from `remediating`) before re-assigning.

### Phase 4 — Module review

After every lesson in a module passes, `complete_module`. The engine
refuses if aggregate score < the module's `mastery_threshold`. If
refused, schedule a remediation lesson for the weakest topic and loop.

### Phase 5 — Course completion

`finalize_course` writes the report card and saves
`user_skill:<subject>` to task memory so future courses can recall what
the learner already knows.

## Assignment kinds

| Kind | Verification | When to use |
| --- | --- | --- |
| `fix-broken-code` | exec + markers | Engine writes a `.pl`/`.py` file with a bug; learner edits. `verify_cmd` runs it; `expected_markers` must literally appear. |
| `implement-from-scratch` | exec + markers | Engine writes a spec + test harness. Learner writes the solution file. |
| `predict-output` | exact / regex match | Engine shows code; learner predicts the output. Compared literally or by regex. |
| `multiple-choice` | exact match | Engine presents N options. Live quiz UI by default. |
| `fill-blank` | regex match | Engine presents text with `___`. Live quiz UI by default. |
| `command-output` | exec + markers | Engine asks the learner to run a shell command and paste output; markers checked. |
| `short-answer` | rubric_judge + keyword gate | Prose answer. Required terms MUST appear before LLM rubric scoring is admitted. |
| `explain-trace` | rubric_judge + keyword gate | Learner explains what code does. Same two-gate flow as short-answer. |
| `socratic-dialog` | dialog close | Back-and-forth Q&A for concepts. Engine enforces `min_turns` and `required_concepts` coverage. |

## Live quiz UI

For `multiple-choice` and `fill-blank` assignments, `assign_exercise`
tries to launch a full-screen prompt-toolkit Application via
`session.ui.run_quiz` immediately at assignment time. Arrow keys
navigate; Enter submits and reveals correctness with the question's
`explanation`; `→` advances; `←` reviews (read-only after submit); `q`
/ `Esc` exits early.

The tool result contains a `live_quiz_launch` envelope so the agent
knows whether the launch actually succeeded:

```json
{
  "attempted": true,
  "launched": false,
  "reason": "no UI with run_quiz available — ...",
  "questions_shown": 0,
  "answers_collected": 0
}
```

When prompt-toolkit can't drive the TTY (CI, redirected stdin, headless
runtime), `launched=false` with a concrete `reason`. The agent must
read this field and either narrate the real outcome or fall back to
chat-flow Q&A — narrating "Quiz launched!" without verifying the
envelope is treated as a bug.

## Operating principles

- **Personal, not generic.** Every explanation reaches for this
  learner's specific background and modality. The auto-injected
  LEARNER PROFILE block is the guardrail — re-read it before each
  lecture chunk.
- **Teach by doing.** Concept briefs are ≤ 3 sentences. Every concept
  is followed by an assignment.
- **Small steps.** Lessons are 5–15 minutes of learner time, not 90.
- **Ask, don't tell.** Whenever you could explain, ask the learner to
  predict. Then reveal.
- **Continuously calibrate.** The diagnostic profile is a hypothesis,
  not gospel. When an analogy lands, a stumbling block emerges, or
  pace/jargon tolerance is off, call `update_learner_profile` with the
  observation and the delta.
- **Verifiable assignments only.** If you can't write a `verify_cmd`,
  expected_answer, or rubric_keywords that pass/fail objectively, fall
  back to `socratic-dialog` with concrete `required_concepts` so the
  engine still enforces coverage.
- **Honest grading.** A failed assignment is data, not a problem.
  Remediate, don't paper over.
- **Memory discipline.** `save_memory` durable facts that should
  outlive this course (preferred analogies, language background, deep
  stumbling blocks). Future courses for the same person pre-load them.

## Output artifacts

Everything lives under `<workspace>/courses/<course_id>/` — a top-level
visible folder in whatever workspace the user attached via `/workspace
folder ...`. No hashed paths, no hidden directories: the learner can
`cd` straight in and operate on assignment work files normally. If no
workspace is attached, the engine falls back to the current working
directory.

```
course.json              # full Course state
curriculum.md            # human-readable syllabus (regenerated)
transcript.md            # running log of interactions (optional)
report_card.md           # written by finalize_course
modules/<id>.md          # per-module overview
lessons/<id>.md          # per-lesson notes
assignments/
  <assignment_id>/
    prompt.md            # full instructions
    work/                # ENGINE-CREATED artifacts (broken code, specs, ...)
    submission/          # USER-PROVIDED artifacts
    grade.json           # Grade record
    dialog.md            # for socratic-dialog assignments: full transcript
```

The conversation is incidental; the directory is the course record.

## Slash commands

`/teach` works from any mode (handy for `/teach status` peeks). See
[commands.md](commands.md) for the full reference.

| Command | Behavior |
| --- | --- |
| `/teach new <subject>` | Create a new course in this workspace. Does NOT auto-switch mode — run `/mode teacher` next. |
| `/teach list` | List courses in this workspace. |
| `/teach load <id>` | Activate an existing course. |
| `/teach exit` / `unload` | Clear active course (course stays on disk). |
| `/teach status` | Current module/lesson, progress %, average score. |
| `/teach next` | Show next pending lesson. |
| `/teach grades` | Markdown table of every graded assignment. |
| `/teach curriculum` | Render the syllabus. |
| `/teach delete <id>` | Delete a course (irreversible). |

## Plan-mode interaction

Toggling `/plan on` while in teacher mode blocks every write-side
teacher tool (`create_course`, `assign_exercise`, `grade_assignment`,
`decide_next`, `complete_module`, `finalize_course`, etc.) with the
standard plan-mode envelope. `get_course_state` and `raise_teacher_blocker`
remain available so the agent can still surface status during planning.
