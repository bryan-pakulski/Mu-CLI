# Skills

Skills are declarative agent extensions — small `SKILL.md` files with
YAML frontmatter and a body prompt. They are discovered from disk and
injected into the model's system prompt under `LAYER 1B`. The model
applies them when the user's intent matches.

## Discovery roots

Scanned in this order; later entries override earlier ones on name
collision:

1. `<repo>/mu/skills/<name>/SKILL.md` — built-in
2. `~/.mu/skills/<name>/SKILL.md` — per user
3. `<workspace>/.mu/skills/<name>/SKILL.md` — per attached workspace

This lets a workspace-local skill shadow a built-in with the same name.

## File format

```markdown
---
name: commit-message
description: Draft a git commit message from staged diff context.
trigger: \b(commit|git\s+message)\b
---

When the user asks you to commit or write a commit message:
1. Run `git diff --cached` to inspect staged changes.
2. Draft a concise one-line subject ...
```

Frontmatter fields:

- `name` (optional) — identifier shown in `/skills` and used by
  `invoke_skill`. Falls back to the directory name if omitted.
- `description` (required) — one-line summary shown in the compact index.
- `trigger` (optional) — a **regex** (case-insensitive) matched against
  the latest user message. A match auto-expands this skill's full body
  for that turn under `### AUTO-EXPANDED SKILLS`. Invalid or
  catastrophic-backtracking patterns (`(.+)+`, `(.*)+`, ...) are
  silently ignored — the skill still loads.

The body is the actual prompt the model reads when the skill is in
effect.

## How skills get loaded

On every turn the session calls `discover_skills()` (cached for 5s),
filters out any names in `session.disabled_skills`, and asks
`render_skills_block` to format the result. The output is injected
under `LAYER 1B` of the system prompt.

Two paths put a skill's full body into context:

1. **Auto-expand on trigger.** If the skill's `trigger` regex matches
   the latest user message, its body is included inline under
   `### AUTO-EXPANDED SKILLS`. Auto-expanded skills get budget priority
   so a triggered skill is never dropped in favor of an inert index
   line.
2. **`invoke_skill(name)` tool.** A model-callable tool that returns
   any installed skill's full body. Use this when the index shows a
   relevant skill that the trigger didn't catch.

When `invoke_skill` fires, mucli prints a visible banner —
`🎯 SKILL ACTIVE: <name>` — so you can see in real time when the
model is applying a skill. Each invocation is also tallied per skill
in `/stats` so you can audit how often the model reaches for each one.

In both cases the model just reads the body and follows its
instructions; there is no separate execution surface.

## Modes and budgets

Configured via session variables:

- `skills_mode` — `"compact"` (default) or `"full"`.
  - `"compact"`: index by default, bodies only via auto-expand or
    `invoke_skill`.
  - `"full"`: every skill body inlined up to the budget (v1 behavior).
- `skills_max_chars` — total char budget for the LAYER 1B block. Default
  `6144`. Set to `0` to disable skills entirely.

When the budget is exceeded the renderer emits a
`... and N more skill(s) not shown (budget reached)` trailer so the
model knows which skills exist even if it can't see them.

## CLI: the `/skills` command

```
/skills                  — list all installed skills (compact)
/skills <name>           — show one skill's full body + source path
/skills reload           — clear the discovery cache and rescan
/skills enable <name>    — re-enable a disabled skill
/skills disable <name>   — hide a skill from the prompt for this session
```

`disable` is in-memory only; it does not persist across sessions or
remove the file on disk.

## Worked example: workspace override

```bash
mkdir -p ./.mu/skills/commit-message
cat > ./.mu/skills/commit-message/SKILL.md <<'EOF'
---
name: commit-message
description: Project-specific commit message format.
trigger: \b(commit|git\s+message)\b
---

Always prefix the subject line with the ticket id from the branch name.
Body should mention which CI workflow exercises the change.
EOF
```

After `/skills reload`, the workspace version shadows the bundled
`commit-message` skill while you're attached to this folder.

## Authoring tips

- **Triggers should be specific.** A trigger like `\bfoo\b` is fine;
  `foo` will match `food`. Anchor with `\b` or include surrounding
  words.
- **Keep descriptions short.** They go in the always-on index — the
  total budget covers all installed skills.
- **Bodies can be long.** They only land in context when needed.
- **Test reload.** Drop a new SKILL.md and run `/skills reload`. No
  restart required.
