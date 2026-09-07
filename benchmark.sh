#!/usr/bin/env bash
# One-command entry point for the pinned MuCLI Terminal-Bench evaluation.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

TB_EXECUTABLE="${TB_PY:-${HOME}/.venvs/tb/bin/tb}"
TB_VERSION="0.2.18"
DATASET="terminal-bench-core==0.1.1"
SOURCE_DATASET_PATH="${TB_DATASET_PATH:-${HOME}/.cache/terminal-bench/terminal-bench-core/0.1.1}"
PREPARED_DATASET_PATH="${TB_PREPARED_DATASET_PATH:-bench/artifacts/tb-prepared/terminal-bench-core/0.1.1}"
MODEL_NAME="${MODEL:-ollama/glm-5.3-flash}"
HARNESS="${TB_HARNESS:-mucli}"
ATTEMPTS="${TB_ATTEMPTS:-3}"
ATTEMPTS_EXPLICIT=0
[ -n "${TB_ATTEMPTS+x}" ] && ATTEMPTS_EXPLICIT=1
SELECTION="full"
SINGLE_TASK=""
RUN_LABEL="${TB_RUN_LABEL:-}"
PREPARE=1
BOOTSTRAP=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: ./benchmark.sh [OPTIONS]

With no options, run the credible MuCLI baseline: all 10 pinned tasks, three
attempts each, using ollama/glm-5.3-flash and reusable prebuilt task images.

Options:
  --harness NAME    mucli, opencode, claude-code, pi, or all (default: mucli)
  --model MODEL     provider/model passed to the harness
  --attempts N      repeated trials per task (default: 3; smoke default: 1)
  --smoke           run hello-world only
  --task TASK       run one pinned dataset task
  --run-label TEXT  record a comparison label in run provenance
  --no-prepare      require and reuse already-prepared task images
  --bootstrap       install pinned TB and fetch/build missing prerequisites
  --dry-run         print the setup and benchmark commands without executing
  -h, --help        show this help

Examples:
  ./benchmark.sh
  ./benchmark.sh --smoke
  ./benchmark.sh --task git-multibranch --attempts 3 --run-label subprocess-v2
  ./benchmark.sh --harness opencode --attempts 3
  ./benchmark.sh --harness all --attempts 3 --run-label glm-baseline
EOF
}

selection_is_unset() {
  [ "$SELECTION" = "full" ] && [ -z "$SINGLE_TASK" ]
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --harness)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      HARNESS="$2"
      shift 2
      ;;
    --model)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      MODEL_NAME="$2"
      shift 2
      ;;
    --attempts)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      ATTEMPTS="$2"
      ATTEMPTS_EXPLICIT=1
      shift 2
      ;;
    --smoke)
      selection_is_unset || { echo "choose only one of --smoke and --task" >&2; exit 2; }
      SELECTION="smoke"
      shift
      ;;
    --task)
      selection_is_unset && [ "$#" -ge 2 ] || {
        echo "choose only one of --smoke and --task" >&2
        exit 2
      }
      SELECTION="task"
      SINGLE_TASK="$2"
      shift 2
      ;;
    --run-label)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      RUN_LABEL="$2"
      shift 2
      ;;
    --no-prepare)
      PREPARE=0
      shift
      ;;
    --bootstrap)
      BOOTSTRAP=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$HARNESS" in
  claude) HARNESS="claude-code" ;;
  mucli|opencode|claude-code|pi|all) ;;
  *) echo "--harness must be mucli, opencode, claude-code, pi, or all" >&2; exit 2 ;;
esac

case "$ATTEMPTS" in
  ''|*[!0-9]*) echo "--attempts must be a positive integer" >&2; exit 2 ;;
esac
[ "$ATTEMPTS" -gt 0 ] || { echo "--attempts must be positive" >&2; exit 2; }
[ -n "$MODEL_NAME" ] || { echo "--model must not be empty" >&2; exit 2; }
[ "$SELECTION" != "smoke" ] || [ "$ATTEMPTS_EXPLICIT" -eq 1 ] || ATTEMPTS=1

case "$SELECTION" in
  full)
    mapfile -t TASKS < <(awk '/^  - / { print $2 }' bench/tb_suite.yaml)
    ;;
  smoke)
    TASKS=(hello-world)
    ;;
  task)
    [ -n "$SINGLE_TASK" ] || { echo "--task must not be empty" >&2; exit 2; }
    TASKS=("$SINGLE_TASK")
    ;;
esac
[ "${#TASKS[@]}" -gt 0 ] || { echo "no tasks found in bench/tb_suite.yaml" >&2; exit 2; }

if [ "$HARNESS" = "all" ]; then
  HARNESSES=(mucli opencode claude-code pi)
else
  HARNESSES=("$HARNESS")
fi

has_external_harness=0
for selected_harness in "${HARNESSES[@]}"; do
  [ "$selected_harness" = "mucli" ] || has_external_harness=1
done
if [ "$has_external_harness" -eq 1 ]; then
  case "$MODEL_NAME" in
    ollama/*|*:cloud) ;;
    *)
      echo "controlled external harnesses currently require an Ollama model" >&2
      exit 2
      ;;
  esac
fi

print_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

run_or_print() {
  if [ "$DRY_RUN" -eq 1 ]; then
    print_command "$@"
  else
    "$@"
  fi
}

bootstrap_tb() {
  local tb_venv="${TB_BOOTSTRAP_VENV:-${HOME}/.venvs/tb}"
  case "$tb_venv" in
    /|"${HOME}"|.)
      echo "refusing unsafe TB_BOOTSTRAP_VENV: $tb_venv" >&2
      exit 2
      ;;
  esac
  command -v uv >/dev/null 2>&1 || {
    echo "uv is required for --bootstrap (https://docs.astral.sh/uv/)" >&2
    exit 2
  }
  mkdir -p "$(dirname "$tb_venv")"
  if [ ! -x "$tb_venv/bin/python" ]; then
    uv venv --python 3.13 "$tb_venv"
  fi
  uv pip install --python "$tb_venv/bin/python" "terminal-bench==$TB_VERSION"
  TB_EXECUTABLE="$tb_venv/bin/tb"
}

if [ "$DRY_RUN" -eq 0 ]; then
  if [ ! -x "$TB_EXECUTABLE" ]; then
    if [ "$BOOTSTRAP" -eq 1 ]; then
      bootstrap_tb
    else
      echo "Terminal-Bench not found at $TB_EXECUTABLE" >&2
      echo "rerun with --bootstrap, or set TB_PY=/path/to/tb" >&2
      exit 2
    fi
  fi

  tb_python="$(dirname "$TB_EXECUTABLE")/python"
  installed_tb_version="$("$tb_python" -c \
    "import importlib.metadata; print(importlib.metadata.version('terminal-bench'))")"
  if [ "$installed_tb_version" != "$TB_VERSION" ]; then
    if [ "$BOOTSTRAP" -eq 1 ]; then
      bootstrap_tb
      tb_python="$(dirname "$TB_EXECUTABLE")/python"
      installed_tb_version="$("$tb_python" -c \
        "import importlib.metadata; print(importlib.metadata.version('terminal-bench'))")"
    else
      echo "Terminal-Bench $TB_VERSION is required; found $installed_tb_version" >&2
      echo "rerun with --bootstrap to install the pinned version" >&2
      exit 2
    fi
  fi
  [ "$installed_tb_version" = "$TB_VERSION" ] || {
    echo "failed to install Terminal-Bench $TB_VERSION" >&2
    exit 2
  }

  if [ ! -d "$SOURCE_DATASET_PATH" ]; then
    if [ "$BOOTSTRAP" -eq 1 ] && [ -z "${TB_DATASET_PATH+x}" ]; then
      "$TB_EXECUTABLE" datasets download --dataset "$DATASET"
    else
      echo "pinned dataset not found at $SOURCE_DATASET_PATH" >&2
      echo "rerun with --bootstrap, or set TB_DATASET_PATH" >&2
      exit 2
    fi
  fi
  [ -d "$SOURCE_DATASET_PATH" ] || {
    echo "dataset download did not create $SOURCE_DATASET_PATH" >&2
    exit 2
  }

  if [[ " ${HARNESSES[*]} " == *" mucli "* ]] && \
      [ ! -f bench/artifacts/mucli-wheelhouse.tar.gz ]; then
    if [ "$BOOTSTRAP" -eq 1 ]; then
      bash bench/build_wheelhouse.sh
    else
      echo "MuCLI wheelhouse is missing; rerun with --bootstrap" >&2
      exit 2
    fi
  fi
  if [ "$has_external_harness" -eq 1 ] && [ -z "${OLLAMA_API_KEY:-}" ]; then
    echo "OLLAMA_API_KEY is required by the controlled external harnesses" >&2
    exit 2
  fi
  command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 2; }
  docker info >/dev/null 2>&1 || {
    echo "the Docker daemon is unavailable" >&2
    exit 2
  }
fi

echo "== Terminal-Bench evaluation =="
echo "   harness: ${HARNESSES[*]}"
echo "   model: $MODEL_NAME"
echo "   tasks: ${#TASKS[@]}; attempts per task: $ATTEMPTS"
echo "   timing: task execution only; harness setup is reported separately"

if [ "$PREPARE" -eq 1 ]; then
  echo "-- preparing/reusing immutable task images (outside timed execution)"
  run_or_print python3 bench/prepare_tb.py \
    --source "$SOURCE_DATASET_PATH" \
    --output "$PREPARED_DATASET_PATH" \
    "${TASKS[@]}"
else
  echo "-- validating existing immutable task images"
  run_or_print python3 bench/prepare_tb.py \
    --check-only \
    --source "$SOURCE_DATASET_PATH" \
    --output "$PREPARED_DATASET_PATH" \
    "${TASKS[@]}"
fi

SELECTION_ARGS=()
case "$SELECTION" in
  smoke) SELECTION_ARGS=(--smoke) ;;
  task) SELECTION_ARGS=(--task "$SINGLE_TASK") ;;
esac
LABEL_ARGS=()
[ -z "$RUN_LABEL" ] || LABEL_ARGS=(--run-label "$RUN_LABEL")

run_harness() {
  local selected_harness="$1"
  local runner_args=("${SELECTION_ARGS[@]}" --attempts "$ATTEMPTS" "${LABEL_ARGS[@]}")
  local command
  if [ "$selected_harness" = "mucli" ]; then
    command=(bash bench/run_pack.sh "${runner_args[@]}")
  else
    command=(bash bench/run_cli_pack.sh --harness "$selected_harness" "${runner_args[@]}")
  fi

  echo "-- running $selected_harness"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '  MODEL=%q TB_PY=%q ' "$MODEL_NAME" "$TB_EXECUTABLE"
    printf '%q ' "${command[@]}"
    printf '\n'
  else
    MODEL="$MODEL_NAME" TB_PY="$TB_EXECUTABLE" "${command[@]}"
  fi
}

failed=0
for selected_harness in "${HARNESSES[@]}"; do
  run_harness "$selected_harness" || failed=1
done

if [ "$failed" -ne 0 ]; then
  echo "one or more harness runs failed; completed results remain under bench/results/" >&2
  exit 1
fi
