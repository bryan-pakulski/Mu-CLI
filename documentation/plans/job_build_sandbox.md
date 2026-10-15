# Plan: a build sandbox that prepares itself

Status: proposal (2026-10-15) · Owner: jobs · Builds on: `job_build_environments.md` (rev 3,
implemented), `durable_engineering_jobs.md`, `container_security.md`.

## 1. Problem: what actually happened in rsq-cloud

Job `1c6b9c8f` ("Fix sidebar collapse", `rsq-cloud`, base `rsq-cloud-v2`), from the local job
store:

| Step | What happened |
|---|---|
| Environment | `build_environments = []`. No environment, Toolchains row, devcontainer or `.mucli/jobs.toml` image applied, so every stage ran in the default `mucli-job-runtime` image (Python + Node only). |
| Implement | The agent ran `npm install` and got **`E403 Forbidden – GET https://registry.npmjs.org/prettier`**. The npm registry is not on the job's egress allowlist. `detect.py` knows npm needs `registry.npmjs.org`, but it only *suggests* that and never applies it. The agent wrote: *"I made the change, but I couldn't run any of the three checks."* |
| Checks | `vitest: not found`, `tsc: not found`, and a stray global ESLint 6.4 from the runtime image ("couldn't find a configuration file"). |
| Triage | The baseline commit failed the same way, so all 3 failures were classed **pre-existing** (`new_failures: 0`). `missing_tools: []`, because only a missing *top-level* executable is detected: `npm` existed, while `vitest` lives in the uninstalled `node_modules/.bin`. |
| Outcome | The job advanced to review and got **`ready_with_nits`**, with **zero checks having actually run**. |

There are two separate failures here:

1. **The sandbox can't prepare itself.** Nothing in the pipeline installs dependencies. The
   stage containers are deliberately locked down: read-only rootfs, no network except
   allowlisted egress, no root. The only ways to get a working toolchain today are manual:
   - hand-tune a container-mode environment;
   - add a Toolchains row pointing at a builder image;
   - commit `.mucli/jobs.toml` `[sandbox] image` or a devcontainer, and approve it.

   For a monorepo with a large dependency tree (Rust + ffmpeg/ONNX/LLVM, several Node apps,
   compose services), that manual work is the blocker.
2. **"Couldn't run" is reported as "nothing regressed".** Unrunnable checks are treated as
   pre-existing failures. A job can therefore reach review and push without its validation ever
   executing. This is a correctness bug and should be fixed first, regardless of the rest of
   this plan (§3).

## 2. Goals

1. **Every job either runs its checks for real or says clearly that it couldn't.** It is never
   silently "verified".
2. **Zero-config for common stacks.** With no configuration at all, a repository with lockfiles
   gets its dependencies installed from the registries those lockfiles need, cached by lockfile
   digest, and reused across jobs.
3. **Agent-assisted for hard stacks.** When deterministic installs aren't enough (system
   libraries, SDKs, native toolchains), an *environment agent* works in a writable **build
   container** until the checks can execute, and records what it did as a reviewable
   **recipe**. That recipe then becomes deterministic for every later job.
4. **Isolation stays the same where it matters.** Plan, implement, review and checks stages keep
   today's locked-down containers. Only the build container is writable and networked, and it
   never holds the model key, git push credentials or the editable worktree.

### Non-goals
- Letting implement or check stages `apt install` or reach arbitrary hosts.
- Running repository CI files automatically. CI files are read as *hints* for the environment
  agent; the decision in `job_build_environments.md` §2 to reject automatic discovery stands
  for direct use.
- Replacing hand-made environments, devcontainers or Toolchains rows. They remain the
  highest-precedence sources; this plan fills the "nothing configured" and "configured but
  incomplete" cases.

## 3. Phase 0: an honest readiness gate (small; do first)

**Classify each check result as `passed`, `failed` or `unrunnable`.** A check is `unrunnable`
when:
- the exit code is 126 or 127; or
- the output matches a "tool missing" signature from a small, tested table, such as:
  - `sh: N: X: not found`, `command not found`, `X: No such file or directory`
  - `Cannot find module`, `ERR_MODULE_NOT_FOUND`
  - `ModuleNotFoundError` *on the test runner itself*, `cargo: not found`
  - `npm ERR! code E403/ENOTFOUND/EAI_AGAIN` during an install;
- or `node_modules/.bin/<x>` is referenced but missing.

Record the missing tool, including nested ones such as `vitest` (via npm), and the likely
cause: dependencies not installed, registry blocked, or tool absent from the image.

**What the classification changes:**
- Triage never turns `unrunnable` into "pre-existing". If any validation command is
  `unrunnable` at the head, verification is **`unverified`**, not `failed`, and the job parks
  with `AttentionReason.ENVIRONMENT_FAILURE`. The message names the commands and the cause, for
  example: "3 checks couldn't run: `vitest`, `tsc` and `eslint` come from `node_modules`, which
  wasn't installed because `registry.npmjs.org` returned 403. **Prepare environment**, or
  allow the registry."
- If the user chooses **Continue unverified**, the review stage is told the checks did not run.
  The review report's verdict is then capped at `needs_work`, never `ready` or
  `ready_with_nits`. Push requires an explicit override, just like `do_not_merge` today.
- The UI shows a new `unverified` status (glyph `?`, semantic `caution`) in the vocabulary and
  in check rows.

**Stray global tools ("wrong tool" is also `unrunnable`).** In the rsq-cloud run, `eslint`
resolved to `/usr/bin/eslint`, which is **ESLint 6.4**. Debian's `npm` package installs
`node-eslint` as a dependency into `mucli-job-runtime`. With `node_modules` missing, the project's
own ESLint never ran; an ancient global one did, and failed with a misleading config error.

Fixes:
- **Runtime image.** Install Node from the upstream binary tarball (or `nodejs` only, without
  Debian's `npm` dependency tree), so the image carries no global dev tools. A build-time test
  asserts that `eslint`, `prettier`, `tsc`, `vitest` and `jest` are absent from `PATH`.
- **Detection.** If a check's tool is declared in the component's manifest (`devDependencies`,
  `[tool.*]`, `rust-toolchain.toml`) but resolves outside the project (for example `/usr/bin`
  instead of `node_modules/.bin`), the result is `unrunnable` with the cause "project-local
  `eslint` not installed; a system copy would have run instead".

**Effort:** about a day. Touches `mu/jobs/checks.py` (classification, triage), `verification.py`,
`review.py` (verdict cap), `publish.py` (override), and `mu/design/vocabulary.json`. Tests use
the rsq-cloud outputs above as fixtures.

## 4. The Prepare stage

A new pipeline stage, **`preparing`**, runs after `provisioning` (environment snapshots) and
before `planning`. It also runs on demand: from the **Prepare environment** action, `/job
prepare`, and Setup → "Prepare repository". Its output is a job image in which the repository's
validation commands can execute.

```
provisioning ─► preparing ─► planning ─► plan_review ─► implementing ⇄ checking ─► …
                  │
                  ├─ 1. resolve components   (detect.py over the base commit)
                  ├─ 2. dependency layer     (deterministic installs, cached by lockfile digest)
                  ├─ 3. readiness probe      (run validation commands on the base commit)
                  ├─ 4. environment agent    (only if the probe has unrunnable checks; budgeted)
                  └─ 5. seal                 (commit image, secret scan, record recipe)
```

### 4.1 Resolve components
`detect_toolchains` already finds manifests, lockfiles, base images, default checks and egress
hosts per component root (depth 3). For rsq-cloud this yields one row per Node app
(`resq-management`, `resq-frontend`, …), each Rust crate, Python packages and so on. Each
component maps to a build-environment binding, using the existing `paths` resolution, so
monorepos keep working.

### 4.2 Dependency layer (deterministic, no agent)

For each component, run the lockfile's canonical install in the **build container** (§5):

| Ecosystem | Command |
|---|---|
| npm / pnpm / yarn / bun | `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`, `bun install --frozen-lockfile` |
| Python | `uv sync --frozen` / `poetry install --no-root` / `pip install -r` |
| Rust / Go | `cargo fetch` (+ `cargo build --tests` optional warm-up) / `go mod download` |
| JVM / .NET / Ruby | `./gradlew --offline` warm-up after `dependencies`, `mvn -B dependency:go-offline`, `dotnet restore`, `bundle install` |

Rules:
- **Egress** is exactly the registries `detect.py` lists for the lockfiles present, plus
  repository-declared private registries. It is approved once per repository and domain set,
  through the existing `environment_approvals.json` flow (kind `"egress"`). Without approval,
  Prepare stops and offers the approval; it never proceeds silently.
- **Credentials for private registries** come from a new Settings → Build secrets store
  (`~/.mucli/jobs/build_secrets/`). They are mounted only into the build container as
  `.npmrc`, `pip.conf` or cargo credential files, and are **never** present in stage images.
  The seal step's secret scan enforces that.
- **The output goes into a dependency cache** keyed by
  `(repository, component root, ecosystem, sha256(lockfile + manifest + toolchain image digest))`
  under `~/.mucli/cache/deps/<repo>/<key>/`, for example `node_modules`, `.venv`,
  `CARGO_HOME/registry`, `target/` warm builds, or `GOMODCACHE`.
  - Entries are content-addressed and reproducible from the lockfile, so unlike environment
    *snapshots* they are safely **shared across jobs**, mounted read-only.
  - A job whose lockfile is unchanged skips the install entirely.
  - Eviction is LRU with a size cap (`jobs.dependency_cache_gb`, default 30).
- **Stage containers** see the cached trees at their usual paths, for example
  `/workspace/resq-management/node_modules`, through read-only bind mounts overlaid on the
  worktree. If the agent edits a lockfile, the cache key changes; see §6.

### 4.3 Readiness probe

Run each validation command (and each `[[checks.suites]]` entry) on the **base commit** in a
normal locked-down check container, using the job image and the dependency mounts. Classify the
results with the §3 rules:
- **Every command is `passed` or `failed`:** the environment is ready. Baseline failures are
  real pre-existing failures; record them for triage.
- **Any command is `unrunnable`:** go to §4.4, or park, if the environment agent is disabled or
  out of budget.

The probe's results double as the **baseline** for later triage, so the checking stage doesn't
need to re-run the base commit.

### 4.4 Environment agent (only when needed)

A dedicated agent whose single goal is **"make these commands runnable"**, not "make them pass".
It runs in the build container with:
- **Tools:** `bash` (as root inside the container), file read access to the repository (a
  read-only mount of the base commit; it cannot edit the worktree), `write_recipe_step`, and
  `run_probe`.
- **Network:** the approved registries plus OS package mirrors (`deb.debian.org`,
  `security.debian.org`, `archive.ubuntu.com`, `dl-cdn.alpinelinux.org`), toolchain installers
  (`static.rust-lang.org`, `sh.rustup.rs`, `nodejs.org`, `go.dev`, `github.com` release assets)
  and any repository-declared hosts. The whole set is approved per repository.
- **Hints:** the readiness report (which command, which missing tool, the error tail),
  `detect.py`'s findings, and the repository's CI files (`.github/workflows/*.yml`,
  `.gitlab-ci.yml`, `Dockerfile*`, `README` "Development" sections). These are summarised as
  context and never executed directly.
- **Budget:** `jobs.prepare.max_iterations` (40), `max_cost_usd` (2), `max_runtime_s` (1800).
  Separate from the job's implementation budget, and shown in the cost view.

**Recording.** Every state-changing command the agent runs successfully is appended to a
**recipe**: an ordered list of `RUN` steps with the reason the agent gave. For example:

```dockerfile
# mucli build recipe · rsq-cloud · generated 2026-10-15 by the environment agent
# Goal: make `cargo test -p stream-processor-rs` and `npm --prefix resq-management test` runnable
FROM mucli-job-runtime:latest
RUN apt-get update && apt-get install -y --no-install-recommends \
      clang llvm-dev libavcodec-dev libavformat-dev libavutil-dev libswscale-dev pkg-config cmake  # ffmpeg-sys needs these
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.83     # rust-toolchain.toml pins 1.83
ENV PATH=/root/.cargo/bin:$PATH
RUN /opt/scripts/fetch-onnxruntime.sh 1.20.1                                                    # from .github/workflows/rust.yml
```

The agent finishes when `run_probe` reports no `unrunnable` commands, or when its budget runs
out (the job parks with the report and the partial recipe).

### 4.5 Seal

1. **Rebuild from the recipe, not the agent's mutated container.** The image is built cleanly
   from the recipe in a fresh build, which proves the recipe reproduces the result. If the
   rebuild's probe disagrees with the agent's, Prepare fails and shows both outputs.
2. Add the mucli job layer (`toolchain.build_image`) and run the existing **secret scan gate**.
3. Record `metadata.build_environments[*].prepared = {recipe_digest, probe, deps_keys, cost}`.
4. **Offer to keep the recipe:**
   - **Keep for this repository** stores it as a mucli-side template
     (`~/.mucli/jobs/recipes/<repo>/<component>.Dockerfile`). Later jobs use it as a
     Toolchains-equivalent base. It is built once and cached by recipe digest; this is a
     template, unlike per-job snapshots.
   - **Propose to the repository** opens a review job or PR that commits
     `.mucli/build/<component>.Dockerfile` and the matching `.mucli/jobs.toml` entries, so the
     team shares it. It is then picked up through the existing `[sandbox] image`/devcontainer
     approval path.
   - **Discard** uses it for this job only.

Next time, the deterministic path covers everything: recipe image, cached dependencies, and the
probe passes in seconds. **The agent should run once per stack change, not once per job.**

## 5. The build container

| Property | Stage containers (unchanged) | Build container (new) |
|---|---|---|
| Rootfs | read-only | **writable** (container layer), discarded after seal |
| User | host uid:gid, `--cap-drop ALL` | root *inside* a user namespace (`--userns` remap, or rootless engine); `--cap-drop ALL --cap-add CHOWN,DAC_OVERRIDE,FOWNER,SETUID,SETGID` for package managers; never `--privileged` |
| Network | relay or bridge only, egress allowlist | egress allowlist = approved registries + OS mirrors + installers; **no model hosts** |
| Worktree | read-write (implement) | **none**; the base commit is mounted read-only at `/src` |
| Model access | via relay | environment agent only, via relay (as today) |
| Credentials | none | registry tokens from Build secrets, mounted as files, scrubbed before seal |
| Docker | per binding (Sysbox engine) | the same Sysbox engine, if the binding has `docker = true`, for image-building recipes |
| Lifetime | per stage | per Prepare run; labelled `io.mucli.job` and swept like other job artifacts |

Installs run with network access and execute lifecycle scripts (`postinstall`, `build.rs`).
That has the same risk class as the existing devcontainer `RUN` approval, and gets the same
approval (per repository and fingerprint) plus these extra containment rules:
- no credentials besides scoped registry tokens;
- no worktree;
- egress limited to the approved list;
- the output image is scanned.

Implement-stage containers then run the result with no network for installs.

## 6. During implementation: adding a dependency

Agents legitimately add dependencies (a `package.json` entry, a `Cargo.toml` crate). Today they
fail with a 403. New broker tool for the implement stage:
`install_dependencies(component, reason)`.

1. It is valid only when the component's **lockfile or manifest changed** in the worktree.
2. The broker re-runs §4.2's deterministic install for that component in a build container,
   using the *worktree's* lockfile (copied in read-only), against the same approved registries.
3. The new dependency tree goes into the cache under the new key and is remounted into the
   stage. The stage keeps running.
4. The tool returns the install log tail. If the install needs a registry outside the approved
   list, the job parks for approval.
5. **System packages and toolchains are never installed this way.** The agent can call
   `request_environment_change(description)`. That parks the job with the request and offers
   **Prepare again**, which re-runs the environment agent with the request as an extra goal and
   produces a new recipe version.

`run_in_environment` (rev 3, §4) keeps working for monorepos. It now resolves each binding to
its prepared image.

## 7. Services for integration tests

Many suites need a database, cache or broker. Add to `.mucli/jobs.toml`:

```toml
[[services]]
name  = "postgres"
image = "postgres:16"
env   = { POSTGRES_PASSWORD = "test" }
ports = [5432]
for   = ["resq-backend"]        # bindings / suites that get it
```

Services start on the job's internal network for check stages, and for implement stages when
the agent runs tests. They are reachable by name, have no egress, and are torn down with the
job. Anything more complex keeps using the Sysbox job engine with compose (rev 3, §7).

## 8. UX

- **New job sheet:** a "Build sandbox" row showing readiness per component, for example
  `resq-management ✓ ready (deps cached) · stream-processor-rs ◆ needs preparing`. Actions:
  **Prepare now** and **Approve registries**.
- **Job page:** the stepper gains **Prepare**. Its tab shows components, dependency cache
  hits, the probe table (command → runnable or unrunnable, with cause), the environment agent's
  transcript, and the recipe diff with Keep / Propose / Discard.
- **Setup → Prepare repository:** runs Prepare on a branch with no job. This is the one-time
  onboarding for rsq-cloud.
- **Checks tab:** an `unverified` row state (§3) with the cause and a one-click **Prepare
  environment**.
- **Terminal:** `/job prepare <repo|job>`, `/job recipe <repo>`, and the `?` unverified glyph
  in the jobs TUI.
- **Android:** the same readiness row, Prepare action and unverified state.

## 9. Settings

| Setting | Default | Purpose |
|---|---|---|
| `jobs.prepare.enabled` | `true` | Run the Prepare stage. |
| `jobs.prepare.agent` | `"ask"` (`ask`, `auto`, `off`) | Whether the environment agent may run without a prompt. |
| `jobs.prepare.max_cost_usd` / `max_iterations` / `max_runtime_s` | 2 / 40 / 1800 | Environment agent budget. |
| `jobs.dependency_cache_gb` | 30 | LRU cap for the shared dependency cache. |
| `jobs.verification.allow_unverified_push` | `false` | Whether pushing with unrunnable checks needs an override. |
| Build secrets | (none) | Registry tokens, mounted only into build containers. |

## 10. Phases

| Phase | Scope | Done when |
|---|---|---|
| **0. Honest gate** | §3 `unrunnable` classification, `unverified` verification, review cap, push override, vocabulary/UI. | Re-running job 1c6b9c8f parks with "3 checks couldn't run…" instead of reaching `ready_with_nits`. |
| **1. Dependency layer** | Prepare stage skeleton, build container, deterministic installs, registry approval, shared dependency cache, probe as baseline. | An rsq-cloud `resq-management` job runs `vitest`, `tsc` and `eslint` for real, with no configuration beyond one registry approval. The second job skips installs. |
| **2. Install broker** | `install_dependencies` during implement; cache rekeying. | An agent adds an npm package mid-job and the checks see it. |
| **3. Environment agent and recipes** | Writable build container, agent tools, recipe recording, clean rebuild, Keep / Propose / Discard, recipe templates. | `stream-processor-rs` (ffmpeg, ONNX, LLVM) becomes runnable through the agent once; the next job uses the kept recipe with no agent. |
| **4. Services and build secrets** | `[[services]]`, private-registry tokens. | A backend suite needing Postgres runs; a private npm scope installs. |
| **5. Surfaces** | Web, Android and terminal parity; Setup → Prepare repository; docs. | All three front ends support the flow. |

## 11. Decisions needed

1. **Shared dependency cache across jobs.** The rev 3 decision was "snapshots are never
   shared". This plan shares *lockfile-keyed dependency trees* (read-only) and *recipe-built
   templates*, but still never shares mutable environment snapshots. Recommended: yes.
2. **Environment agent default.** `ask` (recommended) asks before spending money or running
   installers; `auto` suits trusted repositories.
3. **Recipe home.** mucli-local only, or proposed into the repository
   (`.mucli/build/*.Dockerfile`)? Recommended: offer both, with mucli-local as the default.
4. **Phase 0 ships on its own, first.** It is a correctness fix. Today a job can report
   success without running a single check.
