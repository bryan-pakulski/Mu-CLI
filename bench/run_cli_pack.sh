#!/usr/bin/env bash
# Controlled OpenCode, Claude Code, or Pi Terminal-Bench baseline runner.
set -euo pipefail
cd "$(dirname "$0")/.."

TB_PY="${TB_PY:-$HOME/.venvs/tb/bin/tb}"
DATASET_PATH="${TB_PREPARED_DATASET_PATH:-bench/artifacts/tb-prepared/terminal-bench-core/0.1.1}"
MODEL="${MODEL:-ollama/glm-5.3-flash}"
ATTEMPTS="${TB_ATTEMPTS:-1}"
SETUP_TIMEOUT_SEC="${TB_SETUP_TIMEOUT_SEC:-180}"
OUTER_CLEANUP_MARGIN_SEC="${TB_OUTER_CLEANUP_MARGIN_SEC:-30}"
HARNESS=""
SELECTION="full"
SINGLE_TASK=""
RUN_LABEL="${TB_RUN_LABEL:-}"

usage() {
  echo "usage: $0 --harness opencode|claude-code|pi [--smoke|--task TASK] [--attempts N] [--run-label LABEL]" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --harness) HARNESS="${2:-}"; shift 2 ;;
    --smoke) SELECTION="smoke"; shift ;;
    --task) SELECTION="task"; SINGLE_TASK="${2:-}"; shift 2 ;;
    --attempts) ATTEMPTS="${2:-}"; shift 2 ;;
    --run-label) RUN_LABEL="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

case "$HARNESS" in opencode|claude-code|pi) ;; *) usage; exit 2 ;; esac
case "$ATTEMPTS" in ''|*[!0-9]*) usage; exit 2 ;; esac
[ "$ATTEMPTS" -gt 0 ] || { usage; exit 2; }
[ -x "$TB_PY" ] || { echo "tb not found: $TB_PY" >&2; exit 2; }
[ -n "${OLLAMA_API_KEY:-}" ] || { echo "OLLAMA_API_KEY is required" >&2; exit 2; }
case "$MODEL" in
  ollama/*|*:cloud) ;;
  *) echo "controlled external harnesses require an Ollama model" >&2; exit 2 ;;
esac

case "$SELECTION" in
  full) mapfile -t TASKS < <(python3 bench/list_tb_tasks.py) ;;
  smoke) TASKS=(hello-world) ;;
  task) TASKS=("$SINGLE_TASK") ;;
esac
[ "${#TASKS[@]}" -gt 0 ] || { echo "no tasks found in bench/tb_suite.yaml" >&2; exit 2; }

MANIFEST="$DATASET_PATH/prepare-manifest.json"
python3 bench/prepare_tb.py --check-only \
  --source "${TB_DATASET_PATH:-$HOME/.cache/terminal-bench/terminal-bench-core/0.1.1}" \
  --output "$DATASET_PATH" "${TASKS[@]}" >/dev/null

verify_artifact() {
  local expected="$1"
  local path="$2"
  local actual
  actual="$(sha256sum "$path")"
  actual="${actual%% *}"
  [ "$actual" = "$expected" ] || {
    echo "artifact checksum mismatch: $path" >&2
    return 1
  }
  echo "$path: OK"
}

case "$HARNESS" in
  opencode)
    verify_artifact 6ce6570e7db9a40e7bd3304ebdfff607920bde8cafd2eb5587bd7a26f89ba0b5 bench/artifacts/agents/opencode/1.18.4/opencode
    ;;
  claude-code)
    verify_artifact 8272c8a474ac9ea1bc35f19b9f7c7e7dc4dc4eb6d5ad3e484b19335ac72446b2 bench/artifacts/agents/claude-code/2.1.211/claude
    ;;
  pi)
    verify_artifact ffa2a8214ef6f96fe6a5cdb1ddfb4165396dbcc352e92e42bf4d988986757bc6 bench/artifacts/agents/pi/pi-0.85.1.tar.gz
    verify_artifact b294a556e639d64338823920e5866c21c02741742d2e1529ee1a225c1ec9252a bench/artifacts/agents/node/22.23.2/node-v22.23.2-linux-x64.tar.gz
    ;;
esac
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="bench/results/baseline-${HARNESS}-${STAMP}"
mkdir -p "$OUT"
python3 bench/write_cli_provenance.py \
  --output "$OUT/provenance.json" --repo . --dataset "$DATASET_PATH" \
  --prepared-manifest "$MANIFEST" --harness "$HARNESS" --model "$MODEL" \
  --attempts "$ATTEMPTS" --tb "$TB_PY" \
  --run-label "$RUN_LABEL" \
  --setup-allowance-seconds "$SETUP_TIMEOUT_SEC" \
  --outer-cleanup-margin-seconds "$OUTER_CLEANUP_MARGIN_SEC" "${TASKS[@]}"

echo "== $HARNESS controlled baseline -> $OUT =="
echo "   model: $MODEL (direct Ollama Cloud)"
echo "   attempts per task: $ATTEMPTS; setup excluded from execution timing"
harness_failed=0
for task in "${TASKS[@]}"; do
  execution_timeout=$(python3 - "$DATASET_PATH/$task/task.yaml" <<'PY'
import sys
from pathlib import Path
from bench.tb_support import read_task_execution_timeout
print(read_task_execution_timeout(Path(sys.argv[1])))
PY
  )
  outer_timeout=$(awk -v execution="$execution_timeout" -v setup="$SETUP_TIMEOUT_SEC" -v cleanup="$OUTER_CLEANUP_MARGIN_SEC" 'BEGIN { print execution + setup + cleanup }')
  echo "-- $task (${execution_timeout}s execution; ${SETUP_TIMEOUT_SEC}s setup allowance)"
  if ! "$TB_PY" run --dataset-path "$DATASET_PATH" \
    --agent-import-path bench.tb_cli_agent:ControlledCliAgent \
    --model "$MODEL" --task-id "$task" --output-path "$OUT/$task" \
    --n-concurrent 1 --n-attempts "$ATTEMPTS" --no-rebuild --no-cleanup \
    --global-agent-timeout-sec "$outer_timeout" \
    --agent-kwarg "harness=$HARNESS" \
    --agent-kwarg "execution_timeout_sec=$execution_timeout" \
    --agent-kwarg "setup_timeout_sec=$SETUP_TIMEOUT_SEC"; then
    harness_failed=1
  fi
done

summary_failed=0
python3 bench/summarize_tb.py "$OUT" "${TASKS[@]}" || summary_failed=1
[ "$harness_failed" -eq 0 ] && [ "$summary_failed" -eq 0 ]
