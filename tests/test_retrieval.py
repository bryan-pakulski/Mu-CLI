import json
import time

from mu.retrieval.index import SemanticCodeIndex
from mu.tools._dispatcher import execute_tool
from mu.workspace.folder_context import FolderContext


def _build_synthetic_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        "def authenticate_user(token):\n"
        "    return token == 'ok'\n"
        "\n"
        "def refresh_session(user_id):\n"
        "    return {'user_id': user_id}\n"
    )
    (src / "billing.py").write_text(
        "def charge_card(amount_cents, card_token):\n"
        "    return {'charged': amount_cents, 'token': card_token}\n"
    )
    (src / "search.py").write_text(
        "def rank_documents(query, docs):\n"
        "    return sorted(docs)\n"
    )
    return src


def test_semantic_retrieval_ranking_and_precision(tmp_path):
    _build_synthetic_repo(tmp_path)
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    index = SemanticCodeIndex()
    index.build(ctx)

    # Golden query fixture over synthetic repo.
    query = "how is user authentication token checked"
    result = index.retrieve(query, top_k=3, filters={})
    paths = [item["path"] for item in result["results"]]
    assert any(path.endswith("auth.py") for path in paths)
    # Precision@1 baseline for this seeded query should hit auth.py
    assert result["results"][0]["path"].endswith("auth.py")


def test_incremental_index_refresh_detects_file_changes(tmp_path):
    src = _build_synthetic_repo(tmp_path)
    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    index = SemanticCodeIndex()
    index.build(ctx)
    before = index.retrieve("charge card", top_k=1, filters={})
    assert before["results"][0]["path"].endswith("billing.py")

    billing = src / "billing.py"
    billing.write_text(
        "def charge_card(amount_cents, card_token):\n"
        "    return {'charged': amount_cents}\n"
        "\n"
        "def refund_payment(payment_id):\n"
        "    return {'refund': payment_id}\n"
    )

    t0 = time.perf_counter()
    index.refresh_incremental(ctx)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 2000

    after = index.retrieve("refund payment", top_k=2, filters={})
    assert after["results"][0]["path"].endswith("billing.py")
    assert after["latency_ms"] < 2000


def test_retrieve_relevant_context_filters_extensions(tmp_path):
    _build_synthetic_repo(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "notes.md").write_text("# auth\nrotating token design\n")

    ctx = FolderContext()
    ctx.add_folder(str(tmp_path))

    raw = execute_tool(
        "retrieve_relevant_context",
        {"query": "auth token", "top_k": 5, "filters": {"extensions": [".md"]}},
        ctx,
    )
    payload = json.loads(raw)
    assert payload["count"] >= 1
    assert all(item["path"].endswith(".md") for item in payload["results"])
