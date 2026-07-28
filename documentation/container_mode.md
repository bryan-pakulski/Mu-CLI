# Container mode

Container mode runs the ordinary MuCLI `Session` and agent loop inside a
Docker worker. The host process remains a supervisor and GUI bridge. It does
not translate tool calls: `bash`, file operations, Git, Python, and package
installation are normal process and filesystem operations inside the worker.

## Session type and strategy mode

`session_type` and `agent_mode` are independent:

| Session type | Execution boundary | Tool policy | Approval policy |
| --- | --- | --- | --- |
| `chat` | Host provider client | Research, memory, prompts, history, artifacts | No file/shell tools exist |
| `workspace` | Host | Existing attached-workspace behavior | Existing strict/YOLO controls |
| `container` | Docker worker | Full registered tool surface inside worker | YOLO inside disposable sandbox |

An existing session without `session_type` loads as `workspace`.

Use the CLI:

```text
/session new --type chat notes
/session new --type workspace project
/session new --type container sandbox
```

Web and mobile creation forms keep Dockerfile and network policy inputs behind
expanding editors. Both load the maintained Dockerfile template immediately so
it can be reviewed or edited before creation. The network editor exposes the
allowlist and blocklist without occupying the main form.

The interactive TUI walks through container setup after provider selection. It
can attach the new session to an existing managed worker, or configure a new
container stage by stage: name, Dockerfile, mounts, allowlist, and blocklist.
Build and attach progress is printed as each lifecycle stage executes.

## Runtime layout

- MuCLI source is copied into the image at `/opt/mucli`.
- `/workspace` is a managed named volume.
- the host MuCLI home is mounted at `/root/.mucli`; this is the explicit,
  narrow persistence boundary for session JSON, memory, traces, and artifacts.
- additional host paths appear only through explicit bind mounts.
- the Docker socket, host PID/IPC namespaces, and privileged mode are never
  exposed.
- the worker listens only on its user-defined bridge network. Requests and
  callbacks require a per-container random bearer token.

## Artifacts

The agent publishes deliverables with:

```json
{
  "name": "report.md",
  "content": "# Report"
}
```

or:

```json
{
  "name": "application.zip",
  "file_path": "/workspace/application.zip"
}
```

Artifacts are copied into:

```text
~/.mucli/sessions/<session>/artifacts/<artifact-id>/<name>
```

The registry is atomic and independent from `session.json`. Web and mobile
clients list, download, and delete artifacts through session-scoped endpoints.

## Network allowlist

Container mode uses a normal Docker bridge plus rules in the host
`DOCKER-USER` chain:

1. established/related flows are accepted;
2. DNS is accepted;
3. TCP 80/443 is accepted only to the currently resolved IPv4 addresses of
   allowlisted domains;
4. all other forwarded packets from the worker subnet are dropped.

The optional blocklist has precedence over the allowlist. A domain present in
both is removed before DNS resolution and firewall rules are generated. Entries
that are not allowlisted are already blocked by the default-deny policy.

The worker has neither `NET_ADMIN` nor `SYS_ADMIN`, so root inside the worker
cannot alter host firewall rules. Domain-to-IP mappings should be refreshed
when a provider/CDN rotates addresses. The persisted policy records both the
requested domains and addresses used when rules were installed.

## Dynamic mounts

Docker cannot add a bind mount to a running container. MuCLI therefore:

1. commits the current writable layer to a derived image (preserving packages
   installed during the session);
2. stops and removes the old container object;
3. recreates it with the same session/workspace volumes, network, token, and
   all old mounts plus the new mount;
4. starts the worker, which reloads session state from disk.

Mount changes are refused while a turn is active.

## Operational requirements

- Docker Engine and CLI
- root access, or passwordless `sudo iptables`, to install host policy
- reachability from the host process to Docker bridge addresses
- the GUI server bound to an address reachable through
  `host.docker.internal` (the builder adds the Linux host-gateway mapping)

Use `CommandRunner(dry_run=True)` in tests and planning tools to inspect every
Docker/iptables command without executing it.

## Host-level container manager

Container environments can exist independently of sessions. The browser GUI
exposes the local-only manager at `/containers`; it is linked from both the
session picker and the active-session header. The manager can:

- create a standalone environment from the maintained Dockerfile or a template;
- start, stop, restart, inspect, and remove managed containers;
- attach or detach an existing MuCLI session;
- open an interactive command shell for manual package installation and setup;
- snapshot a configured environment as a reusable template.

Container management and browser shell endpoints reject non-loopback clients.
Use the TUI commands for remote hosts instead of exposing direct shell access
through a LAN-bound GUI.

TUI examples:

```text
/container list
/container create research-box
/container create data-box --template python-data
/container shell research-box
/container attach research-box my-session
/container detach research-box my-session
/container snapshot research-box python-data "Python data tools"
/container stop research-box
/container remove research-box

/template list
/template snapshot research-box python-data
/template use python-data another-box
/template delete python-data
```

`/container shell` uses `docker exec -it` and therefore supports a normal TTY.
It can be run from a chat, workspace, or container session.

## Templates

A template is a named Docker image snapshot stored in
`~/.mucli/container_templates/registry.json`. New standalone environments and
container sessions can use that image as their base without rebuilding the
worker Dockerfile.

Snapshots clear provider API keys, worker tokens, supervisor URLs, and runtime
network environment values before registering the image. Docker does not
include mounted volumes in `docker commit`; session data, `/root/.mucli`, and
`/workspace` volume contents are therefore intentionally excluded. Packages
and files installed in the container's writable image layer are included.
