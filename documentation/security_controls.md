# Security controls

Defense-in-depth against accidentally reading, executing against, or
leaking common secret material — SSH keys, cloud credentials, shell
history, `.env*` files, PEM blocks, API tokens, and so on.

These controls run independently of [Plan mode](commands.md) and the
write-approval flow. They apply even when no workspace is attached, so
"open" sessions cannot be tricked into exfiltrating from `~/.ssh` or
`/etc/shadow`.

Distinct from [Security mode](security_mode.md) — that workflow looks
for vulnerabilities *in the workspace*; the controls here keep the
harness itself from leaking secrets.

## The three layers

### 1. Path denylist (`core/secret_paths.py`)

Always-on. Every file-touching tool (`read_file`, `write_file`,
`apply_diff`, `search_and_replace_file`, `get_chunk`, `bash`) routes
through `_check_bounds`, which calls `is_denied_path()` *before* the
workspace check. Denied paths are refused with a clear error regardless
of workspace state.

Coverage:

| Category | Examples |
| --- | --- |
| SSH / GPG | `~/.ssh/**`, `~/.gnupg/**`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `authorized_keys`, `known_hosts` |
| Cloud credentials | `~/.aws/**`, `~/.azure/**`, `~/.config/gcloud/**`, `~/.kube/**`, `~/.docker/config.json`, `~/.config/gh/**` |
| Crypto material | `*.pem`, `*.key`, `*.pfx`, `*.p12`, `*.jks`, `*.keystore` |
| Shell config / history | `~/.bashrc`, `~/.zshrc`, `~/.profile`, `~/.bash_history`, `~/.zsh_history`, `~/.netrc`, `~/.npmrc`, `~/.pypirc` |
| App credentials | `credentials*.json`, `service-account*.json`, `~/.cargo/credentials*` |
| Dotenv | `.env`, `.env.*` |
| System | `/etc/shadow`, `/etc/sudoers`, `/etc/ssh/**`, `/proc/*/environ`, `/proc/*/cmdline` |

Symlinks are resolved with `os.path.realpath` and both the original
basename and the resolved basename are checked, so a symlink named
`id_rsa` pointing at any target is still denied.

There is **no override at the file layer**. If a workflow genuinely
needs to read a file matching a denylisted pattern, do it from bash
with the override flag below.

### 2. Bash command guard (`mu/agent/secret_guard.py`)

A `pre_tool` hook (priority 5, runs before plan-mode at 10) inspects
`bash` and `bash_background` calls before they reach the shell. Two
checks:

- **Path-argument scan** — every path-shaped token is run through the
  same denylist as Layer 1. Catches `cat ~/.ssh/id_rsa`,
  `cp ~/.aws/credentials /tmp/x`, `tar czf - ~/.ssh`, `openssl rsa -in
  /home/user/private.pem`, etc.
- **Risky-pattern match** — narrow regex against the raw command:
  bare `env` / `printenv`, recursive `find` for known key names,
  `cat ~/.{bash,zsh,sh,fish}_history`, reads from `/proc/*/environ`,
  the `... | base64` exfil pattern on key files.

When a check fires, the tool result is short-circuited with
`error_code: "secret_guard_blocked"` and a message explaining the
reason and the override flag.

### 3. Output scrubber (`core/secret_paths.py:redact_secrets`)

Tool output passes through a regex pass before being returned to the
model. Applied in `read_file`, `bash_command`, `get_chunk`,
`search_for_string`, and `search_references`. Matches are replaced with
`[REDACTED:<label>]` and a trailer `[security: redacted N secret(s) from
output]` is appended so the model knows the output was sanitized.

Patterns: PEM private-key blocks (whole multi-line block), AWS access
keys (`AKIA…`) and `aws_secret_access_key` assignments, GitHub tokens
(classic `ghp_`, OAuth `gho_`, server `ghs_`, refresh `ghr_`,
fine-grained `github_pat_`), GitLab `glpat-`, Slack `xox[a-s]-`,
Anthropic `sk-ant-api…`, OpenAI/sk-style `sk-…`, Google `AIza…`, and
JWTs (`eyJ…`).

The scrubber runs unconditionally — even with the override below set,
known secret patterns are still redacted from output. The override only
relaxes path-based blocking, not pattern-based redaction.

## Override

Some workflows do legitimately need to read a denylisted path (e.g.
auditing your own `~/.ssh/config`). The session variable
`security_allow_secret_paths` bypasses Layer 2's bash guard:

```text
/set security_allow_secret_paths true
```

Effect:
- Bash commands targeting denylisted paths run normally.
- File-layer tools (`read_file`, etc.) **still refuse** denylisted
  paths — read the file via `bash` (`cat`) if you need it.
- Output scrubbing (Layer 3) continues to apply.

The override is per-session and does not persist across reloads. Turn
it off when you're done:

```text
/set security_allow_secret_paths false
```

## Examples

### A denied read
```text
> read_file ~/.ssh/id_rsa
Error: Access denied or file ignored. '~/.ssh/id_rsa' is outside
boundaries or in ignore list.
```

### A blocked bash command
```text
> bash cat ~/.aws/credentials
secret_guard_blocked: command references denied secret directory
(~/.aws): '~/.aws/credentials'.
```

### Output redaction
A workspace file containing an accidentally-committed AWS key:
```text
> read_file config.yml
api_key: [REDACTED:AWS access key]
db_url: postgres://localhost:5432/foo

[security: redacted 1 secret(s) from output]
```

## What this does NOT protect against

- **Workspace exfiltration via the model context.** Once a secret is
  in the chat history (e.g. the user pasted it), nothing here scrubs
  it. Treat chat history like any other secret store.
- **Custom denylist bypasses.** The patterns are heuristics. Renaming
  `id_rsa` to `mykey.txt` would let it slip through. The intent is to
  stop accidental and obvious exfiltration, not a determined attacker
  with shell access.
- **Network exfiltration.** A bash command that reads an in-bounds file
  and sends it via `curl` is not detected; the in-bounds file is
  presumed non-secret. Add `*.pem`, `*credentials*`, etc. to your
  workspace's `.gitignore` to extend the denylist.
- **Provider-side caching.** Anthropic / OpenAI / Gemini may cache
  request payloads. The scrubber redacts *before* the model sees the
  text, so cached payloads also contain the redacted version, but any
  text the user types directly is not scrubbed.

## Implementation notes

- Layer 1 wire-in: `core/tools.py:_check_bounds`.
- Layer 2 hook registration: `core/session.py` imports
  `mu.agent.secret_guard` alongside `mu.agent.plan_mode` so the hook is
  live from the first tool call.
- Layer 3 wire-in: `core/tools.py` — `read_file`, `bash_command`,
  `get_chunk`, `search_for_string`, `search_references`.

Tests live in `tests/test_secret_paths.py`,
`tests/test_secret_scrubber.py`, and `tests/test_bash_secret_guard.py`.
