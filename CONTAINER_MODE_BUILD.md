# MuCLI Container Mode — Complete Source Build

This tree is the complete MuCLI repository with container-mode support integrated directly. No patch or installer step is required.

## Baseline

- Repository: `bryan-pakulski/Mu-CLI`
- Baseline commit: `4a97a7c2fd9df47c8165cda577d7e152a103b908`

## Included integration

- Session capability types: `chat`, `workspace`, and `container`
- Web GUI session-type selector and container configuration fields
- TUI startup selector, `--session-type`, and `/session new --type container`
- Docker worker lifecycle, persistent session mounts, shared-worker attach/detach, and dynamic mounts
- Default-deny HTTP/HTTPS egress using an internal Docker network and an unprivileged dual-network proxy
- Session-scoped downloadable artifacts and `upload_artifact` / `list_artifacts` tools
- Web and Android artifact surfaces
- Container configuration persistence across save/load
- Runtime tool filtering for chat sessions

## Validation performed

- Python compilation: passed for `mu`, `utils`, `tests`, and `mucli.py`
- Web JavaScript syntax: passed for `mu/gui/static/js/app.js` and the inline welcome-form script
- Focused container/artifact/session-router tests: **17 passed**
- Comparable broad test run in this sandbox:
  - Untouched baseline: **1,565 passed, 132 failed**
  - Integrated tree: **1,582 passed, 132 failed**
  - The unchanged failure count is caused primarily by unavailable baseline dependencies such as `tiktoken`, `google-genai`, `sse-starlette`, and `ddgs`, plus existing environment-sensitive tests.
- Android modified files were syntax-parsed by TypeScript; full project typechecking requires `npm ci` because Expo and React Native type packages are not installed in this sandbox.
- Docker command generation and proxy policy were tested in dry-run mode. The proxy forwarding path was tested against a local HTTP endpoint. A live Docker smoke test was not possible in this sandbox.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python mucli.py --gui
```

Container mode requires Docker access for the current user. MuCLI does not invoke `sudo` or modify host firewall rules.
