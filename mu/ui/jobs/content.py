"""Tab content builders for the job view (planos ``views/task/lines.ts``).

Pure functions from core VM dicts to lines. Nothing here reads the store.
"""

from __future__ import annotations

import ast
import json
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .primitives import (Line, clean, cwidth, duration, fit, fit_line, money, pad, progress_text,
                         stacked_bar, status_icon, wrap_text)
from .theme import SEVERITY, STATUS

# ---------------------------------------------------------------- markdown (light)

_INLINE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*)")


def inline(text: str, base: str = "") -> Line:
    out: Line = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(((base + " accent").strip(), part[1:-1]))
        elif part.startswith("**") and part.endswith("**") and len(part) > 3:
            out.append(((base + " bold").strip(), part[2:-2]))
        else:
            out.append((base, part))
    return out


def markdown(text: str, width: int) -> List[Line]:
    """Headings, bullets, numbered lists, fenced code and inline code/bold."""
    out: List[Line] = []
    in_code = False
    body = clean(text or "")
    if body.startswith("---\n"):  # strip YAML front matter
        end = body.find("\n---", 4)
        if end >= 0:
            body = body[end + 4:].lstrip("\n")
    for raw in body.split("\n"):
        if raw.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append([("muted", "│ "), ("", fit(raw, width - 2))])
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", raw)
        if m:
            if out and out[-1]:
                out.append([])
            out.append([("accent bold", m.group(2))])
            continue
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", raw)
        if m:
            indent, bullet, rest = m.groups()
            mark = "•" if bullet in "-*+" else bullet
            prefix = indent + mark + " "
            lines = wrap_text(rest, max(8, width - cwidth(prefix)))
            for i, l in enumerate(lines):
                out.append([("accent" if i == 0 else "", prefix if i == 0 else " " * cwidth(prefix))] + inline(l))
            continue
        if not raw.strip():
            out.append([])
            continue
        for l in wrap_text(raw, width):
            out.append(inline(l))
    while out and not out[-1]:
        out.pop()
    return out


# ---------------------------------------------------------------- session

def _tool_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        for parse in (json.loads, ast.literal_eval):
            try:
                value = parse(raw)
                if isinstance(value, dict):
                    return value
            except (ValueError, SyntaxError, TypeError):
                continue
        return {"args": raw}
    return {}


def _args_summary(args: Dict[str, Any]) -> str:
    for key in ("command", "path", "file_path", "pattern", "string", "query", "url"):
        if args.get(key):
            return clean(str(args[key])).replace("\n", " ⏎ ")
    return ", ".join(f"{k}={clean(str(v))[:40]}" for k, v in list(args.items())[:3])


def _result_text(raw: Any) -> Tuple[str, bool]:
    value: Any = raw
    if isinstance(raw, str):
        try:
            value = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            try:
                value = json.loads(raw)
            except ValueError:
                value = raw
    if isinstance(value, dict):
        ok = value.get("ok", True) is not False
        text = value.get("summary") or value.get("error") or value.get("output") or value.get("result") or ""
        return clean(str(text)), ok
    return clean(str(value or "")), True


ENV_TOOL = "run_in_environment"


def _tool_item(name: str, raw_args: Any) -> Dict[str, Any]:
    args = _tool_args(raw_args)
    item = {"kind": "tool", "name": name, "args": _args_summary(args), "results": []}
    if name == ENV_TOOL:
        # Folds to one line: `› [node-build] npm test`.
        item["env"] = clean(str(args.get("env") or "?"))
        item["args"] = clean(str(args.get("command") or "")).replace("\n", " ⏎ ")
    return item


def session_items(session: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transcript + live sandbox events → chat items (tool results fold into calls)."""
    items: List[Dict[str, Any]] = []
    if not session:
        return items
    for stage in ("plan", "implement", "review"):
        msgs = (session.get("stages") or {}).get(stage) or []
        if not msgs:
            continue
        items.append({"kind": "stage", "text": stage})
        unresolved: List[Dict[str, Any]] = []
        for msg in msgs:
            role = msg.get("role")
            for part in msg.get("parts") or []:
                ptype = part.get("type")
                if ptype == "text" and part.get("text"):
                    kind = "assistant" if role in ("assistant", "model") else "user"
                    items.append({"kind": kind, "text": str(part["text"]), "synthetic": bool(msg.get("synthetic"))})
                elif ptype == "tool_call":
                    name = str(part.get("tool_name") or "tool")
                    item = _tool_item(name, part.get("tool_args"))
                    items.append(item)
                    unresolved.append(item)
                elif ptype == "tool_result":
                    name = str(part.get("tool_name") or "")
                    text, ok = _result_text(part.get("tool_result"))
                    target = next((it for it in unresolved if it.get("name") == name), None)
                    if target is None and unresolved:
                        target = unresolved[0]
                    if target is not None:
                        unresolved.remove(target)
                        target["results"].append({"text": text, "ok": ok})
    live = [e for e in session.get("events") or []]
    if live:
        items.append({"kind": "stage", "text": "live"})
    for ev in live:
        payload = ev.get("payload") or {}
        etype = ev.get("event_type")
        if etype == "sandbox_event":
            inner = payload.get("payload") or {}
            kind = payload.get("event")
            if kind == "agent_message":
                items.append({"kind": "assistant", "text": str(inner.get("text") or "")})
            elif kind == "tool_call_ui":
                name = str(inner.get("tool_name") or "tool")
                if name == ENV_TOOL and not _tool_args(inner.get("tool_args")).get("command"):
                    continue  # the broker's environment_command_started event carries env + command
                items.append(_tool_item(name, inner.get("tool_args")))
            elif kind == "tool_result_ui":
                tool = next((it for it in reversed(items) if it.get("kind") == "tool"), None)
                text = clean(str(inner.get("preview") or inner.get("text") or ""))
                if tool is not None:
                    tool["results"].append({"text": text, "ok": inner.get("ok", True) is not False})
            elif kind == "runtime_status":
                items.append({"kind": "status", "text": str(inner.get("text") or "")})
            elif kind:
                items.append({"kind": "chip", "text": kind.replace("_", " ")})
        elif etype == "stage_message":
            items.append({"kind": "you", "text": str(payload.get("text") or "")})
        elif etype in ("sandbox_starting", "sandbox_stopped"):
            items.append({"kind": "chip", "text": etype.replace("sandbox_", "container ")})
        elif etype == "runtime_error":
            items.append({"kind": "error", "text": str(payload.get("error") or ev.get("reason") or "runtime error")})
        elif etype == "environment_command_started":
            items.append(_tool_item(ENV_TOOL, {"env": payload.get("env"), "command": payload.get("command")}))
        elif etype == "environment_command":
            env, command = clean(str(payload.get("env") or "?")), clean(str(payload.get("command") or "")).replace("\n", " ⏎ ")
            target = next((it for it in reversed(items) if it.get("name") == ENV_TOOL and it.get("env") == env
                           and it.get("args") == command and not it.get("done")), None)
            if target is None:
                target = _tool_item(ENV_TOOL, {"env": env, "command": payload.get("command")})
                items.append(target)
            target["done"] = True
            code = payload.get("return_code")
            status = "timed out" if payload.get("timed_out") else f"exit code {code}"
            secs = float(payload.get("duration_ms") or 0) / 1000.0
            target["results"].append({"text": f"{status} ({secs:.1f}s)", "ok": code == 0 and not payload.get("timed_out")})
        elif etype in ("build_environments_preparing", "build_environment_progress", "build_environments_recreated"):
            text = str(ev.get("reason") or "Preparing build environments…")
            if items and items[-1].get("kind") == "envprep":
                items[-1]["text"] = text  # progress updates replace each other
            else:
                items.append({"kind": "envprep", "text": text})
        elif etype == "build_environment_ready":
            items.append({"kind": "chip", "text": f"build environment {payload.get('binding') or ''} ready"})
        elif etype == "build_environment_failed":
            items.append({"kind": "error", "text": str(ev.get("reason") or "build environment failed")})
        elif etype == "environment_setup" and payload.get("status") in ("ok", "failed"):
            # Template job setup (job_templates.md §4): one line per finished command.
            label = f"setup [{payload.get('binding') or ''}] {payload.get('command') or ''}"
            if payload.get("status") == "ok":
                items.append({"kind": "chip", "text": f"{label} ✓"})
            else:
                items.append({"kind": "error", "text": f"{label} failed (exit {payload.get('exit_code')})"})
        elif etype in ("environment_setup_dirty", "environment_setup_skipped"):
            items.append({"kind": "chip", "text": str(ev.get("reason") or etype)})
        elif etype in ("auto_permission", "auto_answered", "repository_environment_skipped",
                       "build_environment_skipped", "checks_unverified"):
            # Jobs run unattended: automatic decisions and non-blocking fallbacks.
            items.append({"kind": "chip", "text": str(ev.get("reason") or etype)})
        elif etype == "environment_setup_failed":
            items.append({"kind": "error", "text": str(ev.get("reason") or "job setup failed")})
    return items


def session_item_lines(item: Dict[str, Any], width: int, *, expanded: bool, selected: bool) -> List[Line]:
    w = max(20, width)
    mark: Line = [("accent", "›")] if selected else [("", " ")]
    kind = item.get("kind")
    if kind == "stage":
        label = f"── {item['text']} "
        return [[("muted", label + "─" * max(0, w - cwidth(label)))]]
    if kind == "tool":
        results = item.get("results") or []
        failed = any(not r.get("ok", True) for r in results)
        if item.get("name") == ENV_TOOL:
            head = mark + [("danger" if failed else "running", "› "), ("accent", f"[{item.get('env', '?')}] "),
                           ("", item.get("args", ""))]
        else:
            head = mark + [("danger", "✗ ") if failed else ("running", "● "), ("bold", item.get("name", "tool")),
                           ("muted", f"({item.get('args', '')})")]
        if results and not expanded:
            first = (results[0].get("text") or "").strip().split("\n")[0]
            tail = f"  ⎿ {'✗ ' if failed else ''}{first}"
            head = fit_line(head, max(10, w - min(40, cwidth(tail))), pad_to=False) + [("danger" if failed else "muted", tail)]
        out = [fit_line(head, w, pad_to=False)]
        if expanded:
            for r in results:
                for l in wrap_text(r.get("text") or "", w - 6)[:200]:
                    out.append([("", "   "), ("danger" if not r.get("ok", True) else "muted", "⎿ " + l)])
        return out
    if kind in ("assistant",):
        md = markdown(item.get("text", ""), w - 2)
        return [(mark if i == 0 else [("", " ")]) + [("", " ")] + l for i, l in enumerate(md)] + [[]]
    if kind == "user":
        text = item.get("text", "")
        lines = wrap_text(text, w - 3)
        if not expanded and len(lines) > 2:
            lines = lines[:1] + [f"… {len(lines) - 1} more lines (enter expands)"]
        return [(mark if i == 0 else [("", " ")]) + [("ok", "› " if i == 0 else "  "),
                                                     ("muted" if item.get("synthetic") or i else "", l)]
                for i, l in enumerate(lines)] + [[]]
    if kind == "you":
        return [mark + [("ok bold", "you › "), ("", l)] for l in wrap_text(item.get("text", ""), w - 8)] + [[]]
    if kind == "chip":
        return [mark + [("", " "), ("running", f"⬢ {item.get('text', '')}")]]
    if kind == "envprep":
        return [mark + [("", " "), ("running", f"⬢ {item.get('text', '')}")]]
    if kind == "error":
        return [mark + [("danger", "✗ " + l)] for l in wrap_text(item.get("text", ""), w - 4)]
    return [mark + [("muted", "· " + l)] for l in wrap_text(item.get("text", ""), w - 4)]


# ---------------------------------------------------------------- diff

def parse_patch(patch: Optional[str]) -> List[Dict[str, Any]]:
    """Unified diff → files with hunks and numbered lines."""
    files: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    hunk: Optional[Dict[str, Any]] = None
    old_no = new_no = 0
    for raw in (patch or "").split("\n"):
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*?) b/(.*)$", raw)
            path = m.group(2) if m else raw[11:]
            cur = {"path": path, "old_path": m.group(1) if m else path, "added": 0, "removed": 0,
                   "hunks": [], "binary": False, "status": "modified"}
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if raw.startswith("new file"):
            cur["status"] = "added"
        elif raw.startswith("deleted file"):
            cur["status"] = "deleted"
        elif raw.startswith("rename to "):
            cur["status"] = "renamed"
        elif raw.startswith("Binary files"):
            cur["binary"] = True
        elif raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$", raw)
            old_no, new_no = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            hunk = {"header": raw, "context": (m.group(3).strip() if m else ""), "lines": []}
            cur["hunks"].append(hunk)
        elif hunk is not None and raw[:1] in ("+", "-", " ") and not raw.startswith(("+++ ", "--- ")):
            kind = raw[:1]
            text = raw[1:]
            if kind == "+":
                hunk["lines"].append(("+", None, new_no, text))
                new_no += 1
                cur["added"] += 1
            elif kind == "-":
                hunk["lines"].append(("-", old_no, None, text))
                old_no += 1
                cur["removed"] += 1
            else:
                hunk["lines"].append((" ", old_no, new_no, text))
                old_no += 1
                new_no += 1
        elif hunk is not None and raw.startswith("\\"):
            continue
    return files


def diff_file_rows(files: Sequence[Dict[str, Any]], viewed: Sequence[str], width: int, cursor: int,
                   focused: bool) -> List[Line]:
    peak = max([f["added"] + f["removed"] for f in files] + [1])
    bar_w = 6
    out: List[Line] = []
    for i, f in enumerate(files):
        sel = i == cursor
        seen = f["path"] in viewed
        total = f["added"] + f["removed"]
        cells = max(1, round(total / peak * bar_w)) if total else 0
        add_cells = round(cells * (f["added"] / total)) if total else 0
        bar: Line = [("ok", "+" * add_cells), ("danger", "-" * (cells - add_cells)), ("", " " * (bar_w - cells))]
        stat = f"+{f['added']} −{f['removed']}"
        line: Line = [("accent", "›" if sel else " "),
                      ("ok", "✓ ") if seen else ("muted", "○ "),
                      ("muted", pad(stat, 10))] + bar + [("", " ")]
        name = f["path"]
        room = max(6, width - 22)
        if cwidth(name) > room:
            name = "…" + name[-(room - 1):]
        line.append(("muted" if seen else ("bold" if sel else ""), name))
        line = fit_line(line, width)
        out.append([(s + " inverse", t) for s, t in line] if sel and focused else line)
    if not files:
        out.append([("muted", " no changes")])
    return out


def diff_hunk_lines(f: Optional[Dict[str, Any]], width: int, *, side_by_side: bool = False,
                    pins: Optional[Dict[int, str]] = None) -> Tuple[List[Line], Dict[int, int]]:
    """Lines for one file and a map new-line-number → row (for jumps)."""
    out: List[Line] = []
    rows: Dict[int, int] = {}
    pins = pins or {}
    if not f:
        return [[("muted", "select a file")]], rows
    out.append([("bold", f["path"]), ("muted", f"  {f['status']}  "), ("ok", f"+{f['added']} "),
                ("danger", f"−{f['removed']}")])
    if f.get("binary"):
        out.append([("muted", "binary file")])
        return out, rows
    num_w = 4
    for h in f["hunks"]:
        out.append([("accent", fit(h["header"], width))])
        if side_by_side:
            half = max(10, (width - 1) // 2)
            dels: List[Tuple] = []
            adds: List[Tuple] = []

            def flush():
                for k in range(max(len(dels), len(adds))):
                    d = dels[k] if k < len(dels) else None
                    a = adds[k] if k < len(adds) else None
                    left: Line = [("muted", pad(str(d[1]), num_w, "right") + " "), ("danger", "-" + d[3])] if d else []
                    right: Line = [("muted", pad(str(a[2]), num_w, "right") + " "), ("ok", "+" + a[3])] if a else []
                    if a and a[2] in pins:
                        rows[a[2]] = len(out)
                        right = [(pins[a[2]], "⚑")] + right
                    elif a:
                        rows[a[2]] = len(out)
                    out.append(fit_line(left, half) + [("muted", "│")] + fit_line(right, half, pad_to=False))
                dels.clear()
                adds.clear()

            for ln in h["lines"]:
                if ln[0] == "-":
                    dels.append(ln)
                elif ln[0] == "+":
                    adds.append(ln)
                else:
                    flush()
                    rows[ln[2]] = len(out)
                    left = [("muted", pad(str(ln[1]), num_w, "right") + " "), ("", " " + ln[3])]
                    right = [("muted", pad(str(ln[2]), num_w, "right") + " "), ("", " " + ln[3])]
                    if ln[2] in pins:
                        right = [(pins[ln[2]], "⚑")] + right
                    out.append(fit_line(left, half) + [("muted", "│")] + fit_line(right, half, pad_to=False))
            flush()
            continue
        for kind, o, n, text in h["lines"]:
            num = n if n is not None else o
            tone = "ok" if kind == "+" else ("danger" if kind == "-" else "")
            pin = pins.get(n) if n is not None else None
            if n is not None:
                rows[n] = len(out)
            out.append([(pin or "", "⚑" if pin else " "), ("muted", pad(str(num), num_w, "right") + " "),
                        (tone, kind + text)])
    return out, rows


# ---------------------------------------------------------------- review

def review_order(review: Optional[Dict[str, Any]]) -> List[int]:
    findings = (review or {}).get("findings") or []
    return sorted(range(len(findings)),
                  key=lambda i: (SEVERITY.get(str(findings[i].get("severity")), ("", 9))[1], i))


def review_lines(review: Optional[Dict[str, Any]], width: int, selected: int,
                 marked: Sequence[str] = ()) -> Tuple[List[Line], Dict[int, int]]:
    """Summary, verdict, plan coverage and finding cards grouped by severity."""
    if not review:
        return [[("muted", "no review yet")]], {}
    w = max(24, width)
    lines: List[Line] = []
    anchors: Dict[int, int] = {}
    verdict = str(review.get("verdict") or "")
    vtone = {"ready": "ok", "ready_with_nits": "ok", "needs_work": "warn", "do_not_merge": "danger"}.get(verdict, "muted")
    readiness = review.get("merge_readiness")
    head: Line = [("accent bold", "Verdict "), (vtone + " bold", verdict.replace("_", " ") or "—")]
    if isinstance(readiness, (int, float)):
        head += [("muted", "  merge readiness "), (vtone, progress_text(readiness / 100.0, 14)), ("", f" {int(readiness)}/100")]
    lines.append(head)
    lines.append([])
    lines.append([("accent bold", "Summary")])
    lines.extend(markdown(str(review.get("summary") or ""), w))
    coverage = review.get("plan_coverage") or []
    if coverage:
        lines.append([])
        lines.append([("accent bold", "Plan coverage")])
        cmap = {"met": "done", "partial": "waiting", "unmet": "failed", "unverifiable": "skipped"}
        for c in coverage:
            st = cmap.get(str(c.get("status")), "pending")
            lines.append([("", "  "), status_icon(st), ("", f" {c.get('criterion_id', '')} "),
                          (STATUS[st]["token"], str(c.get("status", ""))), ("muted", f"  {clean(c.get('evidence', ''))}")])
    findings = review.get("findings") or []
    counts = {}
    for f in findings:
        counts[f.get("severity")] = counts.get(f.get("severity"), 0) + 1
    lines.append([])
    lines.append([("bold", f"Findings ({len(findings)})"),
                  ("muted", "  " + " · ".join(f"{n} {k}" for k, n in counts.items()))])
    for sev in ("blocker", "major", "minor", "nit"):
        group = [i for i in range(len(findings)) if findings[i].get("severity") == sev]
        if not group:
            continue
        tone = SEVERITY[sev][0]
        lines.append([])
        lines.append([(tone + " bold", f"{sev.upper()} ({len(group)})")])
        for i in group:
            f = findings[i]
            sel = i == selected
            border = "accent" if sel else tone
            anchors[i] = len(lines)
            fid = str(f.get("id", ""))
            disp = str(f.get("disposition") or "open")
            box = [("accent" if fid in marked else "muted", "[x] " if fid in marked else "[ ] ")] if marked else []
            title = [(border, "╭ "), ("muted", f"{fid} · {f.get('category', '')} · ")] + box + \
                [("bold" if sel else "", clean(f.get("title", "")))]
            lines.append(fit_line(title, w, pad_to=False))
            loc = f"{f.get('file') or '—'}{':' + str(f.get('line_start')) if f.get('line_start') else ''}"
            conf = f.get("confidence")
            meta: Line = [(border, "│ "), ("accent", loc)]
            if isinstance(conf, (int, float)):
                meta.append(("muted", f" · {int(conf * 100)}%"))
            meta.append(("ok" if disp == "accepted" else ("muted" if disp in ("dismissed", "wontfix") else "warn"),
                         f" · {disp}"))
            if sel and f.get("file"):
                meta.append(("muted", "  enter: jump to diff"))
            lines.append(meta)
            for l in wrap_text(str(f.get("detail") or ""), w - 2):
                lines.append([(border, "│ ")] + inline(l))
            if f.get("suggestion"):
                for l in wrap_text(f"→ {f['suggestion']}", w - 2):
                    lines.append([(border, "│ "), ("muted", l)])
            lines.append([(border, "╰─")])
    return lines, anchors


# ---------------------------------------------------------------- checks

def run_counts(run: Dict[str, Any]) -> Dict[str, int]:
    p = f = s = 0
    for suite in run.get("suites") or []:
        j = suite.get("junit") or {}
        p += int(j.get("passed") or 0)
        f += int(j.get("failed") or 0) + int(j.get("errors") or 0)
        s += int(j.get("skipped") or 0)
    return {"passed": p, "failed": f, "skipped": s}


def run_status(run: Dict[str, Any]) -> str:
    if run.get("verified") is False:
        return "unverified"  # some checks could not run (job_templates.md §5)
    if run.get("passed") is True:
        return "done"
    if run.get("passed") is False:
        return "failed"
    return "active"


def suite_status(suite: Dict[str, Any]) -> str:
    """Vocabulary status of one suite result: ``done`` / ``failed`` / ``unverified`` (unrunnable)."""
    if str(suite.get("status") or "") == "unrunnable":
        return "unverified"
    return "done" if suite.get("passed") else "failed"


def _status_token(status: str) -> str:
    # The unverified status is the caution semantic (G vocabulary: glyph ``?``).
    return "caution" if status == "unverified" else STATUS[status]["token"]


def _status_seg(status: str) -> Tuple[str, str]:
    if status == "unverified":
        return ("caution", STATUS.get("unverified", {}).get("icon", "?"))
    return status_icon(status)


def failed_cases(checks: Dict[str, Any], run_index: int) -> List[Dict[str, Any]]:
    """Failed cases for a run: VM ``failed_cases`` for the latest run, else junit."""
    runs = checks.get("runs") or []
    if not runs:
        return []
    run = runs[run_index]
    if run_index == len(runs) - 1 and isinstance(checks.get("failed_cases"), list):
        return list(checks["failed_cases"])
    out = []
    pre = set()
    for suite in run.get("suites") or []:
        pre |= set(suite.get("preexisting_failures") or [])
        for case in (suite.get("junit") or {}).get("cases") or []:
            if case.get("status") in ("failed", "error"):
                out.append({"run": run.get("run"), "suite": case.get("suite") or suite.get("suite"),
                            "name": case.get("name") or case.get("id"), "id": case.get("id"),
                            "message": case.get("message") or "", "new": case.get("id") not in pre})
    return out


_PROGRESS = re.compile(r"^\S.*?\s([.FEsxX]+)\s*(\[\s*\d+%\])?\s*$")


def live_progress(live: Sequence[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """pytest-style progress (dots and Fs) from live ``checks_output`` events."""
    passed = failed = 0
    seen = False
    for ev in live:
        payload = ev.get("payload") or {}
        if payload.get("baseline"):
            continue
        for line in clean(payload.get("text") or "").split("\n"):
            m = _PROGRESS.match(line.strip())
            if m:
                seen = True
                passed += m.group(1).count(".")
                failed += m.group(1).count("F") + m.group(1).count("E")
    return {"passed": passed, "failed": failed} if seen else None


def live_tail(live: Sequence[Dict[str, Any]], n: int) -> List[str]:
    text = "".join(str((e.get("payload") or {}).get("text") or "") for e in live)
    lines = [l for l in clean(text).split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-n:]


def checks_lines(checks: Optional[Dict[str, Any]], state: str, width: int, run_cursor: int,
                 now: float, tick: Optional[int] = None) -> List[Line]:
    w = max(30, width)
    checks = checks or {}
    runs = checks.get("runs") or []
    live = checks.get("live") or []
    out: List[Line] = []
    out.append([("muted bold", pad("  RUN", 7) + pad("RESULT", 10) + pad("TRIGGER", 9) + pad("DURATION", 10)
                 + pad("HEAD", 10) + "TESTS")])
    if state == "checking":
        prog = live_progress(live)
        line: Line = [("accent", "  "), status_icon("active", tick), ("", pad(f" {len(runs) + 1}", 5)),
                      ("running", pad("running", 10)), ("muted", pad("", 19))]
        if prog:
            total = prog["passed"] + prog["failed"]
            line += [("", " " * 10), ("danger" if prog["failed"] else "ok", progress_text(prog["passed"] / total if total else 0, 12)),
                     ("ok", f" {prog['passed']} ok"), ("danger" if prog["failed"] else "muted", f" {prog['failed']} ✗")]
        out.append(fit_line(line, w, pad_to=False))
    for idx in range(len(runs) - 1, -1, -1):
        r = runs[idx]
        sel = idx == run_cursor
        st = run_status(r)
        n = run_counts(r)
        dur = duration((r.get("duration_ms") or 0) / 1000.0) if r.get("duration_ms") else "—"
        line = [("accent", "› " if sel else "  "), _status_seg(st), ("", pad(f" {r.get('run', idx + 1)}", 5)),
                (_status_token(st), pad({"done": "passed", "failed": "failed", "unverified": "unverified"}.get(st, "running"), 10)),
                ("muted", pad(str(r.get("trigger") or "—"), 9) + pad(dur, 10) + pad(str(r.get("head_sha") or "")[:8], 10)),
                ("ok", f"{n['passed']} ok"), ("danger" if n["failed"] else "muted", f" {n['failed']} ✗"),
                ("muted", f" {n['skipped']} skipped")]
        line = fit_line(line, w)
        out.append([(s + " inverse", t) for s, t in line] if sel else line)
    if not runs and state != "checking":
        out.append([("muted", "  no check runs yet")])
    history = checks.get("history") or {}
    if history:
        out.append([])
        out.append([("accent bold", "History")])
        for suite, entries in history.items():
            marks: Line = []
            for e in entries[-20:]:
                marks.append(("ok", "✓") if e.get("passed") else ("danger", "✗"))
            last = entries[-1].get("duration_ms") if entries else None
            out.append([("", "  " + pad(str(suite), 14))] + marks +
                       [("muted", f"  {duration((last or 0) / 1000.0) if last else ''}")])
    if runs and 0 <= run_cursor < len(runs):
        r = runs[run_cursor]
        n = run_counts(r)
        out.append([])
        out.append([("accent bold", f"Run {r.get('run', run_cursor + 1)}"),
                    ("muted", f"  {n['passed']} passed · {n['failed']} failed · {n['skipped']} skipped")])
        out.append([("", "  ")] + stacked_bar([(n["passed"], "ok"), (n["failed"], "danger"), (n["skipped"], "muted")],
                                              min(60, w - 4)))
        if r.get("verified") is False:
            count = int(r.get("unrunnable") or 0) or sum(1 for x in r.get("suites") or [] if suite_status(x) == "unverified")
            out.append(fit_line([_status_seg("unverified"),
                                 ("caution bold", f" unverified · {count} check{'s' if count != 1 else ''} could not run"),
                                 ("muted", " · fix the environment, then re-run checks")], w, pad_to=False))
        for suite in r.get("suites") or []:
            sst = suite_status(suite)
            sd = duration((suite.get("duration_ms") or 0) / 1000.0) if suite.get("duration_ms") else ""
            env: Line = [("accent", f"[{suite['environment']}] ")] if suite.get("environment") else []
            label = [("caution", "unrunnable ")] if sst == "unverified" else []
            out.append([("", "  "), _status_seg(sst), ("", f" {suite.get('suite', '')} ")] + label + env +
                       [("muted", f"{clean(suite.get('command', ''))}  {sd}")])
            info = suite.get("unrunnable") or {}
            if sst == "unverified" and info:
                cause = clean(str(info.get("cause") or ""))
                tool = clean(str(info.get("tool") or ""))
                for l in wrap_text((f"`{tool}`: " if tool else "") + cause, w - 8)[:3]:
                    out.append([("caution", "      " + l)])
                if info.get("hint"):
                    for l in wrap_text("→ " + clean(str(info["hint"])), w - 8)[:3]:
                        out.append([("muted", "      " + l)])
        cases = failed_cases(checks, run_cursor)
        if cases:
            out.append([])
            out.append([("danger bold", f"Failed cases ({len(cases)})")])
            for c in cases[:30]:
                out.append(fit_line([("danger", "  ✗ "), ("", f"{c.get('suite') or ''} › {c.get('name') or c.get('id')}"),
                                     ("warn", "  new") if c.get("new") else ("muted", "  pre-existing")], w, pad_to=False))
                for l in wrap_text(str(c.get("message") or ""), w - 6)[:4]:
                    out.append([("muted", "      " + l)])
    tail = live_tail(live, 40) if state == "checking" else []
    if not tail and runs and 0 <= run_cursor < len(runs):
        tail = []
        for suite in runs[run_cursor].get("suites") or []:
            for stream in ("stdout", "stderr"):
                text = clean(suite.get(stream) or "")
                tail.extend(text.split("\n")[-20:] if text.strip() else [])
    if tail:
        out.append([])
        out.append([("accent bold", "Log tail")])
        for l in tail[-40:]:
            tone = "danger" if re.search(r"error|fail|✗|FAIL|ERROR", l) else "muted"
            out.append([(tone, "  " + l)])
    return out


# ---------------------------------------------------------------- errors / log

def error_entries(vm: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ev in ((vm.get("costs") or {}).get("events") or []):
        et = str(ev.get("event_type") or "")
        payload = ev.get("payload") or {}
        if et == "runtime_error" or et.endswith("_error") or et.endswith("_failed") or \
                (et == "status_changed" and ev.get("to_status") == "failed"):
            msg = payload.get("error") or payload.get("attention_detail") or payload.get("detail") or ev.get("reason") or et
            out.append({"at": ev.get("created_at"), "source": et, "message": clean(str(msg))})
    plan = vm.get("plan") or {}
    for e in plan.get("errors") or []:
        out.append({"at": None, "source": "plan", "message": clean(str(e))})
    return out


def error_lines(vm: Dict[str, Any], width: int) -> List[Line]:
    out: List[Line] = []
    if vm.get("state") == "failed":
        out.append([("danger bold", f"✗ Failed in {vm.get('failed_from') or '?'}: "),
                    ("", "y retries that stage")])
        for l in wrap_text(str(vm.get("attention_detail") or ""), width - 2):
            out.append([("", "  " + l)])
        out.append([])
    entries = error_entries(vm)
    if not entries and vm.get("state") != "failed":
        out.append([("muted", "no errors recorded for this job")])
    for e in entries:
        at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["at"])) if e.get("at") else ""
        out.append([("muted", f"{at}  " if at else ""), ("danger bold", e["source"])])
        for l in wrap_text(e["message"], width - 2)[:20]:
            out.append([("", "  " + l)])
        out.append([])
    return out


def log_lines(vm: Dict[str, Any], width: int, *, now: float) -> List[Line]:
    w = max(30, width)
    costs = vm.get("costs") or {}
    out: List[Line] = [[("accent bold", f"Cost {money(costs.get('total_usd', vm.get('cost_usd', 0)))}")]]
    stages = sorted((costs.get("by_stage") or {}).items(), key=lambda kv: -float(kv[1] or 0))
    peak = max([float(v or 0) for _, v in stages] + [1e-9])
    label_w = max([8] + [cwidth(s) for s, _ in stages])
    bar_w = max(8, min(40, w - label_w - 14))
    for s, v in stages:
        out.append([("", "  " + pad(s, label_w) + " "), ("accent", progress_text(float(v or 0) / peak, bar_w + 2)),
                    ("", " " + pad(money(v), 7, "right"))])
    if not stages:
        out.append([("muted", "  no cost recorded yet")])
    out.append([])
    out.append([("accent bold", "Pipeline timings")])
    for s in vm.get("pipeline") or vm.get("steps") or []:
        st = str(s.get("status") or "pending")
        out.append([("", "  "), status_icon(st), ("", " " + pad(str(s.get("label")), 10)),
                    ("muted", pad(duration(s.get("elapsed")) if s.get("elapsed") else "—", 7, "right")
                     + (f"  {s.get('runs')} run{'s' if s.get('runs') != 1 else ''}" if s.get("runs") else "")
                     + (f"  {money(s.get('cost_usd'))}" if s.get("cost_usd") else "")
                     + (f"  {s.get('detail')}" if s.get("detail") else ""))])
    events = costs.get("events") or []
    out.append([])
    out.append([("accent bold", f"Audit timeline ({len(events)})")])
    day = ""
    for ev in events:
        at = float(ev.get("created_at") or 0)
        d = time.strftime("%Y-%m-%d", time.localtime(at)) if at else ""
        if d != day:
            day = d
            out.append([("muted bold", f"  {d}")])
        et = str(ev.get("event_type") or "")
        if et == "status_changed":
            what = f"{ev.get('from_status') or '·'} → {ev.get('to_status')}"
            tone = "danger" if ev.get("to_status") == "failed" else "accent"
        elif et == "command":
            what = f"command {(ev.get('payload') or {}).get('command', ev.get('reason', ''))}"
            tone = "ok"
        else:
            what = et
            tone = "danger" if re.search(r"error|denied|fail", et) else "muted"
        reason = clean(ev.get("reason") or "")
        body = wrap_text(f"{what}{'  ' + reason if reason else ''}", w - 16)
        for i, t in enumerate(body):
            stamp = time.strftime("%H:%M:%S", time.localtime(at)) if at else ""
            out.append([("muted", "            " if i else f"  {stamp}  "), (tone, "│ " if i else "● "), ("", t)])
    return out


def plan_lines(plan: Optional[Dict[str, Any]], width: int) -> List[Line]:
    if not plan or not (plan.get("markdown") or plan.get("draft")):
        if plan and plan.get("error"):
            return [[("danger", "plan error: " + clean(plan["error"]))]]
        return [[("muted", "no plan yet")]]
    head: Line = [("accent bold", f"Plan v{plan.get('version', '?')}")]
    latest, approved = plan.get("latest_version"), plan.get("approved_version")
    if latest and latest != plan.get("version"):
        head.append(("muted", f"  latest v{latest}"))
    head.append(("ok", f"  ✓ approved v{approved}") if approved else ("warn", "  ◆ not approved"))
    if plan.get("draft"):
        head.append(("warn", "  · unsaved draft"))
    out = [head, []]
    out.extend(markdown(plan.get("draft") or plan.get("markdown") or "", width))
    for e in plan.get("errors") or []:
        out.append([("danger", "✗ " + clean(str(e)))])
    return out


# ---------------------------------------------------------------- build environments

def environments_line(vm: Dict[str, Any]) -> Optional[str]:
    """Job header: ``⬢ rust-build → resq-backend · node-build → resq-frontend · ready · ⬢ kept``."""
    info = vm.get("build_environments") or {}
    bindings = info.get("bindings") or []
    if not bindings:
        return None
    statuses = {b.get("status") for b in bindings}
    status = next((s for s in ("failed", "preparing", "cleaned", "ready") if s in statuses), "")
    text = "⬢ " + " · ".join(clean(str(b.get("label") or b.get("name") or "")) for b in bindings)
    if status:
        text += f" · {status}"
    if info.get("kept"):
        text += " · ⬢ kept"
    if info.get("refresh_pending"):
        text += " · refresh pending"
    return text


# ---------------------------------------------------------------- agents

def agents_line(vm: Dict[str, Any]) -> Optional[str]:
    """Job header: ``agents · plan Claude Code claude-opus-5-5 · implement Codex · review mucli``.
    Hidden when every stage runs on mucli (the default)."""
    agents = vm.get("stage_agents") or {}
    if not agents or all((agents.get(s) or {}).get("agent", "mucli") == "mucli" for s in agents):
        return None
    parts = []
    for stage in ("plan", "implement", "review"):
        cfg = agents.get(stage) or {}
        label = clean(str(cfg.get("label") or cfg.get("agent") or "mucli"))
        model = clean(str(cfg.get("model") or "")) if cfg.get("agent") not in (None, "", "mucli") else ""
        parts.append(f"{stage} {label}" + (f" {model}" if model else ""))
    return "◆ agents · " + " · ".join(parts)


# ---------------------------------------------------------------- W10 visibility

def job_patch(vm: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """``(patch, live)``: the live worktree diff while a job is active, else the checkpointed patch."""
    diff = vm.get("diff") or {}
    live = diff.get("live")
    if isinstance(live, dict) and live.get("patch") is not None:
        return live.get("patch"), live
    return diff.get("patch"), None


def live_diff_label(live: Optional[Dict[str, Any]]) -> Optional[str]:
    """Diff tab header while live: ``● live · 3 files · 1 untracked · truncated``."""
    if not live:
        return None
    parts = ["● live", f"{len(live.get('files') or [])} files"]
    untracked = int(live.get("untracked_total") or len(live.get("untracked") or []))
    if untracked:
        parts.append(f"{untracked} untracked")
    if live.get("truncated"):
        parts.append("truncated")
    return " · ".join(parts)


def budget_line(vm: Dict[str, Any]) -> Optional[str]:
    """Job header: ``$5.00 / $20.00 cap · active 12m · PR closed``."""
    budget = vm.get("budget") or {}
    pr = vm.get("pr") or {}
    if not budget and not pr:
        return None
    parts: List[str] = []
    if budget:
        cap = budget.get("max_cost_usd")
        cost = money(budget.get("cost_usd", vm.get("cost_usd")))
        parts.append(f"{cost} / {money(cap)} cap" if cap else f"{cost} · no cap")
        active = duration(budget.get("active_seconds") or 0) or "0s"
        limit = budget.get("max_runtime_seconds")
        parts.append(f"active {active}" + (f" / {duration(limit)}" if limit else ""))
    state = str(pr.get("state") or "")
    if state:
        parts.append(f"PR {state}" + (f" #{pr['number']}" if pr.get("number") else ""))
    elif pr.get("url"):
        parts.append("PR open" + (f" #{pr['number']}" if pr.get("number") else ""))
    return " · ".join(parts)


def environment_approval_lines(approval: Optional[Dict[str, Any]]) -> List[str]:
    """What a repository-declared environment approval will build/use (kind, ref, label, fingerprint)."""
    if not approval:
        return []
    kind = str(approval.get("kind") or "image")
    out = [f"kind {kind} · ref {clean(str(approval.get('ref') or '?'))}"]
    if approval.get("label"):
        out.append(clean(str(approval["label"])))
    out.append(f"fingerprint {clean(str(approval.get('fingerprint') or ''))}")
    if approval.get("message"):
        out.append(clean(str(approval["message"])).split("\n")[0])
    return out
