# MuCLI Eval Harness

This directory provides a **deterministic benchmark replay harness** for Phase 5 / item 8:

- Task corpus includes: `bugfix`, `refactor`, `retrieval-heavy`, and `safety-sensitive` tasks.
- Replay is deterministic via `--seed` and fixed corpus fixtures.
- Metrics emitted per run:
  - fix-rate,
  - token usage (total + average),
  - tool success/failure rates,
  - unsafe action rate.
- SLO thresholds are tracked in each run artifact:
  - `fix_rate_min` (default `0.70`),
  - `token_usage_avg_max` (default `1500`),
  - `unsafe_action_rate_max` (default `0.05`).

## Run locally

```bash
python -m evals.harness \
  --seed 1337 \
  --corpus evals/corpus/tasks.json \
  --output evals/artifacts/eval_run_latest.json \
  --trend evals/artifacts/trend_report.md \
  --digest evals/artifacts/eval_digest_latest.md
```

Or use the make target:

```bash
make eval
```

## SWE-bench compatibility (offline replay)

You can replay against a local SWE-bench-style JSONL file (e.g., sample exports) without model access:

```bash
python -m evals.harness \
  --corpus /path/to/swebench_lite.jsonl \
  --corpus-format swebench-lite \
  --swebench-limit 100
```

Notes:
- This is currently an **adapter** for deterministic replay benchmarking, not an execution harness for applying patches/tests inside benchmark containers.
- We map SWE-bench records (`instance_id`, `repo`, `problem_statement`) into MuCLI eval tasks to compare metric trends consistently.

## Outputs

- `evals/artifacts/eval_run_*.json`: run summary, SLO status, per-task records, and corpus snapshot.
- `evals/artifacts/eval_digest_latest.md`: human-readable one-page run digest.
- `evals/artifacts/trend_report.md`: markdown trend table generated from recent run artifacts.

## Local-only behavior

Evaluation runs are currently local-only (no GitHub runners/workflows). Use `make eval` or `make eval-swebench` as needed.
