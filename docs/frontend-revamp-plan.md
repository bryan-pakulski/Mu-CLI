# Frontend revamp plan (HTMX + Alpine.js)

## Goal
Deliver a reactive, lightweight UI that keeps Python/Flask in control and avoids a heavy npm dependency graph.

## Phase plan
1. **Foundation (executed in this change)**
   - Add a new HTMX + Alpine entry UI.
   - Keep legacy UI available at a separate route for fallback.
   - Create server-rendered message partials and form-based chat posting.
2. **Progressive enhancement**
   - Add approval modal interactions via HTMX swaps.
   - Add settings panels (provider/model/approval/workspace + runtime/debug/research toggles) as server-rendered partials.
   - Add background job/plan status polling with lightweight polling intervals.
3. **Parity + cleanup**
   - Reach functional parity with the legacy single-page template.
   - Remove obsolete inline JS from legacy template once parity is complete.
   - Add focused web tests for new UI routes and partial rendering.

## Execution status
- ✅ Phase 1 foundation is implemented.
- ✅ Phase 2 started: state sidebar and trace feed now update via HTMX polling.
- ✅ Settings port in progress: provider/model/approval/workspace/runtime/condense/debug/agentic/research settings now editable in HTMX sidebar.
- ⏳ Remaining Phase 2 controls (approval modal and deeper parity) and Phase 3 cleanup are planned follow-up work.
