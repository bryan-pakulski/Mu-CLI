# Plan: Orchestration — a safe, AI-designed administration layer for a fleet of machines

Status: implemented 2026-10-18 (P0–P5, see §14) · decisions recorded 2026-10-18 (§12) · Owner: TBD · Builds on: `durable_engineering_jobs.md`, `job_templates.md`,
`container_security.md` · Reference: Wash design guide

## 1. Idea

A third top-level mode next to **Chat** and **Work**: **Orchestration**. One mucli **master**
(the GUI host) manages many **nodes** (machines running a small `mucli-node` agent). An AI helps
the user design **tools**: typed, reviewed, versioned administrative actions ("rotate nginx logs",
"report disk usage", "restart service X with a health check"). Published tools form a
**catalog**. Tools run on nodes through a **safe execution layer**: the AI proposes and composes,
but never gets a shell on a node, never sees credentials, and cannot run anything that a person
has not approved as a tool. Tools, runbooks and policy **import/export** as bundles, so system
management becomes configuration that can be reviewed, versioned and shared.

The point is that capability is **grown deliberately**. Each new tool is a small, reviewed
contract, and the catalog becomes the organisation's vocabulary of safe operations. The AI is a
designer and operator on top of that vocabulary, not a root shell.

### Goals

- **No ambient access.** Nodes run only signed, catalogued tool versions with typed parameters.
  There is no "run this command" path from the model to a node.
- **Interactive tool discovery.** Tool creation is a guided conversation: intent, then spec, then
  implementation, then a test on a canary, then human review, then publishing.
- **Production-grade runs.** Targeting by labels, canary and batched rollouts, failure
  thresholds, timeouts, cancellation, idempotency, rollback tools, approvals and a full audit
  trail.
- **Config as code.** Import and export of tools, runbooks, policies and groups as signed
  bundles or a git-friendly directory. Secrets are never exported.
- **Defence in depth.** A compromised master, a compromised model session or a malicious
  imported bundle must not be enough to run arbitrary code on a node (see §6.7, node policy).

### Non-goals (for now)

- Replacing Ansible or Salt wholesale. Existing playbooks can be wrapped as tools later.
- Interactive remote shells or terminals.
- Running a model on nodes. Nodes never talk to a model provider.
- Platforms other than **Linux with systemd** (decision §12.4).
- User accounts, roles and multi-person approval: one operator, one master (decision §12.1).
- Any listener on the public internet: master and nodes talk only over a private network
  (decision §12.7, §6.0).
- Multi-master or HA. A single master with sqlite (WAL), designed so the store can move to
  Postgres later.

## 2. What exists already (reuse, don't rebuild)

| Need | Existing piece |
|---|---|
| Page + API router pattern | `mu/gui/routers/containers.py` serves `/containers` HTML **and** its API; nav in `templates/partials/sidebar.html` (`primary` tuple list) |
| Live updates | `mu/jobs/core/changes.py` `ChangeBus` (coalesced, scoped parts) + SSE; `mu/gui/bus.py` `EventBus` (replay ring, `Last-Event-ID`) |
| State machine / actions / commands / view models | `mu/jobs/core/states.py`, `actions.py` (`available_actions` → `ActionVM{enabled,reason}`), `commands.py` (`dispatch`, `CommandRejected`→409), `vm.py` |
| Human-readable timeline | `mu/jobs/core/notes.py` (notes derived from events at read time) |
| Leases / workers | `mu/jobs/lease.py` (`StageLease`, heartbeats, fencing), `JobController` |
| Remote worker auth | `mu/container/worker.py` (`X-MuCLI-Worker-Token`, supervisor callback), `supervisor.py` `validate_token` (hmac `compare_digest`), tokens minted with `secrets.token_urlsafe(32)` |
| "AI never holds the credential" | `mu/jobs/sandbox/model_relay.py` (host-side credentials, budgets, `RelayDenied`) |
| Pure policy decisions | `mu/jobs/sandbox/docker_proxy.py` `decide(...) -> Decision(allowed, reason)` |
| Approvals | `mu/agent/approval.py`, `mu/gui/prompts.py` `PromptStore`, descriptor `approval_policy` |
| Redaction / secret scan | `mu/security/secret_paths.py` `redact_secrets`, `mu/tools/_scrub.py`, `mu/security/diff_scan.py` |
| Tool registry | `mu/tools/__init__.py` `@tool`, `descriptors.py` `ToolDescriptor` (phase, group, approval_policy), `capabilities.py` (per-session-type allowlists) |
| Settings schema | `mu/jobs/job_settings.py` `RULES_SCHEMA` / `_f(...)` → generated `configuration.md` |
| Evidence and notes UI | `mu/jobs/evidence.py`, Notes tab |
| Audit trail | `mu/trace/emitter.py` (JSONL), job events table |
| Mobile | `mobile/android/src/navigation/AppNavigator.tsx` (bottom tabs), per-feature `api/*.ts`, `api/sse.ts` |

**No user authentication, by decision (§12.1).** The GUI has none (`launcher.py` refuses
non-loopback binds unless `MUCLI_GUI_ALLOW_REMOTE=1`), and orchestration keeps it that way: one
operator, one master. Safety comes from **where things listen** instead (§6.0): the GUI stays on
loopback, the node endpoint listens only on a private interface, and machine-to-machine trust
(node credentials, signed dispatches) is independent of users.

## 3. Concepts and data model

All of it lives under `$MUCLI_HOME/orchestration/`, in `orch.sqlite3` (WAL, schema-versioned like
`JobStore`) plus a content-addressed blob store `blobs/<sha256>` for scripts and bundle assets.

### 3.1 Node

`id`, `name`, `labels{}` (e.g. `env=prod, role=web, region=syd`), `groups[]`, `status`
(`pending → active → quarantined → revoked`), `last_seen`, `agent_version`, the public key or
cert fingerprint, **facts** (collected by built-in read-only collectors: OS, kernel, arch, CPU,
memory, disks, uptime, services, installed package managers, container runtime) with
`facts_at`, and a copy of the node's **local policy summary** (§6.7), so the master can see what
the node will refuse.

### 3.2 Tool and ToolVersion (the catalog)

A **Tool** is a named capability; a **ToolVersion** is an immutable, content-hashed
implementation of it. The manifest is YAML in bundles and JSON in the DB:

```yaml
id: nginx.rotate-logs            # dotted namespace
version: 3                       # monotonically increasing; immutable once published
title: Rotate nginx logs
summary: Rotate and compress nginx access/error logs, then reopen them.
category: maintenance            # inventory | diagnostics | maintenance | deploy | security | ...
effect: mutating                 # read_only | mutating | destructive  (drives approvals & UI)
idempotent: true
targets: {os: [linux], requires: [systemd, nginx]}   # matched against node facts
privilege: {run_as: root}        # or a named service user; node policy may cap it (§6.7)
inputs:                          # JSON Schema; validated on master AND node
  type: object
  properties:
    keep_days: {type: integer, minimum: 1, maximum: 90, default: 14}
    compress: {type: boolean, default: true}
  additionalProperties: false
outputs:                         # structured result the tool writes to $MUCLI_RESULT (JSON)
  type: object
  properties: {rotated_files: {type: array, items: {type: string}}, freed_bytes: {type: integer}}
implementation:
  runtime: bash                  # bash | python3 | builtin
  entry: rotate.sh               # blob(s) referenced by sha256 in the version record
  check_mode: true               # supports MUCLI_CHECK=1 (dry run: report, change nothing)
limits: {timeout_s: 300, max_output_kb: 512, cpu: 1, memory_mb: 256}
rollback: nginx.restore-logs@2   # optional compensating tool
preconditions: [nginx.config-test@1]   # tools that must pass first (read_only)
approval: confirm                # none | confirm (the operator confirms each run; access policy §6.6 decides)
rollout: {max_nodes: 50, default_batch: 10, canary: 1}
tests:                           # run during discovery / review (§4)
  - name: dry run on canary
    params: {keep_days: 7}
    check_mode: true
    expect: {exit_code: 0, output_schema: true}
```

**Parameter safety.** Parameters are **never interpolated into script text**. The executor passes
them as a JSON file (`$MUCLI_PARAMS`) and as typed, validated environment variables
(`MUCLI_P_KEEP_DAYS=14`). Strings get schema `pattern`s and `enum`s, and lint flags scripts that
`eval` parameters. Tool states: `draft → testing → in_review → published → deprecated →
retired`. Only `published` versions are dispatchable to nodes that are not canaries.

### 3.3 Runbook

An ordered composition of catalog tool versions: steps with `params` (literals, run inputs, or
the previous step's outputs via a small expression language, no code), `when:` conditions,
`on_failure: stop | continue | rollback`, and per-step targeting. A runbook is versioned and
approved like a tool, and its effect is the maximum of its steps' effects.

### 3.4 Run and Execution

A **Run** is one approved request: a tool or runbook version, params, a **target selector**
(labels, groups or explicit nodes, resolved to a frozen node list at approval time), a
**strategy** (`canary: 1, batch: 10, max_parallel: 10, failure_threshold: 10%, pause_between_s`),
`check_mode`, a deadline, and who proposed and approved it.

An **Execution** is that run on one node: states `queued → dispatched → running →
succeeded | failed | timed_out | cancelled | unreachable | refused` (refused means the node
policy said no). It records exit code, structured output, a capped log (stdout and stderr,
redacted), duration and the output digest. Runs and executions reuse the jobs-core shape:
a pure `states.py` transition table, `actions.py` availability, `commands.py` as the single
mutation path, `vm.py`, and notes. Live updates use `ChangeBus` with new scopes `node`, `tool`
and `run`.

### 3.5 Schedule and Trigger (phase 3)

Cron-style schedules for runs; later, triggers ("node fact changed", "disk > 90%" from a
read-only collector run).

### 3.6 Audit log

An append-only, **hash-chained** `audit` table: `seq`, `at`, `actor` (user, AI session or node),
`action`, `subject`, `digest(payload)`, `prev_hash`. Every proposal, approval, publish, dispatch,
result, import, enrolment and revocation goes in. It is exportable and verifiable
(`mucli orch audit verify`).

## 4. Interactive tool discovery (the "Studio")

Tool creation is a guided, stateful conversation in an **orchestration session**: a new session
type in `mu/tools/capabilities.py` with its own tool allowlist and no host shell. It is
modelled as a durable **discovery job**, so it survives restarts and shows a stepper:

1. **Intent.** The user describes the goal. The AI asks clarifying questions (targets, OS,
   frequency, what "done" looks like, what must never happen) and searches the catalog first
   (`orch_catalog_search`), so it reuses or extends an existing tool rather than duplicating it.
2. **Spec.** The AI drafts the manifest (inputs schema, effect, privilege, idempotency, limits,
   rollback, approval policy). The user edits it in a structured form plus a YAML view
   (reusing the plan-editor pattern). Nothing executes yet.
3. **Discover.** To learn the real environment, the AI may run **built-in read-only collectors**
   (`orch_node_facts`, and catalog tools whose `effect: read_only` and policy allows it) on a
   node the user picks. Output reaches the model **redacted and size-capped**. This is how the
   AI "looks" at a system without access to it.
4. **Implement.** The AI writes the script(s) and tests. Static checks run on the master:
   shellcheck or ruff, a banned-pattern lint (`curl | sh`, `eval "$param"`, `rm -rf /`,
   writes outside declared paths), a secret scan, and schema validation. Findings go back
   to the AI to fix.
5. **Test.** With approval, the tool runs **in check mode on a canary node** (or a disposable
   container node, §6.8). Results, logs and evidence are shown side by side with the manifest's
   `tests` expectations.
6. **Review.** The operator sees the manifest, the script diff against the
   previous version, lint and test results, and the AI's notes (headline plus risks, as in job
   notes), and publishes or requests changes. `destructive` tools additionally require typing the
   tool id to confirm.
7. **Publish.** The master **signs** the version (§6.3) and it enters the catalog as
   `published`. Older versions stay pinned in runbooks until upgraded deliberately.

The AI's orchestration tools, all mediated by the master:

| Tool | Effect | Approval |
|---|---|---|
| `orch_catalog_search`, `orch_tool_describe` | read catalog | none |
| `orch_nodes_list`, `orch_node_facts` | read inventory and facts | none |
| `orch_tool_draft` / `orch_tool_update` | write a **draft** | none (drafts can't run on nodes) |
| `orch_tool_lint` | static checks on master | none |
| `orch_tool_test` | run draft in check mode on a canary | per the access policy's `run: test` rules; default: human approval per test |
| `orch_run_propose` | create a **proposed** run | becomes a run only when approved |
| `orch_run_status` / `orch_run_results` | read redacted results | none |
| `orch_runbook_draft` | write a draft runbook | none |
| `orch_runbook_validate` | check a runbook spec without saving | none |
| `orch_runbook_test` | run a draft runbook in check mode on a canary | as `orch_tool_test` |

There is **no** tool that publishes, approves, edits node policy, reads secrets or dispatches
without a person. Proposals land in an **Approvals inbox** (web and mobile) using the
`PromptStore` / attention pattern already used by jobs.

## 5. Running things

- **Targeting.** The selector resolves against current node facts and labels. Nodes whose facts
  fail `targets` are excluded and the plan shows why (no silent partial runs).
- **Plan view.** Before approval: resolved node list, batches, effect badge, check-mode toggle,
  rollback available or not, estimated blast radius ("47 prod nodes in 5 batches").
- **Rollout.** Canary first. Each batch must succeed within the failure threshold before the
  next starts; otherwise the run pauses (attention) with "continue / roll back / cancel". This
  reuses the jobs "park, don't fail" behaviour.
- **Live view.** A per-node grid (status glyphs from the Wash vocabulary), streaming logs,
  structured outputs rendered as tables, and a run-level notes timeline.
- **Idempotency and retries.** Transient node disconnects re-dispatch only idempotent tools;
  non-idempotent executions become `unreachable` and need a human decision.
- **Cancellation and kill switch.** Cancel a run (nodes receive a signed cancel and kill the
  process group). A global **pause all dispatch** switch, and per-node **quarantine**.
- **Outputs.** Structured outputs and redacted logs are kept per execution. Only what a tool's
  `outputs` schema declares (plus a capped, redacted log tail) is offered to the AI.

## 6. Security architecture

### 6.0 Network: nothing on the public internet

Neither the master nor any node runs a listener on a public interface (decision §12.7). Nodes
always dial **out** to the master over a private path; the master's node endpoint binds only to a
private address. Three supported layouts:

| Layout | When | How |
|---|---|---|
| **Private network** | Master and nodes share a LAN, VPC or VPN already | Master binds the node endpoint to its private IP (e.g. `10.0.0.5:30312`); nodes dial it. Nothing else needed. |
| **WireGuard overlay** (recommended across sites/NAT) | Nodes in different networks, behind NAT | Master and nodes are WireGuard peers on a private subnet (`wg-mucli`, e.g. `10.77.0.0/24`); the node endpoint binds to the master's overlay address. One peer needs a reachable UDP port: the master itself, or a tiny **hub** (intermediary VPS running only WireGuard, no TCP services). WireGuard never answers a packet that is not from a known key, so the port is invisible to scans: nothing is advertised. Existing Tailscale/Headscale tailnets work the same way. |
| **SSH reverse tunnel** | WireGuard impossible (locked-down hosts) | The node runs `ssh -N -R` to a bastion it can already reach; the master dials the tunnelled port. The node agent only ever sees `localhost`. |

The master's helpers make the overlay painless without adding node dependencies:
`mucli orch overlay init` generates the WireGuard keys and subnet for the master (or hub), and the
enrolment dialog emits, per node, a `wg-quick` config plus the `install.sh` command (WireGuard
itself comes from the distribution's `wireguard-tools`; the node agent stays standard-library
only). The node endpoint **refuses to start** on `0.0.0.0` or a public address unless explicitly
overridden, and the Nodes page shows which interface it listens on.

The **GUI** stays on loopback. The operator reaches it on the master itself, over the same
overlay (bind to the overlay address), or via `ssh -L 30311:127.0.0.1:30311 master`.

### 6.1 One operator, no user accounts

There are no users, roles or logins (decision §12.1); whoever can reach the GUI is the operator.
That is acceptable **because** of §6.0 (the GUI is loopback or overlay only), and it keeps the
safety properties that don't depend on users:

- The AI still cannot publish tools, change policy, read secrets or start a run that the access
  policy (§6.6) says needs confirmation. Its proposals wait for the operator in an approvals
  inbox; `confirm` is a deliberate click (destructive tools: type the tool id).
- Mutating endpoints require a per-browser **CSRF token**, so a malicious web page on the
  operator's machine cannot drive the GUI.
- Machine trust (node credentials, signatures, node policy) is unaffected by the absence of users.
- The audit log records the actor as `operator`, `ai:<session>` or `node:<id>`.

If accounts are wanted later, they slot in front of the same command layer (`commands.py`)
without changing the model.

### 6.2 Node enrolment and transport

The node agent uses **only the Python standard library** (decision §12.2): no WebSocket or
crypto packages, so it installs cleanly on any systemd distribution with Python 3.10+.

- **Outbound only, HTTPS long-poll.** The node calls `POST /api/orchestration/node/poll`
  (held open up to ~25 s; returns dispatches and cancels) and `POST /api/orchestration/node/events`
  (acks, output chunks, results), using `urllib.request` + `ssl`. A poll doubles as the
  heartbeat. Nodes need no inbound ports, which suits NAT, firewalls and HTTP proxies.
- **TLS pinning.** At enrolment the node stores the master's certificate (or its SHA-256
  fingerprint) and verifies it on every connection (`ssl.create_default_context(cafile=…)`
  plus a fingerprint check), so a self-signed master works without trusting a public CA.
- **Enrolment.** The operator creates a **one-time join token** (TTL 1 h, optional label preset).
  `mucli-node enroll --master URL --token T --fingerprint SHA256:…` exchanges it for a
  per-node **credential** (32 random bytes, stored 0600 on the node; the master keeps only a
  hash). Status stays `pending` until the operator accepts the node, with its facts shown.
- **Revocation** deletes the credential hash; the node is refused on its next poll. Credentials
  rotate on a schedule through the poll channel.

### 6.3 Signing: what a node will run

- The master holds a **signing key** (Ed25519 via `cryptography`, which becomes a declared
  master requirement; stored 0600, HSM/KMS later). Nodes verify with a small **vendored,
  verify-only Ed25519** implementation (RFC 8032 reference code, tested against the RFC test
  vectors), so the node stays dependency-free. Published tool versions are signed over `(tool id, version, manifest
  digest, blob digests)`.
- Each **dispatch** is a signed envelope:
  `{exec_id, run_id, tool_id, version, version_sig, params, check_mode, deadline, nonce}`.
  The node verifies both signatures, the deadline and the nonce (replay cache), validates
  `params` against the manifest's schema **locally**, and checks its own policy (§6.7) before
  running anything.
- Drafts under test are signed with a separate **test key** that nodes accept only if they
  are marked `canary`.

### 6.4 Secrets

- A **built-in** master secret store (decision §12.5): AES-256-GCM (`cryptography`) with a data
  key in a 0600 key file, optionally wrapped by an operator passphrase (scrypt). No external
  vault. Tools declare `secrets: [db_password]`; the
  executor injects them as environment variables or files **on the node at execution time**.
  The AI, manifests, logs and exports only ever see names.
- Node output is redacted on the node, then again on the master (`redact_secrets` plus the
  declared secret values as literals, like the injected-credential check in jobs) before it is
  stored or shown to the AI.

### 6.5 Execution sandbox on the node

- Each execution runs in a fresh temp dir with a clean environment (only `MUCLI_*`, declared
  secrets and the tool's declared env), as the manifest's `run_as` user (capped by node policy),
  with timeout and output caps.
- Where systemd exists: `systemd-run --scope` with `MemoryMax`, `CPUQuota`, `NoNewPrivileges`,
  `PrivateTmp`, and optional `ProtectSystem`/`ReadWritePaths` from the manifest's declared
  paths. The process-group kill on timeout or cancel reuses `verification._kill_process_group`.
- Linux with systemd only (decision §12.4): `systemd-run` is a hard requirement, not optional.
- Privilege: the node agent runs as an unprivileged `mucli-node` user. Root tools go through a
  generated sudoers entry scoped to `mucli-node-exec` (a fixed helper that only runs verified
  blobs from the node's content store), never a general sudo.

### 6.6 Access policy on the master (who may run what, where)

Decision §12.3: the AI may run read-only tools, governed by a policy you control. **Access
policy** is an ordered list of rules evaluated by a pure function
`access.decide(actor, tool_version, nodes) -> allow | confirm | deny` (first match wins):

```yaml
- name: AI diagnostics outside prod
  match: {actor: ai, effect: read_only, nodes: "env!=prod"}
  decision: allow
- name: AI diagnostics on prod are confirmed first
  match: {actor: ai, effect: read_only, nodes: "env=prod"}
  decision: confirm
- name: anything that changes a system is confirmed
  match: {effect: mutating}
  decision: confirm
- name: destructive changes are confirmed by typing the tool id
  match: {effect: destructive}
  decision: {confirm: typed}
- name: AI never touches the payment hosts
  match: {actor: ai, nodes: "role=payments"}
  decision: deny
```

Matches can use actor (`ai` or `operator`), effect, category, tool id glob and a label selector.
Since the runbook editor plan (`runbook_editor.md` §3 item 7, decision §9.3) they can also use
`run: test | run`: a rule without `run` matches ordinary runs only, and canary tests of drafts are
decided by `run: test` rules (the shipped default confirms them, which keeps the earlier "every
test is approved by the operator" behaviour unless you add an `allow` rule). Canary-only
targeting, test-key signing, the digest freeze and typed confirmation for destructive effects
hold whatever the rules say. Rules are edited in Settings, versioned, audited and exported with bundles. Every decision is
shown in the run plan ("allowed by rule *AI diagnostics outside prod*"), and the node-local
policy below still has the final veto.

### 6.7 Node-local policy (defence in depth)

`/etc/mucli-node/policy.yaml` is owned by root and edited **on the node**, not from the master:

```yaml
accept_categories: [inventory, diagnostics, maintenance]   # e.g. never "deploy" here
max_effect: mutating          # refuse destructive tools outright
max_privilege: root
allowed_tools: ["*"]           # or an explicit allowlist of tool ids
denied_tools: ["db.*"]
require_check_mode_first: true
quiet_hours: "Mon-Fri 09:00-17:00 Australia/Sydney"
```

The node refuses (execution state `refused`, with the reason) anything outside it. A fully
compromised master can therefore only run what each node's owner allowed. Implement it as a
pure `decide(envelope, manifest, policy) -> Decision` function, modelled on `docker_proxy.decide`
and exhaustively unit-tested.

### 6.8 Disposable test nodes

The master can start a **container node** (reusing `mu/container` templates) from a chosen
image for discovery tests, so tools are exercised against a throwaway OS before any real
canary.

## 7. Import / export

- **Bundle**: `*.mucli-orch.tar.zst` containing
  `manifest.json` (format version, created_by, bundle digest),
  `tools/<id>/<version>/{tool.yaml, files…, tests…}`,
  `runbooks/<id>/<version>.yaml`, `policies/` (access policy rules, rollout defaults),
  `groups.yaml` (label selectors, not machines), `schedules.yaml`, and `SIGNATURE` (Ed25519
  over the digest tree).
- **Never exported:** secrets (only names), node credentials, audit internals, node identities
  (optional anonymised inventory snapshot for documentation).
- **Import** shows a diff (new, updated, conflicting versions). Imported versions arrive as
  `in_review`, unless the bundle is signed by a key the operator has marked trusted, in which case
  they may be published directly (still audited). Version numbers can't be overwritten;
  conflicts become a new version.
- **Config as code**: `mucli orch export --dir ./ops` writes the same layout as plain files for
  git; `mucli orch import --dir ./ops` (or a git URL plus ref) syncs it. CI can lint and test
  bundles with `mucli orch lint ./ops`.
- **Starter packs**: a curated built-in bundle of read-only tools (disk, memory, services,
  pending updates, certificate expiry, open ports, failed systemd units). It makes the system
  useful on day one and gives the AI examples to learn the house style from.

## 8. Surfaces

**Web** (`/orchestration`, one router `mu/gui/routers/orchestration.py` serving the page and
`/api/orchestration/*`, nav entry in `partials/sidebar.html`, `static/js/orchestration/*.js`,
`patterns/orchestration.css`, Wash components only):

- **Overview**: fleet health, attention items (approvals, paused runs, unreachable nodes),
  recent runs.
- **Nodes**: inventory table (labels, status, last seen, agent version, facts), node detail
  (facts, policy summary, recent executions), enrol (create join token, copy command), accept,
  quarantine, revoke.
- **Catalog**: tools by category with effect badges, version history, diff, tests and usage;
  runbooks.
- **Studio**: discovery chat (orchestration session) beside the live tool draft (spec, script,
  tests, lint, test results), plus the stepper of §4.
- **Runs**: plan view, approval, live per-node grid, logs, outputs, notes, rollback.
- **Approvals**: inbox (also surfaced in the global "needs you" chip).
- **Audit**: filterable log with chain verification.
- **Settings**: network (node endpoint interface, overlay), access policy, signing keys, trusted bundle keys, secret store, defaults
  (rollout, approvals), import/export.

**Mobile**: a fourth tab, Orchestration, scoped to what's useful on a phone: approvals inbox,
run status, node health, kill switch. API in `api/orchestration.ts`, SSE on
`/api/orchestration/events`.

**CLI**: `mucli orch nodes|tools|runs|export|import|lint|audit verify` for automation and CI.

## 9. Code layout

```
mu/orchestration/
  __init__.py
  models.py            # Node, Tool, ToolVersion, Runbook, Run, Execution dataclasses
  store.py             # sqlite (WAL), schema versioning, blob store
  states.py            # pure transition tables (tool lifecycle, run, execution)
  actions.py           # facts -> available actions (shared web/mobile/CLI)
  commands.py          # the only mutation path; emits ChangeBus parts
  vm.py                # view models
  notes.py             # human-readable run/tool timelines
  manifest.py          # schema, validation, digests
  lint.py              # static checks (shellcheck/ruff adapters, banned patterns, secret scan)
  signing.py           # Ed25519 keys, sign/verify, trust store
  access.py            # access policy rules: allow | confirm | deny (pure decide())
  overlay.py           # WireGuard key/config generation for master, hub and nodes; bind checks
  scheduler.py         # rollout engine: batches, thresholds, pauses, retries
  hub.py               # long-poll hub: node sessions, dispatch queue, results, heartbeats
  enroll.py            # join tokens, certificates, accept/revoke
  secrets.py           # encrypted secret store, injection descriptors
  bundles.py           # import/export, diff, trust
  audit.py             # hash-chained audit log
  agent_tools.py       # orch_* tools for the orchestration session type
  node/                # the node agent (separately packaged)
    agent.py           # connect, hello/facts, heartbeat, reconnect with backoff
    executor.py        # sandboxed execution, caps, check mode, cancel
    policy.py          # node-local decide()
    collectors.py      # built-in read-only fact collectors
    store.py           # verified blob cache, replay cache
mu/gui/routers/orchestration.py
mu/gui/templates/orchestration.html
mu/gui/static/js/orchestration/{view,studio,runs,nodes,catalog}.js
mobile/android/src/{api/orchestration.ts,screens/Orchestration*.tsx}
```

The node agent is packaged as a single **`mucli-node.pyz` zipapp** with `install.sh` (creates
the `mucli-node` user, `/etc/mucli-node/`, `/var/lib/mucli-node/`, the systemd unit and the scoped
sudoers entry). It needs **only Python 3.10+ and its standard library**: no pip install, no model
or provider code. The master serves the current zipapp and the install command from the
enrolment dialog.

## 10. Phases

| Phase | Delivers | Exit criteria |
|---|---|---|
| **P0 Foundations** | Private-only node endpoint (bind checks), overlay helper (WireGuard configs), CSRF on mutations; `orch` store and audit; stdlib-only node agent: enrol, accept, long-poll heartbeat, facts; Nodes UI; `ChangeBus` scopes | Enrol 3 nodes behind NAT over WireGuard via a hub; revoke one; audit chain verifies; the node endpoint refuses public/any-address binds; a port scan of the hub's public IP shows nothing |
| **P1 Catalog + manual runs** | Manifest and validation; blob store; signing (vendored Ed25519 verify on nodes); executor with systemd sandbox, check mode and cancel; access policy and node policy `decide()`; Runs UI (plan, approve, live grid); starter pack of read-only tools | A signed read-only tool runs on 3 nodes; a tampered blob, bad signature, replay, out-of-policy tool and invalid params are each refused **on the node** |
| **P2 AI Studio** | Orchestration session type and `orch_*` tools; discovery job and stepper; lint; canary and container test nodes; review and publish flow; approvals inbox (web and mobile) | From a chat, the AI designs, tests (check mode) and submits a tool; the operator publishes it; the AI cannot run it unconfirmed (tested) |
| **P3 Production runs** | Runbooks; rollout strategies; failure thresholds and pause; rollback tools; schedules; secret store | A 3-step runbook across 20 container nodes with canary plus batches, auto-pausing on injected failures, then rolled back |
| **P4 Import/export** | Bundles, signature trust, diff-on-import, config-as-code dir, `mucli orch lint` for CI | Export, wipe, import into a fresh master: identical catalog, versions in review, secrets absent |
| **P5 Hardening** | Quarantine, kill switch, drift detection triggers, rate limits, metrics, backup/restore, credential rotation | Chaos test: master killed mid-rollout resumes correctly; node offline mid-execution handled per idempotency |

## 11. Tests

- **Pure functions, exhaustively:** run, execution and tool state tables; node `decide()`
  (every policy field × effect × privilege); rollout scheduler (thresholds, pauses, retries,
  idempotency); manifest validation; signature verification (tamper, replay, expiry, wrong
  key, test key on non-canary).
- **Integration (in-process):** master and N node agents over a local WebSocket with real
  enrolment, signing and execution in temp dirs; disconnect and reconnect; cancellation;
  output redaction, including declared secret literals.
- **Docker:** container nodes (`mu/container`) as a realistic fleet for rollout and runbook
  tests; the systemd sandbox on a systemd-enabled image.
- **Security regressions:** no `orch_*` tool can publish, approve or dispatch; the AI never
  receives raw logs over the cap or secret values; parameters are never interpolated (fuzz
  strings with shell metacharacters through to the script and assert they arrive literally);
  CSRF on every mutation; the node endpoint never binds a public address; imported bundles never auto-publish without a
  trusted signature.
- **UI:** Playwright flows on a spare port (enrol, propose, approve, watch, roll back), matching
  the existing GUI testing approach, plus contract tests in the Wash style.

## 12. Decisions (2026-10-18)

1. **No user authentication or user control for now:** one operator, one master; no accounts,
   roles or multi-person approval. Runs the access policy marks `confirm` wait for the operator.
2. **Node agent:** Python, **standard library only** (no pip dependencies), shipped as one
   zipapp. This drives HTTPS long-poll instead of WebSockets (§6.2) and a vendored verify-only
   Ed25519 (§6.3).
3. **AI and read-only tools:** allowed, governed by a user-controlled **access policy** (§6.6),
   with sensible defaults (allow outside prod, approve on prod).
4. **Platform:** Linux with systemd only.
5. **Secrets:** built-in encrypted store on the master.
6. **Jobs:** orchestration stays separate from code jobs for now. A later bridge (for example a
   job's review calling published read-only tools for verification evidence) is expected and
   the data model leaves room for it.
7. **Nothing on the public internet:** master and nodes connect over a private network,
   preferably a WireGuard overlay with an optional intermediary hub (§6.0).

## 13. Why this is useful in production

- **Least privilege by construction.** The fleet only ever executes reviewed, signed,
  parameter-validated tools, and each node can veto categories regardless of the master.
- **Change management built in.** Plan view, approvals, canaries, thresholds, rollbacks and a
  verifiable audit trail cover what change-control processes require.
- **Knowledge accumulates.** Every solved problem becomes a catalog entry with tests and docs,
  exportable and reviewable as code, instead of a one-off shell session.
- **The AI is most useful where it is safest.** It designs tools, reads redacted facts and
  results, composes runbooks and explains outcomes, while people keep the keys.

## 14. Implementation status (2026-10-18)

All six phases are implemented on branch `orchestration`. Operator guide:
[`documentation/orchestration.md`](../orchestration.md). Code: `mu/orchestration/` (layout as §9,
plus `service.py` (the master facade and scheduler loop), `runs.py` (planning, approval and
applying scheduler actions), `catalog.py` (drafts, lint, review, publish), `selectors.py`,
`schedules.py`, `server.py` (node endpoint), `zipapp.py`, `container_nodes.py`, `backup.py`,
`cli.py`); `mu/gui/routers/orchestration.py`, `mu/gui/csrf.py`; Android
`mobile/android/src/{api/orchestration.ts,screens/Orch*.tsx}`. Tests: `tests/test_orch_*.py`.

Exit criteria and how they are covered:

| Phase | Covered by | Not verified |
|---|---|---|
| P0 | Real TLS endpoint + node agents enrol over HTTPS long-poll, revoke refuses the next poll, audit chain verification and tamper detection, public/any-address binds refused (`test_orch_integration.py`, `test_orch_units.py`) | WireGuard configs are generated and unit-tested, but three NAT'd nodes through a real hub and a port scan of a hub were not run |
| P1 | Signed read-only tool on three real agents; tampered blob, bad signature, replay, out-of-policy tool and invalid params each refused on the node | The systemd sandbox and root helper (`systemd-run`, sudo) are unit-tested by argv and by a fake helper, not run as root on a systemd host |
| P2 | The AI drafts, lints, tests on a canary (operator-approved), submits; the operator publishes; the AI's proposal of the published mutating tool waits for approval (`test_orch_surfaces.py`) | A live model session in the Studio chat |
| P3 | 3-step runbook over 20 nodes with canary + batches auto-pauses on injected failures, then rolls back (`test_orch_master.py`, in-process nodes) | The same over 20 *container* nodes; one container node is tested (opt-in `MUCLI_ORCH_DOCKER_TESTS=1`) |
| P4 | Export, import into a fresh master: identical catalog, versions in review, secrets absent; trusted keys publish; tampered bundles refused (`test_orch_bundles.py`) | |
| P5 | Master restart mid-rollout resumes; node offline mid-execution re-dispatches idempotent tools and marks others unreachable; quarantine, kill switch, rate limits, metrics counters, credential rotation, backup/restore, fact-change (drift) triggers | |

Deviations from the plan text:

- **Transient service instead of `systemd-run --scope`** (§6.5). Scopes cannot carry
  `NoNewPrivileges`, `PrivateTmp` or `ProtectSystem`, so executions run as a transient service
  (`systemd-run --wait --pipe --collect`) started by the root helper. Every execution goes through
  `mucli-node-exec`, which re-verifies the signed envelope, parameters and node policy with
  root-owned keys and policy before running anything (so a compromised agent user gains nothing).
  `--insecure-no-sandbox` (plain subprocess, process-group kill) exists for development and the
  disposable container nodes, and the node reports it (shown as a warning in the UI).
- **Default node policy is read-only** (`max_effect: read_only`) when no policy file exists; the
  owner opts nodes into changes.
- **Test key acceptance** is node-local: a node accepts test-signed drafts only when its own
  policy says `canary: true` (in addition to the master's canary marking).
- **Bundles** are `.tar.zst` when the optional `zstandard` package is installed, otherwise
  `.tar.gz`; import reads both.
- **Integration tests** use HTTPS long-poll (per decision §12.2), not WebSockets (§11 text).
- **CSRF**: tokens are per page load (HMAC-signed nonces), sent as `X-MuCLI-CSRF`; mutating
  endpoints also reject foreign `Origin` headers and non-IP, non-localhost `Host` names (DNS
  rebinding). Phones using a host name need `MUCLI_GUI_ALLOWED_HOSTS`.
- **Tool lifecycle**: an approved test moves a draft to `testing`; editing it returns it to
  `draft`. A test approved for one digest refuses to dispatch if the draft changes afterwards.
- The AI's allowlist also includes `planRun` (a read-only plan preview, `dry_run`) and
  `orch_tool_submit` (submit for review), which §4's table implies.
