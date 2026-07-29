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
- the worker listens only on its user-defined bridge network. Managed workers
  receive unique internal ports beginning at `30312`; the ports are not
  published on the host. Requests and callbacks require a per-container random
  bearer token.

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
The web chat always shows an expandable **Artifacts** section, including an
empty state before the first deliverable is published.

## Worker upgrades and diagnostics

Container registry records include a worker protocol version. Loading an older
container session automatically rebuilds the worker image from the current
MuCLI source while preserving its named home/workspace volumes and explicit
bind mounts. Template-backed workers retain the template filesystem but receive
a fresh MuCLI worker layer.

Host-to-worker requests bypass parent-process HTTP proxy environment variables;
provider traffic inside the worker still uses the configured egress proxy. A
failed worker request reports the response detail and a redacted Docker log tail
in the GUI or TUI instead of only returning an opaque HTTP status.

## Network allowlist

Container mode does not modify the host firewall. Each worker is attached only
to a Docker ``--internal`` bridge, which has no direct external route. A small
dedicated egress proxy container is attached to both that internal bridge and a
separate ordinary bridge. Worker HTTP and HTTPS clients receive proxy
environment variables, so provider traffic can leave only through the proxy.

The proxy applies these rules in order:

1. MuCLI control-plane endpoints such as the supervisor callback are allowed
   only on their exact configured host and port;
2. the user blocklist is evaluated and takes precedence;
3. the user allowlist is evaluated by hostname, wildcard hostname, explicit IP,
   or CIDR entry;
4. every other CONNECT or HTTP destination is rejected.

The worker network has no external route, so connecting directly to a resolved
provider IP does not bypass the proxy. The egress proxy has no host mounts, runs
read-only as UID 65534, drops all Linux capabilities, and has
``no-new-privileges`` enabled. Neither the worker nor proxy receives the Docker
socket.

This model requires only normal access to the Docker daemon. MuCLI does not run
``sudo``, iptables, nftables, or any other host firewall command during create,
start, stop, migration, or removal.

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

- Docker Engine and CLI available to the current user
- reachability from the host process to Docker bridge addresses
- the GUI server bound to an address reachable through
  `host.docker.internal` (the builder adds the Linux host-gateway mapping)

Use `CommandRunner(dry_run=True)` in tests and planning tools to inspect every
Docker command without executing it.

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
