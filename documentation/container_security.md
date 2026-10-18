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
- **Unprivileged network isolation:** the worker is attached only to an
  internal Docker bridge. A read-only, capability-free proxy container is the
  only egress path and enforces allowlist/blocklist policy.
- **Filesystem exposure:** host data is visible only through the MuCLI state
  mount and user-declared bind mounts. Within that Docker boundary the agent is
  not constrained to workspace roots or gitignore rules; it can administer the
  complete container filesystem. The secret-path denylist, credential-dump
  guard, and output scrubber remain active and cannot be disabled by a
  container-session variable.
- **Authenticated bridge:** a random per-container token authenticates host to
  worker calls and worker event callbacks.

## Important limitations

- Provider keys passed as environment variables are readable by root inside
  the worker and by host users allowed to inspect Docker containers.
- A user-supplied Dockerfile runs through the host Docker daemon during build.
  Treat it like running `docker build` manually; review untrusted Dockerfiles.
- The proxy resolves allowed hostnames when connections are made. A compromised
  proxy could misuse its outbound bridge, so it has no host mounts, runs as an
  unprivileged UID, is read-only, and drops all capabilities.
- Explicit read/write bind mounts intentionally grant access to those paths.
- The session-state mount is writable so history, traces, memory, and artifacts
  persist. Do not place unrelated secrets under the MuCLI home directory.
- Linux kernel and Docker daemon vulnerabilities remain outside MuCLI's threat
  model. Keep both patched and consider a dedicated host for high-risk work.

## Recommended deployment

- run the supervisor as a dedicated unprivileged user;
- grant that user Docker access only on hosts where Docker's root-equivalent
  control boundary is acceptable;
- use read-only mounts unless writes are necessary;
- keep the egress list minimal;
- set CPU, memory, PIDs, and disk quotas appropriate to the workload;
- remove idle containers and derived snapshot images periodically;
- do not mount SSH agents, cloud credential directories, Docker config, or the
  Docker socket.

# Job sandbox: repository-declared settings, caches and hosts

Job stages (plan, implement, review, checks) run in their own locked-down
containers (`mu/jobs/sandbox/`): `--network none`, read-only root, all
capabilities dropped, `no-new-privileges`, the host user's uid, a PID cap, and
only the job worktree, its inputs/outputs and a relay socket mounted. Models
are reached through the host relay; other egress only through the host egress
bridge and its allowlist. Approvals are answered on the host. Plan and review
mount the worktree read-only and get no Docker engine.

## Repository `.mucli/jobs.toml` `[sandbox]`

Read with `git show <base commit>:.mucli/jobs.toml`, never from the worktree a
stage edits, so a stage cannot widen its own sandbox. Malformed values are
dropped.

| Key | Effect |
|---|---|
| `allow_paths = [glob, ...]` | Tracked secret-looking files outside test directories the pre-check may mount. Untracked files there never. Files under a test or fixture directory (`tests/`, `test/`, `__tests__/`, `spec/`, `fixtures/`, `testdata/`, `__mocks__/`, `e2e/` …) need no entry: they are test data, and the diff scan's generic patterns do not count there (an injected credential always does). |
| `egress = [domain, ...]` | Added to (never replacing) Settings → Extra egress domains, for implement and checks only, **once approved** (below). Host names only: IP literals, `*`, bare-TLD wildcards (`*.com`) and wildcards over a public suffix (`*.co.uk`) are dropped; `*.` over a registrable domain is allowed. Model-provider hosts are dropped. The egress bridge still validates every connection's port and resolved public address. |
| `validation_allowlist = [prefix, ...]` | Extra check command prefixes, e.g. `./test.sh`, `bash scripts/ci.sh`, `bazel`. No shell operators; a bare shell or launcher (`bash`, `env`, `sudo` …) is refused unless followed by its script. |
| `image = "ref"` | A local image used as the job's build environment (never pulled). `image = "devcontainer"` uses the repository's devcontainer. Both need approval (below). |

### Repository egress needs approval

Implement and checks hold the agent's credential, so a repository may not widen
their network on its own. `[sandbox] egress` is approved per repository and
exact domain list (same `environment_approvals.json` store; fingerprint
`egress:<hash of the sorted list>`, kind `egress`, ref = the domain list).
Until approved it is **not applied**: the job is never parked for it, stages
run with Settings → Extra egress only, a `repository_egress_approval_required`
job event records the request, and the approval is offered next to other
repository environment approvals (New job sheet / environments preview,
`approval_required` row of kind `egress`). If checks fail because a host is
unreachable, approve it there and retry; it applies from the next stage launch.
Changing the list requires approving again.

Built-in check executables now include `uv`, `poetry`, `pdm`, `hatch`, `bun`,
`deno`, `just`, `./mvnw`, `sbt` and `bazel` besides npm/pnpm/yarn, cargo, go,
mvn, gradle/`./gradlew`, make, dotnet, swift, mix and bundle/rake.

## Devcontainers and repository images: approval

A repository can declare its build environment via `[sandbox] image` or
`.devcontainer/devcontainer.json` (or `.devcontainer.json`; JSONC comments and
trailing commas are accepted). Supported: `image`, or `build.dockerfile` +
`build.context` (+ literal `build.args`, `build.target`). Not supported:
devcontainer **features** (ignored, and listed in the approval prompt),
`dockerComposeFile` (the devcontainer is unusable), lifecycle commands
(`postCreateCommand` …) and `${...}` variables in build args.

Every repository-declared image or build needs the user's explicit approval,
recorded in `~/.mucli/jobs/environment_approvals.json` per repository path and
fingerprint (the image ref, or the Dockerfile blob, context tree, args and
target at the base commit). Changing the Dockerfile requires approving again.
An unapproved explicit `[sandbox] image` parks the job; an unapproved
devcontainer is only suggested and the job keeps its previous environment.

`environments = [...]` in `.mucli/jobs.toml` (named environments, images,
`docker = true`, GPUs) is read from the repository's working tree for the
user's own local repositories. For a **cloned** repository (created from a URL:
`metadata.repository_url` set, or a checkout under `$MUCLI_HOME/repos`) that
file is untrusted remote content: it is read from the base commit only, and the
resolved bindings need approval (kind `environment`, fingerprint over the exact
bindings: source, ref, paths, docker, GPUs, preload images). Unapproved, the job
parks with the usual Approve environment / Reject prompt.

A devcontainer build runs from a `git archive` of the base commit with
tracked secret-looking files removed from the context. Its `RUN` steps execute
on the host's container engine **with network access**, like a manual
`docker build`: review the Dockerfile before approving. The resulting image
still gets mucli's job layer and the post-build secret scan.

## Git metadata inside stages

VCS-aware tools (go `-buildvcs`, setuptools-scm, husky) need a working
`.git`. Stages get, read-only: the job worktree's private gitdir (index, HEAD),
a sanitized config (no remotes, credentials, includes or hooks), and a
**per-job minimal object store** instead of the repository's common dir. The
store (`$MUCLI_HOME/jobs/.sandbox-git/<id>/store-<hash>`) is a bare repository
filled with `git fetch --depth=1 --no-tags <common dir> <worktree HEAD>`: it
holds only the current HEAD commit and its tree, one branch ref, and nothing
else: no other branches or jobs, no tags, no `refs/stash` (which can contain
untracked files such as `.env`), and no history, so a secret committed and
later deleted cannot be read back with `git show <old>:.env`. It is rebuilt
whenever HEAD moves (checked at every stage launch). `git status`, `git log -1`
and `rev-parse` work; `git describe` has no tags and falls back to its
`--always` form (setuptools-scm then uses its untagged fallback version). If the
store cannot be built, the stage gets only the bare `.git` pointer file (no
history) and a `sandbox_git_metadata_unavailable` event records why.

## Shared dependency cache

Implement and checks containers mount a host directory at `/cache`, shared by
all jobs with the same repository path, build environment and lockfile hash
(lockfiles at the base commit): `$MUCLI_HOME/cache/deps/<repo>/<toolchain>/<lock>`.
Package managers are pointed into it (`PIP_CACHE_DIR`, `UV_CACHE_DIR`,
`npm_config_cache`, `YARN_CACHE_FOLDER`, `PNPM_STORE_DIR`, `CARGO_HOME`,
`GOMODCACHE`, `GOCACHE`, `GRADLE_USER_HOME`, `MAVEN_OPTS` …); `CARGO_TARGET_DIR`
is per job and removed with it. Plan and review do not mount it.
`sandbox.cache_max_gb` (default 20, 0 = unlimited) caps the total; least
recently used entries are removed, never one used in the last 24 hours.

Trust boundary: a job's implement stage can write into its cache entry and a
later job of the same repository reads it. That is the same trust as the
repository's own code, which that later job runs anyway; different clones
never share a cache. Delete `$MUCLI_HOME/cache/deps` to start clean.

## Docker-in-Docker engine

A build environment with `docker` gets a per-job `dockerd` under the Sysbox
runtime (never `--privileged`), with `--network none`. Its API is a Unix socket
in a host directory bind-mounted at `/run/mucli`
(`<runtime dir>/mucli-<uid>/engine-<home>-<job>/docker.sock`; the parent is
0700 and owned by the user). The host talks HTTP over that socket, so nothing
depends on reaching a container IP. Stage containers never see it directly:
each gets a policy proxy socket. The proxy judges bind mounts, bind-style
volumes (`/volumes/create` and inline `Mounts[].VolumeOptions.DriverConfig`)
alike: only `/workspace` and `/cache` (by real host path), only the `local`
driver, and no other `type=`/device options (nfs, overlay, block devices), so
nothing can mount `/run/mucli` and reach the raw engine socket. Image pulls happen host-side through the
proxy and need the registry on the job's egress allowlist. Sysbox itself must
be supported by the host's Docker (it is not available on Docker Desktop or
with Podman).

## Container CLI, Podman, rootless and SELinux

`jobs.container_cli` = `docker` | `podman` | `auto` (default: Docker if
installed, else Podman) selects the CLI every sandbox command uses.

- **Podman:** stage and check containers are supported; rootless Podman gets
  `--userns=keep-id` so the stage user is the host user. The Sysbox engine is
  not supported (jobs that need Docker-in-Docker park with an explanation).
- **Rootless Docker:** `--user <uid>:<gid>` maps to a subordinate host uid
  that cannot write the bind-mounted worktree. Doctor warns; use rootful
  Docker or Podman.
- **SELinux (enforcing):** bind mounts get the shared container label (`:z` /
  `relabel=shared`; shared, not private, because a job's containers use the
  same worktree and cache concurrently). System trees (`/usr`, `/etc` …) are
  never relabelled.

Doctor (Setup → Doctor) reports the CLI in use, rootless mode, Podman,
SELinux, free disk under the engine's data root, the per-container CPU,
memory, PID and cache limits, and this limitation:

**Not yet supported:** tests that start services or publish ports from inside
a stage container (testcontainers, `docker compose` port mapping, a database
on localhost). Stage containers have no network and the engine is reachable
only through the policy proxy.
