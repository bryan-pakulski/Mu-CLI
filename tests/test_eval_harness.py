import json

from evals.harness import (
    DEFAULT_CORPUS_PATH,
    EvalTask,
    execute_tasks,
    evaluate_slos,
    load_swebench_task_corpus,
    load_task_corpus,
    run,
    summarize,
)


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
    assert "| Task | Session | Exit | Success | Duration(ms) | Log | Response Preview |" in digest.read_text(encoding="utf-8")


def test_eval_harness_slo_evaluation_shape():
    tasks = [
        EvalTask(
            id="shape",
            category="bugfix",
            prompt="shape",
            expected_tools=[],
            unsafe_tools=[],
            baseline_success_rate=1.0,
            baseline_tokens=0,
            verification_command="python -c \"import sys; sys.exit(0)\"",
            expected_exit_code=0,
        )
    ]
    records = execute_tasks(tasks, seed=11)
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


def test_execute_tasks_runs_real_commands(tmp_path):
    tasks = [
        EvalTask(
            id="pass",
            category="bugfix",
            prompt="pass",
            expected_tools=[],
            unsafe_tools=[],
            baseline_success_rate=1.0,
            baseline_tokens=0,
            verification_command="python -c \"import sys; sys.exit(0)\"",
            working_dir=str(tmp_path),
            expected_exit_code=0,
        ),
        EvalTask(
            id="fail",
            category="bugfix",
            prompt="fail",
            expected_tools=[],
            unsafe_tools=[],
            baseline_success_rate=1.0,
            baseline_tokens=0,
            verification_command="python -c \"import sys; sys.exit(1)\"",
            working_dir=str(tmp_path),
            expected_exit_code=0,
        ),
    ]
    records = execute_tasks(tasks, seed=1, agent_mode="feature", auto_approve_feature_plan=True)
    by_id = {r.task_id: r for r in records}
    assert by_id["pass"].success is True
    assert by_id["pass"].command_exit_code == 0
    assert by_id["fail"].success is False
    assert by_id["fail"].command_exit_code == 1
