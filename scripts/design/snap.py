#!/usr/bin/env python3
"""Playwright snapshots of the web GUI for visual review (Wash spec §8.2).

    python3 scripts/design/snap.py --out build/snaps/w1 [--port 30397] [--pages / /work ...]

Boots ``create_app`` in-process on a spare port (never 30311, where a
developer's own GUI usually runs) with a stub session builder, then captures
every page in Paper and Dusk at desktop (1440×900) and mobile (390×844)
sizes. Console errors are written next to the screenshots and make the
script exit non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_PAGES = ["/", "/work", "/containers", "/trace", "/jobs/trace", "/models/costs", "/design"]
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
IGNORED_CONSOLE = (
    "Failed to load resource",  # stub session: API 404/500s are expected
    "Cannot read properties of null (reading 'iters')",  # known trace bootstrap race
)


def _serve(port: int):
    os.environ.setdefault("MUCLI_HOME", tempfile.mkdtemp(prefix="wash-snap-"))
    os.environ.setdefault("MUCLI_DESIGN_GALLERY", "1")
    import uvicorn

    from mu.gui.app import create_app

    def _no_session(**_kwargs):
        raise RuntimeError("snapshot server has no session")

    args = SimpleNamespace(workspace=None, session=None, provider=None, model=None)
    app = create_app(args=args, build_session_fn=_no_session, port=port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("server did not start")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "build/snaps/latest")
    parser.add_argument("--port", type=int, default=30397)
    parser.add_argument("--pages", nargs="*", default=DEFAULT_PAGES)
    parser.add_argument("--full-page", action="store_true")
    args = parser.parse_args()
    if args.port == 30311:
        parser.error("30311 is the default GUI port; pick a spare one")

    from playwright.sync_api import sync_playwright

    server = _serve(args.port)
    args.out.mkdir(parents=True, exist_ok=True)
    problems: dict[str, list[str]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for theme in ("paper", "dusk"):
            for vp_name, (w, h) in VIEWPORTS.items():
                ctx = browser.new_context(viewport={"width": w, "height": h})
                ctx.add_init_script(f"try{{localStorage.setItem('mucli-theme','{theme}')}}catch(e){{}}")
                page = ctx.new_page()
                errors: list[str] = []
                page.on("console", lambda m, e=errors: m.type == "error" and e.append(m.text))
                page.on("pageerror", lambda exc, e=errors: e.append(f"pageerror: {exc}"))
                for path in args.pages:
                    errors.clear()
                    slug = (path.strip("/").replace("/", "_") or "index")
                    try:
                        resp = page.goto(f"http://127.0.0.1:{args.port}{path}", timeout=30000)
                        page.wait_for_timeout(1500)
                        target = args.out / f"{slug}-{theme}-{vp_name}.png"
                        page.screenshot(path=str(target), full_page=args.full_page)
                        status = resp.status if resp else 0
                        if status >= 400:
                            errors.append(f"HTTP {status}")
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"navigation: {exc}")
                    real = [e for e in errors if not any(s in e for s in IGNORED_CONSOLE)]
                    if real:
                        problems[f"{slug}-{theme}-{vp_name}"] = real
                ctx.close()
        browser.close()
    server.should_exit = True
    (args.out / "console-errors.json").write_text(json.dumps(problems, indent=2))
    print(f"snapshots in {args.out}")
    for key, errs in problems.items():
        print(f"  {key}: {errs[:3]}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
