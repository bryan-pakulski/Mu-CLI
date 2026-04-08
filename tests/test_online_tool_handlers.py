import json

from core.workspace import FolderContext


def test_reddit_handler_argument_wiring(monkeypatch):
    import core.tools as tools

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
    ctx = FolderContext()

    tools._handle_reddit_search({"query": "python", "subreddit": "learnpython", "sort": "new", "num_results": 7}, ctx, None, None)

    assert captured["query"] == "python"
    assert captured["subreddit"] == "learnpython"
    assert captured["sort"] == "new"
    assert captured["limit"] == 7
    assert captured["folder_context"] is ctx


def test_stackoverflow_handler_argument_wiring(monkeypatch):
    import core.tools as tools

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
    ctx = FolderContext()

    tools._handle_stackoverflow_search({"query": "async io", "tag": "python", "sort": "votes", "num_results": 4}, ctx, None, None)

    assert captured["query"] == "async io"
    assert captured["tags"] == ["python"]
    assert captured["sort"] == "votes"
    assert captured["limit"] == 4
    assert captured["folder_context"] is ctx


def test_hackernews_handler_argument_wiring(monkeypatch):
    import core.tools as tools

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
    ctx = FolderContext()

    tools._handle_hackernews_search({"query": "startup", "sort": "date", "num_results": 3}, ctx, None, None)

    assert captured["query"] == "startup"
    assert captured["sort"] == "date"
    assert captured["num_results"] == 3
    assert captured["folder_context"] is ctx
