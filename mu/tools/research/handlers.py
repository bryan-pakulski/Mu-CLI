"""Research tools: web/forum/Q&A search + URL/document fetching.

All eight are read-only; underlying implementations live in
`core/tools.py`. Each registers a `structured+collated` result mode so
tool output flows through the collation buffer (matching legacy).
"""

from typing import Any, Dict

from mu.tools import tool


@tool(
    name="url_grounding",
    description=(
        "Accesses a URL to gather additional context. Supports "
        "JavaScript-heavy websites."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to access.",
            }
        },
        "required": ["url"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
    result_mode="structured+collated",
)
def url_grounding(args: Dict[str, Any], context) -> str:
    from core.tools import url_grounding as _impl

    return _impl(args.get("url", ""), context.folder_context)


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
            "query": {
                "type": "string",
                "description": "The search query string.",
            },
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
def web_search(args: Dict[str, Any], context) -> str:
    from core.tools import web_search as _impl

    return _impl(
        args.get("query", ""),
        args.get("engine", "duckduckgo"),
        args.get("num_results", 10),
        context.folder_context,
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
            "query": {
                "type": "string",
                "description": "The search query for papers.",
            },
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
def arxiv_search(args: Dict[str, Any], context) -> str:
    from core.tools import arxiv_search as _impl

    return _impl(
        args.get("query", ""),
        context.folder_context,
        args.get("max_results", 10),
        args.get("category", ""),
    )


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
def doi_resolve(args: Dict[str, Any], context) -> str:
    from core.tools import doi_resolve as _impl

    return _impl(
        args.get("doi", ""),
        args.get("format", "json"),
        context.folder_context,
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
def reddit_search(args: Dict[str, Any], context) -> str:
    from core.tools import reddit_search as _impl

    return _impl(
        args.get("query", ""),
        subreddit=args.get("subreddit"),
        sort=args.get("sort", "relevance"),
        limit=args.get("num_results", args.get("max_results", 10)),
        folder_context=context.folder_context,
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
def stackoverflow_search(args: Dict[str, Any], context) -> str:
    from core.tools import stackoverflow_search as _impl

    tags = args.get("tags")
    if tags is None and args.get("tag"):
        tags = [args.get("tag")]
    return _impl(
        args.get("query", ""),
        tags=tags,
        sort=args.get("sort", "relevance"),
        limit=args.get("num_results", args.get("max_results", 10)),
        folder_context=context.folder_context,
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
                    "The search query string to find relevant Hacker "
                    "News stories."
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
def hackernews_search(args: Dict[str, Any], context) -> str:
    from core.tools import hackernews_search as _impl

    return _impl(
        args.get("query", ""),
        sort=args.get("sort", "relevance"),
        num_results=args.get("num_results", args.get("max_results", 10)),
        folder_context=context.folder_context,
    )


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
def read_document(args: Dict[str, Any], context) -> str:
    from core.tools import read_document as _impl

    return _impl(args.get("filename", ""), context.folder_context)
