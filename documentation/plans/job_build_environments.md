# Plan: job build environments from container-mode environments

Status: implemented (rev 3, 2026-10-13) · Owner: jobs · Related: `durable_engineering_jobs.md`,
`container_mode.md`, `container_security.md`, Settings → Toolchains (`mu/jobs/sandbox/toolchain.py`).

Rev 3 decisions:
- Docker-in-Docker uses **Sysbox only**; there is no privileged-sidecar fallback.
- Every job builds its **own** snapshots; snapshots are never shared between jobs.
- A job's environments can be **kept** (pinned) past idle retention.
- Idle retention defaults to **72 h**.
- Job stages **can use GPUs** (opt-in per binding; §7a).

Rev 2 changes, per review:
- Snapshots are **temporary and per job**: no shared reuse and no freshness window. They are
  removed automatically when the job ends.
- A job can use **one or many** environments (monorepos).
- **Docker-in-Docker** is supported, as an isolated per-job engine.

Implementation notes (2026-10-13):
- Container-mode environments keep only `/root/.mucli` in their named volume; `/root` itself is in the
  container layer, so `docker commit` captures it. The snapshot therefore deletes excluded `/root` paths
  in its own layer (stages never see them), and captures a filtered tar only when `/root` is a volume.
  Templates use the same filtering (secrets, mucli state and history removed; caches and rc files kept).
- Shell start-up files (`.bashrc`, `.profile`, …) are excluded from job snapshots like other
  `is_denied_path` files; the login-shell probe has already captured the PATH edits they make.
- The engine's network has no route out. Pulls go through the policy proxy, which pulls host-side
  (registry on the egress allowlist) and loads the image into the engine.
- Every job artifact also carries `io.mucli.home=<hash of MUCLI_HOME>`; the sweeper only touches its
  own home's resources on a shared Docker daemon.

## 1. Problem

Every job stage runs in the shared `mucli-job-runtime` image (Python + Node). Real projects need
their own toolchains, and each project's environment is slightly different. SCRUM-40's checks
failed because `cargo` was missing. `stream-processor-rs` also needs ffmpeg dev libraries, ONNX
Runtime, clang/LLVM, cmake and the sibling `resqvision-core` crate. Some suites (compose
integration tests, image builds) need a Docker engine.

Container mode already lets users build an environment from a Dockerfile, tune it interactively
by chatting with the agent inside it, and snapshot it. That makes it the natural place to define
build environments.

## 2. Goals

1. **Per-job temporary snapshots.** When a job starts, it snapshots the base environment(s) it
   needs. It uses those images for its whole life (every stage, fix attempt and retry) and deletes
   them when it ends. The job is reproducible, the live environment can keep changing, and nothing
   accumulates.
2. **One or many environments per job.** A monorepo job can map components to different
   environments, e.g. `resq-backend` → `rust-build`, `resq-frontend` → `node-build`,
   `inference-backend-rs` → `trt-build`.
3. **Docker-in-Docker.** An environment can be marked "needs Docker". Its stages and checks then
   get a private, per-job Docker engine, also discarded at job end.
4. **Isolation unchanged** for normal stages: fresh containers, `--network none` (egress only via
   relay/bridge), read-only rootfs, `--cap-drop ALL`, host uid:gid.

### Non-goals
- Running stages inside the live environment container, or sharing its volumes or network.
- Automatic discovery of images from Dockerfiles or CI files (rejected in review).
- Reusing snapshots across jobs. Decided: every job builds and owns its own snapshots.
- Privileged Docker-in-Docker sidecars. Decided: Sysbox only.

## 3. Concepts

| Term | Meaning |
|---|---|
| **Environment** | A container-mode managed container (`mucli-<name>`, `~/.mucli/containers/registry.json`): image, named home volume `mucli-<name>-home` at `/root`, workspace volume, egress allowlist, attached sessions. |
| **Template / image** | A container-mode template (`mucli/template-<slug>:<ts>`) or any local image. Templates and images can also be bases. |
| **Environment binding** | One entry of a job's environment set: `{name, source: environment\|template\|image, ref, paths: [..], primary: bool, docker: bool}`. `paths` are repository-relative component roots; an empty list means the whole repository. |
| **Job snapshot** | A temporary image built for one job from one binding: `mucli/job-<job12>-<binding>:<ts>`, labelled `io.mucli.job=<job id>`. |
| **Job image** | The job snapshot plus mucli's job layer (venv, git/patch/ripgrep, `/cache`), built by `toolchain.build_image`. Also job-scoped and labelled. |
| **Job engine** | An optional per-job Docker engine (DinD), used by bindings with `docker: true`. |

## 4. Choosing a job's environments

The environment set is resolved **once, at job start** (before the first sandboxed stage) and
stored on the job as `metadata.build_environments = [binding + {snapshot_image, job_image, digest,
probed_env, home_bytes, skipped_paths}]`.

**Sources, in precedence order:**
1. **Job override:** `execution.environments` from the New job sheet ("Build environments"
   field), from `/job create --env`, or from the `setEnvironments` command.
2. **Repository config committed in the repo:** `.mucli/jobs.toml`
   ```toml
   [[environments]]
   name = "rust-build"          # a container-mode environment, template:<name>, or image:<ref>
   paths = ["resq-backend", "edge-ingest-service", "stream-processor-rs", "resqvision-core"]

   [[environments]]
   name = "node-build"
   paths = ["resq-frontend", "resq-app"]

   [[environments]]
   name = "compose-it"
   paths = ["."]
   docker = true                # this environment gets a per-job Docker engine
   ```
   The file is already read for checks (`checks.resolve_suites`), so environment mapping lives
   next to the suites, is versioned with the code, and is shared by the team.
3. **Session environment:** if the job's `metadata.origin_session` is a container session
   (`session.json → container_config.container_name`), that environment covers the whole repo.
4. **Settings → Toolchains:** the repository → source rows (gaining a `paths` column and a
   `docker` flag).
5. **Default:** the shared `mucli-job-runtime` image.

The first source that yields bindings wins. Bindings are not merged across sources, which keeps
behaviour predictable.

**Picking a binding for a piece of work:**
- **Primary binding.** The implement and plan agent containers run in the binding marked
  `primary`. If none is marked, mucli picks the binding whose paths cover the most of the plan's
  "files likely touched".
- **Check suites.** A suite runs in the binding that owns its working directory. A suite can also
  name one explicitly (`[[checks.suites]] env = "node-build"`, or `env:` in the plan's Checks
  section). Each suite already runs in its own container (`run_suite_in_container`), so per-suite
  environments need no new isolation work.
- **Agent commands in other environments (monorepos).** The agent gets one new tool,
  `run_in_environment(env, command, cwd)`, executed host-side by a **broker** on the existing relay
  socket. The broker starts a sibling container from that binding's job image with the same
  isolation, the same worktree mount and that binding's `/cache` and egress. It streams the output
  back and records an `environment_command` event. This mirrors planos's
  `toolchain --env NAME <cmd>`. The tool's description lists the job's environments and their
  paths, so the agent knows where to build what.

## 5. Snapshot pipeline (`mu/jobs/sandbox/environments.py`, new)

`snapshot_binding(job, binding) -> JobSnapshot`, run for each binding at job start (in parallel,
up to the concurrency limit):

1. **Resolve the source.**
   - Environment: through `ContainerSupervisor.resolve`. It may be stopped, since stopped
     containers can be committed.
   - Template or image: used as-is (go to step 5).
2. **Commit the container filesystem** with `docker commit --pause=true`, applying
   `ContainerSupervisor.snapshot`'s secret-env scrub. Extend the scrub with
   `MUCLI_CONTAINER_MODE` and `MUCLI_WORKER_*`, so the sandbox worker never thinks it is in
   container mode. The result is a temporary tag.
3. **Capture the home volume.** `/root` is a *named volume*, which `docker commit` skips, so
   interactively installed toolchains (rustup, pip `--user`, nvm, sdkman) would be lost.
   - A helper container mounts the volume read-only and streams a tar of `/root`, excluding:
     - secrets: `.ssh`, `.gnupg`, `.aws`, `.azure`, `.config/gcloud`, `.config/gh`, `.kube`, `.netrc`, `.git-credentials`, `.docker/config.json`, `.npmrc`, `.pypirc`, `.cargo/credentials*`
     - mucli state: `.mucli`
     - shell history: `.*_history`
     - caches, which go to the per-job `/cache`: `.cache`, `.cargo/registry`, `.cargo/git`, `.npm/_cacache`, `go/pkg/mod`
     - anything `mu.security.secret_paths.is_denied_path` flags
     - the user's own excludes from Settings
   - Skipped paths and size are recorded.
4. **Probe the shell environment.** Run `bash -lic 'env -0'` (falling back to `sh -lc`) in a
   throwaway container with the home tar applied. Keep `PATH` and the toolchain variables:
   `CARGO_HOME`, `RUSTUP_HOME`, `NVM_DIR`, `PYENV_ROOT`, `GOROOT`, `GOPATH`, `JAVA_HOME`,
   `ANDROID_HOME`, `DOTNET_ROOT`, `CONDA_PREFIX`, `LD_LIBRARY_PATH`, `PKG_CONFIG_PATH`, plus a
   Settings allowlist. This captures PATH edits made in `.bashrc`/`.profile`.
5. **Build the job snapshot:**
   ```
   FROM <committed image | template | image>
   COPY home/ /root/                  # environments only
   ENV <probed allowlist>
   RUN chmod -R a+rX /root
   LABEL io.mucli.job=<job id> io.mucli.binding=<name> io.mucli.source=<ref>
   ```
6. **Add the job layer** with `toolchain.build_image(snapshot)`. It skips the package-manager step
   when Python ≥3.10 exists (container-mode environments already have it), neutralises the
   container-mode entrypoint and env, and adds the venv, tools and `/cache` support. The job image
   carries the same `io.mucli.job` label.
7. **Secret scan gate.** List `/root`, `/home` and `/etc` in the job image and run
   `is_denied_path`. Any hit blocks the job (`needs_input`) and names the paths. The user removes
   them from the environment, or allows a specific path in Settings.
8. **Record and emit.** Store the result in `metadata.build_environments`, emit
   `build_environment_ready`, and remove the intermediate tags.

**Cost:** committing plus copying `/root` takes tens of seconds for large toolchains (rustup is
about 1 GB). It's paid once per job, at start, and shown in Activity as "Preparing build
environments (2/3)…". Docker's layer store deduplicates identical layers, so many jobs from one
environment cost far less disk than their nominal size.

**Refresh:** the action **`refreshEnvironments`** (key `E`, confirm "Re-snapshot the build
environments for this job? Stages started after this use the new images.") rebuilds the job's
snapshots from the current environments and deletes the old ones.

## 6. Lifecycle and cleanup

Everything a job creates is labelled `io.mucli.job=<id>`: snapshot and job images, broker sibling
containers, the job engine container, and its volumes and networks.

- **Cleanup triggers:**
  - the job reaches a terminal state (`done`, `cancelled`) or `pushed`
  - the job is archived or deleted (`JobManagementService.archive/delete`)
  - discard (`_discard`)
- **Kept while** the job is `failed` or `needs_input`, so you can retry with the same images.
  `jobs.environment_retention_h` (default 72) controls how long such idle jobs keep their images
  before they are cleaned and re-created on retry.
- **Cleanup does:**
  - `docker rm -f` containers labelled with the job id;
  - `docker volume rm` and `docker network rm` the job engine's volume and network;
  - `docker image rm` the job's images.
  
  Failures are logged as `environment_cleanup` events and retried by the sweeper.
- **Sweeper:** extends the controller's retention sweep. Every
  `jobs.drift_check_interval_s`-style tick, anything labelled with a job id that is gone or
  terminal is removed. This catches crashes and manual edits.
- Activity's orphan list (already present) shows any leftovers, and the existing remove button
  works on them.
- **Keep (pin).** A job's environments can be kept: the action `keepEnvironments` (toggle, key `K`,
  shown as "⬢ kept" on the job). A kept job is exempt from idle retention, so its images and job
  engine volume stay until you unkeep it, or the job is done, archived or deleted. Use it to come
  back to a failed job days later with the exact same environment. The job page and Activity show
  the disk used by kept jobs, and Settings → Build environments lists every kept job with Unkeep.

## 7. Docker-in-Docker (per-job engine)

**Use case:** compose-based integration tests, building images, testcontainers, kamctest-style
clusters. Enabled per binding with `docker = true`.

**Engine: Sysbox.** The job engine is a Sysbox "system container" (`--runtime=sysbox-runc`)
running `dockerd`, with no `--privileged`. Sysbox's user-namespace isolation keeps the inner engine
and its containers off host root.

- **Detection.** `docker info` must list `sysbox-runc`. Settings → Build environments shows the
  engine status as available or not installed.
- **Not installed.** Bindings with `docker = true` park the job with "This job needs
  Docker-in-Docker, which requires Sysbox. Install it (see Settings → Build environments), then
  retry." Settings shows the install steps: the Sysbox package, the Docker daemon runtime entry and
  a daemon restart. mucli never installs it itself, since that needs sudo.
- **Engine image.** `docker:dind`, or the image set in `sandbox.docker_engine_image`, run under
  `sysbox-runc`.

**Topology per job:**
1. The job engine container, `mucli-job-<job12>-engine`, runs `dockerd` with its own storage volume
   `mucli-job-<job12>-docker`.
2. It sits on a per-job internal network `mucli-job-<job12>-net`, which has no route out except
   the job's egress bridge, so image pulls are limited to the egress allowlist.
3. Stage and suite containers of that binding get a **policy proxy**: a small host-side API
   proxy, like planos's per-session proxy. It is mounted at `/var/run/docker.sock` in the stage,
   and `DOCKER_HOST` is set. The stage never talks to the engine directly. The proxy:
   - labels every container, network and volume `io.mucli.job=<id>`;
   - refuses bind mounts outside `/workspace` (the job worktree) and `/cache`, judged by real path;
   - refuses host devices, host network/pid/ipc namespaces and `--volumes-from` foreign containers;
   - refuses touching containers not created by this job;
   - allows `--privileged` *inside* the engine, which is safe under Sysbox (that's how
     systemd/kind/compose test stacks run), but never on the host engine.
4. **Images inside the engine.** On engine start, the binding's declared `preload_images` and the
   job images themselves are loaded through `docker save | docker load` from the host engine.
   Further pulls go through the egress allowlist; add registry domains such as
   `registry-1.docker.io` or `git.resqvision-util.com.au`.
5. **Path parity.** The worktree is mounted at the same `/workspace` path in the stage and in
   engine-created containers (through the engine's view of the job worktree mount). That way
   `docker run -v "$PWD:$PWD"` and compose relative volumes work, as with planos's same-path rule.
6. **Limits:** CPU, memory and pids come from the job's sandbox rules, applied to the engine
   container. A per-job storage cap is set by `sandbox.docker_storage_gb`.
7. **Cleanup:** the engine container, its storage volume and network are removed with the rest of
   the job (§6).

**Audit.** The proxy emits `docker_api` events for creates, starts and denials. They appear in the
session transcript and Diagnostics, like planos's workbench events.

## 7a. GPUs

A binding can request GPUs: `gpus = "all" | "0,1" | ""` in `.mucli/jobs.toml` or the Build
environments editor. The default comes from the source environment's `gpu_request`, so a CUDA or
TensorRT environment like `inference-backend-rs`'s stays GPU-capable for its jobs.

- **Stages and suites.** Containers of a GPU binding get `--gpus <request>`, using the same
  request syntax and device validation as container mode (`mu/container/hardware.py`). The NVIDIA
  runtime injects the driver libraries. All other isolation flags are unchanged.
- **Job engine.** For a binding with `docker = true` and `gpus` set, the engine container gets the
  GPUs. Containers it creates may request them through the policy proxy, which allows `--gpus`
  and `DeviceRequests` only within the binding's request and refuses other host devices.
- **Global switch.** The setting `sandbox.gpus_enabled` (default on) can turn GPUs off for all
  jobs. When the host has no GPU or the NVIDIA runtime is missing, a GPU binding parks with "This
  job's build environment `x` requests GPUs, but the host has no usable NVIDIA runtime".
- **Sharing.** Several jobs may use the same GPUs at once. `jobs.max_concurrent` and the per-stage
  memory limits stay the only throttles; per-GPU scheduling is out of scope.
- **Activity** shows the GPU request per job container.
- **Tests:** a unit test for `--gpus` argument construction and proxy `DeviceRequests` policy; a
  Docker integration test (skipped without a GPU) that runs `nvidia-smi -L` in a GPU binding's
  suite.

## 8. Isolation summary

- Normal stages are unchanged.
- **Egress:** a binding's stages get `sandbox.egress_extra ∪ environment.egress_allow`, filtered to
  drop model-provider hosts. Stages reach models only through the relay. This is gated by
  `environments.inherit_egress`.
- **Environment ownership:** a job's agent can't change its environments. Only
  `setEnvironments`/`refreshEnvironments` from a UI or the CLI can, and both require the job not to
  be mid-stage.
- **No secrets:** env scrub, home denylist and post-build scan.
- **DinD** is never on by default, and it only runs under Sysbox. There is no privileged-container
  path.
- **Pause:** `docker commit --pause` briefly freezes the source environment, and the UI says so.

## 9. Backend changes

| Area | Change |
|---|---|
| `mu/jobs/sandbox/environments.py` (new) | Binding resolution (§4), `snapshot_binding` (§5), probe, home capture, secret scan, `cleanup_job(job_id)`, sweeper hook, `list_sources()` (environments, templates, local images). |
| `mu/jobs/sandbox/engine.py` (new) | Job engine under `sysbox-runc`: detection, start, preload, policy proxy (UNIX socket per stage), audit events, teardown. |
| `mu/jobs/sandbox/broker.py` (new) | `run_in_environment` execution: sibling containers with the binding's image, same isolation, streamed output, events. |
| `mu/jobs/sandbox/policy.py` | `with_build_environment(policy, job, binding, service)` replaces `with_toolchain`. It applies the binding's image, Python, cache, egress, and Docker socket when `docker`. |
| `mu/jobs/sandbox/runner.py`, `check_runner.py` | Resolve and prepare environments at job start; choose the primary binding for agent stages and the owning binding per suite; register the broker tool on the relay. |
| `mu/jobs/sandbox/worker.py` (in the container) | Expose the `run_in_environment` tool when the job has more than one binding (talks to the broker over the relay socket). |
| `mu/jobs/checks.py` | Suites accept `env`; each result records which binding ran it. |
| `mu/jobs/controller.py` / `management.py` / `core/commands.py` | Cleanup on terminal/archive/delete/discard; idle retention; sweeper. New commands `setEnvironments`, `refreshEnvironments` and `keepEnvironments` (toggle; exempts the job from idle retention), listed with `tone`/`confirm`. |
| `mu/jobs/job_settings.py` | New section **Build environments**: `environments.use_session_environment` (true), `environments.inherit_egress` (true), `environments.home_exclude_extra` (list), `environments.probe_env_extra` (list), `jobs.environment_retention_h` (72), `sandbox.docker_engine_image` (`docker:dind`), `sandbox.docker_storage_gb` (20). Docker-in-Docker is available iff Sysbox is detected. The Toolchains rows gain `paths` and `docker`, and their base becomes a source picker. |
| `mu/jobs/core/vm.py` | The job VM gets `build_environments` (per binding: source, paths, docker, status, image size, prepared at). New `environments_vm()` for the pickers. Activity VM: job engines and broker containers under their job. |
| `mu/gui/routers/jobs.py` | `GET /api/jobs/environments` (sources); `GET /api/jobs/sessions` adds `environment`. |
| `mu/gui/routers/containers.py` | Environment rows get `jobs_using: n`. |
| `mu/container/supervisor.py` | `snapshot()` reuses the home capture, so container-mode templates stop losing `/root` (a latent bug today). |

## 10. UI by surface

**Web jobs mode**
- *New job sheet.* "Build environments" shows the resolved set, e.g.
  `rust-build → resq-backend, stream-processor-rs · node-build → resq-frontend · compose-it ⬢ docker`,
  with its source (repo config / session / settings). Change edits the set: environment, template
  or image per row, plus paths and Docker.
- *Job page.* A header pill `⬢ 3 environments` opens a panel with each binding's status
  (preparing / ready / failed / cleaned), image size, probe summary, skipped home paths, engine
  type, and Refresh (`E`).
- *Session tab.* `run_in_environment` calls fold to one line, `› [node-build] npm test`.
- *Checks tab.* A per-suite environment column.
- *Activity.* Job engines and broker containers appear under their job with CPU and memory
  sparklines.
- *Settings.* The "Build environments" section; Toolchains rows with source picker, paths and
  Docker; the detected engine option with Sysbox install guidance.

**Web Environments page (`/containers`):** a "Used by N jobs" badge, and a warning when a job's
secret scan blocked this environment.

**Android:** the same New job field, job panel, checks column, Activity items and Settings.

**Terminal:**
- `/job environments` lists sources.
- `/job create --env name[:paths][:docker]` (repeatable).
- `/job env <job>` shows bindings; `/job env refresh <job>`.
- The `/jobs` TUI job header shows the bindings, and `E` refreshes.
- `run_in_environment` lines appear folded in the Session tab.

## 11. Failure modes and messages

| Situation | Behaviour |
|---|---|
| A binding's source environment is deleted before job start | Job parks: "Build environment `x` no longer exists; choose another (Change)". |
| Snapshot build fails | Job parks with the build log tail. Retry re-attempts the snapshot. |
| Secret scan hit | Job parks and names the paths (§5.7). |
| Job images were cleaned while the job sat idle past retention | On retry or resume, the snapshots are re-created from the current environments, with a notice. |
| Suite needs Docker but its binding has `docker = false` | Missing-tool path: "Suite `it` needs Docker; enable Docker for binding `compose-it`". |
| Sysbox not installed | Job parks: "This job needs Docker-in-Docker, which requires Sysbox…" with install guidance in Settings. |
| Kept job holds a lot of disk | The job panel and Settings show its size; Unkeep, or archive the job, to release it. |
| Tool missing at checks | Existing missing-tool path, now naming the binding: "Checks need `cargo`, which build environment `rust-build` doesn't include…". |
| Cleanup fails (container busy, docker down) | Logged and retried by the sweeper; leftovers visible in Activity → orphans. |

## 12. Testing

- **Unit:**
  - precedence and binding selection (repo config → session → settings → default)
  - primary-binding and per-suite selection by path, and explicit `env`
  - `.mucli/jobs.toml` parsing
  - home-exclude filter and probe parsing
  - secret-scan block
  - egress inheritance that filters model hosts
  - cleanup triggers, idle retention and sweeper selection by label
  - proxy policy decisions: allowed/denied binds, devices, namespaces, foreign containers
  - VM, API and command shapes
- **Docker integration** (opt-in marker, like `test_job_sandbox_docker.py`):
  1. an environment with rustup in `/root` and a PATH change in `.bashrc` → snapshot → `cargo test` on a fixture crate;
  2. a planted `/root/.ssh/id_test` is excluded, and the job blocks when it is allowed in;
  3. a two-binding job: a Rust suite and a Node suite run in different images;
  4. `run_in_environment` through the broker;
  5. job end removes every `io.mucli.job` artifact;
  6. where Sysbox is installed (skipped otherwise): a compose up/down inside the job engine, and a denied host bind;
  7. a kept job survives idle retention, and unkeeping releases its images on the next sweep.
- **UI:** web Playwright on a fixture server with stubbed docker; Android Jest; terminal frame tests.

## 13. Phases and acceptance

| Phase | Scope | Done when |
|---|---|---|
| **1. Temporary snapshots, single binding** | `environments.py` (commit, home capture, probe, scan), job-scoped labels, cleanup triggers, idle retention (72 h), Keep, sweeper, session-environment and Toolchains resolution, `build_environments` in the VM, the missing-tool message naming the binding. | A job in a container session whose environment can `cargo test` passes checks. Its images exist only while the job is live and are gone after done/archive/delete. |
| **2. Many bindings (monorepo)** | `.mucli/jobs.toml [[environments]]`, per-suite binding by path or `env`, primary binding for agents, New job "Build environments" editor, job panel. | `rsq-cloud` with `rust-build` + `node-build` runs each suite in the right image. |
| **3. Broker** | `run_in_environment` tool, sibling containers, events, Session tab folding. | The implement agent builds `resq-frontend` and `stream-processor-rs` in one job. |
| **4a. GPUs** | `gpus` on bindings, `--gpus` on stage/suite/engine containers, proxy `DeviceRequests` policy, `sandbox.gpus_enabled`, detection and messages. | A GPU binding's suite runs `nvidia-smi -L` successfully; a non-GPU binding gets no devices. |
| **4. Docker-in-Docker** | `engine.py` (Sysbox detection and engine), policy proxy, preload, path parity, limits, cleanup, audit, install guidance in Settings. | A compose integration suite runs and is torn down; denied host binds are refused and audited. |
| **5. Surfaces and hardening** | Android and terminal parity, Environments page badge, Activity engines, templates switched to home capture, docs. | All three front ends support the flows; container-mode templates keep `/root` toolchains. |

**First slice for SCRUM-40:** Phase 1.
1. Create a `rust-build` environment.
2. Make `cargo test` work for `stream-processor-rs` in container mode.
3. Attach it to the `rsq-cloud` session, or add a Toolchains row.
4. Re-run checks.

## 14. Decisions and open questions

- **Decided:**
  - Docker-in-Docker uses Sysbox only, with no privileged fallback.
  - Snapshots are independent per job and never shared.
  - Jobs can be kept (pinned) past idle retention.
  - Idle retention is 72 h.
  - Job stages and job engines may use GPUs, per binding (§7a).

No open questions remain.
