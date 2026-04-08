import json

from evals.harness import (
    DEFAULT_CORPUS_PATH,
    evaluate_slos,
    load_swebench_task_corpus,
    load_task_corpus,
    replay_tasks_deterministically,
    run,
    summarize,
)


def test_eval_harness_is_deterministic():
    tasks = load_task_corpus(DEFAULT_CORPUS_PATH)
    first = replay_tasks_deterministically(tasks, seed=2026)
    second = replay_tasks_deterministically(tasks, seed=2026)
    assert first == second


def test_eval_harness_generates_artifacts(tmp_path):
    output = tmp_path / "eval_run_20260408.json"
    trend = tmp_path / "trend_report.md"
    digest = tmp_path / "digest.md"
    payload = run(
        seed=1337,
        corpus_path=DEFAULT_CORPUS_PATH,
        output_path=output,
        trend_path=trend,
        digest_path=digest,
    )

    assert output.exists()
    assert trend.exists()
    assert digest.exists()

    on_disk = json.loads(output.read_text(encoding="utf-8"))
    assert on_disk["summary"]["total_tasks"] >= 4
    assert "unsafe_action_rate" in on_disk["summary"]
    assert isinstance(payload["slo_results"], dict)
    assert "MuCLI Eval Run Digest" in digest.read_text(encoding="utf-8")


def test_eval_harness_slo_evaluation_shape():
    tasks = load_task_corpus(DEFAULT_CORPUS_PATH)
    records = replay_tasks_deterministically(tasks, seed=11)
    summary = summarize(records, tasks, seed=11)
    status = evaluate_slos(summary)
    assert set(status.keys()) == {"fix_rate", "token_usage", "unsafe_action_rate"}


def test_swebench_corpus_loader_supports_jsonl(tmp_path):
    jsonl = tmp_path / "swebench-sample.jsonl"
    jsonl.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "instance_id": "django__django-10001",
                        "repo": "django/django",
                        "problem_statement": "Fix queryset annotation regression.",
                        "hints_text": "bugfix",
                    }
                ),
                json.dumps(
                    {
                        "instance_id": "pallets__flask-10002",
                        "repo": "pallets/flask",
                        "problem_statement": "Refactor config loading path.",
                        "hints_text": "refactor",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    tasks = load_swebench_task_corpus(jsonl, limit=10)
    assert len(tasks) == 2
    assert tasks[0].id.startswith("swebench:")
    assert tasks[1].category == "refactor"
