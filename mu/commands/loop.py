"""``/loop`` — the loop mission (``mu.loop``). You own the mission; the agent works it.

    /loop                          — status: goal, budget left, done-when, last increments, backlog
    /loop start <goal> [--done "<criterion>"]... [--check "<cmd>"] [--max-increments N]
                       [--max-cost USD] [--max-hours H] [--max-turns N]
                                   — start a mission (switches to loop mode)
    /loop pause | resume | stop    — pause (watchdog stops at the next boundary), resume, end it
    /loop goal <text>              — change the goal (recorded with you as the actor)
    /loop budget [--max-cost USD] [--max-hours H] [--max-increments N] [--max-turns N]
    /loop attest <criterion id> <why>  — mark a done-when criterion met without a run

``--check`` attaches a command to the preceding ``--done`` criterion (it must exit 0).
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, List, Tuple

from . import CommandResult, command
from ._mode_util import reply, save

_BUDGET_FLAGS = {"--max-increments": ("max_increments", 1.0), "--max-cost": ("max_cost_usd", 1.0),
                 "--max-hours": ("max_runtime_s", 3600.0), "--max-minutes": ("max_runtime_s", 60.0),
                 "--max-turns": ("max_turns", 1.0)}


def _parse(tokens: List[str]) -> Tuple[List[str], List[Dict[str, Any]], Dict[str, float]]:
    words: List[str] = []
    done: List[Dict[str, Any]] = []
    budget: Dict[str, float] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--done" and i + 1 < len(tokens):
            done.append({"text": tokens[i + 1]})
            i += 2
        elif tok == "--check" and i + 1 < len(tokens):
            if not done:
                raise ValueError("--check follows a --done criterion")
            done[-1]["check"] = {"command": tokens[i + 1]}
            i += 2
        elif tok in _BUDGET_FLAGS and i + 1 < len(tokens):
            key, scale = _BUDGET_FLAGS[tok]
            budget[key] = float(tokens[i + 1]) * scale
            i += 2
        else:
            words.append(tok)
            i += 1
    return words, done, budget


@command("/loop", help="Loop mission: /loop [start <goal> [--done ...]|pause|resume|stop|goal <text>|budget ...|attest <id> <why>].")
def loop_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    from mu.loop import engine
    from mu.modes.events import GateError

    try:
        tokens = shlex.split(args or "")
    except ValueError as exc:
        return reply(session, f"Could not parse arguments: {exc}", allow_prompt, ok=False)
    sub = tokens[0].lower() if tokens else "status"
    rest = tokens[1:]
    try:
        if sub == "status":
            return reply(session, engine.status_text(session), allow_prompt)
        if sub == "start":
            words, done, budget = _parse(rest)
            mission = engine.start_mission(session, " ".join(words), done_when=done, budget=budget,
                                           actor="user:cli")
            session.variables["agent_mode"] = "loop"
            save(session)
            return reply(session, f"Mission {mission.id} started: {mission.goal}\n"
                         + engine.status_text(session), allow_prompt)
        if sub in ("pause", "resume", "stop"):
            status = {"pause": "paused", "resume": "running", "stop": "stopped"}[sub]
            mission = engine.set_status(session, status, reason=" ".join(rest), actor="user:cli")
            save(session)
            return reply(session, f"Mission {mission.id} {mission.status}.", allow_prompt)
        if sub == "goal" and rest:
            mission = engine.set_goal(session, " ".join(rest), actor="user:cli")
            save(session)
            return reply(session, f"Goal: {mission.goal}", allow_prompt)
        if sub == "budget":
            _words, _done, budget = _parse(rest)
            if not budget:
                return reply(session, "Usage: /loop budget --max-cost 5 --max-hours 2 ...", allow_prompt, ok=False)
            engine.set_budget(session, budget, actor="user:cli")
            save(session)
            return reply(session, engine.status_text(session), allow_prompt)
        if sub == "attest" and len(rest) >= 2:
            crit = engine.attest(session, rest[0], note=" ".join(rest[1:]), actor="user:cli")
            save(session)
            return reply(session, f"{crit.id} marked met (attested).", allow_prompt)
    except (GateError, ValueError) as exc:
        return reply(session, str(exc), allow_prompt, ok=False)
    return reply(session, __doc__.strip().split("\n\n", 1)[1], allow_prompt, ok=False)
