# MuCLI Eval Harness

This directory provides a benchmark harness with **real command/test execution** for each task's `verification_command`.

## Task corpus

`evals/corpus/tasks.json` now includes real verification commands over local fixture repos.
Each task supports:

- `verification_command`
- `working_dir`
- `expected_exit_code`

## Run locally (real execution)

```bash
make eval
```

Equivalent CLI:

```bash
python -m evals.harness \
  --seed 1337 \
  --corpus evals/corpus/tasks.json \
  --output evals/artifacts/eval_run_latest.json \
  --trend evals/artifacts/trend_report.md \
  --digest evals/artifacts/eval_digest_latest.md
```

## SWE-bench compatibility (execution-capable adapter)

Use local SWE-bench JSONL and optionally map repo checkouts under `SWEBENCH_ROOT`:

```bash
make eval-swebench SWEBENCH_PATH=/path/to/swebench_lite.jsonl SWEBENCH_ROOT=/path/to/repos SWEBENCH_LIMIT=100
```

Behavior:
- If a mapped repo directory exists (`<SWEBENCH_ROOT>/<repo with / replaced by __>`), harness runs `python -m pytest -q` there.
- If no mapped repo exists, task remains in corpus but has no verification command and is marked unsuccessful in execute mode.

## Outputs

- `evals/artifacts/eval_run_*.json`: summary, SLO status, per-task records including command exit code/duration in execute mode.
- `evals/artifacts/eval_digest_latest.md`: one-page run digest.
- `evals/artifacts/trend_report.md`: trend table from recent artifacts.

## Local-only behavior

Evaluation runs are local-only. No GitHub workflow runners are used.
