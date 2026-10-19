# Plan: Runbook editor, dynamic pickers and node setup (orchestration)

Status: implemented 2026-10-19 (see §10), decisions in §9 · Builds on: `orchestration.md` (§3.3 runbooks, §4 Studio, §14 status) ·
Reference: Wash design guide

## 1. Problem

Runbooks can be read, reviewed, published and run in the web UI, but only the AI can write them
(`orch_runbook_draft`). An operator who wants to compose three published tools into a safe
sequence has to ask the AI in a chat or call `saveRunbookDraft` by hand with YAML. There is no
way to start a runbook from the UI, change a published one into a new version, see what an
expression will resolve to, or check a draft before submitting it.

### Goals

- **Operator authoring.** Create a runbook, edit a draft, and start a new version from a
  published one, entirely in the web UI.
- **Two views of one document.** A structured step editor and a YAML view of the same spec,
  switchable without losing edits (the plan-editor pattern of `orchestration.md` §4 step 2).
- **Validation while editing.** The same `runbook.normalize` checks the server runs, plus the
  checks it doesn't run yet (step references, literal params against each tool's schema), shown
  next to the field they're about, without saving.
- **Safe collaboration with the AI.** The operator and the discovery chat can both edit a draft;
  neither silently overwrites the other.
- **Try before review.** A check-mode run of a draft runbook on canary nodes, approved like a
  tool test.
- **Nothing typed from memory.** Every place that picks a tool, runbook, version, node, label or
  group offers what actually exists right now, and parameter fields are generated and
  pre-filled from the chosen version (§4.1).
- **Guided node setup.** A wizard that hands out the node agent, walks through registration and
  ends only when the node is accepted and has proved it is alive by running a tool (§5).

### Non-goals

- A graph or branching editor. Runbooks stay ordered lists with `when:` conditions (`orchestration.md` §3.3).
- New expression features (arithmetic, functions). The language stays as it is.
- Mobile editing. The Android tab keeps runbooks read-only.

## 2. What exists (reuse)

| Need | Existing piece |
|---|---|
| Spec validation | `mu/orchestration/runbook.py` `normalize(raw, version, resolve_tool, allow_in_review)`, `check_syntax`, `evaluate`, `render` |
| Draft storage | `catalog.save_runbook_draft` (creates the next version, or updates the open draft); `runbook_versions` table |
| Lifecycle | `catalog.set_runbook_status`, `states.RUNBOOK_TRANSITIONS` (`draft → in_review → published → deprecated → retired`), actions `submitRunbook`, `requestRunbookChanges`, `publishRunbook`, … in `actions.runbook_actions` |
| Commands | `saveRunbookDraft` (in `AI_COMMANDS`) in `commands.py` |
| View model | `vm.runbook_vm` (steps, inputs, `spec_yaml`, problems, actions) |
| Web | `static/js/orchestration/catalog.js` `renderRunbook` (read-only detail); `studio.js` tool editor (dirty tracking, save, stale banner, stepper); `runs.js` `paramsForm(container, schema)` (form from JSON Schema); `ui.js` dialogs, prompts, tables, code blocks |
| Tool tests | `runs.propose_test` + `kind="test"` runs, always operator-approved, canary-only |
| Diff | `vm._diff` (unified diff), `static/js/wash/diff.js` |

## 3. Gaps to close on the backend

1. **No validation without saving.** `normalize` only runs inside `save_runbook_draft` and
   `runbook_problems`. The editor needs a read-only `validateRunbook` command that returns the
   normalized spec, the problems (each with a `path` such as `steps[2].params.keep_days`), the
   computed effect and, per step, the resolved tool's title, effect, status, input schema and
   declared outputs.
2. **Shallow checks.** `normalize` checks expression syntax but not meaning. Add:
   - `steps.<name>` references must name an *earlier* step (a later or unknown step always
     resolves to `null` at run time, which is almost always a bug);
   - `inputs.<name>` references must be declared in the runbook's `inputs`;
   - literal (non-template) params are validated against the step tool's `inputs` schema,
     unknown param names are errors, required params without a value are errors (templates are
     accepted; their type is only known at run time);
   - `steps.<name>.outputs.<field>` gets a warning when the referenced tool doesn't declare that
     output field;
   - step `nodes` selectors that can never match a node the runbook could target are not
     checkable statically and stay run-time skips.
   Problems become objects `{path, message, severity}`; `runbook_problems` keeps returning
   strings (message with path prefix) for existing callers.
3. **Lost-update risk.** `save_runbook_draft` overwrites the open draft unconditionally. Add an
   optional `base_digest`: when given and the stored draft's digest differs, refuse with 409
   ("the draft changed since you opened it") and return the current spec. The UI always sends
   it; the AI tool keeps working without it (last write wins for the AI, as today), but the
   editor shows a stale banner on the change-bus event (same as the tool editor).
4. **No discard, no explicit new version.** Add `discardRunbookDraft` (draft or in-review only)
   and `newRunbookVersion` (copy a published/deprecated version into the next draft). Saving a
   spec whose id has no open draft already creates a new version; the explicit command makes the
   UI action and its availability (`runbook_actions`) unambiguous.
5. **Create from the UI.** `saveRunbookDraft` already serves this; the UI sends a minimal spec
   (`id`, `title`, one empty step). The id is fixed after creation (as for tools).
6. **Draft runbook tests.** `build_plan` refuses unpublished runbooks. Add a `runbook_test` run
   kind, proposed by `proposeRunbookTest {ref, nodes, params, check_mode=true}`:
   - only canary nodes (same rule as tool tests), always operator-approved;
   - every step's tool must still be *published* (drafts of tools are tested separately), so no
     test-key signing is needed — dispatch is unchanged;
   - the frozen plan records the runbook digest; dispatch refuses if the draft changed after
     approval (the same rule as tool tests);
   - results land in the runbook version's `review.tests` (needs a `review` column on
     `runbook_versions`, schema version 2 with an `ALTER TABLE` migration).
   `proposeRunbookTest` joins `AI_COMMANDS` (proposing a test is already allowed for tools).
7. **Access-policy rules for tests (decision §9.3).** Today every test (tool or runbook) is
   forced to at least `confirm`. Access-policy rules get a new match key `run: test | run`
   (default: a rule without `run` matches only normal runs, so existing policies keep their
   meaning), and the forced floor is replaced by a default rule shipped in `DEFAULT_RULES`:
   `{name: "Tests are approved by the operator", match: {run: test}, decision: confirm}`.
   The operator can then add rules before it, for example
   `{name: "AI may test read-only drafts on canaries", match: {actor: ai, run: test, effect: read_only}, decision: allow}`.
   Invariants that no rule can relax (enforced in `build_plan`/`hub`, not in the policy):
   tests only target canary nodes (master label *and* node-local `canary: true`); draft tool
   versions are signed with the test key; a test refuses to dispatch if the draft changed after
   approval; `destructive` effects keep the typed-confirmation floor. Applies to tool tests and
   runbook tests alike; `validate_rules` accepts the new key; the Settings policy editor and
   `documentation/orchestration.md` document it.
8. **Diff and notes.** `runbook_vm` gains `diff` (YAML diff against the previous published
   version), `notes` (from the audit log, like `notes.tool_notes`) and `versions`.

New and changed commands (all through `commands.dispatch`, all audited):

| Command | Actor | Notes |
|---|---|---|
| `validateRunbook {spec}` | operator, AI | read-only; returns `{spec, problems, effect, steps_info}` |
| `saveRunbookDraft {spec, base_digest?}` | operator, AI | 409 on digest mismatch when `base_digest` is given |
| `newRunbookVersion {ref}` | operator, AI | copies into the next draft |
| `discardRunbookDraft {ref}` | operator | draft or in review |
| `proposeRunbookTest {ref, nodes?, params?, check_mode?}` | operator, AI | canary-only; approval per the access policy's test rules (§3 item 7) |
| `upgradeRunbookTools {spec, steps?}` | operator, AI | read-only; upgraded spec + per-step impact report |

Publishing, approving and running stay operator-only; nothing in `AI_COMMANDS` changes except
the additions above. None of them publishes or approves; a test proposal dispatches without a
person only when the operator has written an access rule that allows it (§3 item 7).

## 4. The editor (web)

Route `#/studio/runbook/<id>/<version>` (new runbook: `#/studio/runbook/new`), listed in the
Studio next to tool drafts ("Runbooks" section with the same cards: status chip, effect chip,
step count, problems count, updated time). The catalog runbook page gets **Edit draft** /
**New version** buttons from the actions.

Layout (same two-column shape as the tool editor):

**Left — the document**

- Header: id (read-only after creation), title, summary.
- **Inputs**: a small table editor for the runbook's `inputs` (name, type, default, enum,
  pattern/max length, description) generating the JSON Schema subset; an "Edit as YAML" escape
  hatch for the schema.
- **Steps**: an ordered list of step cards. Each card:
  - name; tool picker (search over published tool versions, showing effect chip and
    `id@version`; deprecated versions shown with a warning; an "upgrade to latest" hint when a
    newer published version exists);
  - params: a form generated from the tool's input schema (reuse `paramsForm`), where every
    field has a *literal / expression* toggle. Expression mode is a one-line input with
    completion for `inputs.*`, earlier `steps.<name>.outputs.*` (from the tool's declared
    outputs), `steps.<name>.state` and `node.labels.*`;
  - `when:` (expression input with the same completion, empty = always);
  - `on_failure` (segmented control: stop / continue / roll back; roll back disabled with the
    reason when the tool has no rollback);
  - `nodes` selector (optional, validated as you type);
  - per-card problems inline, effect chip, a "preconditions run first" note when the tool
    declares preconditions;
  - an "upgrade to vX" button when a newer published version of the step's tool exists;
  - reorder with drag handles *and* keyboard (move up / move down buttons, `Alt+↑/↓`); moving a
    step above one it references shows the reference error immediately; duplicate and delete.
- **YAML** tab: the whole spec in a textarea. Switching tabs converts through the server
  (`validateRunbook`), so the structured view and YAML can never disagree; a YAML parse error
  keeps you on the YAML tab with the error.

**Right — checks**

- Problems list (grouped by step, click to focus the field), computed effect, the access
  decision preview for the operator (`access.decide` with the computed effect), warnings
  (no rollback for a changing step, deprecated tool versions).
- **Expression preview**: pick a node and sample inputs; shows what each param and `when:`
  would resolve to with that node's labels and *null* for step outputs not yet known
  (evaluated by `validateRunbook {spec, preview: {node_id, inputs}}` on the server with the
  real evaluator — never in JS).
- Test results (`review.tests`), notes, diff against the previous version.

**Bulk upgrade (decision §9.2).** "Upgrade tools" in the toolbar lists every step whose tool has
a newer published version (`id@3 → id@5`), each with a checkbox (all selected by default), and
for each the manifest diff between the pinned and the new version plus the consequences the
server computes: effect changes (read-only → mutating raises the runbook's effect and its access
decision), parameters that no longer validate against the new input schema, removed output
fields that later steps reference, rollback added/removed. Applying it edits the draft (it is
an ordinary unsaved change, undoable before save) and re-validates. On a published runbook the
action is "New version with upgraded tools". Served by `upgradeRunbookTools {spec, steps?}`
(read-only: returns the upgraded spec and the per-step report; nothing is stored until save).

**Toolbar**: Save draft (disabled until dirty; sends `base_digest`), Test on canary (dialog:
canary nodes, inputs form, check mode on by default), Submit for review, Discard, Open
discovery chat (the chat can be asked to draft or refine the same runbook), Catalog view.

Behaviour copied from the tool editor: unsaved-changes chip, live updates never overwrite local
edits (stale banner with "reload" / "keep mine — save will be refused until you reload"),
in-review drafts are read-only with a link to request changes, `beforeunload` guard when dirty.

Validation runs debounced (400 ms) after edits, cancelling in-flight requests; the UI shows the
last result with its age.

**Invalid drafts are saveable, never submittable (decision §9.1).** Save always succeeds for a
well-formed request (even when the spec has YAML-level or semantic problems, the last parseable
spec plus the raw YAML text are kept). Problems are impossible to miss:

- a status strip at the top of the editor: "✗ 3 problems — this draft can't be submitted" (Wash
  danger chip) or "✓ Valid" ;
- every problem inline at its field (step card, param, `when:`, selector) and, in the YAML tab,
  as a gutter marker on its line with the message (YAML parse errors carry line and column);
- a problem count on the draft's Studio card and on the catalog row;
- **Submit for review** stays visible but disabled with the reason ("fix 3 problems first"),
  from `runbook_actions` (already the case for invalid runbooks), and the server refuses
  `submitRunbook` for an invalid spec regardless of the UI.

Because a YAML text that does not parse cannot be normalized, the draft stores it separately:
`runbook_versions.source` holds the operator's raw YAML when it fails to parse, `spec` keeps the
last valid parse, and the editor reopens on the raw text with the parse error. Submitting
requires `source` to be empty (the text parsed) and `problems` to be empty.

Wash: `.w-card`, `.w-tabs`, `.w-field`, `.w-stepper`, segmented control, chips from
`orch_effect` / `orch_tool_status` vocabulary; new classes in
`static/css/wash/patterns/orchestration.css` (`.orch-rb-*`), no hex colours, no coloured side
accents. New JS module `static/js/orchestration/runbooks.js`; shared helpers (`paramsForm`,
`schemaTable`) move into `ui.js` or stay exported from their modules.

### 4.1 Dynamic selection and auto-populated inputs

One set of picker components (`static/js/orchestration/pickers.js`) used everywhere a target or
input is chosen: the runbook editor's step cards, the run wizard, the schedule form, the tool and
runbook test dialogs, the access-policy editor's match fields and the bulk-upgrade dialog. No
list is baked into the page; each picker reads the server and follows the change stream.

**Tool and runbook picker**
- A searchable combobox (ARIA combobox + listbox, keyboard-first) over the live catalog: search
  by id, title, summary and category; grouped by category; each option shows the effect chip,
  status, latest version and a one-line summary.
- Choosing a tool picks its **latest published version**; a version selector next to it lists
  every version with its status (deprecated shown with a warning, retired and drafts hidden
  except in test dialogs, which list drafts and in-review versions instead).
- Filtered by context: runbook steps and runs offer only dispatchable versions; test dialogs
  offer only versions that can be tested; the policy editor offers tool-id globs as free text
  with the catalog as suggestions.
- Live: when the catalog changes (`tool` scope on `/api/orchestration/events`) the options
  refresh; if the selected version is deprecated or retired meanwhile, the field shows it and the
  plan/validation reports it. A newly published version shows an "update available" hint.

**Inputs generated from the selected version**
- The parameter form is rebuilt from the selected version's `inputs` schema every time the
  selection changes: enums become selects, booleans toggles, numbers number fields with
  min/max, string lists list editors, objects a JSON field with schema hints; descriptions are
  hints; required fields are marked.
- **Pre-filled** with, in order: values already entered (kept when the new schema still accepts
  them), the schema `default`, and for runbook steps nothing else (an empty optional field means
  "use the default at run time"). Values the new schema rejects are not silently dropped: they
  are listed under the form ("`keep_days: 120` no longer valid (max 90)") with a one-click
  remove.
- Each field validates as you type with the same subset validator as the server (the shared
  `node/schema.py` rules are mirrored by a small JS validator whose test cases are generated from
  the Python tests so the two cannot drift), and the server's `planRun` / `validateRunbook`
  remain the authority.
- Runbook runs: the form is generated from the runbook's `inputs`; runbook steps: from the step
  tool's inputs, with the literal/expression toggle of §4.
- Expression completion offers only what exists: declared runbook inputs, *earlier* steps and
  their tools' declared outputs (with types), `steps.<name>.state`, and `node.labels.<key>` keys
  seen on the fleet.

**Targets: nodes, labels and groups**
- The label selector becomes a selector builder: key and value pickers populated from the labels
  actually present on nodes (with node counts), operators `=`, `!=`, `in`, `notin`, exists,
  absent, plus a raw text mode; groups from the group list; explicit nodes from a node picker
  (name, status, online, labels). Test dialogs list only active canary nodes.
- A live "matches N nodes (M excluded)" count under the selector, computed by the server
  (`planRun` for runs, a new `previewSelector` for schedules and step `nodes` fields), with the
  exclusion reasons one click away.

**Backend support**
- `GET /api/orchestration/catalog/options?purpose=run|step|test|schedule` — compact option list
  (ref, id, version, title, category, effect, status, latest, inputs schema, outputs schema)
  so pickers don't load full tool details.
- `GET /api/orchestration/labels` — `{key: {value: count}}` over non-revoked nodes, plus groups.
- `previewSelector {selector}` (read-only command, operator and AI) — matched/excluded nodes
  with reasons, without a tool.

## 5. Node setup helper

A guided **Set up a node** wizard on the Nodes page (and the Overview's empty state), replacing
the one-shot enrol dialog as the default path (the dialog stays as "Quick enrol" for experts).
Each step shows its status with the Wash stepper and can be resumed: the wizard's state is the
join token and node it created, so closing the browser and coming back picks up where it was.

1. **Master readiness.** Checks the node endpoint: configured, running, bind kind (private,
   overlay, loopback/tunnel), fingerprint. If it isn't configured, an inline form offers the
   master's own addresses as choices (from a new `GET /api/orchestration/network/interfaces`:
   every local address with its classification; public and any-address entries are shown
   disabled with the reason), and saves through `setNetwork`. If the node will be on a different
   network, a link into the WireGuard overlay helper.
2. **Describe the node.** Name, labels (key/value pickers seeded with existing labels, §4.1),
   canary flag, optional WireGuard peer (generates the peer config to install first), token
   lifetime.
3. **Get the agent onto the node.** Three ways, all producing the same result:
   - **One-liner** (default): the digest-checked install command (`zipapp.install_command`),
     copy button.
   - **Download**: `mucli-node.pyz` and a pre-filled `install.sh` (token, master URL and
     fingerprint embedded) as two downloads from the GUI itself, for machines that cannot reach
     the master's endpoint over HTTP before enrolment or for review before running; the
     wizard shows their SHA-256 and the `scp` + `sudo bash install.sh` commands. New routes on
     the GUI (loopback, CSRF-protected because the script carries a live token):
     `GET /api/orchestration/setup/<token_id>/agent.pyz` and `.../install.sh`.
   - **Manual**: the individual commands (`python3 mucli-node.pyz enroll ...`, policy file,
     systemd unit) for hosts where the installer isn't wanted.
   A short checklist of node prerequisites (Linux with systemd, Python 3.10+, sudo, reachability
   of `<master>:30312`) with the command to test reachability from the node.
4. **Waiting for the node.** Live (change stream, no polling): "waiting for enrolment" until the
   join token is used, then the node's reported facts (hostname, OS, kernel, Python, systemd) and
   its **local policy summary** (with a warning if it is read-only only, invalid, or runs without
   the sandbox), and the address it connected from. Clear failure hints: token expired (create a
   new one in place), fingerprint mismatch, nothing after N minutes (reachability checklist).
5. **Accept.** Review name, labels, facts and policy, then accept (`acceptNode`). The node's
   labels can still be corrected here.
6. **Confirm it is alive.** Two checks, both shown with their results:
   - **Heartbeat:** a poll received after acceptance (last seen < 30 s, agent version);
   - **Round trip:** a smoke run of the built-in read-only `sys.facts` tool on just this node,
     proposed by the operator (`verifyNode {node_id}` → an ordinary run with `title "Setup check"`,
     so it is planned, policy-checked, signed, dispatched and audited like any other). Success
     shows the duration and the facts it returned; failure shows the execution's reason (for
     example *refused by node: tool not in allowed_tools*) and what to change on the node.
   The wizard ends with links to the node page and "Set up another" (keeps the labels).

Backend for §5: `GET /api/orchestration/network/interfaces`; `setup/<token_id>/…` downloads
(the token id is the stored hash prefix, the raw token is embedded only in the downloaded
script and is never retrievable again after download expiry); join tokens record `node_id` when
used (already the case) and the change stream emits on use (already the case: `node.enroll`);
`verifyNode` command (operator only; falls back with a clear message when `sys.facts` is not
installed, offering to install the starter pack). Everything is audited (`setup.download`,
`node.verify`).

## 6. CLI and AI

- `mucli orch runbooks [list|show|validate FILE|save FILE|submit REF|publish REF|test REF --nodes …]`
  for config-as-code workflows (`validate` exits non-zero on errors, like `lint`).
- `orch_runbook_draft` gains `base_digest` (optional) and returns structured problems;
  new `orch_runbook_validate` (read-only) and `orch_runbook_test` (proposal) tools, added to
  `ORCHESTRATION_TOOLS`. The orchestration session prompt mentions validating before saving.

## 7. Phases

| Phase | Delivers | Exit criteria |
|---|---|---|
| **R1 Backend** | `validateRunbook` with structured problems and the new semantic checks; `base_digest` on save; `newRunbookVersion`, `discardRunbookDraft`; `runbook_vm` diff/notes/versions | A spec referencing a later step, an undeclared input or a wrong literal param type is reported with its path; a stale save returns 409 |
| **R2 Editor (YAML + checks)** | Route, Studio list section, create/new version/discard, YAML editor with live validation and line markers, the problems strip, saving invalid drafts (`source` column for unparseable YAML), checks column, save with conflict handling | An operator creates, edits, saves and submits a runbook without the chat; a draft with a YAML syntax error and one with a semantic error both save, reopen showing the errors, and cannot be submitted (UI and server); a concurrent AI edit shows the stale banner and the operator's save is refused until reload |
| **R3 Structured editor + bulk upgrade** | Step cards, tool picker, schema-driven params with literal/expression toggle and completion, `when`/`on_failure`/`nodes`, inputs table, reorder (mouse and keyboard), lossless YAML ↔ form switching, server-side expression preview, per-step and bulk tool upgrade with impact report | Round-trip: every runbook in the starter/test fixtures survives form → YAML → form unchanged (digest-equal); all actions reachable by keyboard; a bulk upgrade that turns a read-only step mutating shows the effect change and the new access decision before it is applied, and a param the new schema rejects is reported on its step |
| **R4 Draft tests + test rules** | `runbook_test` run kind, `proposeRunbookTest`, `review.tests` for runbooks (schema v2 migration), test dialog and results; the `run: test` access-policy match key and the default test rule | A draft runbook runs in check mode on a canary after operator approval; with an `allow` test rule for read-only effects an AI-proposed read-only test starts without approval while a mutating one still waits; editing the draft afterwards blocks the dispatch; non-canary nodes are excluded with the reason; existing policies (no `run` key) behave exactly as before |
| **P1 Dynamic pickers** | `pickers.js` (tool/runbook/version combobox, selector builder, node picker), `catalog/options`, `labels`, `previewSelector`; the run wizard, schedule form and test dialogs switched to them; form rebuild with kept/rejected values; JS schema validator with shared test cases | Publishing a new version while the wizard is open updates its options without a reload; switching versions keeps compatible values and lists rejected ones; the selector builder offers only labels that exist and shows the live match count |
| **N1 Node setup helper** | Setup wizard (readiness, describe, agent download/one-liner/manual, live wait, accept, heartbeat + `sys.facts` round trip), `network/interfaces`, setup downloads, `verifyNode` | On a spare port with a local node agent: the wizard configures the endpoint from the offered addresses, the downloaded `install.sh` and `mucli-node.pyz` match their shown digests, the wizard advances by itself when the agent enrols, and ends green only after the smoke run succeeds; a node whose policy refuses `sys.facts` ends with that reason shown |
| **R5 CLI/AI** | `mucli orch runbooks …`, AI tool updates | CI can `validate` a runbook directory; the AI validates before saving in the Studio chat |

R1 and R2 deliver most of the value and can ship alone; R3 is the largest UI piece and builds on
P1 (its step cards use the shared pickers). P1 and N1 are independent of the runbook work and
can go first.

## 8. Tests

- **Pure:** the new semantic checks (earlier-step references, undeclared inputs, literal params
  vs schema, unknown/missing params, unknown output fields as warnings), problem paths, and that
  `normalize` stays idempotent by digest (as for manifests).
- **Commands:** `validateRunbook` is read-only (no audit write, no change emitted);
  `base_digest` conflict; discard/new-version availability through `runbook_actions`; the AI can
  validate, save, propose tests but not publish, approve or discard (extend the allowlist test).
- **Runs:** `runbook_test` canary-only, digest freeze, results recorded, schema v2 migration
  (`review`, `source` columns) on an existing v1 store.
- **Test rules:** policies without `run` keys decide exactly as before for normal runs and
  tests (default rule → `confirm`); an `allow` test rule auto-starts only matching tests; no rule
  can put a test on a non-canary node, skip the digest freeze or remove typed confirmation for
  destructive effects; `validate_rules` accepts `run: test|run` and rejects other values.
- **Invalid drafts:** saving YAML that doesn't parse keeps `source` and the last valid `spec`;
  submit is refused by the server while `source` is set or problems exist; problems carry paths
  and YAML line/column.
- **Pickers:** option endpoints return only versions valid for the purpose; `labels` counts
  exclude revoked nodes; `previewSelector` matches `planRun`'s node resolution; the JS schema
  validator passes the same generated cases as `node/schema.py`; Playwright: live option refresh
  on publish, value carry-over and rejection listing on version change, keyboard-only use of the
  comboboxes.
- **Node setup:** interface listing classifies addresses like `overlay.classify`; setup downloads
  require CSRF and a live token, embed the right URL/fingerprint/digest, and are audited;
  `verifyNode` creates an ordinary audited run limited to the node and fails clearly without
  `sys.facts`; Playwright drives the whole wizard against a real local agent (sandbox `none`).
- **Bulk upgrade:** report lists only steps with newer published versions; effect escalation,
  schema-incompatible params, removed referenced outputs and rollback changes are detected;
  nothing is stored until save.
- **GUI:** router tests for the new commands (CSRF already covered by the route test);
  Playwright on a spare port (never 30311): create a runbook from scratch, add three steps with
  an expression param, reorder, YAML round-trip, save, conflict banner, submit, propose a test —
  in Paper and Dusk, with console-error checks; Wash contract tests for the new CSS.

## 9. Decisions (2026-10-19)

1. **Drafts save with errors; submit is blocked.** Any draft can be saved, including YAML that
   does not parse, and the editor makes every syntax and validation problem visible (status
   strip, inline at the field, YAML line markers, counts on cards). Submitting for review is
   refused in the UI and on the server until the draft parses and has no problems (§4).
2. **Bulk upgrade is allowed.** One action upgrades every (selected) step to the latest published
   tool version, after showing the per-step diff and impact (§4, R3).
3. **Test approval rules are allowed.** Tests stop being hard-wired to operator approval; the
   access policy decides through a `run: test` match key, with a default rule that keeps
   today's behaviour (confirm). Canary-only targeting, test-key signing, the digest freeze and
   typed confirmation for destructive effects remain non-negotiable (§3 item 7, R4). This
   supersedes the "every test is approved by the operator" rule in `orchestration.md` §4 step 5
   once R4 ships; update that document then.
4. **Selection is dynamic.** Tool, runbook, version, node, label and group choices come from
   the live catalog and fleet, and parameter fields are generated and pre-filled from the
   selected version (§4.1).
5. **Node setup is guided.** A wizard hands out the agent (one-liner, downloads or manual),
   follows registration live, and confirms the node is alive with a heartbeat and a real
   round-trip run (§5).

## 10. Implementation status (2026-10-19)

All phases (R1–R5, P1, N1) are implemented on branch `orchestration`. Operator guide:
[`../orchestration.md`](../orchestration.md) (*Enrol nodes*, *Runbooks*, *Access policy*, CLI).

| Phase | Code | Covered by |
|---|---|---|
| R1 | `runbook.analyze` (structured problems with paths, reference and literal-param checks, YAML positions, preview), `catalog.validate_runbook` / `save_runbook_draft(base_digest, source)` / `discard_runbook_draft` / `new_runbook_version`, `vm.runbook_vm(detail)` | `tests/test_orch_runbook_editor.py` |
| R2, R3 | `static/js/orchestration/runbooks.js` (editor, YAML tab with gutter, checks column, conflict banner, bulk upgrade, test dialog), `catalog.js` runbook page, `studio.js` Runbooks section; `catalog.upgrade_runbook_tools` | `tests/test_orch_web_runbooks.py` (form ↔ spec round trip under node), Playwright (below) |
| R4 | `runs` kind `runbook_test` (canary-only, digest frozen at proposal, approval and dispatch), `review.tests` on runbook versions, schema v2 migration (`review`, `source`), `access` match key `run` and the default test rule, `Orchestrator.start_if_allowed` | `tests/test_orch_runbook_editor.py` |
| P1 | `static/js/orchestration/pickers.js`, `schema.js`; `GET catalog/options`, `GET labels`, `previewSelector`; the run wizard, schedule form and test dialogs use them | `tests/test_orch_web_pickers.py` (JS validator vs `node/schema.py` on one generated case list, selector grammar), Playwright |
| N1 | `static/js/orchestration/setup.js`; `GET network/interfaces`, `GET setup/<token id>[/agent.pyz|/install.sh]` (CSRF), `verifyNode`, `zipapp.install_script(token=…)` / `manual_commands` | `tests/test_orch_runbook_editor.py`, `tests/test_orch_web_setup.py`, Playwright with a real agent |
| R5 | `mucli orch runbooks …`, `orch_runbook_validate`, `orch_runbook_test`, `orch_runbook_draft(base_digest)` | `tests/test_orch_runbook_editor.py` |

Browser verification (Playwright on spare ports, Paper and Dusk, console errors checked; the only
console error is the deliberate 409 of a refused save or a missing `sys.facts`):

- **Runbook editor:** create a runbook from scratch, add an input and three steps with an
  expression param (completion), reorder so a step references a later one (the error appears on
  the card and the strip), move it back with `Alt+↓`, YAML round trip, save, an AI save during
  unsaved edits (stale banner, *Keep mine*, refused save, reload), save YAML that doesn't parse and
  reopen on the YAML tab with the error, propose a canary test, submit for review; bulk upgrade of
  a read-only step to a mutating version shows the effect and access-decision change and the
  rejected parameter before applying, then on the step.
- **Pickers:** publishing a version while the run wizard is open shows "v2 available"; switching
  keeps fitting values and lists the rejected one; label conditions show a live match count;
  schedule form and tool test dialog.
- **Node setup:** the wizard configures the endpoint from the offered addresses, both downloads
  match their shown digests, a real `mucli-node.pyz` (enrolled with the token from the downloaded
  `install.sh`, sandbox `none`) moves the wizard on by itself, accept, the missing-`sys.facts` path
  installs the starter pack, and it ends green after the round trip; resuming in a second browser
  finds the finished check. A node whose policy denies `sys.*` ends with that reason and the
  matching hint.

Deviations and limits:

- **Raw join tokens for the wizard** are held in the GUI process's memory until used or expired
  (the store keeps hashes only). After a GUI restart the wizard offers a new token in place.
- **The YAML tab shows the normalized spec** (with `version`, `effect` and empty fields), since
  switching views goes through `validateRunbook`.
- **Deprecated step tools** are a warning, as the plan's warnings list says, although runs refuse
  them until restored.
- **Approval of a runbook test** re-checks the digest frozen at proposal (a change in between
  refuses the approval), in addition to the dispatch-time check.
