"""Conversation watcher for teacher mode.

The watcher classifies chat messages (assistant + learner) into engine
events without the agent having to call `record_lecture_turn`-style
tools explicitly. The model just teaches in chat; the watcher derives
the structured transcript from what was actually said.

This is the "engine-as-watcher, not engine-as-narrator" design: the
state machine is real and gated, but the agent doesn't fabricate
transcript entries — they're classified out of the chat the user
actually saw.

Design constraints:
- **Best-effort.** A classifier failure (malformed JSON, provider
  timeout, network) must NEVER break the main turn loop. We log and
  skip.
- **Hard cap.** No more than one `agent_explanation` recorded per
  assistant message. Extra explanations in the same message are
  dropped with a feedback note appended to the events list — the
  agent is supposed to end the message after one explanation+check
  pair and wait for the learner.
- **Substantive only.** Explanations shorter than
  `MIN_EXPLANATION_CHARS_BEFORE_CHECK` are dropped (the engine would
  refuse them anyway).
- **Live.** Fires after every assistant message AND every user
  message in teacher mode when a lesson is in `presenting` or
  `lecturing` status.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from mu.teacher.engine import (
    LESSON_LECTURING,
    LESSON_PRESENTING,
    MIN_EXPLANATION_CHARS_BEFORE_CHECK,
    Course,
    Lesson,
    LectureTurn,
    add_event,
    advance_lesson_status,
    conclude_lecture,
    find_lesson,
    record_lecture_turn,
    start_lecture,
)
from providers.base import Message, MessagePart

logger = logging.getLogger(__name__)


@dataclass
class WatcherResult:
    """Outcome of a single classification pass.

    `events_applied` mirrors what was recorded into the engine.
    `dropped` lists the events the watcher saw but refused to record
    (e.g. the second explanation in one message under the hard cap).
    `agent_feedback` is a short string for the watcher to surface back
    into the agent's next turn (typically: "you bulldozed; end the next
    message after one explanation+check and wait").
    """

    events_applied: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    agent_feedback: str = ""
    lesson_status_after: Optional[str] = None
    auto_started_lecture: bool = False
    auto_concluded_lecture: bool = False


# ---------- classification prompts --------------------------------------------

_ASSISTANT_CLASSIFIER_SYSTEM = """\
You are a transcript classifier for a one-on-one tutoring session.

Read the assistant's chat message and extract the *first* substantive
teaching beat. Output strict JSON, nothing else. Schema:

{
  "narration_only": <bool>,
  "wrap_up": <bool>,
  "explanation": <string or null>,
  "check": <string or null>
}

Rules:
- `narration_only=true` when the message is meta-talk (e.g. "let me
  record that", "now I'll do X", "covered already"). No teaching
  content.
- `wrap_up=true` when the message signals the lecture is closing
  (e.g. "let's lock this in with an exercise", "that wraps lesson 1",
  "ready to test what we covered"). Even narration messages can be
  `wrap_up=true` if they announce a transition.
- `explanation`: the actual teaching content the assistant wrote. The
  full chunk verbatim (or a faithful condensed version if very long).
  Null if there is no teaching content.
- `check`: the single comprehension question the assistant asked at
  the end, if any. Null otherwise.
- Output ONE explanation and ONE check at most. If the assistant
  bulldozed multiple explanations into one message, return only the
  first one — the engine will surface guidance back to the agent.

Output ONLY the JSON. No prose, no code fences.
"""

_LEARNER_CLASSIFIER_SYSTEM = """\
You are classifying a single learner reply in a one-on-one tutoring session.

Given the agent's most recent question (if any) and the learner's
reply, output strict JSON:

{
  "kind": "response" | "question" | "acknowledgement" | "other",
  "comprehension_signal": "on track" | "partial" | "confused" | null,
  "content": <string>
}

Rules:
- `response`: an actual answer to the agent's check.
  `comprehension_signal` must be set.
  - "on track" — answer is correct or nearly so.
  - "partial" — partial correct understanding, missing pieces.
  - "confused" — incorrect, contradicts the explanation, or asks
    for re-teaching.
- `question`: the learner interrupted with a question instead of
  answering. `comprehension_signal` may be null.
- `acknowledgement`: a low-content "ok / got it / continue" with no
  meaningful answer. `comprehension_signal` should be null.
- `other`: off-topic or anything else.
- `content`: the learner's message text (verbatim or faithful
  paraphrase, max ~300 chars).

Output ONLY the JSON.
"""


# ---------- top-level entry points --------------------------------------------


def classify_assistant_message(
    provider,
    assistant_text: str,
    *,
    lesson: Optional[Lesson] = None,
) -> Optional[dict[str, Any]]:
    """Call the classifier on an assistant message. Returns the parsed
    JSON dict or None on failure (caller treats failure as no-op)."""
    text = (assistant_text or "").strip()
    if not text:
        return None
    context = _lesson_context_block(lesson)
    user_blob = (
        f"{context}\n\nAssistant message to classify:\n---\n{text}\n---"
        if context
        else f"Assistant message to classify:\n---\n{text}\n---"
    )
    return _call_classifier(
        provider, _ASSISTANT_CLASSIFIER_SYSTEM, user_blob, kind="assistant"
    )


def classify_user_message(
    provider,
    user_text: str,
    *,
    lesson: Optional[Lesson] = None,
) -> Optional[dict[str, Any]]:
    text = (user_text or "").strip()
    if not text:
        return None
    last_check = _last_agent_check(lesson) if lesson else ""
    context_lines = []
    if lesson is not None:
        context_lines.append(f"Lesson: {lesson.title!r} ({lesson.lesson_id})")
    if last_check:
        context_lines.append(f"Agent's last check: {last_check!r}")
    ctx = "\n".join(context_lines)
    user_blob = (
        f"{ctx}\n\nLearner reply:\n---\n{text}\n---"
        if ctx
        else f"Learner reply:\n---\n{text}\n---"
    )
    return _call_classifier(
        provider, _LEARNER_CLASSIFIER_SYSTEM, user_blob, kind="learner"
    )


def apply_assistant_classification(
    course: Course,
    lesson_id: str,
    classification: dict[str, Any],
) -> WatcherResult:
    """Translate a classifier result into engine events.

    Returns the WatcherResult describing what was applied, what was
    dropped, and any agent_feedback string to surface back into the
    next turn.
    """
    result = WatcherResult()
    lesson = find_lesson(course, lesson_id)
    if lesson is None:
        return result

    narration_only = bool(classification.get("narration_only"))
    wrap_up = bool(classification.get("wrap_up"))
    explanation = (classification.get("explanation") or "").strip() or None
    check = (classification.get("check") or "").strip() or None

    # Auto-start lecture on first explanation, lifting presenting → lecturing.
    if explanation and lesson.status == LESSON_PRESENTING:
        try:
            start_lecture(course, lesson_id, plan="")
            result.auto_started_lecture = True
            lesson = find_lesson(course, lesson_id) or lesson
        except Exception as exc:
            logger.warning("watcher: start_lecture failed: %s", exc)

    # Hard cap: at most one explanation+check pair per assistant message.
    # The classifier already returns at most one of each — that's the
    # contract — but defend against drift.
    if explanation:
        if len(explanation) < MIN_EXPLANATION_CHARS_BEFORE_CHECK:
            result.dropped.append(
                {"kind": "explanation", "reason": "too_short", "content": explanation}
            )
        else:
            try:
                turn = record_lecture_turn(
                    course,
                    lesson_id,
                    role="agent_explanation",
                    content=explanation,
                )
                result.events_applied.append(_serialize_lecture_turn(turn))
            except Exception as exc:
                result.dropped.append(
                    {"kind": "explanation", "reason": str(exc), "content": explanation}
                )

    if check:
        try:
            turn = record_lecture_turn(
                course,
                lesson_id,
                role="agent_check",
                content=check,
            )
            result.events_applied.append(_serialize_lecture_turn(turn))
        except Exception as exc:
            result.dropped.append(
                {"kind": "check", "reason": str(exc), "content": check}
            )

    # Pure narration with no teaching content — note it but don't record.
    if narration_only and not explanation and not check and not wrap_up:
        result.agent_feedback = (
            "Watcher: that message was meta-narration with no teaching "
            "content. Write the actual explanation in chat — the engine "
            "is watching."
        )

    # Inline multiple-choice detection. The agent must use ask_user_choice
    # for discrete-option checks — inline "A) … B) …" bypasses the TUI.
    # Surface as agent_feedback; the check turn is still recorded so the
    # comprehension count isn't lost, but the agent is told to redo it.
    if (explanation or check) and _looks_like_inline_multiple_choice(
        " ".join(filter(None, [explanation, check]))
    ):
        result.agent_feedback = (
            "Watcher: that comprehension check was written inline as "
            "'A) … B) … C) …' text. Multiple-choice questions MUST go "
            "through `ask_user_choice(question, options, …)` so the "
            "interactive picker fires — inline letters force the "
            "learner to type the answer manually and bypass the TUI. "
            "Re-issue this check via ask_user_choice before continuing."
        )

    # Wrap-up: auto-conclude when comprehension signal is sufficient.
    if wrap_up and lesson.status == LESSON_LECTURING:
        score = _derive_comprehension_pct(lesson)
        if score is None:
            result.agent_feedback = (
                "Watcher: you signaled wrap-up but no learner responses "
                "have been recorded yet. End the next message after one "
                "explanation+check and wait for the learner before "
                "assigning."
            )
        elif score < lesson.lecture_comprehension_threshold:
            result.agent_feedback = (
                f"Watcher: comprehension is {score}% — below the "
                f"{lesson.lecture_comprehension_threshold}% threshold. "
                "Re-explain the gaps before assigning."
            )
        else:
            try:
                conclude_lecture(
                    course,
                    lesson_id,
                    comprehension_pct=score,
                    summary="auto-concluded by watcher",
                )
                result.auto_concluded_lecture = True
            except Exception as exc:
                logger.warning("watcher: conclude_lecture failed: %s", exc)

    lesson = find_lesson(course, lesson_id) or lesson
    result.lesson_status_after = lesson.status
    return result


def apply_learner_classification(
    course: Course,
    lesson_id: str,
    classification: dict[str, Any],
) -> WatcherResult:
    """Record a learner reply derived from the user's chat message."""
    result = WatcherResult()
    lesson = find_lesson(course, lesson_id)
    if lesson is None or lesson.status != LESSON_LECTURING:
        return result

    kind = str(classification.get("kind") or "").strip()
    content = (classification.get("content") or "").strip()
    signal_raw = classification.get("comprehension_signal")
    signal = str(signal_raw).strip() if signal_raw else None

    role_for_kind = {
        "response": "learner_response",
        "question": "learner_question",
    }
    role = role_for_kind.get(kind)
    if not role or not content:
        # acknowledgement / other → don't pollute transcript.
        return result

    try:
        turn = record_lecture_turn(
            course,
            lesson_id,
            role=role,
            content=content,
            comprehension_signal=signal if role == "learner_response" else None,
        )
        result.events_applied.append(_serialize_lecture_turn(turn))
    except Exception as exc:
        result.dropped.append(
            {"kind": role, "reason": str(exc), "content": content}
        )

    lesson = find_lesson(course, lesson_id) or lesson
    result.lesson_status_after = lesson.status
    return result


# ---------- helpers -----------------------------------------------------------


def _call_classifier(
    provider,
    system_prompt: str,
    user_blob: str,
    *,
    kind: str,
) -> Optional[dict[str, Any]]:
    if provider is None:
        return None
    try:
        messages = [
            Message(
                role="user",
                parts=[MessagePart(type="text", text=user_blob)],
            ),
        ]
        response = provider.generate(messages=messages, system_prompt=system_prompt)
        raw = (response.text or "").strip()
    except Exception as exc:
        logger.warning("watcher: classifier call failed (%s): %s", kind, exc)
        return None
    return _parse_classifier_json(raw)


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Detects inline multiple-choice option lists in chat (e.g. "A) foo  B) bar"
# or "1. foo  2. bar"). Requires ≥2 distinct enumerators with content after
# them to avoid false positives on prose like "Answer is A) the position…".
_INLINE_MC_LETTER_RE = re.compile(r"(?m)^\s*[A-D]\)\s+\S")
_INLINE_MC_NUMBER_RE = re.compile(r"(?m)^\s*[1-9]\.\s+\S")


def _looks_like_inline_multiple_choice(text: str) -> bool:
    """True iff the assistant message contains an inline MC option list.

    The agent is supposed to deliver discrete-answer questions through
    `ask_user_choice` so the interactive picker fires. Writing options
    inline as `A) … B) …` text bypasses the TUI entirely and forces
    the learner to type the letter — bad UX, and exactly what the
    teacher prompt forbids.
    """
    if not text:
        return False
    letter_hits = len(_INLINE_MC_LETTER_RE.findall(text))
    number_hits = len(_INLINE_MC_NUMBER_RE.findall(text))
    return letter_hits >= 2 or number_hits >= 3


def _parse_classifier_json(raw: str) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    # Strip code fences if the model added them despite instructions.
    cleaned = _JSON_FENCE_RE.sub("", raw).strip()
    # Find the first {...} block — some models prefix prose.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        return None
    blob = cleaned[start : end + 1]
    try:
        data = json.loads(blob)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _lesson_context_block(lesson: Optional[Lesson]) -> str:
    if lesson is None:
        return ""
    lines = [f"Active lesson: {lesson.title!r} ({lesson.lesson_id})"]
    if lesson.learning_objectives:
        objs = "; ".join(str(o) for o in lesson.learning_objectives)
        lines.append(f"Lesson objectives: {objs}")
    return "\n".join(lines)


def _last_agent_check(lesson: Lesson) -> str:
    for turn in reversed(lesson.lecture_turns):
        if turn.role == "agent_check":
            return turn.content
    return ""


def _derive_comprehension_pct(lesson: Lesson) -> Optional[int]:
    """Average comprehension signal across recorded learner_response
    turns. None when there are no signals yet.

    Mapping: on_track=100, partial=60, confused=20.
    """
    weights = {"on track": 100, "on_track": 100, "partial": 60, "confused": 20}
    scores: list[int] = []
    for turn in lesson.lecture_turns:
        if turn.role != "learner_response":
            continue
        signal = (turn.comprehension_signal or "").strip().lower()
        if signal in weights:
            scores.append(weights[signal])
    if not scores:
        return None
    return int(round(sum(scores) / len(scores)))


def _serialize_lecture_turn(turn: LectureTurn) -> dict[str, Any]:
    return {
        "kind": turn.role,
        "content": turn.content,
        "comprehension_signal": turn.comprehension_signal,
        "turn_index": turn.turn_index,
    }


def is_watcher_eligible(course: Course, lesson_id: Optional[str]) -> bool:
    """True when the watcher should classify this turn's messages.

    Eligible iff there's a current lesson in `presenting` or
    `lecturing` status. Other lesson states (graded, completed,
    remediating, …) are skipped so we don't fabricate transcript
    entries for non-teaching chatter.
    """
    if not lesson_id:
        return False
    lesson = find_lesson(course, lesson_id)
    if lesson is None:
        return False
    return lesson.status in {LESSON_PRESENTING, LESSON_LECTURING}
