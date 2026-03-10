# Testing Guide

## Quick start

From repository root:

```bash
make test
```

This runs the unit test suite via Python `unittest` discovery.

## Useful targets

- `make test` — all tests, concise output.
- `make test-verbose` — all tests with verbose output.
- `make test-web` — only Flask/web endpoint tests.
- `make test-fast` — alias for `test` (for local loops/CI scripts).
- `make check` — full local verification target (currently test suite).
- `make build-frontend` — rebuild `agents/mu_cli/static/app.js` from modular source files under `agents/mu_cli/static/app/`.

## Running a specific test module/case

```bash
PYTHONPATH=agents python -m unittest agents.tests.test_web.WebTests.test_chat_stream_endpoint
```

## Optional: run with pytest

If you prefer pytest output style and have it installed:

```bash
PYTHONPATH=agents pytest -q
```

## Test design notes

- Keep most tests as **unit-style** test-client flows for fast feedback.
- Add regression tests for bugs fixed in route/session/runtime behavior.
- Prefer deterministic tests with minimal external network access (mock where possible).
- When adding a new API behavior, add:
  1. success-path test
  2. invalid payload test
  3. state-transition test (if route mutates runtime/session)
