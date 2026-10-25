"""Research tools: search (candidates), reading (url_grounding, research_fetch,
read_document) and source assessment.

Search tools register each result row as a *candidate* (``s<n>``): deduped,
with its snippet, but never numbered or listed in the bibliography. Reading a
source extracts its main content, keeps the full text in the result store
(``result_ref``) and the research text store (for excerpt checks), and gives it
a stable citation number ``[^n]``. Only the passages most relevant to the query
come back inline, bounded by ``max_chars``.

Each tool is a positional-signature body (publicly callable, easy to test)
plus a thin ``_<name>_tool(args, context)`` wrapper that carries the ``@tool``
registration and binds the session's ``ResearchState``. Fetched text is
untrusted data: returned as tool output only.
"""

from __future__ import annotations

import contextlib
import contextvars
import io
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from mu.research import ResearchError, ResearchState, SourceType, get_citation_manager, use_research_state
from mu.tools import tool
from mu.tools._bounds import check_bounds as _check_bounds
from mu.tools.capabilities import session_type_from_context
from utils.logger import logger

from utils.threads import NamedThread

FETCH_MAX_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT = 20.0
DEFAULT_MAX_CHARS = 6000
MIN_MAX_CHARS = 800
MAX_MAX_CHARS = 20000
FETCH_MAX_URLS = 6
MIN_TEXT_CHARS = 80  # less main content than this is a nav shell, not a source
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


# ============================================================== session state

def _engine(context=None) -> ResearchState:
    """The session's ResearchState, else the context-bound/default one."""
    manager = getattr(getattr(context, "session", None), "session_manager", None)
    state = getattr(manager, "research", None)
    return state if isinstance(state, ResearchState) else get_citation_manager()


def _bound(context):
    """Bind the session's state for a handler body (dispatch also binds it)."""
    manager = getattr(getattr(context, "session", None), "session_manager", None)
    state = getattr(manager, "research", None)
    return use_research_state(state) if isinstance(state, ResearchState) else contextlib.nullcontext()


def _result_cache(context):
    return getattr(getattr(context, "session", None), "tool_result_cache", None)


def _candidate(url: str, title: str = "", source_type: Any = SourceType.WEB, *, snippet: str = "",
               authors=None, date=None, metadata=None) -> Dict[str, Any]:
    """Register a search row as a candidate; returns the id fields for the row."""
    try:
        source = get_citation_manager().add_candidate(
            url=url, title=title, source_type=source_type, snippet=snippet or "",
            authors=authors, date=date, metadata=metadata,
        )
    except ValueError:
        source = None
    if source is None:
        return {}
    fields: Dict[str, Any] = {"source_id": source.candidate_id}
    if source.citation_id:
        fields["citation_id"] = source.citation_id
    return fields


_CANDIDATE_NOTE = (
    "Results are unread candidates (source_id s<n>). Read the promising ones with "
    "url_grounding / research_fetch (pass the URL or source_id) before citing them."
)


def _clamp_chars(value: Any, default: int = DEFAULT_MAX_CHARS) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(MIN_MAX_CHARS, min(MAX_MAX_CHARS, n))


def _assess_payload(source) -> Dict[str, Any]:
    return {"citation_id": source.citation_id, "credibility_score": source.credibility_score,
            "origin": source.origin}


@tool(
    name="assess_source",
    description=(
        "Rate a read research source after evaluating its authority, "
        "methodology, relevance, and corroboration. Use 0.0–1.0; source "
        "type supplies a hard safety cap, not a flat rating. Call this for "
        "each source you rely on — the AI owns the weighting decision. "
        "Set origin when the source is not independent of another (e.g. "
        "syndicated wire copy) so corroboration counts it once."
    ),
    parameters={
        "type": "object",
        "properties": {
            "citation_id": {"type": "integer", "description": "Source citation number [^n]."},
            "importance": {"type": "number", "description": "Model-assessed evidence strength from 0.0 to 1.0."},
            "rationale": {"type": "string", "description": "Brief reason for the assessment."},
            "origin": {"type": "string", "description": "Optional independence group override (e.g. 'reuters.com')."},
        },
        "required": ["citation_id", "importance"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _assess_source_tool(args: Dict[str, Any], context) -> Any:
    try:
        source = _engine(context).assess_source(
            args.get("citation_id"), float(args.get("importance") or 0.0),
            str(args.get("rationale") or ""), origin=args.get("origin"),
        )
    except (ResearchError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return json.dumps(_assess_payload(source))


# ============================================================== fetching

class FetchError(Exception):
    pass


def _fetch(url: str, timeout: float = FETCH_TIMEOUT) -> Dict[str, Any]:
    """GET ``url`` with a size cap. Returns final url, content type and body bytes."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx is a hard dependency
        raise FetchError("httpx is not installed") from exc
    if not re.match(r"^https?://", url or "", re.I):
        raise FetchError(f"not an http(s) URL: {url!r}")
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"}) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                ctype = (response.headers.get("content-type") or "").lower()
                chunks: List[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > FETCH_MAX_BYTES:
                        raise FetchError(f"response larger than {FETCH_MAX_BYTES // (1024 * 1024)} MB")
                    chunks.append(chunk)
                return {"url": str(response.url), "content_type": ctype, "body": b"".join(chunks),
                        "encoding": response.encoding or "utf-8"}
    except FetchError:
        raise
    except httpx.HTTPStatusError as exc:
        raise FetchError(f"HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(str(exc) or exc.__class__.__name__) from exc


def _render_with_playwright(url: str) -> Optional[str]:
    """Rendered HTML for JS-heavy pages; None when Playwright is unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=int(FETCH_TIMEOUT * 1000))
                return page.content()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.info("url_grounding: Playwright render failed for %s: %s", url, exc)
        return None


def _store_full_text(context, text: str, url: str) -> Optional[str]:
    cache = _result_cache(context)
    if cache is None or not text:
        return None
    from mu.research.texts import text_key

    try:
        return cache.store(f"research:{text_key(text)}", "url_grounding", text, force=True)
    except Exception:  # noqa: BLE001
        logger.debug("url_grounding: result store failed for %s", url, exc_info=True)
        return None


def _format_read(source, text: str, query: Optional[str], max_chars: int, *, method: str,
                 locator_hint: str = "") -> str:
    from mu.research.extract import select_passages

    meta = [f"[^{source.citation_id}]"]
    if source.site:
        meta.append(source.site)
    if source.authors:
        meta.append(", ".join(source.authors[:3]) + (" et al." if len(source.authors) > 3 else ""))
    if source.date:
        meta.append(str(source.date))
    meta.append(source.url)
    head = [f"# {source.title}", " · ".join(meta)]
    ref = f" · result_ref {source.result_ref} (result_search / result_range for more)" if source.result_ref else ""
    budget = max(200, max_chars - sum(len(h) for h in head) - 300)
    picked = select_passages(text, query, budget)
    head.append(
        f"Extracted page text (untrusted content, via {method}): {len(text):,} chars in "
        f"{picked['total_passages']} passages{ref}"
    )
    if query:
        head.append(f"Passages ranked for: {query}")
    body: List[str] = []
    for p in picked["passages"]:
        title = f"## {p.heading}" if p.heading else f"## Passage {p.index + 1}"
        body.append(f"{title}\n\n{p.text}")
    out = "\n".join(head) + "\n\n" + "\n\n".join(body)
    if picked["outline"]:
        out += "\n\nOther sections: " + " · ".join(picked["outline"][:30])
    out += (
        f"\n\nCite with record_claim(evidence=[{{citation_id: {source.citation_id}, stance, "
        f"excerpt}}]); excerpts must quote this text{locator_hint}."
    )
    return out[: max_chars + 400]


def _default_query(state: ResearchState) -> Optional[str]:
    q = state.current_question()
    return q.text if q is not None else None


def _resolve_target(state: ResearchState, target: str) -> str:
    target = (target or "").strip()
    if re.fullmatch(r"[sS]\d+", target):
        source = state.find(target)
        if source is None:
            raise FetchError(f"no candidate {target}")
        return source.url
    return target


def read_url(url: str, *, query: Optional[str] = None, max_chars: int = DEFAULT_MAX_CHARS,
             state: Optional[ResearchState] = None, context=None) -> str:
    """Fetch, extract and register one URL; returns the bounded read block.

    Raises ``FetchError`` with a reason the agent can act on.
    """
    from mu.research.extract import extract_html

    state = state or get_citation_manager()
    url = _resolve_target(state, url)
    fetched = _fetch(url)
    body = fetched["body"]
    ctype = fetched["content_type"]
    query = (query or "").strip() or _default_query(state)
    if "pdf" in ctype or body.startswith(b"%PDF-"):
        return _read_pdf_bytes(body, url, query=query, max_chars=max_chars, state=state, context=context)
    if ctype and not any(t in ctype for t in ("html", "xml", "text/plain")):
        raise FetchError(f"unsupported content type {ctype!r}")
    html = body.decode(fetched["encoding"], errors="replace")
    if "text/plain" in ctype:
        from mu.research.extract import Extracted
        ex = Extracted(title=url, text=html.strip(), method="text")
    else:
        ex = extract_html(html, fetched["url"])
        if len(ex.text) < 200:
            rendered = _render_with_playwright(url)
            if rendered:
                again = extract_html(rendered, fetched["url"])
                if len(again.text) > len(ex.text):
                    again.method += "+playwright"
                    ex = again
    if len(ex.text.strip()) < MIN_TEXT_CHARS:
        raise FetchError(
            "no readable text on the page (it may need JavaScript or be behind a login); "
            "try another source"
        )
    ref = _store_full_text(context, ex.text, url)
    metadata = {"extraction": ex.method}
    if fetched["url"] != url:
        metadata["final_url"] = fetched["url"]
    source = state.mark_read(
        url=url, title=ex.title, source_type=SourceType.WEB, text=ex.text,
        html_canonical=ex.canonical, authors=ex.authors, date=ex.published, site=ex.site,
        metadata=metadata, result_ref=ref,
    )
    return _format_read(source, ex.text, query, max_chars, method=ex.method)


def url_grounding(url: str, folder_context=None, *, query: Optional[str] = None,
                  max_chars: int = DEFAULT_MAX_CHARS, context=None) -> str:
    """Read a web page: main content, metadata, and the passages that match ``query``."""
    try:
        return read_url(url, query=query, max_chars=_clamp_chars(max_chars),
                        state=_engine(context), context=context)
    except (FetchError, ResearchError) as exc:
        return f"Error reading {url}: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("url_grounding: failed for %s: %s", url, exc)
        return f"Error reading {url}: {exc}"


@tool(
    name="url_grounding",
    description=(
        "Read a web page (or a search candidate by source_id, e.g. 's4'): extracts the main "
        "content without navigation/boilerplate, records the source with a citation number, "
        "and returns the passages most relevant to `query` (default: the current research "
        "question) within max_chars. The full text stays retrievable via result_ref."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to read, or a candidate source_id like 's4'."},
            "query": {"type": "string", "description": "What you are looking for; ranks the passages returned."},
            "max_chars": {"type": "integer", "description": "Inline budget (default 6000, 800–20000)."},
        },
        "required": ["url"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _url_grounding_tool(args: Dict[str, Any], context) -> str:
    with _bound(context):
        return url_grounding(args.get("url", ""), context.folder_context, query=args.get("query"),
                             max_chars=args.get("max_chars", DEFAULT_MAX_CHARS), context=context)


def research_fetch(urls: List[str], *, query: Optional[str] = None, max_chars: int = 12000,
                   context=None) -> str:
    """Read up to ``FETCH_MAX_URLS`` URLs concurrently; one failing URL never fails the rest."""
    targets: List[str] = []
    for u in urls or []:
        u = str(u or "").strip()
        if u and u not in targets:
            targets.append(u)
    if not targets:
        return "Error: urls must list at least one URL or source_id."
    dropped = targets[FETCH_MAX_URLS:]
    targets = targets[:FETCH_MAX_URLS]
    state = _engine(context)
    total = _clamp_chars(max_chars, 12000)
    per_url = max(MIN_MAX_CHARS, total // len(targets))

    def _one(target: str):
        try:
            return target, read_url(target, query=query, max_chars=per_url, state=state, context=context), None
        except Exception as exc:  # noqa: BLE001 — reported per URL
            return target, None, str(exc) or exc.__class__.__name__

    with ThreadPoolExecutor(max_workers=len(targets), thread_name_prefix="research-fetch") as pool:
        futures = [pool.submit(contextvars.copy_context().run, _one, t) for t in targets]
        results = [f.result() for f in futures]
    ok = [(t, block) for t, block, err in results if block]
    failed = [(t, err) for t, block, err in results if not block]
    lines = [f"Read {len(ok)}/{len(targets)} URL(s)."]
    for t, err in failed:
        lines.append(f"- failed: {t} — {err}")
    if dropped:
        lines.append(f"- skipped (limit {FETCH_MAX_URLS} per call): " + ", ".join(dropped))
    return "\n".join(lines) + "\n\n" + "\n\n---\n\n".join(block for _, block in ok)


@tool(
    name="research_fetch",
    description=(
        f"Read up to {FETCH_MAX_URLS} web pages or candidate source_ids at once (concurrently). "
        "Each is extracted and recorded like url_grounding; the max_chars budget is split "
        "between them. A failing URL is reported without failing the others."
    ),
    parameters={
        "type": "object",
        "properties": {
            "urls": {"type": "array", "items": {"type": "string"},
                     "description": f"URLs or source_ids (max {FETCH_MAX_URLS})."},
            "query": {"type": "string", "description": "What you are looking for; ranks the passages returned."},
            "max_chars": {"type": "integer", "description": "Total inline budget (default 12000)."},
        },
        "required": ["urls"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _research_fetch_tool(args: Dict[str, Any], context) -> str:
    urls = args.get("urls")
    if isinstance(urls, str):
        urls = [u for u in re.split(r"[\s,]+", urls) if u]
    with _bound(context):
        return research_fetch(urls or [], query=args.get("query"),
                              max_chars=args.get("max_chars", 12000), context=context)


# ============================================================== web_search

_DDGS_HARD_TIMEOUT = 20.0


def _run_with_timeout(func, *, timeout: float, label: str):
    """Run `func()` on a daemon thread; raise TimeoutError after `timeout`.

    Uses raw `threading.Thread(daemon=True)` rather than
    `concurrent.futures.ThreadPoolExecutor` on purpose: the executor's
    `with` block (or `shutdown(wait=True)`) blocks waiting for the
    worker to finish even after `future.result(timeout=...)` raises.
    With a daemon thread we leave the hung thread in the background and
    return immediately — it won't keep the process alive on exit, and
    in a long-running REPL it just gets garbage-collected the next time
    the underlying request times out at the HTTP layer.
    """
    import threading

    result_box: list = []
    error_box: list = []

    def _runner() -> None:
        try:
            result_box.append(func())
        except BaseException as exc:  # noqa: BLE001
            error_box.append(exc)

    # Run in a copy of the caller's context so the session's bound
    # ResearchState (a ContextVar) is visible to the search body.
    ctx = contextvars.copy_context()
    thread = NamedThread(target=lambda: ctx.run(_runner), daemon=True, name=label)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise TimeoutError(
            f"{label} did not return within {timeout:.0f}s"
        )
    if error_box:
        raise error_box[0]
    if not result_box:
        raise RuntimeError(f"{label} returned without a value")
    return result_box[0]


def _ddgs_text_search(query: str, max_results: int):
    """Run a DDGS text search with a hard wall-clock timeout.

    The `ddgs` library does NOT plumb a timeout to its underlying HTTP
    layer in every release, so a flaky DDG endpoint can hang the call
    indefinitely. We dispatch the call to a daemon thread; on timeout
    the caller falls through to the HTML and InstantAnswer fallbacks.
    """

    def _do_search() -> list[dict]:
        from ddgs import DDGS

        with DDGS() as ddg:
            return list(ddg.text(query, max_results=max_results))

    try:
        return _run_with_timeout(
            _do_search, timeout=_DDGS_HARD_TIMEOUT, label=f"ddgs.text({query!r})"
        )
    except TimeoutError as exc:
        # Surface as a TimeoutError so the caller's broad `except Exception`
        # handles it identically to other transient failures.
        raise TimeoutError(
            f"ddgs.text() did not return within {_DDGS_HARD_TIMEOUT:.0f}s "
            f"for query {query!r}"
        ) from exc


_WEB_SEARCH_HARD_TIMEOUT = 60.0


def _normalize_ddg_href(href: str) -> str:
    """Normalize a DuckDuckGo HTML result href to an absolute https URL.

    DDG's HTML endpoint wraps every result in a redirect URL of the form
    ``//duckduckgo.com/l/?uddg=<url-encoded target>&rut=...`` —
    protocol-relative AND redirect-wrapped. Returning that verbatim gives
    callers unusable ``//`` URLs. This unwraps to the real target:

      * ``//host/...``           → ``https://host/...``
      * ``https://duckduckgo.com/l/?uddg=<enc>`` → the decoded ``uddg`` URL
      * anything else            → unchanged
    """
    from urllib.parse import urlparse, parse_qs, unquote

    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
        if "duckduckgo.com" in (parsed.netloc or "").lower() and parsed.path.startswith("/l/"):
            uddg = (parse_qs(parsed.query).get("uddg") or [""])[0]
            if uddg:
                return unquote(uddg)
    except Exception:  # noqa: BLE001
        pass
    return href


def web_search(query: str, engine: str = "duckduckgo", num_results: int = 10, folder_context=None) -> str:
    """Search the web using DuckDuckGo or Google Custom Search API.

    Bounded by `_WEB_SEARCH_HARD_TIMEOUT` so even pathological combinations
    of slow fallbacks (DNS hang on every endpoint, etc.) can't freeze the
    chat indefinitely. Runs on a daemon thread; on timeout the daemon is
    abandoned (it cleans up on its own) and a structured error envelope
    is returned to the agent.
    """

    def _run() -> str:
        return _web_search_impl(query, engine, num_results, folder_context)

    try:
        return _run_with_timeout(
            _run,
            timeout=_WEB_SEARCH_HARD_TIMEOUT,
            label=f"web_search({query!r})",
        )
    except TimeoutError:
        logger.warning(
            "web_search: hard timeout (%.0fs) for query %r — every fallback hung.",
            _WEB_SEARCH_HARD_TIMEOUT,
            query,
        )
        return json.dumps(
            {
                "query": query,
                "engine": engine,
                "error": (
                    f"web_search timed out after {_WEB_SEARCH_HARD_TIMEOUT:.0f}s. "
                    "All search backends were unresponsive — try again, "
                    "or use a different engine via engine='google' if "
                    "GOOGLE_SEARCH_API_KEY is set."
                ),
                "num_results": 0,
                "urls_used": [],
                "results": [],
            }
        )


def _web_search_impl(query: str, engine: str = "duckduckgo", num_results: int = 10, folder_context=None) -> str:
    num_results = min(max(1, num_results), 50)

    if not query or not query.strip():
        return json.dumps({"error": "Query cannot be empty", "results": []})

    query = query.strip()

    def _duckduckgo_instantapi_fallback() -> list[dict]:
        fallback_results: list[dict] = []
        endpoint = (
            "https://api.duckduckgo.com/?"
            + urllib.parse.urlencode(
                {
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "no_redirect": "1",
                }
            )
        )
        request = urllib.request.Request(
            endpoint,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))

        def _append_result(title: str, url: str, snippet: str):
            if not url:
                return
            fallback_results.append(
                {
                    "title": title or url,
                    "url": url,
                    "snippet": snippet or "",
                    "relevance_score": max(0.1, 1.0 - (len(fallback_results) * 0.05)),
                    **_candidate(url, title or url, SourceType.WEB, snippet=snippet or ""),
                }
            )

        abstract_url = str(payload.get("AbstractURL", "") or "").strip()
        if abstract_url:
            _append_result(
                str(payload.get("Heading", "") or "").strip() or "DuckDuckGo Abstract",
                abstract_url,
                str(payload.get("AbstractText", "") or "").strip(),
            )

        def _consume_topics(topics):
            for topic in topics:
                if len(fallback_results) >= num_results:
                    return
                if not isinstance(topic, dict):
                    continue
                if isinstance(topic.get("Topics"), list):
                    _consume_topics(topic.get("Topics", []))
                    continue
                url = str(topic.get("FirstURL", "") or "").strip()
                text = str(topic.get("Text", "") or "").strip()
                if url:
                    title = text.split(" - ")[0].strip() if text else url
                    _append_result(title, url, text)

        _consume_topics(
            payload.get("RelatedTopics", [])
            if isinstance(payload.get("RelatedTopics"), list)
            else []
        )
        return fallback_results[:num_results]

    if engine.lower() == "duckduckgo":
        results: list[dict] = []

        def _scrape_html_fallback() -> list[dict]:
            try:
                import httpx
                from bs4 import BeautifulSoup
            except ImportError:
                return []
            try:
                response = httpx.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    timeout=30.0,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
            except Exception as exc:
                logger.warning(
                    "web_search: DuckDuckGo HTML fallback failed for '%s': %s",
                    query,
                    exc,
                )
                return []
            soup = BeautifulSoup(response.text, "html.parser")
            scraped: list[dict] = []
            for i, row in enumerate(soup.select(".result")[:num_results]):
                link = row.select_one(".result__a")
                snippet = row.select_one(".result__snippet")
                href = _normalize_ddg_href(link.get("href", "")) if link else ""
                title = link.get_text(strip=True) if link else ""
                body = snippet.get_text(strip=True) if snippet else ""
                if not href and not title:
                    continue
                scraped.append(
                    {
                        "title": title,
                        "url": href,
                        "snippet": body,
                        "relevance_score": 1.0 - (i * 0.05),
                        **_candidate(href, title, SourceType.WEB, snippet=body),
                    }
                )
            return scraped

        try:
            for i, r in enumerate(_ddgs_text_search(query, max_results=num_results)):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                        "relevance_score": 1.0 - (i * 0.05),
                        **_candidate(r.get("href", ""), r.get("title", ""), SourceType.WEB,
                                     snippet=r.get("body", "")),
                    }
                )
        except ImportError:
            logger.info(
                "web_search: `ddgs` package not installed; using HTML fallback."
            )
        except Exception as e:
            logger.warning(
                "web_search: ddgs search failed for '%s': %s — trying HTML fallback.",
                query,
                e,
            )

        if not results:
            results = _scrape_html_fallback()

        if not results:
            try:
                results = _duckduckgo_instantapi_fallback()
            except Exception as e:
                logger.warning(
                    "web_search: InstantAnswer fallback failed for '%s': %s",
                    query,
                    e,
                )

        urls_used = [r.get("url", "") for r in results if r.get("url")]
        return json.dumps(
            {
                "query": query,
                "engine": "duckduckgo",
                "num_results": len(results),
                "urls_used": urls_used,
                "note": _CANDIDATE_NOTE,
                "results": results,
            },
            indent=2,
        )

    elif engine.lower() == "google":
        api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
        search_engine_id = os.environ.get("GOOGLE_SEARCH_ENGINE_ID")

        if not api_key or not search_engine_id:
            return json.dumps(
                {
                    "error": (
                        "Google Custom Search requires GOOGLE_SEARCH_API_KEY "
                        "and GOOGLE_SEARCH_ENGINE_ID environment variables"
                    ),
                    "results": [],
                }
            )

        try:
            import httpx

            url = (
                f"https://www.googleapis.com/customsearch/v1?"
                f"key={api_key}&cx={search_engine_id}&q={query}&num={num_results}"
            )
            response = httpx.get(url, timeout=30.0)
            response.raise_for_status()

            data = response.json()
            results = []
            for i, item in enumerate(data.get("items", [])):
                results.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "relevance_score": 1.0 - (i * 0.05),
                        **_candidate(item.get("link", ""), item.get("title", ""), SourceType.WEB,
                                     snippet=item.get("snippet", "")),
                    }
                )

            urls_used = [r.get("url", "") for r in results if r.get("url")]
            return json.dumps(
                {
                    "query": query,
                    "engine": "google",
                    "num_results": len(results),
                    "urls_used": urls_used,
                    "note": _CANDIDATE_NOTE,
                    "results": results,
                },
                indent=2,
            )
        except ImportError:
            return json.dumps(
                {"error": "httpx package required for Google search", "results": []}
            )
        except Exception as e:
            logger.error(f"web_search: Error searching Google for '{query}': {e}")
            return json.dumps({"error": f"Search failed: {str(e)}", "results": []})

    else:
        return json.dumps(
            {
                "error": (
                    f"Unknown search engine: {engine}. Use 'duckduckgo' or 'google'"
                ),
                "results": [],
            }
        )


@tool(
    name="web_search",
    description=(
        "Search the web using DuckDuckGo or Google Custom Search API. "
        "Returns search results with title, URL, snippet, and relevance "
        "score. Use this for research to find relevant information on "
        "the internet."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query string."},
            "engine": {
                "type": "string",
                "description": (
                    "The search engine to use. Options: 'duckduckgo' "
                    "(default) or 'google'."
                ),
                "default": "duckduckgo",
            },
            "num_results": {
                "type": "integer",
                "description": (
                    "Maximum number of results to return (default 10, max 50)."
                ),
                "default": 10,
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _web_search_tool(args: Dict[str, Any], context) -> str:
    with _bound(context):
        return web_search(
            args.get("query", ""),
            args.get("engine", "duckduckgo"),
            args.get("num_results", 10),
            context.folder_context,
        )


# ============================================================== arxiv_search

def arxiv_search(query: str, folder_context=None, max_results: int = 10, category: str = "") -> str:
    """Search arXiv for academic papers via the arXiv Atom API."""
    if max_results is None:
        max_results = 10
    max_results = min(max(1, max_results), 50)

    if not query or not query.strip():
        return json.dumps(
            {"engine": "arxiv", "error": "Query cannot be empty", "results": []}
        )

    query = query.strip()

    try:
        import httpx

        base_url = "http://export.arxiv.org/api/query"

        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        from utils.anti_detection import get_spoofed_headers

        headers = get_spoofed_headers()

        response = httpx.get(
            base_url,
            params=params,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )
        response.raise_for_status()

        root = ET.fromstring(response.content)

        namespaces = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        results = []
        for i, entry in enumerate(root.findall("atom:entry", namespaces)):
            title_elem = entry.find("atom:title", namespaces)
            summary_elem = entry.find("atom:summary", namespaces)
            published_elem = entry.find("atom:published", namespaces)
            link_elem = entry.find("atom:id", namespaces)

            authors = []
            for author in entry.findall("atom:author", namespaces):
                name_elem = author.find("atom:name", namespaces)
                if name_elem is not None:
                    authors.append(name_elem.text)

            categories = []
            for cat in entry.findall("atom:category", namespaces):
                term = cat.get("term")
                if term:
                    categories.append(term)

            pdf_link = None
            for link in entry.findall("atom:link", namespaces):
                if link.get("type") == "application/pdf":
                    pdf_link = link.get("href")
                elif link.get("title") == "pdf":
                    pdf_link = link.get("href")

            arxiv_id = (
                link_elem.text.split("/abs/")[-1]
                if link_elem is not None and link_elem.text
                else None
            )
            if not pdf_link and arxiv_id:
                pdf_link = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            results.append(
                {
                    "title": title_elem.text.strip() if title_elem is not None else "",
                    "authors": authors,
                    "abstract": (
                        summary_elem.text.strip()[:500] + "..."
                        if summary_elem is not None and summary_elem.text
                        else ""
                    ),
                    "arxiv_id": arxiv_id,
                    "categories": categories,
                    "url": link_elem.text if link_elem is not None else "",
                    "pdf_link": pdf_link,
                    "published": (
                        published_elem.text if published_elem is not None else ""
                    ),
                    "relevance_score": 1.0 - (i * 0.05),
                }
            )

        results_with_citations = []
        for result in results:
            result.update(_candidate(
                result.get("url", ""), result.get("title", ""), SourceType.ACADEMIC,
                snippet=result.get("abstract", ""), authors=result.get("authors", []),
                date=(result.get("published") or "")[:10] or None,
            ))
            results_with_citations.append(result)

        urls_used = [
            r.get("pdf_link", "") or r.get("url", "")
            for r in results_with_citations
            if r.get("pdf_link") or r.get("url")
        ]
        return json.dumps(
            {
                "query": query,
                "engine": "arxiv",
                "num_results": len(results_with_citations),
                "urls_used": urls_used,
                "note": _CANDIDATE_NOTE,
                "results": results_with_citations,
            },
            indent=2,
        )

    except ImportError:
        return json.dumps(
            {
                "error": (
                    "httpx package required for arXiv search. "
                    "Install with: pip install httpx"
                ),
                "results": [],
            }
        )
    except Exception as e:
        logger.error(f"arxiv_search: Error searching for '{query}': {e}")
        return json.dumps(
            {
                "engine": "arxiv",
                "error": f"arXiv search failed: {str(e)}",
                "results": [],
            }
        )


@tool(
    name="arxiv_search",
    description=(
        "Search arXiv for academic papers. Returns paper metadata "
        "including title, authors, abstract, arXiv ID, and PDF link. "
        "Use this for academic research to find scientific papers."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query for papers."},
            "category": {
                "type": "string",
                "description": (
                    "Optional arXiv category filter (e.g., 'cs.AI', "
                    "'physics', 'math.CO')."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": (
                    "Maximum number of results to return (default 10, max 50)."
                ),
                "default": 10,
            },
            "date_range": {
                "type": "string",
                "description": (
                    "Optional date range filter (e.g., '2023-01-01 TO 2024-01-01')."
                ),
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _arxiv_search_tool(args: Dict[str, Any], context) -> str:
    with _bound(context):
        return arxiv_search(
            args.get("query", ""),
            context.folder_context,
            args.get("max_results", 10),
            args.get("category", ""),
        )


# ============================================================== doi_resolve

def doi_resolve(doi: str, format: str = "full", folder_context=None) -> str:
    """Resolve a DOI to get metadata about the publication via CrossRef."""
    if not doi or not doi.strip():
        return json.dumps({"error": "DOI cannot be empty", "results": []})

    doi = doi.strip()

    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)

    if not re.match(r"^10\.\d{4,}/[^\s]+$", doi):
        return json.dumps(
            {
                "error": f"Invalid DOI format: {doi}. Expected format: 10.XXXX/...",
                "doi": doi,
            }
        )

    try:
        import httpx

        url = f"https://api.crossref.org/works/{doi}"

        headers = {
            "Accept": "application/json",
            "User-Agent": "Mu-CLI Research Tool (mailto:contact@example.com)",
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()

            data = response.json()
            message = data.get("message", {})

            result = {
                "doi": message.get("DOI", doi),
                "title": (
                    message.get("title", [""])[0] if message.get("title") else ""
                ),
                "authors": [
                    f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in message.get("author", [])
                ],
                "journal": (
                    message.get("container-title", [""])[0]
                    if message.get("container-title")
                    else ""
                ),
                "year": (
                    message.get("published-print", {}).get("date-parts", [[None]])[0][0]
                    or message.get("published-online", {}).get("date-parts", [[None]])[0][0]
                    or message.get("created", {}).get("date-parts", [[None]])[0][0]
                ),
                "publisher": message.get("publisher", ""),
                "type": message.get("type", ""),
                "url": message.get("URL", f"https://doi.org/{doi}"),
                "abstract": message.get("abstract", ""),
                "is_open_access": False,
            }
            ids = _candidate(
                f"https://doi.org/{result['doi']}", result["title"], SourceType.ACADEMIC,
                snippet=re.sub(r"<[^>]+>", " ", result["abstract"] or "")[:500],
                authors=result["authors"], date=str(result["year"]) if result["year"] else None,
                metadata={"journal": result["journal"], "publisher": result["publisher"]},
            )
            result.update(ids)

            if format == "citation" or format == "apa":
                authors_str = (
                    ", ".join(result["authors"][:-1])
                    + (
                        " & " + result["authors"][-1]
                        if len(result["authors"]) > 1
                        else result["authors"][0] if result["authors"] else ""
                    )
                )
                return json.dumps(
                    {
                        "citation": (
                            f'{authors_str} ({result["year"]}). {result["title"]}. '
                            f'{result["journal"]}, {result["doi"]}.'
                        ),
                        "doi": result["doi"],
                        "format": "apa",
                    },
                    indent=2,
                )
            elif format == "mla":
                author = result["authors"][0] if result["authors"] else ""
                last_first = (
                    author.split()[-1] + ", " + " ".join(author.split()[:-1])
                    if author
                    else ""
                )
                return json.dumps(
                    {
                        "citation": (
                            f'{last_first}. "{result["title"]}." '
                            f'{result["journal"]}, {result["year"]}, {result["doi"]}.'
                        ),
                        "doi": result["doi"],
                        "format": "mla",
                    },
                    indent=2,
                )
            elif format == "chicago":
                authors_str = (
                    ", ".join(result["authors"][:-1])
                    + (
                        " and " + result["authors"][-1]
                        if len(result["authors"]) > 1
                        else result["authors"][0] if result["authors"] else ""
                    )
                )
                return json.dumps(
                    {
                        "citation": (
                            f'{authors_str}. "{result["title"]}." '
                            f'{result["journal"]} ({result["year"]}): {result["doi"]}.'
                        ),
                        "doi": result["doi"],
                        "format": "chicago",
                    },
                    indent=2,
                )
            elif format == "bibtex":
                first_author = (
                    result["authors"][0].split() if result["authors"] else ["Unknown"]
                )
                cite_key = (
                    f'{first_author[-1].lower()}{result["year"] or "nodate"}'
                )
                authors_bibtex = " and ".join(result["authors"])
                bibtex = (
                    f"@article{{{cite_key},\n"
                    f"  author = {{{authors_bibtex}}},\n"
                    f'  title = {{{result["title"]}}},\n'
                    f'  journal = {{{result["journal"]}}},\n'
                    f'  year = {{{result["year"] or "n.d."}}},\n'
                    f'  doi = {{{result["doi"]}}}\n'
                    "}"
                )
                return json.dumps(
                    {"citation": bibtex, "doi": result["doi"], "format": "bibtex"},
                    indent=2,
                )

            return json.dumps(result, indent=2)

    except ImportError:
        return json.dumps(
            {
                "error": (
                    "httpx package required for DOI resolution. "
                    "Install with: pip install httpx"
                ),
                "doi": doi,
            }
        )
    except httpx.HTTPStatusError as e:
        return json.dumps(
            {
                "error": f"DOI not found: {doi}",
                "status_code": e.response.status_code,
                "doi": doi,
            }
        )
    except Exception as e:
        logger.error(f"doi_resolve: Error resolving DOI '{doi}': {e}")
        return json.dumps({"error": f"DOI resolution failed: {str(e)}", "doi": doi})


@tool(
    name="doi_resolve",
    description=(
        "Resolves a DOI (Digital Object Identifier) to retrieve "
        "publication metadata and access information. Use this to get "
        "detailed information about a specific academic paper from its DOI."
    ),
    parameters={
        "type": "object",
        "properties": {
            "doi": {
                "type": "string",
                "description": (
                    "The DOI to resolve (e.g., '10.1000/xyz123' or full "
                    "URL 'https://doi.org/10.1000/xyz123')."
                ),
            },
            "format": {
                "type": "string",
                "description": (
                    "Output format - 'full' (complete metadata) or "
                    "'citation' (formatted citation). Default is 'full'."
                ),
                "default": "full",
            },
        },
        "required": ["doi"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _doi_resolve_tool(args: Dict[str, Any], context) -> str:
    with _bound(context):
        return doi_resolve(
            args.get("doi", ""),
            args.get("format", "json"),
            context.folder_context,
        )


# ============================================================== reddit_search

def reddit_search(
    query: str,
    subreddit: str = None,
    sort: str = "relevance",
    limit: int = 10,
    folder_context=None,
) -> str:
    """Searches Reddit for posts and comments using Reddit's JSON API."""
    if limit is None:
        limit = 10

    base_url = "https://old.reddit.com"

    if subreddit:
        search_url = (
            f"{base_url}/r/{subreddit}/search.json?q={quote(query)}"
            f"&restrict_sr=on&sort={sort}&limit={limit}"
        )
    else:
        search_url = f"{base_url}/search.json?q={quote(query)}&sort={sort}&limit={limit}"

    try:
        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(search_url, headers=headers)
            response.raise_for_status()
            data = response.json()

        results = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            ids = _candidate(
                f"https://reddit.com{post.get('permalink', '')}", post.get("title", ""),
                SourceType.SOCIAL, snippet=(post.get("selftext") or "")[:500],
            )
            results.append(
                {
                    "title": post.get("title", ""),
                    "author": post.get("author", "[deleted]"),
                    "subreddit": post.get("subreddit", ""),
                    "score": post.get("score", 0),
                    "upvote_ratio": post.get("upvote_ratio", 0),
                    "num_comments": post.get("num_comments", 0),
                    "url": f"https://reddit.com{post.get('permalink', '')}",
                    "created_utc": post.get("created_utc", 0),
                    "selftext": (
                        post.get("selftext", "")[:500] if post.get("selftext") else ""
                    ),
                    **ids,
                    "link_flair_text": post.get("link_flair_text"),
                    "is_video": post.get("is_video", False),
                }
            )

        urls_used = [r.get("url", "") for r in results if r.get("url")]
        num_results = len(results)

        return json.dumps(
            {
                "query": query,
                "subreddit": subreddit,
                "sort": sort,
                "count": len(results),
                "num_results": num_results,
                "urls_used": urls_used,
                "total_results": len(results),
                "note": _CANDIDATE_NOTE,
                "results": results,
            },
            indent=2,
        )

    except ImportError:
        return json.dumps(
            {
                "error": (
                    "httpx package required for Reddit search. "
                    "Install with: pip install httpx"
                ),
                "query": query,
            }
        )
    except httpx.HTTPStatusError as e:
        return json.dumps(
            {
                "error": f"Reddit search failed: HTTP {e.response.status_code}",
                "query": query,
            }
        )
    except Exception as e:
        logger.error(f"reddit_search: Error searching Reddit for '{query}': {e}")
        return json.dumps(
            {"error": f"Reddit search failed: {str(e)}", "query": query}
        )


@tool(
    name="reddit_search",
    description=(
        "Searches Reddit for relevant discussions and posts. Use this "
        "for finding community opinions, discussions, and user-generated "
        "content on various topics."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string to find relevant Reddit posts.",
            },
            "subreddit": {
                "type": "string",
                "description": (
                    "Optional subreddit to limit the search to (e.g., "
                    "'programming', 'MachineLearning')."
                ),
            },
            "num_results": {
                "type": "integer",
                "description": (
                    "Maximum number of results to return (default 10, max 50)."
                ),
                "default": 10,
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _reddit_search_tool(args: Dict[str, Any], context) -> str:
    with _bound(context):
        return reddit_search(
            args.get("query", ""),
            subreddit=args.get("subreddit"),
            sort=args.get("sort", "relevance"),
            limit=args.get("num_results", args.get("max_results", 10)),
            folder_context=context.folder_context,
        )


# ============================================================== stackoverflow_search

def stackoverflow_search(
    query: str,
    tags: list = None,
    sort: str = "relevance",
    limit: int = 10,
    folder_context=None,
) -> str:
    """Searches Stack Overflow for questions using the Stack Exchange API."""
    if not str(query or "").strip():
        return json.dumps(
            {"error": "query is required", "query": query, "results": []}
        )

    query = query.strip()

    if limit is None:
        limit = 10

    base_url = "https://api.stackexchange.com/2.3/search/advanced"

    params = {
        "order": "desc",
        "sort": sort,
        "q": query,
        "site": "stackoverflow",
        "pagesize": limit,
        "filter": "withbody",
    }

    if tags:
        params["tagged"] = ";".join(tags)

    try:
        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            ),
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }

        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(base_url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("items", []):
            ids = _candidate(item.get("link", ""), item.get("title", ""), SourceType.FORUM,
                             snippet=re.sub(r"<[^>]+>", " ", item.get("body") or "")[:500])
            result = {
                "title": item.get("title", ""),
                "question_id": item.get("question_id"),
                "link": item.get("link", ""),
                "score": item.get("score", 0),
                "answer_count": item.get("answer_count", 0),
                "is_answered": item.get("is_answered", False),
                "view_count": item.get("view_count", 0),
                "tags": item.get("tags", []),
                **ids,
                "body": item.get("body", "")[:500] if item.get("body") else "",
                "creation_date": item.get("creation_date", 0),
                "last_activity_date": item.get("last_activity_date", 0),
                "owner": {
                    "display_name": item.get("owner", {}).get("display_name", ""),
                    "reputation": item.get("owner", {}).get("reputation", 0),
                },
            }
            results.append(result)

        urls_used = [r.get("link", "") for r in results if r.get("link")]

        return json.dumps(
            {
                "query": query,
                "tags": tags,
                "sort": sort,
                "count": len(results),
                "total_results": len(results),
                "urls_used": urls_used,
                "has_more": data.get("has_more", False),
                "note": _CANDIDATE_NOTE,
                "results": results,
            },
            indent=2,
        )

    except ImportError:
        return json.dumps(
            {
                "error": (
                    "httpx package required for Stack Overflow search. "
                    "Install with: pip install httpx"
                ),
                "query": query,
            }
        )
    except httpx.HTTPStatusError as e:
        return json.dumps(
            {
                "error": f"Stack Overflow search failed: HTTP {e.response.status_code}",
                "query": query,
            }
        )
    except Exception as e:
        logger.error(
            f"stackoverflow_search: Error searching Stack Overflow for '{query}': {e}"
        )
        return json.dumps(
            {"error": f"Stack Overflow search failed: {str(e)}", "query": query}
        )


@tool(
    name="stackoverflow_search",
    description=(
        "Searches Stack Overflow for relevant questions and answers "
        "using the Stack Exchange API. Use this for finding programming "
        "solutions, debugging help, and technical discussions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search query string to find relevant Stack "
                    "Overflow questions."
                ),
            },
            "tag": {
                "type": "string",
                "description": (
                    "Optional tag to filter results (e.g., 'python', 'javascript')."
                ),
            },
            "num_results": {
                "type": "integer",
                "description": (
                    "Maximum number of results to return (default 10, max 50)."
                ),
                "default": 10,
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _stackoverflow_search_tool(args: Dict[str, Any], context) -> str:
    with _bound(context):
        tags = args.get("tags")
        if tags is None and args.get("tag"):
            tags = [args.get("tag")]
        return stackoverflow_search(
            args.get("query", ""),
            tags=tags,
            sort=args.get("sort", "relevance"),
            limit=args.get("num_results", args.get("max_results", 10)),
            folder_context=context.folder_context,
        )


# ============================================================== hackernews_search

def hackernews_search(
    query: str,
    sort: str = "relevance",
    num_results: int = 10,
    folder_context=None,
) -> str:
    """Searches Hacker News via the Algolia HN API."""
    if not query or not query.strip():
        return json.dumps(
            {"error": "Query is required for Hacker News search", "query": query}
        )

    query = query.strip()
    num_results = min(max(1, num_results), 50)

    if sort not in ["relevance", "date"]:
        sort = "relevance"

    try:
        import httpx

        base_url = "https://hn.algolia.com/api/v1"
        endpoint = "search" if sort == "relevance" else "search_by_date"
        url = f"{base_url}/{endpoint}"

        params = {
            "query": query,
            "hitsPerPage": num_results,
            "tags": "story",
        }

        headers = {
            "User-Agent": "Mu-CLI Research Tool",
            "Accept": "application/json",
        }

        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        results = []
        for hit in data.get("hits", []):
            ids = _candidate(
                hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
                hit.get("title", ""), SourceType.NEWS,
                snippet=(hit.get("story_text") or "")[:500],
            )
            result = {
                "title": hit.get("title", ""),
                "url": hit.get("url", ""),
                "author": hit.get("author", ""),
                "points": hit.get("points", 0),
                "num_comments": hit.get("num_comments", 0),
                "objectID": hit.get("objectID", ""),
                "created_at": hit.get("created_at", ""),
                "story_text": (
                    hit.get("story_text", "")[:500] if hit.get("story_text") else ""
                ),
                **ids,
            }
            results.append(result)

        urls_used = [r.get("url", "") for r in results if r.get("url")]

        return json.dumps(
            {
                "query": query,
                "sort": sort,
                "count": len(results),
                "total_results": len(results),
                "urls_used": urls_used,
                "note": _CANDIDATE_NOTE,
                "results": results,
            },
            indent=2,
        )

    except ImportError:
        return json.dumps(
            {
                "error": (
                    "httpx package required for Hacker News search. "
                    "Install with: pip install httpx"
                ),
                "query": query,
            }
        )
    except httpx.HTTPStatusError as e:
        return json.dumps(
            {
                "error": f"Hacker News search failed: HTTP {e.response.status_code}",
                "query": query,
            }
        )
    except Exception as e:
        logger.error(f"hackernews_search: Error searching Hacker News for '{query}': {e}")
        return json.dumps(
            {"error": f"Hacker News search failed: {str(e)}", "query": query}
        )


@tool(
    name="hackernews_search",
    description=(
        "Searches Hacker News for relevant stories and discussions "
        "using the Algolia HN API. Use this for finding tech news, "
        "startup discussions, and community insights from the Hacker "
        "News community."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search query string to find relevant Hacker News stories."
                ),
            },
            "sort": {
                "type": "string",
                "description": (
                    "Sort order: 'relevance' (default) or 'date' for "
                    "chronological order."
                ),
                "enum": ["relevance", "date"],
            },
            "num_results": {
                "type": "integer",
                "description": (
                    "Maximum number of results to return (default 10, max 50)."
                ),
                "default": 10,
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _hackernews_search_tool(args: Dict[str, Any], context) -> str:
    with _bound(context):
        return hackernews_search(
            args.get("query", ""),
            sort=args.get("sort", "relevance"),
            num_results=args.get("num_results", args.get("max_results", 10)),
            folder_context=context.folder_context,
        )


# ============================================================== read_document

def _looks_like_url(target: str) -> bool:
    target = (target or "").strip().lower()
    return target.startswith("http://") or target.startswith("https://")


def _extract_pdf_text(reader) -> str:
    """Extracted text with a ``## Page N`` heading per page (empty pages skipped).

    The headings make each page its own passage, so the page number is the
    locator for evidence quoted from it.
    """
    chunks = []
    for number, page in enumerate(reader.pages, start=1):
        extracted = page.extract_text()
        if extracted and extracted.strip():
            chunks.append(f"## Page {number}\n\n{extracted.strip()}")
    return "\n\n".join(chunks)


def _pdf_metadata(reader) -> Dict[str, Any]:
    """Best-effort title/author/date from the PDF ``/Info`` dict."""
    try:
        info = reader.metadata
    except Exception:
        return {}
    if not info:
        return {}
    out: Dict[str, Any] = {}
    title = getattr(info, "title", None) or info.get("/Title", "")  # type: ignore[union-attr]
    if str(title or "").strip():
        out["title"] = str(title).strip()
    author = getattr(info, "author", None) or info.get("/Author", "")  # type: ignore[union-attr]
    if str(author or "").strip():
        out["authors"] = [a.strip() for a in re.split(r";|,| and ", str(author)) if a.strip()][:10]
    created = str(info.get("/CreationDate", "") or "")  # type: ignore[union-attr]
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?", created)
    if m:
        out["date"] = "-".join(g for g in m.groups() if g)
    return out


def _open_pdf(data) -> Any:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data)
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:
            raise FetchError("PDF is encrypted and cannot be parsed without a password") from exc
    return reader


def _read_pdf_bytes(body: bytes, url: str, *, query: Optional[str], max_chars: int,
                    state: ResearchState, context=None) -> str:
    try:
        reader = _open_pdf(body)
        text = _extract_pdf_text(reader)
    except FetchError:
        raise
    except ImportError as exc:
        raise FetchError("pypdf is not installed; cannot parse PDF files") from exc
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"could not parse PDF: {exc}") from exc
    if not text.strip():
        raise FetchError(
            f"PDF fetched ({len(body):,} bytes) but no text was extractable; it may be a "
            "scanned/image-only PDF (OCR not supported)"
        )
    meta = _pdf_metadata(reader)
    ref = _store_full_text(context, text, url)
    source = state.mark_read(
        url=url, title=meta.get("title") or url,
        source_type=SourceType.ACADEMIC if "arxiv.org" in url.lower() else SourceType.DOCUMENTATION,
        text=text, authors=meta.get("authors"), date=meta.get("date"),
        metadata={"extraction": "pypdf", "pages": len(reader.pages)}, result_ref=ref,
    )
    return _format_read(source, text, query, max_chars, method="pypdf",
                        locator_hint="; use the page heading (e.g. 'Page 3') as locator")


def _read_pdf_from_url(url: str, *, query: Optional[str] = None, max_chars: int = DEFAULT_MAX_CHARS,
                       context=None) -> str:
    """Fetch a PDF by URL, extract text, register the source, return the read block."""
    state = _engine(context)
    try:
        fetched = _fetch(url, timeout=60.0)
        body = fetched["body"]
        ctype = fetched["content_type"]
        if "pdf" not in ctype and not body.startswith(b"%PDF-"):
            return (
                f"Error: URL did not return a PDF (content-type={ctype!r}, first bytes="
                f"{body[:8]!r}). Use url_grounding for HTML pages."
            )
        query = (query or "").strip() or _default_query(state)
        return _read_pdf_bytes(body, url, query=query, max_chars=_clamp_chars(max_chars),
                               state=state, context=context)
    except (FetchError, ResearchError) as exc:
        return f"Error reading PDF {url}: {exc}"


def read_document(filename: str, folder_context, *, session_type: str = "workspace",
                  query: Optional[str] = None, max_chars: int = DEFAULT_MAX_CHARS, context=None) -> str:
    """Read a document (PDF by path or URL, other files via read_file).

    PDFs are recorded as research sources and come back as the pages most
    relevant to ``query`` within ``max_chars``; the full text stays reachable
    through ``result_ref``.
    """
    target = str(filename or "").strip()
    if _looks_like_url(target):
        return _read_pdf_from_url(target, query=query, max_chars=max_chars, context=context)

    if not _check_bounds(filename, folder_context, session_type=session_type):
        logger.warning(f"read_document: Access denied or file ignored: {filename}")
        return f"Error: Access denied or file ignored. '{filename}'"

    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        state = _engine(context)
        path = os.path.realpath(os.path.expanduser(filename))
        try:
            with open(path, "rb") as fh:
                body = fh.read(FETCH_MAX_BYTES * 4)
            query = (query or "").strip() or _default_query(state)
            return _read_pdf_bytes(body, "file://" + path, query=query,
                                   max_chars=_clamp_chars(max_chars), state=state, context=context)
        except FetchError as exc:
            return f"Error reading PDF: {exc}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"read_document: Error reading PDF {filename}: {e}")
            return f"Error reading PDF: {e}"

    # Defer to read_file for non-PDF documents.
    from mu.tools.workspace.handlers import read_file

    return read_file(filename, folder_context, session_type=session_type)


@tool(
    name="read_document",
    description=(
        "Read a document such as a PDF, by local path or http(s) URL (no need to download "
        "first). PDFs are recorded as research sources with a citation number and return the "
        "pages most relevant to `query` within max_chars; the full text stays retrievable via "
        "result_ref. Non-PDF files are read like read_file."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Either a local path to the document or an http(s) "
                    "URL (e.g. 'https://arxiv.org/pdf/2301.12345.pdf')."
                ),
            },
            "query": {"type": "string", "description": "What you are looking for; ranks the pages returned."},
            "max_chars": {"type": "integer", "description": "Inline budget (default 6000, 800–20000)."},
        },
        "required": ["filename"],
    },
    requires_approval=False,
    phase="research",
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _read_document_tool(args: Dict[str, Any], context) -> str:
    with _bound(context):
        return read_document(
            args.get("filename", ""),
            context.folder_context,
            session_type=session_type_from_context(context),
            query=args.get("query"),
            max_chars=args.get("max_chars", DEFAULT_MAX_CHARS),
            context=context,
        )
