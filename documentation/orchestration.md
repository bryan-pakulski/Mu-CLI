# Orchestration

Orchestration lets one mucli **master** (the machine running the GUI) manage many Linux
machines (**nodes**) through a catalog of reviewed, signed **tools**. An AI helps you design
those tools in a guided chat, but it never gets a shell on a node, never sees credentials, and
cannot run anything you have not approved. The design and the reasons behind it are in
[`plans/orchestration.md`](plans/orchestration.md); this page is the operator's guide.

- Web: **Orchestration** in the sidebar (`/orchestration`).
- Android: the **Orchestration** tab (approvals, run status, node health, kill switch).
- Terminal and CI: `mucli orch ...`.

## Concepts

| Term | Meaning |
|---|---|
| Node | A machine running the `mucli-node` agent. Status `pending → active → quarantined → revoked`. |
| Tool | A named administrative action (`nginx.rotate-logs`). |
| Tool version | An immutable, signed implementation of a tool (`nginx.rotate-logs@3`): a manifest plus files. |
| Runbook | Ordered steps over pinned tool versions, with params, `when:` conditions and `on_failure`. |
| Run | One approved request to run a tool or runbook version on a frozen set of nodes. |
| Execution | A run on one node (one step). |
| Access policy | Your rules on the master: who (AI or you) may run what, where (`allow`, `confirm`, `deny`). |
| Node policy | The node owner's rules in `/etc/mucli-node/policy.yaml`. The master cannot change them. |

## 1. Network: nothing on the public internet

Nodes always dial **out** to the master's **node endpoint**, a TLS listener that binds only to a
private address. The GUI stays on loopback.

Set the endpoint in **Settings → Network** (or `mucli orch serve --bind ADDR`):

- **Private network** (LAN, VPC, VPN): bind to the master's private IP, e.g. `10.0.0.5`, port `30312`.
- **WireGuard overlay** (across sites / NAT): **Settings → WireGuard overlay** (or
  `mucli orch overlay init`) generates the keys and subnet (default `10.77.0.0/24`, the master is
  `.1`). Give a hub endpoint if the master is not reachable; the hub runs only WireGuard
  (`mucli orch overlay hub-config`). Add a peer per node (`mucli orch overlay add web1`) and
  install the printed `wg-quick` config on that node; its private key is shown once and not kept.
  Then bind the node endpoint to the overlay address (`10.77.0.1`). Tailscale/Headscale addresses
  (`100.64.0.0/10`) work too.
- **SSH tunnel**: bind to `127.0.0.1` and have each node forward a local port to it.

The endpoint refuses `0.0.0.0`, `::` and public addresses. (`MUCLI_ORCH_ALLOW_PUBLIC_BIND=1`
overrides this; don't.) To reach the GUI remotely, use `ssh -L 30311:127.0.0.1:30311 master`.

## 2. Enrol nodes

**Nodes → Set up a node** (also on the Overview) walks through it and can be resumed: the
wizard's address (`#/setup/<token id>`) is remembered, so closing the browser and coming back
picks up where you were.

1. **Master readiness.** If the node endpoint isn't configured, pick one of the master's own
   addresses (public and any-address entries are listed but refused, with the reason). For nodes
   on another network, set up the WireGuard overlay first.
2. **Describe the node.** Name, labels (suggested from the labels already in use), canary flag,
   optionally a WireGuard peer config, and how long the join token lives.
3. **Get the agent onto the node**, three ways:
   - the **one-liner** (it checks the installer's SHA-256; the installer pins the agent's SHA-256
     and the master's TLS fingerprint);
   - **downloads** of `mucli-node.pyz` and an `install.sh` with the token, master URL and
     fingerprint filled in, for machines that can't reach the master over HTTP before enrolment
     (copy both over and run `sudo bash install.sh`; the wizard shows both digests);
   - the **manual** commands, for hosts where you don't want the installer.
   The installer creates the `mucli-node` user, `/etc/mucli-node/policy.yaml` (read-only by
   default), the root helper `mucli-node-exec` with a sudoers entry scoped to it, enrols, and
   starts `mucli-node.service`.
4. **Waiting for the node.** The wizard moves on by itself when the node enrols and shows its
   facts, its local policy (with a warning when it only allows read-only tools, is invalid or
   runs without the sandbox) and the address it connected from.
5. **Accept** it (labels can still be corrected here).
6. **Confirm it is alive**: a heartbeat after acceptance, and a round trip — an ordinary,
   audited run of the read-only `sys.facts` on just this node (`verifyNode`). If the node's
   policy refuses it, the reason is shown with what to change on the node. `sys.facts` comes with
   the starter pack.

**Quick enrol** (or `mucli orch nodes token --labels env=dev,role=web`) still creates a one-time
join token and shows the install command directly. The raw token stays available to the wizard
only in the GUI process that created it, and only until it is used or expires; the store keeps
only its hash.

Requirements on a node: Linux with systemd, Python 3.10+, `curl`, `sudo`. The agent is a single
standard-library zipapp; nothing is installed with pip.

**Python 3.10+:** the installer uses the newest Python it finds (`python3.13` down to `python3.10`,
then `python3`) and links it as `/usr/local/lib/mucli-node/python` for the service. It never
changes the system `python3`. On Amazon Linux 2023 and RHEL 9 that is 3.9, and `dnf`, `yum` and
`needs-restarting` depend on it. Install a newer one beside it first: `sudo dnf install python3.11`.

A node installed before this, whose `/usr/bin/python3` was repointed to a newer Python, has broken
`dnf` and a reboot check that can't run. To fix it:
1. Point `python3` back at the system version: `sudo alternatives --set python3 /usr/bin/python3.9`.
2. Point the agent at its own Python:
   ```
   sudo ln -sfn /usr/bin/python3.11 /usr/local/lib/mucli-node/python
   sudo sed -i 's|/usr/bin/python3 |/usr/local/lib/mucli-node/python |' \
     /etc/systemd/system/mucli-node.service /usr/local/libexec/mucli-node/mucli-node-exec
   sudo systemctl daemon-reload && sudo systemctl restart mucli-node
   ```

### Node policy

`/etc/mucli-node/policy.yaml` is the node owner's veto; a compromised master can only run what it
allows. Edit it on the node, or from the master (below); changes apply on the next poll.

```yaml
accept_categories: [inventory, diagnostics, maintenance]
max_effect: mutating            # read_only (default) | mutating | destructive
max_privilege: root             # or unprivileged
allowed_run_as: ["*"]
allowed_tools: ["*"]
denied_tools: ["db.*"]
require_check_mode_first: true  # a changing tool must succeed in check mode here first
quiet_hours: ["Mon-Fri 09:00-17:00 Australia/Sydney"]
canary: false                   # true accepts test-signed drafts
allow_agent_updates: true       # the master may update this agent (see "Keeping agents up to date")
allow_remote_policy: true       # the operator may edit this file from the master
```

A policy that does not parse refuses everything. Refused executions show the reason.

**Editing from the master.** The node page's *Node-local policy* section has **Edit policy**: the
current file in an editor with a live check (errors, what changes, and warnings such as raising
`max_effect` or turning remote edits off). **Send** signs the new text; the node's next poll
carries it, and the node checks it again (in the root helper on systemd nodes) before
installing it, keeping the previous file as `policy.yaml.prev`. The section shows the edit as
waiting, in force or refused (with the node's reason); a waiting edit can be cancelled.

- Operator only: `setNodePolicy` is never available to the AI. There is no approval step; the
  edit is audited (`node.policy.set`, then `node.policy.applied` or `node.policy.refused`).
- The node decides with the policy in force: `allow_remote_policy: true` allows it; `false`
  keeps the file local; unset follows `allow_agent_updates` (a node that accepts master-signed
  agents already trusts the master that far). An invalid local policy is only fixed on the node.
- Needs agent 1.2.0 (older agents don't report their policy text); new installs write
  `allow_remote_policy: true`.

### Keeping agents up to date

When the master runs a newer `mucli-node` than a node, the Nodes page flags the node and offers
**Update agents** (or `mucli orch nodes update [NODE]`). That proposes an **update job**: an
ordinary run of kind `agent_update`, so it has a plan listing every node it can't update and why,
goes through the access policy (`tool: mucli-node`, effect mutating: confirmed by default), runs
canary first in batches, pauses on failures and is audited.

On each node:

- Nothing happens unless the node's own `policy.yaml` says `allow_agent_updates: true` (new
  installs write it; absent means off). An agent update replaces code that runs as root, so this
  is the node owner's choice, like every other node-local veto.
- The agent checks the signed order (its own signature domain, so a tool dispatch can never be
  replayed as an update), waits until no execution is running (and starts none meanwhile),
  downloads the agent and checks its SHA-256.
- The root helper checks the order, the policy and the digest again, test-runs the new agent
  (`version` must print the ordered version), keeps the old one as
  `/usr/local/lib/mucli-node/mucli-node.pyz.prev` and swaps the file atomically. The agent exits
  and systemd starts the new one, which reports the result: a node only counts as updated once
  the new agent is running. If it never comes back, that node times out and the job pauses.
  To roll back by hand: `sudo cp mucli-node.pyz.prev mucli-node.pyz && sudo systemctl restart mucli-node`.
- The order is frozen to the agent that was approved: if the master's agent changes before a node
  is reached, that node fails instead of getting a different version.

**Settings → Node agents → Keep node agents up to date** makes the master propose an update job
by itself when nodes fall behind (one open job at a time, at most one proposal per version per
day). To let it run without asking, add an access rule such as
`{name: Agent updates, match: {tool: mucli-node}, decision: allow}`.

Not covered: disposable test nodes and nodes without the root helper (`sandbox: none`) — replace
their agent or image instead. Agents older than 1.1.0 can't update themselves: update them once
by hand (copy the new `mucli-node.pyz` from **Set up a node → Download** to
`/usr/local/lib/mucli-node/`, restart `mucli-node`), and only *then* add
`allow_agent_updates: true` — older agents treat the unknown key as an invalid policy and refuse
everything.

### Canary and test nodes

Mark a node **canary** on the master *and* set `canary: true` in its local policy to let it run
drafts under test. **Nodes → Start test node** (needs Docker on the master) starts a disposable
container node (`python:3.12-slim` by default) that enrols itself as an accepted canary; remove it
when done.

### Node dashboard and groups

**Nodes** opens on the dashboard: overall health for the fleet, a group, or the ungrouped nodes.
Each node is **healthy**, **warning** or **critical** (or awaiting acceptance), with the reasons in
words, and the worst come first:
- **Critical:** offline, quarantined, a disk ≥ 95 % full, memory ≥ 95 % used.
- **Warning:** a disk ≥ 85 %, memory ≥ 90 %, load above 1.5 per CPU, needs a reboot, an outdated
  agent, local policy errors, or facts older than a day.

The dashboard also shows memory, disk and load averages and maxima, busy nodes, and the last 24 hours
of runs on those nodes. The thresholds are in `mu/orchestration/vm.py`.

**Groups** collect nodes for viewing and for runs. A node belongs to a group when it is a
**member** or the group's **label selector** matches it (a group with no selector has only its
members).
- **Web:** **+ Create group** beside the groups list. Drag a node onto a group to add it, from one group to another to move it, or onto **Ungrouped** to take it out. Each row's menu does the same without dragging.
- **Phone:** long-press a node, then **Move to group…**.
- **Groups tab:** rename a group, edit its description, selector and members, or delete it.

**Run on this group** proposes a run targeting the group (`{groups: [name]}`), the same as choosing
the group in the run wizard. Group changes are operator actions (audited) and never available to the
AI. The fleet chat's read-only `fleet_overview` reports each node's groups and health, and each
group's health counts.

From the terminal: `/orch dashboard [GROUP|-]`, `/orch groups` and `/orch group …` in the chat,
or `mucli orch dashboard [--group G]` and `mucli orch groups …` (below).

### Node history

Nodes running agent **1.3.0** or newer report a small utilisation sample with their poll, about
every 20 seconds:
- CPU %, memory %, the fullest disk % and load;
- network in and out, and disk read and write, in bytes per second.

The rates come from the kernel's counters between two samples (Linux `/proc`). The master stamps
each sample with its own clock and keeps full resolution for two days. After that, samples become
5-minute averages, kept for 30 days.
- **Node page:** charts for the last hour, 6 hours, day, week or month.
- **Dashboard:** the same charts for the scope. CPU, memory and load are averaged across its nodes, network and disk I/O summed, and disk is the fullest.
- **Older agents:** they have no history, and the charts say so. Update them (**Update agents**, or automatic updates) to start recording.

In the terminal: `/orch history [NODE|GROUP|-] [1h|6h|24h|7d|30d]` draws sparklines, as do
`mucli orch nodes history NODE --range R` and `mucli orch dashboard --history [--group G]`.

### Node maintenance

Every node (agent 1.2.0 or newer) reports whether it **needs a reboot** with its facts:
Debian/Ubuntu's `reboot-required` flag (and the packages behind it), RHEL's
`needs-restarting -r`, or a newer kernel installed than the one running. It shows on the node
row ("needs reboot"), the node page, the fleet board and **Now** ("N nodes need a reboot ·
Reboot one at a time…"), and the AI's `fleet_overview`. Update older agents with
**Update this node** (or the agent update job).

The node page's **Maintenance** panel does routine work without writing tools:

| Action | Built-in tool | Notes |
|---|---|---|
| Check for updates | `sys.pending-updates` | read-only |
| Install security updates | `os.security-updates` | apt (`*-security`), dnf/yum `--security`, zypper patches; refused packages are skipped and reported |
| Upgrade all packages | `os.full-upgrade` | apt, dnf/yum, zypper, pacman, apk; no removals unless allowed |
| Reboot | `os.reboot` | scheduled 30 s out so the run reports first; optionally only if needed; fleet reboots go one node at a time |
| Restart a service | `os.service` | restart, reload, start or stop one systemd unit |

Each button proposes an ordinary run (operator only; the AI can propose the same tools through
`proposeRun`). The access policy decides the confirmation as for any mutating tool, the node's
own `policy.yaml` still refuses what it doesn't allow (the panel says so up front, e.g. "allow it
in /etc/mucli-node/policy.yaml (max_effect: mutating)"), and every change has a **Dry run**
(check mode). The tools are part of the starter pack; **Install built-in tools** (or
`installStarterPack`) adds any that a master is missing without touching the rest.

Proposing from the node page keeps you on the node. **Open runs here** lists every unfinished
run that includes it, with its decision buttons (Approve and start, Reject, Continue, Cancel;
typed confirmation still applies), its state on this node and an **Open run** link. Following a
link from one item to another kind of item (a node to its run) leaves a **← Back to web-1**
link above the heading.

**Connecting.** For two minutes after the master starts, an active node it hasn't heard from
since then shows as *connecting* (○) rather than offline: agents back off for up to a minute
before they reconnect. Connecting nodes don't count as needing you; a node that's still silent
after the grace period is offline as before.

## 3. The catalog

The **starter pack** installs read-only tools: disk usage, memory, services, failed units,
pending updates, certificate expiry, listening ports, uptime/load and facts.

A tool manifest (YAML) declares typed inputs (JSON Schema subset), the effect
(`read_only | mutating | destructive`), privilege, limits, idempotency, check-mode support,
rollback tool, preconditions and tests. Scripts receive parameters as `$MUCLI_PARAMS` (JSON) and
`MUCLI_P_<NAME>` variables — never interpolated into code — honour `MUCLI_CHECK=1` as a dry run,
and write structured results to `$MUCLI_RESULT`. Declared secrets arrive as
`MUCLI_SECRET_<NAME>` on the node only.

Lifecycle: `draft → testing → in_review → published → deprecated → retired`. Lint (manifest
validation, banned patterns, secret scan, shellcheck/ruff when installed) must pass before review.
Only you publish; destructive tools are published by typing their id. Published versions are
signed and immutable; change a tool by creating a new version.

### Fleet chat

**Ask** in the orchestration header (or `.`) docks a chat beside the panes on every route: ask
about your nodes and runs, or ask for something to be done. It is an *orchestration* session
(`POST /api/orchestration/chat/session`) with the same `orch_*` tools as Studio, so it can look up facts and results, draft tools
and **propose** runs. A run it proposes appears in the chat as a card with **Approve and start /
Reject** (or Continue / Cancel): those are your own commands, exactly as on the run page,
typed confirmation included. The AI still can't approve, dispatch, publish or touch policy
(`AI_COMMANDS` is unchanged). Tool approvals and questions it asks are answered inline. On a
node, run, tool or runbook page the message carries that item as context ("About node web-1",
removable). The fleet chat never changes the main chat's current session.

Fleet chats are temporary. There is one conversation at a time (kept across reloads); **New**
starts another and deletes the old one, along with any other leftover fleet chat (one with a turn
still running is removed on the next pass). They never appear in your session lists (web,
terminal or mobile): their sessions carry `internal_session_kind: fleet_chat`
(`mu/session/visibility.py`).

## 4. Designing tools with the AI (Studio)

**Studio → New tool**, then **Open discovery chat**. The chat is an *orchestration* session: the
AI has only the `orch_*` tools (plus research and memory). It will:

1. clarify the intent and search the catalog first;
2. draft the manifest;
3. look at real machines through facts and read-only tools;
4. write the script and fix lint findings;
5. propose a check-mode test on a canary — **you approve each test**, unless your access policy
   has a `run: test` rule that allows it (see *Access policy*);
6. submit for review with a headline and risks.

You review the manifest, the diff against the previous version, lint and test results, then
publish or request changes. The AI cannot publish, approve, change policy, read secrets or
dispatch without you.

**The chat picks up where it left off.** When a run the chat proposed (a test or a published
tool or runbook) finishes, the chat resumes on its own. It gets a short automatic message
naming the run and its outcome, reads the results and carries on, so you don't type "done" after
approving. This covers every terminal state, including rejected, cancelled and expired runs.
Each run wakes the chat at most once. A run the AI already read through `orch_run_status` or
`orch_run_results` wakes nothing, and runs that finish while the chat is busy are handed over
when its turn ends. The browser GUI does this (the terminal CLI does not). Turn it off for a
session with `/set orch_auto_continue false`.

### Runbooks

**Studio → New runbook** (or **Edit draft** / **New version** on a runbook's catalog page) opens
the runbook editor:

- **Steps** are cards: a tool picker over published versions (deprecated ones are flagged, and a
  newer published version offers an *upgrade* button), parameters generated from that version's
  inputs, each with a *literal / expression* toggle (`${{ inputs.x }}`,
  `${{ steps.check.outputs.free_pct }}`, `node.labels.env`, with completion for what exists),
  `when:`, `on_failure` (stop / continue / roll back) and an optional node selector. Reorder by
  dragging, with the move buttons or `Alt+↑/↓`; duplicate and delete.
- **Inputs** are a small table (name, type, default, enum, pattern, max length, description)
  with a YAML escape hatch. The **YAML** tab shows the whole spec; switching goes through the
  server, so the two views never disagree.
- **Checks** run as you type: the same validation the server uses, plus references to later or
  unknown steps, undeclared inputs, literal params the tool's schema rejects, missing required
  params, and output fields the tool doesn't declare (a warning). Each problem shows at its field
  (and on its YAML line); the strip at the top says whether the draft can be submitted. The
  **expression preview** evaluates every `when:` and param on a node you pick with sample inputs
  (earlier steps' outputs are unknown there, so they are null).
- A draft always **saves**, even with problems or YAML that doesn't parse (the text is kept and
  reopens with the error); it can't be **submitted** until it parses and has no problems. If the
  AI (or another tab) changed the draft since you opened it, saving is refused until you reload.
- **Upgrade tools** lists every step whose tool has a newer published version with the manifest
  diff and what changes: the effect (and so the access decision), parameters the new version
  rejects, removed outputs later steps use, rollback added or removed. Applying it is an ordinary
  unsaved edit; on a published runbook it starts a new version.
- **Test on canary** runs the draft (check mode by default) on canary nodes; its steps must use
  published tools. Results appear under the runbook's tests; editing the draft after approval
  stops the test from dispatching.

Every place that picks a tool, runbook, version, node, label or group (the run wizard, schedules,
test dialogs, the editor) offers what exists right now and updates live: a version published
while the form is open appears without a reload, switching versions keeps the parameter values
that still fit and lists the ones that don't, and selectors show how many nodes they match.

## 5. Running things

**Runs → New run**: choose a published tool or runbook, fill in the parameters, choose targets
(labels such as `env=prod,role in (web,api)`, groups, or explicit nodes) and a strategy
(canary, batch size, max parallel, failure threshold, pause between batches), optionally check
mode. The **plan view** shows the resolved nodes, excluded nodes with the reason, batches, the
effect, the blast radius, whether a rollback exists, and the access decision with the rule that
made it.

- Canary first; any canary failure pauses the run. A later batch whose failure share exceeds the
  threshold pauses it. A paused run waits for you: **Continue**, **Roll back** or **Cancel**.
- Rollback runs each completed change's rollback tool, newest first.
- A node that goes silent mid-execution: idempotent tools are re-dispatched; others become
  *unreachable* and wait for your decision (**Retry node**).
- **Pause all dispatch** (Overview, Android, `mucli orch pause`) is the kill switch. Quarantine
  stops one node receiving work.

### Fleet results

A tool's declared `outputs` schema turns what every node returned into tables across the fleet
(`mu/orchestration/results.py`):

- **Per node**: one row per node with the tool's top-level values (counts for lists). Nodes
  that failed stay in the table with their state.
- **One view per list of objects**: e.g. every disk on every node, or every certificate.
- Each view sorts and charts by its key metric: percentages highest first, `days` soonest
  first, otherwise the first number. Click a header to sort; Chart shows bars of the sorted
  column, coloured where they cross a warning line (≥75% / ≥90%, ≤30 / ≤14 days).
- **Where:** the run page (first, once the run finishes) and a tool's **Results** tab, which
  shows each node's latest successful result across runs.
- **CSV:** `GET /api/orchestration/runs/<id>/results.csv?step=&view=` and
  `/api/orchestration/tools/<id>/results.csv?view=`. Raw values; cells that would start a
  spreadsheet formula are prefixed with `'` because node output is untrusted.
- Tools without an `outputs` schema get columns inferred from what they returned. Declare one
  for labelled, typed columns (`*_bytes` shows sizes, `*_pct` percentages, `days*` days).
- **Everywhere else:** the same tables are available to
  - **the AI in any chat** (chat, workspace, container and orchestration sessions) through the
    read-only `fleet_overview` and `fleet_results` tools: "which nodes are over 80% disk?",
    "which certificates expire this month?". They read only stored, redacted outputs, never
    mutate, and report "not set up" on a machine that hasn't used orchestration (including
    job containers);
  - **the terminal** as `/orch results <tool|run id> [view] [sort column]`;
  - **Chat from the web**: *Ask about this* on any results table opens Chat with a question
    about that view in the composer, unsent.

### Access policy

**Settings → Access policy**, first match wins:

```yaml
rules:
  - name: AI diagnostics outside prod
    match: {actor: ai, effect: read_only, nodes: "env!=prod"}
    decision: allow
  - name: AI diagnostics on prod are confirmed first
    match: {actor: ai, effect: read_only, nodes: "env=prod"}
    decision: confirm
  - name: Operator read-only runs need no extra confirmation
    match: {actor: operator, effect: read_only}
    decision: allow
  - name: Anything that changes a system is confirmed
    match: {effect: mutating}
    decision: confirm
  - name: Destructive changes are confirmed by typing the tool id
    match: {effect: destructive}
    decision: {confirm: typed}
```

Match keys: `actor` (`ai`/`operator`), `run` (`run`/`test`), `effect`, `category`, `tool` (glob),
`nodes` (selector). Each node is judged separately and the strictest result wins. Proposals not
approved within 24 hours expire.

**Tests** (canary runs of draft tools and draft runbooks) are decided by rules with
`run: test`; a rule without `run` matches only ordinary runs, so older policies keep their
meaning. The default policy starts with

```yaml
  - name: Tests are approved by the operator
    match: {run: test}
    decision: confirm
```

and a test no rule matches is confirmed too. To let the AI test read-only drafts without asking,
put a rule before it:

```yaml
  - name: AI may test read-only drafts on canaries
    match: {actor: ai, run: test, effect: read_only}
    decision: allow
```

No rule can relax the test invariants: tests only reach canary nodes (marked on the master *and*
`canary: true` in the node's policy), drafts are signed with the test key, a test refuses to
dispatch if the draft changed after approval, and destructive effects keep typed confirmation.

### Schedules and triggers

**Settings → Schedules**: cron (`0 3 * * *`), interval, or a fact trigger such as
`max_disk_used_pct > 90` or drift detection (`kernel changed`). Scheduled runs start only when the
access policy allows them outright; otherwise they wait in **Approvals**.

## 6. Secrets

**Settings → Secrets** keeps values encrypted on the master (AES-256-GCM). Optionally protect the
key with a passphrase; then unlock the store after each restart before runs that need secrets.
Values are injected on the node at execution time and redacted from logs and outputs on the node
and again on the master. The AI, manifests, exports and the audit log only ever see names.

## 7. Import, export and config as code

- **Export** a signed bundle (`mucli orch export`; `.tar.zst` when `zstandard` is installed,
  otherwise `.tar.gz`) or a directory for git (`mucli orch export --dir ./ops`).
- **Import** shows a diff (new / identical / conflict) before applying
  (`mucli orch import ./ops`, `--git URL --ref main`). Imported versions arrive *in review*, unless
  the bundle is signed by a key you marked trusted (**Settings → Trusted keys**). Version numbers
  are never overwritten; a conflict becomes a new version and references are rewritten.
- **CI**: `mucli orch lint ./ops` exits non-zero on lint or validation errors;
  `mucli orch runbooks validate ./runbooks` checks runbook YAML files (a file or every `*.yaml`
  under a directory) against the master's catalog and exits non-zero on errors, printing each
  problem with its file and line.

Never exported: secret values, node credentials and identities, keys, the audit internals.

### What needs you, everywhere

Runs awaiting approval, paused runs, tools and runbooks in review, nodes awaiting acceptance and
offline nodes (not connecting ones, just after the master starts) count as needing you. The same counts appear on every surface:

- **Web:** a count on the Orchestration item in the sidebar, on every page (a bubble on the icon
  when the sidebar is collapsed). It reads `GET /api/orchestration/badge`, which never starts
  orchestration on a GUI that has not used it.
- **Android:** a badge on the Orchestration tab, refreshed every 30 seconds while the app is in
  the foreground and on every attention event. Node screens offer agent updates.
- **Terminal launcher:** `mucli` lists **Jobs** and **Orchestration** next to Sessions, each
  showing what needs you. Orchestration opens a menu of the pending decisions (approve or reject
  runs, continue or roll back paused ones, accept nodes), the runs and nodes, agent updates and
  the kill switch.
- **Terminal chat:** a startup banner and a toolbar segment (`◆ 2 orchestration need you`), and
  the `/orch` command. It acts as the operator through the same commands as the GUI:

```
/orch                         what needs you (default)
/orch runs [STATE]            /orch run RUN
/orch nodes
/orch approve RUN [TYPED]     /orch reject RUN [REASON]
/orch continue|pause-run|cancel|rollback RUN
/orch accept|quarantine|release NODE
/orch pause [REASON] | resume /orch update-agents [NODE...]
```

Approvals from the terminal are recorded and audited; runs start when a master (`mucli --gui`
or `mucli orch serve`) is running.

## 8. Audit, backup and the CLI

Every proposal, approval, publish, dispatch, result, import, enrolment and revocation is in a
hash-chained audit log (**Audit**, `mucli orch audit verify`). `mucli orch backup FILE` writes a
consistent archive of the store, keys and blobs (it contains private keys: protect it);
`mucli orch restore FILE` restores into an empty home.

```
mucli orch nodes [list|show|accept|quarantine|unquarantine|revoke|token|update] [NODE]
mucli orch dashboard [--group G|-] [--history] [--range R]   (health; --history adds sparklines)
mucli orch nodes history NODE [--range 1h|6h|24h|7d|30d]
mucli orch groups [list|show|create|delete|add|remove|rename] [NAME] [NODES…|NEW]
               [--selector SEL] [--description D] [--from G]
mucli orch tools [list|show|publish|deprecate|starter] [REF]
mucli orch runbooks [list|show|validate FILE|DIR|save FILE|DIR|submit REF|publish REF|test REF]
               [--nodes IDS] [--params JSON] [--no-check]
mucli orch runs  [list|show|plan|start|approve|reject|cancel|continue|rollback] [RUN|REF]
               [--labels SEL] [--nodes IDS] [--groups G] [--params JSON] [--runbook] [--check] [--wait]
mucli orch export [PATH] [--dir DIR]      mucli orch import [PATH] [--dir DIR] [--git URL]
mucli orch lint PATH                      mucli orch audit [verify|list|export]
mucli orch pause | resume                 mucli orch serve [--bind ADDR] [--port N]
mucli orch overlay [init|add|master-config|hub-config]
mucli orch backup FILE | restore FILE
```

State lives in `$MUCLI_HOME/orchestration/` (`orch.sqlite3`, `blobs/`, `keys/`).
