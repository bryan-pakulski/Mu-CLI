#!/usr/bin/env bash
set -euo pipefail

plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
nvim_bin="${NVIM_BIN:-nvim}"
test_state="$(mktemp -d "${TMPDIR:-/tmp}/mucli-nvim-tests.XXXXXX")"
trap 'rm -rf -- "$test_state"' EXIT

mkdir -p "$test_state/data" "$test_state/state" "$test_state/cache" "$test_state/config"

NVIM_APPNAME=mucli-nvim-tests \
XDG_DATA_HOME="$test_state/data" \
XDG_STATE_HOME="$test_state/state" \
XDG_CACHE_HOME="$test_state/cache" \
XDG_CONFIG_HOME="$test_state/config" \
"$nvim_bin" --headless -u NONE -i NONE \
  --cmd "set runtimepath^=$plugin_root" \
  -l "$plugin_root/tests/run.lua"
