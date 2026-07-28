# Container mode security model

Container mode reduces the impact of autonomous file and shell operations; it
does not make arbitrary code intrinsically trustworthy.

## Enforced boundaries

- **Agent placement:** provider calls, agent loop, tool dispatcher, shell, and
  file functions execute inside the worker.
- **No Docker control:** the Docker socket is not mounted and the worker does
  not contain supervisor credentials.
- **No privileged mode:** `NET_ADMIN`, `SYS_ADMIN`, and `SYS_PTRACE` are
  explicitly dropped. Host PID and IPC namespaces are not shared.
- **Host firewall ownership:** allowlist rules live in `DOCKER-USER`, outside
  the worker network namespace.
- **Filesystem exposure:** host data is visible only through the MuCLI state
  mount and user-declared bind mounts. The existing secret-path denylist and
  output scrubber still run in the worker.
- **Authenticated bridge:** a random per-container token authenticates host to
  worker calls and worker event callbacks.

## Important limitations

- Provider keys passed as environment variables are readable by root inside
  the worker and by host users allowed to inspect Docker containers.
- A user-supplied Dockerfile runs through the host Docker daemon during build.
  Treat it like running `docker build` manually; review untrusted Dockerfiles.
- IP allowlists are snapshots of DNS results. Refresh policy after DNS/CDN
  changes, and do not treat domain allowlisting as an application-layer proxy.
- Explicit read/write bind mounts intentionally grant access to those paths.
- The session-state mount is writable so history, traces, memory, and artifacts
  persist. Do not place unrelated secrets under the MuCLI home directory.
- Linux kernel and Docker daemon vulnerabilities remain outside MuCLI's threat
  model. Keep both patched and consider a dedicated host for high-risk work.

## Recommended deployment

- run the supervisor as a dedicated unprivileged user;
- grant only the minimal firewall elevation needed for the generated chain;
- use read-only mounts unless writes are necessary;
- keep the egress list minimal;
- set CPU, memory, PIDs, and disk quotas appropriate to the workload;
- remove idle containers and derived snapshot images periodically;
- do not mount SSH agents, cloud credential directories, Docker config, or the
  Docker socket.
