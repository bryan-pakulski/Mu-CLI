---
name: commit-message
description: Draft a git commit message from staged-diff context.
trigger: \b(commit\s+message|git\s+commit|write\s+(a\s+)?commit)\b
---

When asked to draft a commit message:

1. Run `git diff --cached` to see the staged change. If nothing is staged, fall back to `git diff HEAD`.
2. Group the change into one of: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `perf`, `style`.
3. Subject line: <= 70 chars, imperative ("Add", "Fix", "Remove"), no trailing period.
4. Body (only when non-trivial): explain the **why**, not the **what** — the diff already shows what.
5. Reference the issue or PR number if you can infer it from branch name or recent history.
6. Never invent file paths or features; if context is thin, ask the user instead of guessing.

Pass the message back via a HEREDOC `git commit` invocation. Never push automatically.
