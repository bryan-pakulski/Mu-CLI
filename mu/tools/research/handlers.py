"""Research tools: web/forum/Q&A search + URL/document fetching.

All eight are read-only and registered with `structured+collated` result
mode so tool output flows through the collation buffer.

Each tool is a positional-signature body (publicly callable, easy to
test) plus a thin `_<name>_tool(args, context)` wrapper that carries
the `@tool` registration.
"""

from __future__ import annotations

import io
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict
from urllib.parse import quote

from mu.tools import tool
from mu.tools._bounds import check_bounds as _check_bounds
from utils.citation_manager import SourceType, register_source
from utils.logger import logger


# ============================================================== url_grounding

def url_grounding(url: str, folder_context) -> str:
    """Accesses a URL to gather additional context. Supports JavaScript-heavy websites."""
    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:
                logger.info(
                    "url_grounding: chromium launch failed (%s); falling back to httpx.",
                    exc,
                )
                raise

            page = browser.new_page()
            page.goto(url, wait_until="networkidle")

            content = page.content()
            browser.close()

            soup = BeautifulSoup(content, "html.parser")

            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()

            text = soup.get_text(separator="\n")

            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)

            citation_id = register_source(url=url, title=url, source_type=SourceType.WEB)
            return f"{text}\n\n---\nCitation: [^{citation_id}]"

    except (ImportError, Exception):
        try:
            import httpx
            from bs4 import BeautifulSoup

            response = httpx.get(url, follow_redirects=True, timeout=30.0)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()

            text = soup.get_text(separator="\n")
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)

            citation_id = register_source(url=url, title=url, source_type=SourceType.WEB)
            return (
                "(Note: Playwright not installed or failed, JS-heavy content might be missing)"
                f"\n\n{text}\n\n---\nCitation: [^{citation_id}]"
            )
        except Exception as e:
            return f"Error accessing URL: {e}"


@tool(
    name="url_grounding",
    description=(
        "Accesses a URL to gather additional context. Supports "
        "JavaScript-heavy websites."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to access."}
        },
        "required": ["url"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _url_grounding_tool(args: Dict[str, Any], context) -> str:
    return url_grounding(args.get("url", ""), context.folder_context)


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

    thread = threading.Thread(target=_runner, daemon=True, name=label)
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
                    "citation_id": register_source(
                        title=title or url,
                        url=url,
                        source_type="web",
                    ),
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
                href = link.get("href", "") if link else ""
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
                        "citation_id": register_source(
                            title=title, url=href, source_type="web"
                        ),
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
                        "citation_id": register_source(
                            title=r.get("title", ""),
                            url=r.get("href", ""),
                            source_type="web",
                        ),
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
                        "citation_id": register_source(
                            title=item.get("title", ""),
                            url=item.get("link", ""),
                            source_type="web",
                        ),
                    }
                )

            urls_used = [r.get("url", "") for r in results if r.get("url")]
            return json.dumps(
                {
                    "query": query,
                    "engine": "google",
                    "num_results": len(results),
                    "urls_used": urls_used,
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
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _web_search_tool(args: Dict[str, Any], context) -> str:
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
            citation_id = register_source(
                url=result.get("url", ""),
                title=result.get("title", ""),
                # arxiv papers are academic; SourceType has no ARXIV member.
                source_type=SourceType.ACADEMIC,
                authors=result.get("authors", []),
                date=result.get("published", ""),
            )
            result["citation_id"] = citation_id
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
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _arxiv_search_tool(args: Dict[str, Any], context) -> str:
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
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _doi_resolve_tool(args: Dict[str, Any], context) -> str:
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
    if not _check_bounds(query, folder_context):
        logger.warning(f"reddit_search: Access denied for query: {query}")
        return json.dumps({"error": "Access denied"})

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
            citation_id = register_source(
                url=f"https://reddit.com{post.get('permalink', '')}",
                title=post.get("title", ""),
                source_type=SourceType.SOCIAL,
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
                    "citation_id": citation_id,
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
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _reddit_search_tool(args: Dict[str, Any], context) -> str:
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
            citation_id = register_source(
                url=item.get("link", ""),
                title=item.get("title", ""),
                source_type=SourceType.FORUM,
            )
            result = {
                "title": item.get("title", ""),
                "question_id": item.get("question_id"),
                "link": item.get("link", ""),
                "score": item.get("score", 0),
                "answer_count": item.get("answer_count", 0),
                "is_answered": item.get("is_answered", False),
                "view_count": item.get("view_count", 0),
                "tags": item.get("tags", []),
                "citation_id": citation_id,
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
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _stackoverflow_search_tool(args: Dict[str, Any], context) -> str:
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
            citation_id = register_source(
                url=hit.get("url", ""),
                title=hit.get("title", ""),
                source_type=SourceType.NEWS,
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
                "citation_id": citation_id,
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
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _hackernews_search_tool(args: Dict[str, Any], context) -> str:
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
    """Concatenate every page's extracted text. Empty pages skipped."""
    chunks = []
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            chunks.append(extracted)
    return "\n".join(chunks)


def _pdf_metadata_title(reader) -> str:
    """Best-effort title pulled from the PDF's `/Info` dict. Returns ''
    if not present so the caller can fall back to the URL."""
    try:
        info = reader.metadata
    except Exception:
        return ""
    if not info:
        return ""
    title = getattr(info, "title", None) or info.get("/Title", "")  # type: ignore[union-attr]
    return str(title or "").strip()


def _read_pdf_from_url(url: str) -> str:
    """Fetch a PDF, extract text, register the source, return text + citation."""
    try:
        import httpx
    except ImportError:
        return "Error: 'httpx' not installed. Cannot fetch PDFs by URL."
    try:
        from pypdf import PdfReader
    except ImportError:
        return "Error: 'pypdf' not installed. Cannot parse PDF files."

    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=60.0,
            headers={"User-Agent": "Mozilla/5.0 (mucli read_document)"},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return f"Error fetching PDF: HTTP {exc.response.status_code} for {url}"
    except httpx.HTTPError as exc:
        return f"Error fetching PDF: {exc}"
    except Exception as exc:
        logger.error("read_document: fetch failed for %s: %s", url, exc)
        return f"Error fetching PDF: {exc}"

    content_type = (response.headers.get("content-type") or "").lower()
    body = response.content
    looks_pdf = body.startswith(b"%PDF-")
    if "pdf" not in content_type and not looks_pdf:
        return (
            f"Error: URL did not return a PDF (content-type={content_type!r}, "
            f"first bytes={body[:8]!r}). Use url_grounding for HTML pages."
        )

    try:
        reader = PdfReader(io.BytesIO(body))
    except Exception as exc:
        return f"Error parsing PDF: {exc}"

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:
            return "Error: PDF is encrypted and cannot be parsed without a password."

    try:
        text = _extract_pdf_text(reader)
    except Exception as exc:
        return f"Error extracting PDF text: {exc}"

    citation_footer = ""
    try:
        title = _pdf_metadata_title(reader) or url
        citation_id = register_source(
            title=title,
            url=url,
            source_type=(
                SourceType.ACADEMIC
                if "arxiv.org" in url.lower()
                else SourceType.DOCUMENTATION
            ),
        )
        citation_footer = f"\n\n---\nCitation: [^{citation_id}]\nSource: {url}"
    except Exception:
        logger.debug("read_document: citation registration failed", exc_info=True)

    if not text.strip():
        return (
            f"PDF fetched ({len(body):,} bytes) but no text was extractable. "
            "It may be a scanned/image-only PDF (OCR not supported)."
            + citation_footer
        )

    return text + citation_footer


def read_document(filename: str, folder_context) -> str:
    """Reads and parses documents like PDFs to gather additional context.

    Accepts either a local path (subject to workspace bounds) or an
    http(s) URL — URLs skip the curl+download dance and go straight
    through httpx + pypdf, with the source auto-registered in the
    citation engine on success.
    """
    target = str(filename or "").strip()
    if _looks_like_url(target):
        return _read_pdf_from_url(target)

    if not _check_bounds(filename, folder_context):
        logger.warning(f"read_document: Access denied or file ignored: {filename}")
        return f"Error: Access denied or file ignored. '{filename}'"

    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(filename)
            text = _extract_pdf_text(reader)
            return text
        except ImportError:
            logger.error("read_document: 'pypdf' not installed.")
            return (
                "Error: 'pypdf' is not installed. "
                "Please install it to parse PDF files."
            )
        except Exception as e:
            logger.error(f"read_document: Error reading PDF {filename}: {e}")
            return f"Error reading PDF: {e}"

    # Defer to read_file for non-PDF documents.
    from mu.tools.workspace.handlers import read_file

    return read_file(filename, folder_context)


@tool(
    name="read_document",
    description=(
        "Reads and parses documents like PDFs to gather additional "
        "context. Accepts either a local filesystem path OR an http(s) "
        "URL — URLs are fetched directly (no need to curl/download "
        "first) and any successful URL fetch is auto-registered in the "
        "citation engine."
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
            }
        },
        "required": ["filename"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def _read_document_tool(args: Dict[str, Any], context) -> str:
    return read_document(args.get("filename", ""), context.folder_context)
