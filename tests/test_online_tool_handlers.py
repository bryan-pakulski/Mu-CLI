"""Argument-wiring tests for the research-tool handlers.

These pin the small bits of arg coercion the handlers do (e.g. mapping
`tag` → `tags=[tag]`, defaulting `sort`, falling back from `num_results`
to `max_results`). After the Phase 1.5 migration the handlers live in
`mu/tools/research/handlers.py` and are exercised through
`mu.tools.execute`; the impls are monkey-patched on `mu.tools` so we
see the args the handler actually forwarded.
"""

import json

import mu.tools as _mu_tools
from mu.workspace.folder_context import FolderContext


def _ctx(folder_context):
    return _mu_tools.build_tool_context(
        folder_context=folder_context, ui=None, variables={}, session=None
    )


def test_reddit_handler_argument_wiring(monkeypatch):
    import mu.tools.research.handlers as tools

    captured = {}

    def fake_reddit_search(query, subreddit=None, sort="relevance", limit=10, folder_context=None):
        captured.update({
            "query": query,
            "subreddit": subreddit,
            "sort": sort,
            "limit": limit,
            "folder_context": folder_context,
        })
        return json.dumps({"ok": True})

    monkeypatch.setattr(tools, "reddit_search", fake_reddit_search)
    fc = FolderContext()

    _mu_tools.execute(
        "reddit_search",
        {"query": "python", "subreddit": "learnpython", "sort": "new", "num_results": 7},
        _ctx(fc),
    )

    assert captured["query"] == "python"
    assert captured["subreddit"] == "learnpython"
    assert captured["sort"] == "new"
    assert captured["limit"] == 7
    assert captured["folder_context"] is fc


def test_stackoverflow_handler_argument_wiring(monkeypatch):
    import mu.tools.research.handlers as tools

    captured = {}

    def fake_stackoverflow_search(query, tags=None, sort="relevance", limit=10, folder_context=None):
        captured.update({
            "query": query,
            "tags": tags,
            "sort": sort,
            "limit": limit,
            "folder_context": folder_context,
        })
        return json.dumps({"ok": True})

    monkeypatch.setattr(tools, "stackoverflow_search", fake_stackoverflow_search)
    fc = FolderContext()

    _mu_tools.execute(
        "stackoverflow_search",
        {"query": "async io", "tag": "python", "sort": "votes", "num_results": 4},
        _ctx(fc),
    )

    assert captured["query"] == "async io"
    assert captured["tags"] == ["python"]
    assert captured["sort"] == "votes"
    assert captured["limit"] == 4
    assert captured["folder_context"] is fc


def test_hackernews_handler_argument_wiring(monkeypatch):
    import mu.tools.research.handlers as tools

    captured = {}

    def fake_hackernews_search(query, sort="relevance", num_results=10, folder_context=None):
        captured.update({
            "query": query,
            "sort": sort,
            "num_results": num_results,
            "folder_context": folder_context,
        })
        return json.dumps({"ok": True})

    monkeypatch.setattr(tools, "hackernews_search", fake_hackernews_search)
    fc = FolderContext()

    _mu_tools.execute(
        "hackernews_search",
        {"query": "startup", "sort": "date", "num_results": 3},
        _ctx(fc),
    )

    assert captured["query"] == "startup"
    assert captured["sort"] == "date"
    assert captured["num_results"] == 3
    assert captured["folder_context"] is fc
