"""browser_snapshot: headless-Chromium screenshot + page text for self-review.

Distinct from research/url_grounding (text extraction for citations):
this tool renders a URL or local HTML file, captures a full-page PNG
into the session artifact registry, and returns a compact page summary.
Primary uses: reviewing published visualizations, debugging the GUI,
checking web layouts the agent just wrote.

Inside a sandbox — a container session's worker (``MUCLI_CONTAINER_MODE=1``)
or a job stage container (``MUCLI_JOB_STAGE``) — loopback targets are
allowed, so the agent can look at the dev server it started: there
``localhost`` is the sandbox itself, never the host. Private, link-local and
metadata addresses stay blocked everywhere; on the host loopback stays blocked.

Options: a device preset or explicit viewport, light/dark colour scheme,
waiting for a selector, capturing one element, and a short list of
interaction steps (click, fill, press, hover, select, wait) run before the
capture. Steps are only allowed on local targets (a workspace file or the
sandbox's own localhost), so the tool never submits anything to the web.
In a job stage the screenshot is also saved as job evidence
(``/output/evidence``, mu/jobs/evidence.py) for the human reviewer.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from mu.tools import tool
from utils.logger import logger

TOOL_NAME = "browser_snapshot"

_NAV_TIMEOUT_MS = 30_000
_STEP_TIMEOUT_MS = 15_000
_MAX_TEXT_CHARS = 4000
_MAX_STEPS = 20
_MAX_WAIT_MS = 10_000
DEVICES = {
    "desktop": {"viewport": {"width": 1280, "height": 900}},
    "tablet": {"viewport": {"width": 820, "height": 1180}, "has_touch": True, "device_scale_factor": 2},
    "mobile": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "device_scale_factor": 3},
}
STEP_KINDS = ("click", "fill", "press", "hover", "select", "wait_for", "wait_ms")
OUTPUT_ROOT = "/output"  # a job stage's output mount (mu/jobs/sandbox/policy.py)


def sandboxed() -> bool:
    """True inside a container session's worker or a job stage container (never on the host)."""
    return os.environ.get("MUCLI_CONTAINER_MODE") == "1" or bool(os.environ.get("MUCLI_JOB_STAGE"))


class _SSRFBlocked(Exception):
    """Raised by the page-level request hook when a redirect or subresource
    targets a non-public address (codex round-9 F2)."""


def _envelope(ok: bool, error_code: str | None, message: str, data: dict | None = None, artifacts: list | None = None) -> Dict[str, Any]:
    return {
        "ok": ok,
        "error_code": error_code,
        "message": message,
        "data": data or {},
        "artifacts": artifacts or [],
        "telemetry": {"tool_name": TOOL_NAME},
    }


def _ssrf_check_hostname(hostname: str, *, allow_loopback: bool = False) -> str | None:
    """SSRF gate for a URL hostname (codex round-9 F2).

    Resolves every hostname — raw IPs and names alike — and refuses the
    request if ANY resolved address is non-public. This closes the
    round-8 gap where a DNS name pointing at loopback/private/link-local
    space (or cloud metadata endpoints) passed the literal-IP check.
    Returns an error message, or None when allowed.
    """
    import ipaddress as _ip
    import socket

    lowered = hostname.lower()
    if allow_loopback and (lowered == "localhost" or lowered.endswith(".localhost")):
        return None  # Chromium resolves *.localhost to loopback itself
    if lowered in {"metadata.google.internal", "metadata"} or lowered.endswith(
        (".internal", ".local", ".localhost")
    ):
        return f"blocked internal hostname: {hostname}"
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        return f"cannot resolve hostname {hostname}: {exc}"
    if not infos:
        return f"cannot resolve hostname {hostname}: no addresses returned"
    seen: set[str] = set()
    for info in infos:
        addr = _ip.ip_address(info[4][0])
        key = str(addr)
        if key in seen:
            continue
        seen.add(key)
        if allow_loopback and addr.is_loopback:
            continue  # the sandbox's own services (checked before is_private: 127/8 is private too)
        if (
            addr.is_loopback
            or addr.is_link_local
            or addr.is_private
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return f"blocked non-public address: {hostname} -> {key}"
    return None


def _resolve_target(target: str, context=None) -> tuple[str | None, str | None]:
    """Return (playwright_url, error). Local file paths become file:// URLs.

    Security (codex round-8 F2): local targets must pass the same
    capability boundary as filesystem tools (workspace bounds + secret
    path denylist); remote targets must not be loopback/link-local/
    metadata addresses (SSRF).
    """
    target = str(target or "").strip()
    if not target:
        return None, "browser_snapshot requires a url or file path"
    if target.startswith(("http://", "https://")):
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(target)
            hostname = parsed.hostname or ""
        except ValueError:
            return None, f"unparseable URL: {target}"
        if not hostname:
            return None, f"URL has no hostname: {target}"
        err = _ssrf_check_hostname(hostname, allow_loopback=sandboxed())
        if err:
            return None, err
        return target, None
    if target.startswith("file://"):
        path = target[len("file://") :]
    else:
        path = os.path.expanduser(target)
        if not os.path.isabs(path):
            # Relative targets anchor to the workspace, never the host
            # process cwd (same contract as the file tools).
            from mu.tools._bounds import resolve_tool_path

            _session = getattr(context, "session", None)
            _fc = getattr(context, "folder_context", None) or getattr(
                _session, "folder_context", None
            ) or getattr(getattr(_session, "session_manager", None), "folder_context", None)
            _stype = str(
                (getattr(_session, "variables", None) or {}).get("session_type", "workspace")
                or "workspace"
            )
            path = resolve_tool_path(path, _fc, _stype)
    path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    if not os.path.isfile(path):
        return None, f"target not found: {target}"
    # Capability boundary: same gate the file tools use.
    try:
        from mu.security.secret_paths import is_denied_path
        from mu.tools._bounds import check_bounds

        session = getattr(context, "session", None)
        folder_context = getattr(context, "folder_context", None) or getattr(
            session, "folder_context", None
        ) or getattr(
            getattr(session, "session_manager", None), "folder_context", None
        )
        session_type = str(
            (getattr(session, "variables", None) or {}).get("session_type", "workspace")
            or "workspace"
        )
        denied, reason = is_denied_path(path)
        if denied:
            return None, f"blocked: {reason}"
        if not check_bounds(path, folder_context, session_type=session_type):
            return None, f"path outside workspace bounds: {path}"
        # Fail closed for local files when a dispatch context exists but
        # carries no workspace boundary. A direct call with no context at
        # all (unit helpers, ad-hoc resolution) keeps the historical
        # open-but-denylisted behavior; the dispatcher always supplies a
        # context, so production calls are covered.
        if context is not None and not (
            folder_context and getattr(folder_context, "folders", None)
        ):
            return None, (
                "blocked: no workspace folder attached; "
                "local file targets require an explicit workspace boundary"
            )
    except ImportError:
        pass
    return "file://" + path, None


def _registry(context) -> Any | None:
    session = getattr(context, "session", None)
    registry = getattr(session, "artifact_registry", None)
    if registry is not None:
        return registry
    # No live session (unit tests, ad-hoc runs): use a stable scratch
    # session directory. ArtifactRegistry expects a SESSION dir and appends
    # artifacts/ itself — do not pass an artifacts path directly.
    home = os.environ.get("MUCLI_HOME", os.path.expanduser("~/.mucli"))
    from mu.artifact.registry import ArtifactRegistry

    return ArtifactRegistry(os.path.join(home, "sessions", "tool-snapshots"))


def _options(args: Dict[str, Any], *, local: bool) -> tuple[dict | None, str | None]:
    """Validated render options: browser-context kwargs, waits, element, steps."""
    device = str(args.get("device") or "desktop").strip().lower()
    if device not in DEVICES:
        return None, f"device must be one of {', '.join(DEVICES)}"
    context_kwargs: Dict[str, Any] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEVICES[device].items()}
    for key, low, high in (("width", 320, 3840), ("height", 240, 4320)):
        if args.get(key) not in (None, ""):
            try:
                value = int(args[key])
            except (TypeError, ValueError):
                return None, f"{key} must be a whole number"
            if not low <= value <= high:
                return None, f"{key} must be between {low} and {high}"
            context_kwargs["viewport"][key] = value
    scheme = str(args.get("color_scheme") or "").strip().lower()
    if scheme:
        if scheme not in ("light", "dark"):
            return None, "color_scheme must be light or dark"
        context_kwargs["color_scheme"] = scheme
    steps = args.get("actions") or []
    if not isinstance(steps, list):
        return None, "actions must be a list"
    if steps and not local:
        return None, ("actions run only on local targets (a workspace file, or localhost inside a container "
                      "session or job sandbox); public pages are captured read-only")
    if len(steps) > _MAX_STEPS:
        return None, f"at most {_MAX_STEPS} actions"
    clean = []
    for i, step in enumerate(steps):
        kinds = [k for k in STEP_KINDS if isinstance(step, dict) and step.get(k) not in (None, "")]
        if len(kinds) != 1:
            return None, f"actions[{i}] needs exactly one of {', '.join(STEP_KINDS)}"
        kind = kinds[0]
        if kind == "wait_ms":
            try:
                ms = int(step["wait_ms"])
            except (TypeError, ValueError):
                return None, f"actions[{i}].wait_ms must be a number"
            clean.append({"wait_ms": max(0, min(ms, _MAX_WAIT_MS))})
            continue
        entry = {kind: str(step[kind])}
        if kind == "fill":
            entry["text"] = str(step.get("text") or "")
        if kind == "select":
            entry["value"] = str(step.get("value") or "")
        clean.append(entry)
    return {"context": context_kwargs, "device": device, "wait_for": str(args.get("wait_for") or "").strip(),
            "selector": str(args.get("selector") or "").strip(), "steps": clean}, None


def _run_step(page, step: Dict[str, Any]) -> None:
    if "wait_ms" in step:
        page.wait_for_timeout(step["wait_ms"])
    elif "click" in step:
        page.click(step["click"], timeout=_STEP_TIMEOUT_MS)
    elif "fill" in step:
        page.fill(step["fill"], step["text"], timeout=_STEP_TIMEOUT_MS)
    elif "press" in step:
        page.keyboard.press(step["press"])
    elif "hover" in step:
        page.hover(step["hover"], timeout=_STEP_TIMEOUT_MS)
    elif "select" in step:
        page.select_option(step["select"], step["value"], timeout=_STEP_TIMEOUT_MS)
    elif "wait_for" in step:
        page.wait_for_selector(step["wait_for"], timeout=_STEP_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
    except Exception:  # noqa: BLE001 - long-polling pages never go idle; the step itself succeeded
        pass


def _save_job_evidence(png_path: str, name: str, caption: str) -> str | None:
    """In a job stage, also save the screenshot as evidence for the human reviewer."""
    if not os.environ.get("MUCLI_JOB_STAGE") or not os.path.isdir(OUTPUT_ROOT):
        return None
    import json
    import re
    import shutil

    folder = os.path.join(OUTPUT_ROOT, "evidence")
    try:
        os.makedirs(folder, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name.removesuffix(".png")).strip("-.") or "snapshot"
        target, n = os.path.join(folder, f"{stem}.png"), 2
        while os.path.exists(target):
            target, n = os.path.join(folder, f"{stem}-{n}.png"), n + 1
        shutil.copyfile(png_path, target)
        index_path = os.path.join(folder, "index.json")
        try:
            with open(index_path, encoding="utf-8") as handle:
                index = json.load(handle)
        except (OSError, ValueError):
            index = []
        if not isinstance(index, list):
            index = []
        index.append({"file": os.path.basename(target), "caption": caption[:300]})
        with open(index_path, "w", encoding="utf-8") as handle:
            json.dump(index, handle)
        return target
    except OSError as exc:
        logger.warning("browser_snapshot: could not save job evidence: %s", exc)
        return None


@tool(
    name=TOOL_NAME,
    phase="browser",
    description=(
        "Render a URL or local HTML file in headless Chromium and return a screenshot (registered as a "
        "session artifact; vision models see it) plus the page text. Use it to check UI you built or changed. "
        "Inside a container session or job sandbox, http://localhost:<port> reaches the dev server you started "
        "there. Options: device (desktop/tablet/mobile) or width/height, color_scheme (light/dark), wait_for a "
        "selector, capture one element (selector), and actions run before the capture on local targets "
        "(click, fill+text, press, hover, select+value, wait_for, wait_ms). In a job stage the screenshot is "
        "also saved as evidence for the reviewer."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "HTTP(S) URL, file:// URL, or local HTML file path."},
            "name": {"type": "string", "description": "Name for the screenshot (default: browser-snapshot)."},
            "full_page": {"type": "boolean", "description": "Capture the full scrollable page (default true)."},
            "device": {"type": "string", "enum": list(DEVICES),
                       "description": "Viewport preset: desktop 1280x900, tablet 820x1180, mobile 390x844 (default desktop)."},
            "width": {"type": "integer", "description": "Viewport width in CSS pixels (overrides the preset)."},
            "height": {"type": "integer", "description": "Viewport height in CSS pixels (overrides the preset)."},
            "color_scheme": {"type": "string", "enum": ["light", "dark"], "description": "prefers-color-scheme to emulate."},
            "wait_for": {"type": "string", "description": "CSS selector to wait for before capturing."},
            "selector": {"type": "string", "description": "Capture only this element (CSS selector)."},
            "actions": {
                "type": "array",
                "description": f"Up to {_MAX_STEPS} steps run in order before the capture (local targets only). "
                               "Each object has one key: click, fill (with text), press (a key such as Enter), "
                               "hover, select (with value), wait_for (selector) or wait_ms.",
                "items": {"type": "object"},
            },
        },
        "required": ["url"],
    },
    requires_approval=False,
    execution_kind="io",
    result_mode="json",
)
def browser_snapshot(args: Dict[str, Any], context) -> Dict[str, Any]:
    url, err = _resolve_target(args.get("url"), context)
    if err:
        return _envelope(False, "invalid_target", err)
    # Local = a workspace file, or (only inside a sandbox) its own loopback services.
    local = url.startswith("file://") or (sandboxed() and _is_loopback_url(url))
    options, err = _options(args, local=local)
    if err:
        return _envelope(False, "invalid_options", err)
    name = str(args.get("name") or "browser-snapshot").strip() or "browser-snapshot"
    full_page = bool(args.get("full_page", True))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _envelope(
            False,
            "playwright_missing",
            "playwright not installed; add the 'browser' extra (pip install .[browser]).",
        )

    tmp_png = None
    steps_done = 0
    try:
        with sync_playwright() as p:
            launch: Dict[str, Any] = {"headless": True}
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            if proxy:
                # Container egress goes through the session proxy; the sandbox's own services don't.
                launch["proxy"] = {"server": proxy, "bypass": "localhost,127.0.0.1,::1,*.localhost"}
            try:
                browser = p.chromium.launch(**launch)
            except Exception as exc:
                return _envelope(
                    False,
                    "chromium_unavailable",
                    f"chromium launch failed: {exc}. Run 'playwright install --with-deps chromium'.",
                )
            try:
                page = browser.new_context(**options["context"]).new_page()
                # Round-9 F2: re-check every network request (redirects and
                # subresources included) — the initial gate only sees the
                # entry URL, but a page can redirect or pull scripts/images
                # from internal addresses.
                from urllib.parse import urlsplit as _split

                allow_loopback = sandboxed()

                def _deny_internal(request_url: str) -> str | None:
                    try:
                        host = _split(request_url).hostname or ""
                    except ValueError:
                        return "unparseable subresource URL"
                    if not host:
                        return None  # data:, blob:, about: — no host to check
                    return _ssrf_check_hostname(host, allow_loopback=allow_loopback)

                # Route-based enforcement: Playwright event listeners do
                # not abort requests, so raising from "request" would let
                # the fetch complete before the exception surfaced. A
                # context route aborts denied requests before any bytes
                # are sent, then reports through the shared flag.
                ssrf_error: list = []

                def _route_handler(route, request):
                    err = _deny_internal(request.url)
                    if err:
                        if not ssrf_error:
                            ssrf_error.append(err)
                        try:
                            route.abort()
                        except Exception:
                            pass
                        return
                    route.continue_()

                page.context.route("**/*", _route_handler)
                page.goto(url, wait_until="networkidle", timeout=_NAV_TIMEOUT_MS)
                if ssrf_error:
                    raise _SSRFBlocked(ssrf_error[0])
                if options["wait_for"]:
                    page.wait_for_selector(options["wait_for"], timeout=_STEP_TIMEOUT_MS)
                for step in options["steps"]:
                    try:
                        _run_step(page, step)
                    except Exception as exc:  # noqa: BLE001 - report which step failed, keep the evidence
                        raise RuntimeError(f"action {steps_done + 1} ({next(iter(step))}) failed: {exc}") from exc
                    steps_done += 1
                    if ssrf_error:
                        raise _SSRFBlocked(ssrf_error[0])
                title = page.title()
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
                    tmp_png = fh.name
                if options["selector"]:
                    page.locator(options["selector"]).first.screenshot(path=tmp_png, timeout=_STEP_TIMEOUT_MS)
                else:
                    page.screenshot(path=tmp_png, full_page=full_page)
                text = page.evaluate(
                    "() => document.body ? document.body.innerText.slice(0, %d) : ''"
                    % _MAX_TEXT_CHARS
                )
                final_url = page.url
            finally:
                browser.close()
    except _SSRFBlocked as exc:
        if tmp_png and os.path.exists(tmp_png):
            try:
                os.unlink(tmp_png)
            except OSError:
                pass
        return _envelope(False, "ssrf_blocked", f"blocked: {exc}")
    except Exception as exc:
        if tmp_png and os.path.exists(tmp_png):
            try:
                os.unlink(tmp_png)
            except OSError:
                pass
        return _envelope(False, "render_failed", f"page render failed: {exc}")

    evidence = _save_job_evidence(tmp_png, name, f"{title or final_url} ({options['device']}"
                                  f"{', ' + options['context'].get('color_scheme', '') if options['context'].get('color_scheme') else ''})")
    try:
        registry = _registry(context)
        descriptor = registry.add(
            name=name if name.endswith(".png") else f"{name}.png",
            source_path=tmp_png,
            mime_type="image/png",
            kind="file",
            display="download",
            title=f"Browser snapshot: {title or url}",
        )
    except Exception as exc:
        if tmp_png and os.path.exists(tmp_png):
            try:
                os.unlink(tmp_png)
            except OSError:
                pass
        return _envelope(False, "artifact_failed", f"could not register screenshot: {exc}")

    # The artifact registry has already copied the PNG into its own store;
    # remove the temp file so successful runs don't leak /tmp files.
    png_size = os.path.getsize(tmp_png)
    try:
        os.unlink(tmp_png)
        tmp_png = None
    except OSError:
        pass

    logger.info("browser_snapshot: %s -> artifact %s", url, descriptor.get("artifact_id"))
    return _envelope(
        True,
        None,
        f"Captured {title or final_url} ({png_size} bytes)." + (" Saved as job evidence." if evidence else ""),
        data={
            "artifact_id": descriptor.get("artifact_id"),
            "artifact_name": descriptor.get("name"),
            "final_url": final_url,
            "page_title": title,
            "page_text": text or "",
            "full_page": full_page and not options["selector"],
            "device": options["device"],
            "viewport": options["context"]["viewport"],
            "actions_run": steps_done,
            **({"evidence": os.path.basename(evidence)} if evidence else {}),
        },
        artifacts=[descriptor],
    )


def _is_loopback_url(url: str) -> bool:
    import ipaddress
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        import socket
        try:
            return all(ipaddress.ip_address(i[4][0]).is_loopback for i in socket.getaddrinfo(host, None))
        except OSError:
            return False
