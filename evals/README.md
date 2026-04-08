# MuCLI Eval Harness

This harness runs **real model execution + real verification commands** for every eval task, using MuCLI sessions with attached workspaces.

Each task run now has:
1. a dedicated temporary session with provider/model,
2. workspace attachment to the task's eval folder,
3. model prompting to complete the task,
4. optional feature-mode auto-approval follow-up prompt,
5. verification command (`pytest`/etc.) with exit-code scoring,
6. per-task visibility in the digest table.

## Task corpus

`evals/corpus/tasks.json` includes:

- `prompt`
- `verification_command`
- `working_dir`
- `expected_exit_code`

## Run locally

```bash
make eval EVAL_PROVIDER=openai EVAL_MODEL=gpt-4o-mini EVAL_AGENT_MODE=feature
```

(For Ollama: add `OLLAMA_HOST=http://localhost:11434` if needed.)

Equivalent CLI:

```bash
python -m evals.harness \
  --provider openai \
  --model gpt-4o-mini \
  --corpus evals/corpus/tasks.json \
  --output evals/artifacts/eval_run_latest.json \
  --trend evals/artifacts/trend_report.md \
  --digest evals/artifacts/eval_digest_latest.md
```

If provider/model are not passed and terminal is interactive, the harness prompts for them.

By default eval uses `agent_mode=feature` and sends an automatic second message approving the plan so feature-mode gating does not stall task execution. Disable this with `--no-auto-feature-approval`.

## SWE-bench compatibility

```bash
make eval-swebench \
  SWEBENCH_PATH=/path/to/swebench_lite.jsonl \
  SWEBENCH_ROOT=/path/to/repos \
  SWEBENCH_LIMIT=100 \
  EVAL_PROVIDER=openai \
  EVAL_MODEL=gpt-4o-mini
```

Behavior:
- If `<SWEBENCH_ROOT>/<repo with / replaced by __>` exists, harness runs `python -m pytest -q` there.
- If mapped repo is missing, task is marked unsuccessful in execute mode.

## Outputs

- `evals/artifacts/eval_run_*.json`: includes provider/model, per-task session name, assistant response preview, command exit code, and duration.
- `evals/artifacts/eval_digest_latest.md`: one-page digest with per-task result table.
- `evals/artifacts/trend_report.md`: trend table from recent artifacts.

## Local-only behavior

Evaluation runs are local-only. No GitHub workflow runners are used.
