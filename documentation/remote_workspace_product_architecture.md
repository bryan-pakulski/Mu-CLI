# MuCLI Remote Workspace Product Architecture

## Product thesis

MuCLI should not become "the local GUI exposed to the internet".

The paid product is a **secure remote delegation and supervision layer over engineering workspaces**:

> Give MuCLI engineering work, leave the workstation, and safely supervise, approve, review and merge the result from anywhere.

The existing ReAct/session harness remains the execution engine. Durable jobs, verification, evidence, cost attribution, recovery and remote workspace control are the product layer customers pay for.

## What the recent durable-job architecture gets right

The Friday Five work establishes several boundaries that should survive the hosted product:

- **Jobs, not browser sessions, own work lifecycle.** GUI, TUI and mobile are control planes over one durable job model.
- **Execution is detached from presentation.** Closing a client does not terminate the job.
- **Per-job Git isolation.** Each engineering job gets its own branch/worktree/process boundary.
- **Verification is independent.** The agent cannot grant itself `READY_FOR_REVIEW`.
- **Human interaction is durable.** Questions and approvals become structured gates rather than terminal prompts.
- **Evidence is first-class.** Receipts, events, verification, diagnostics and Job Trace provide a review/audit surface.
- **Cost is attributable.** Model/API spend is versioned per attempt rather than inferred from a mutable current price.

These are substantially more important to the paid product than adding another agent mode.

## Current boundary that must change before remote commercial use

The present GUI/mobile topology is appropriate for a trusted LAN/developer setup, but it should **not** be the production remote-access model.

Current shape:

```text
Phone / Browser
      |
      | direct HTTP/SSE
      v
MuCLI GUI daemon :30311
      |
      v
local sessions / jobs / files / tools
```

A commercial remote workspace must not require opening this daemon to the public internet. Authentication/TLS added to the same topology would improve it, but would still make the source-code machine an inbound application server and would tightly couple product identity, remote access and workspace execution.

## Target architecture

```text
                      Browser / Mobile / TUI
                               |
                      HTTPS + user identity
                               |
                               v
+----------------------------------------------------------------+
|                    MuCLI Cloud Control Plane                    |
|                                                                |
|  Organizations / users / devices / RBAC                        |
|  Workspace registry + presence                                 |
|  Durable job metadata + state projection                       |
|  Human gates / approvals / notifications                       |
|  Policy + budgets + billing ledger                             |
|  Evidence index / audit                                        |
|  Git provider / PR / CI integration                            |
+-------------------------------+--------------------------------+
                                |
                    authenticated outbound tunnel
                  (runner initiates; no inbound port)
                                |
               +----------------+----------------+
               |                                 |
               v                                 v
+------------------------------+  +------------------------------+
| Customer Workspace Runner    |  | Hosted MuCLI Workspace      |
|                              |  |                              |
| source code                  |  | isolated source checkout     |
| Git credentials              |  | secrets / provider keys      |
| provider secrets             |  | worktrees                    |
| worktrees                    |  | worker processes             |
| worker processes             |  | verification                 |
| verification                 |  | local recovery store         |
| local recovery store         |  | workspace compute meter      |
+------------------------------+  +------------------------------+
```

### Control plane owns

- user / organization / project identity;
- workspace registration and online/offline presence;
- job submission and state projection;
- authorization and approval policy;
- notification routing;
- billing and spend envelopes;
- audit/evidence metadata;
- GitHub/GitLab/Bitbucket issue, PR and CI metadata;
- team reporting and fleet administration.

### Workspace runner owns

- source code and working copy;
- Git operations that require repository credentials;
- agent processes and local model runtimes;
- provider credentials when BYOK is used;
- tool execution;
- job worktrees;
- deterministic verification;
- raw/local artifacts that policy says should not leave the workspace;
- local durable recovery when the cloud/control connection disappears.

This gives MuCLI an enterprise-friendly option where **code and secrets never need to leave the customer's network** while the customer still gets the hosted control plane.

## Remote runner protocol

A runner should enroll once and then establish an outbound authenticated connection to the control plane.

### Identity

Use separate identities for:

- human user;
- organization/project;
- device;
- workspace;
- runner installation/instance;
- individual job worker attempt.

A job should never be authorized merely because a TCP connection came from a known workspace.

### Enrollment

Target onboarding:

```text
mu workspace enroll
```

or a QR/pairing code from mobile/browser.

Enrollment exchanges a one-time code for a long-lived runner identity stored in the OS keychain/secure service. Operational sessions use short-lived credentials. Enterprise deployments can provision identities administratively.

### Connection

- outbound-only from the runner by default;
- authenticated WebSocket/HTTP2/QUIC-style multiplexed channel is sufficient;
- TLS always;
- mutual runner identity (mTLS or equivalent signed device credential);
- resumable stream with monotonically increasing event sequence numbers;
- idempotency keys on every command/control mutation;
- explicit runner generation/instance ID after restart;
- signed/scoped execution lease per job attempt;
- reconnect performs state reconciliation rather than assuming either side is authoritative.

The current worker heartbeat/lease design is a good seed, but process IDs alone are not sufficient once the scheduler and worker may be on different machines or after runner restart.

## Cloud/local durability model

Do not discard SQLite simply because the product becomes hosted.

Recommended split:

- **Runner SQLite/WAL**: local execution journal, recovery, pending event outbox and offline operation.
- **Cloud relational store (e.g. Postgres)**: organization/workspace/job metadata, synchronized state projection, policy, billing and audit index.
- **Object storage**: encrypted evidence/artifacts that policy permits to leave the runner.

Use an outbox/replay protocol:

```text
runner transaction
  -> update local job state
  -> append durable event/outbox item
  -> execute next step

connection available
  -> send ordered events with idempotency key
  -> cloud acknowledges cursor
  -> runner safely compacts acknowledged outbox later
```

This preserves the current rule that state is persisted before execution continues while adding disconnected remote operation.

## Security model

Security is part of the product, not a deployment checkbox.

### User authentication

Minimum commercial baseline:

- OIDC/passkeys/email identity;
- short-lived access tokens;
- refresh/session material stored in OS secure storage on mobile/desktop;
- device/session revocation;
- MFA support;
- organization membership.

Team/enterprise:

- SAML/OIDC SSO;
- SCIM;
- conditional access;
- audit export;
- data residency/retention controls.

### Authorization

RBAC should scope down through:

```text
organization
  -> project
      -> workspace
          -> repository
              -> job
```

Example permissions:

- view job metadata;
- read agent transcript/evidence;
- inspect diff;
- answer questions;
- approve write tool;
- approve shell/network action;
- request changes;
- create PR;
- merge;
- manage workspace/secrets/policies;
- view billing.

### Secrets

Browser/mobile clients should never need raw provider or repository secrets.

Prefer:

1. BYOK credential stored on the runner / hosted workspace secret store;
2. short-lived Git/provider credentials where providers support them;
3. secret references in cloud metadata, not secret plaintext;
4. enterprise integration with Vault / cloud secret managers later.

Debug traces and exported evidence require a **redaction/classification pipeline** before remote synchronization or sharing. A debug ZIP must be treated as potentially sensitive by default.

### Network policy

The job policy should be able to define:

- no network;
- allowlisted domains;
- normal internet egress;
- blocked metadata/control-plane endpoints;
- repository/provider-specific credentials injected only for the command requiring them.

Policy decisions should themselves be durable job events visible in Job Trace.

## Approval model

The existing durable human gate is the correct primitive, but paid users should not have to approve repetitive low-risk operations one prompt at a time.

Extend approvals to scoped grants:

- approve once;
- approve this tool for this job;
- approve command pattern for this job;
- approve writes under these paths;
- approve network access to these domains;
- approve until a timestamp;
- deny + rationale;
- organization policy may forbid a grant regardless of user preference.

Every grant/revocation should be auditable and included in the work receipt.

## Workspace lifecycle

Separate two concepts explicitly:

### Workspace

Persistent engineering environment:

- repository/repositories;
- dependencies/toolchains;
- caches;
- secrets;
- policy;
- CPU/RAM/GPU capabilities;
- region/host;
- persistent volume/snapshot.

### Job workspace

Disposable/isolated execution boundary inside a workspace:

- fixed base SHA;
- branch/worktree or clone;
- worker process/container;
- job-specific credentials/policy;
- checkpoints;
- verification evidence.

Hosted MuCLI should eventually support:

- reproducible base images;
- warm caches;
- snapshot/restore;
- auto-suspend;
- CPU/RAM/GPU tiers;
- ephemeral and persistent workspaces;
- regional placement;
- retention policy.

A repository-level `mu-workspace.yml` (or similar) can become an important product primitive for validation defaults, protected paths, network policy, budgets and PR rules.

## Cost and billing architecture

Model cost is only one meter.

Keep an immutable economics ledger:

```text
job economics
  model_api_usd
  workspace_cpu_usd
  workspace_gpu_usd
  storage_usd
  egress_usd
  paid_tool_usd
  credits/discounts
  total_attributed_usd
```

Each line item should persist:

- quantity;
- unit;
- rate;
- currency;
- price/version ID;
- source/provider;
- timestamp;
- job/attempt/workspace attribution.

Do **not** replace this with a single mutable `total_cost` field.

The current versioned model pricing map is the beginning of the `model_api_usd` meter. Local Ollama can legitimately have `$0` provider/API spend while still consuming billable hosted GPU/CPU compute.

### Current accounting boundary

Durable Engineering Work is the authoritative cost path. Each attempt snapshots input/output/cached/reasoning token deltas plus pricing key/version/rates into its durable evidence. Work receipts and Job Trace distinguish `metered`, `local_zero`, `partial`, `unpriced` and `legacy` economics, so unknown or plan-based spend cannot be mistaken for a true `$0` provider bill.

The older interactive-session `/stats` path still calls the historical Gemini-era pricing helper in `utils/config.py`. It is not billing evidence and should be migrated to the shared pricing registry during the next harness/config cleanup. Durable job accounting must remain the source of truth for paid workspace billing until that cleanup lands.

### Recommended early commercial model

Start with **BYOK model providers** plus MuCLI subscription/remote-workspace billing rather than becoming an LLM reseller immediately.

Benefits:

- customers keep provider relationships/enterprise terms;
- less margin exposure to model-price changes;
- cleaner trust story for provider credentials;
- hosted workspace compute is a service MuCLI controls and can meter accurately.

A managed-provider option can be added later once billing/reconciliation is mature.

## Budget enforcement

Displaying cost is not enough for unattended work.

Before paid launch, enforce:

- per-job model spend cap;
- per-job runtime cap;
- iteration cap;
- subagent cap;
- workspace compute cap;
- project/org monthly spend envelope.

Use projected next-call cost where enough context is known. If the next model request could cross a hard limit, stop at a durable budget human gate before sending it where practical. Post-call reconciliation remains necessary because output length is not known in advance.

Unknown/plan-based pricing must never be interpreted as `$0` for enforcement.

## Git / review / completion

Milestone 5 is the highest-value product gap after reliable job execution.

Paid remote delegation should complete:

```text
ticket
 -> isolated implementation
 -> deterministic verification
 -> review
 -> PR
 -> CI
 -> requested changes / repair
 -> mergeable
 -> merge
```

Required capabilities:

- base-branch drift detection;
- update/rebase strategy;
- conflict state + evidence;
- final commit policy;
- PR creation/linkage;
- CI/check ingestion;
- review feedback ingestion;
- mergeability state;
- protected-branch policy awareness;
- merge action with authorization/audit.

Without this, MuCLI still hands the user back to another tool for the most important final step.

## Notifications and remote UX

Remote access becomes desirable when the user does **not** need to watch it.

Notifications should be state/action driven:

- `OAuth validation needs approval`;
- `CSV export is ready to review`;
- `Redis upgrade failed verification`;
- `API latency reached 80% of its model budget`;
- `PR CI failed after the job was ready`;
- `Workspace runner is offline while two jobs are active`.

Do not send raw event spam.

The paid mobile/browser home should emphasize:

```text
Needs you
Running
Ready to review
Failed

Workspace health / online state
Spend / budget
```

Chat is a drill-down and intervention surface, not necessarily the primary remote product screen.

## Team and enterprise desirability

High-value team controls:

- shared workspaces/projects;
- role-based approval limits;
- model allowlists/denylists;
- per-project spend policies;
- protected repository/path rules;
- central job/audit history;
- team performance/economics reports;
- retention and export policy;
- workspace fleet/version management.

Enterprise differentiators:

- self-hosted/VPC runner/data plane;
- "source code and secrets stay in your network" architecture;
- SSO/SCIM;
- audit logs;
- data residency;
- custom retention;
- policy-as-code;
- customer-managed keys later.

## Evidence and trust

The Work Receipt is a strong product primitive. Strengthen it into a verifiable execution receipt:

- hash base/head commit;
- hash verification artifacts;
- include exact validation command + exit code;
- include policy/approval decisions;
- include pricing/usage provenance;
- content-hash the final receipt;
- eventually sign it with the runner identity;
- optionally hash-chain critical audit events.

This gives "Ready for review" an auditable meaning and is marketable to teams that cannot accept opaque agent claims.

## Model economics and routing

Do not hard-code an "AI quality score" or aggressively auto-route jobs yet.

Use accumulated Job Trace evidence to build real cohort data:

- first-pass verification rate by model/repo/job class;
- active time;
- retries;
- human gates;
- cost;
- tool-call volume;
- runtime failures excluded from model-quality measures.

Then MuCLI can make evidence-backed recommendations such as:

> For this repository and ticket class, the balanced model has historically used 35% less model spend with similar first-pass verification.

A future policy/router can choose:

- premium model for difficult implementation;
- cheaper model for reconnaissance/summarization;
- cheap verifier commentary where deterministic checks carry the truth;
- local Ollama where privacy/cost/available hardware make it attractive.

## Market positioning

Do not lead with "terminal AI assistant", "another coding IDE", or "multi-provider chat".

Lead with the outcome:

> **Secure remote agent workspaces for engineering teams.**

or:

> **Mission control for autonomous software engineering work.**

A useful product sentence:

> Give it the tickets. Leave. Review what is ready from anywhere.

The differentiating bundle is:

- persistent secure workspaces;
- durable engineering jobs;
- off-site mobile/browser supervision;
- independent verification;
- actionable human gates;
- cost/budget control;
- evidence and retrospective analysis;
- end-to-end PR/CI/merge workflow.

## Recommended build order from current branch

### P0 — complete the engineering transaction

1. **Milestone 5: Git/PR/CI/merge completion.**
2. **Hard budget/runtime/iteration enforcement.**
3. **Formal Friday Five failure-injection benchmark.**

### P1 — make workspaces safely remote

4. Runner identity + outbound authenticated tunnel.
5. Cloud workspace registry + durable state synchronization/outbox.
6. User auth/device enrollment/RBAC.
7. Runner-side secret store + redacted remote telemetry.
8. State-driven mobile/browser push notifications.

### P2 — make it a paid team product

9. Immutable multi-meter economics ledger + billing export.
10. Hosted workspace lifecycle/compute metering/auto-suspend.
11. Scoped approval/policy engine.
12. Organization/project/team administration and audit.
13. Git provider integration beyond basic PR creation.

### P3 — enterprise and optimization

14. SSO/SCIM + VPC/self-hosted runner deployment.
15. Retention/data-residency/customer policy controls.
16. Signed evidence receipts.
17. Historical model economics/performance recommendations and optional routing.

## Commercial success criterion

The product should eventually pass a stronger version of Friday Five:

> On Friday, a developer assigns five tickets to a secured workspace and closes every MuCLI client. From another network/device they can approve only what needs them. On Monday, they can see verified results, exact attributable spend, complete evidence and PR/CI/merge state without regaining shell access to the original workstation.

That is a materially different product from a ReAct loop with a remote web page.
