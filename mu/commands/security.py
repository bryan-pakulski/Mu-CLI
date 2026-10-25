"""Operator controls for security scans; host consent is deliberately not a model tool."""
import json

from . import command
from ._mode_util import reply, save
from mu.modes.evidence import CheckRefused
from mu.security import engine
from mu.security.state import security_state
from mu.ui.markup import esc


@command("/security", help="Security: status|scan <title>|list|open <id>|scope [JSON]|coverage|runner auto|docker|host|report")
def security_cmd(session, args, *, allow_prompt=True):
    parts = (args or "").split(None, 1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1].strip() if len(parts) > 1 else ""
    try:
        with security_state(session).lock:
            if sub == "status":
                text = engine.capsule(session)
            elif sub == "list":
                text = "\n".join(f"{r['scan_id']}: {r['title']} ({r['status']})" for r in engine.list_reports(session)) or "No scans."
            elif sub == "scan" and rest:
                report = engine.create_security_report(owner=session, title=rest)
                text = f"Opened {report.scan_id}. Set scope before review."
            elif sub == "open" and rest:
                text = f"Opened {engine.open_report(session, rest).scan_id}."
            elif sub == "runner" and rest:
                choice = engine.configure_runner(session, rest, actor="user:cli")
                text = ("Host execution enabled for this scan. PoCs run against the real workspace after command approval."
                        if choice["host_allowed"] else "Container execution selected; there is no automatic host fallback.")
            elif sub in {"scope", "coverage", "report"}:
                report = engine.active_report(session)
                if sub == "scope":
                    if rest:
                        engine.writable(session)
                        engine.set_scope(report, **json.loads(rest))
                        engine.persist(session, report)
                    text = json.dumps(report.scope, indent=2)
                elif sub == "coverage":
                    text = json.dumps(engine.coverage(report), indent=2)
                else:
                    engine.writable(session)
                    text = engine.generate_report(report)
            else:
                raise ValueError("Usage: /security status|scan <title>|list|open <id>|scope [JSON]|coverage|runner auto|docker|host|report")
            save(session)
        return reply(session, esc(text), allow_prompt)
    except (ValueError, TypeError, CheckRefused) as exc:
        return reply(session, esc(str(exc)), allow_prompt, ok=False)
