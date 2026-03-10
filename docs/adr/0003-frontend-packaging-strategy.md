# 0003 - Frontend packaging strategy

- Status: accepted
- Date: 2026-03-10

## Context

The GUI started as a single large inline script in `index.html`, which hindered maintainability and refactoring.

## Decision

- Keep server-rendered template deployment model (Flask template + static assets).
- Move client logic to `agents/mu_cli/static/app.js` loaded by `index.html`.
- Enforce boundary-oriented organization in frontend code:
  - networking helpers
  - state/reducers
  - render functions
  - event wiring
- Prefer incremental modularization without introducing heavy frontend tooling unless justified.

## Consequences

### Positive
- Lower template complexity.
- Clearer separation for future refactors and testing.
- Preserves simple deployment/runtime assumptions.

### Trade-offs
- Without a bundler/module pipeline, long-term growth still requires discipline to keep boundaries clean.
