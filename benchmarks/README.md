# μCLI Benchmark Suite

Measure agent performance across modes against repeatable tasks. Establishes
baselines that can be compared across:

* different LLM providers + models (`gpt-4o` vs. `gemini-2.5-flash` vs. an Ollama model)
* different μCLI versions (HEAD vs. a tagged release)
* different prompt / mode-instruction tweaks

This is **NOT** part of the regular pytest suite. Benchmarks make real LLM
calls (slow, $$, non-deterministic) and are explicitly opt-in.

## Anatomy

```
benchmarks/
  spec.py         BenchmarkSpec + BenchmarkRun + BenchmarkResult dataclasses
  rubrics.py      Scoring primitives: FileContains, CommandSucceeds, MaxToolCalls, ...
  harness.py      run_benchmark(spec, provider) — drives the agent end-to-end
  recorder.py     JSONL persistence + summarize() + diff()
  cli.py          `python -m benchmarks {list,run,summary,compare}`
  specs/          Built-in specs, one per mode
  fixtures/       Workspace templates copied into a tmpdir per run
  baselines/      Recorded result files (.jsonl, one per provider+model+date)
```

## Built-in specs

One canonical benchmark per agent mode:

| Mode | Spec | What it tests |
|---|---|---|
| default | `default__fix_off_by_one` | Locate + fix a 1-line bug; verify with pytest |
| debug | `debug__none_dereference` | Reproduce AttributeError, add a None guard, re-run |
| feature | `feature__add_calc_subtract` | Use the Feature Plan Engine to add a function + test |
| research | `research__explain_calc_module` | Read-only summary of a module (no edits) |
| loop | `loop__iterative_calc_polish` | Multi-step backlog: fix bug + add fn + add type hints |

Each spec carries a rubric of weighted checks; the final score is the
weighted ratio of passed rubrics.

## Running

```bash
# List
python -m benchmarks list
python -m benchmarks list --mode debug

# Run everything against gpt-4o-mini and save a fresh baseline
export OPENAI_API_KEY=...
python -m benchmarks run --provider openai --model gpt-4o-mini -v

# Run just one spec against a local Ollama model
python -m benchmarks run --name debug__none_dereference --provider ollama --model llama3

# Summary of a baseline
python -m benchmarks summary benchmarks/baselines/openai__gpt-4o-mini__2026-05-11.jsonl

# Compare two baselines — exit code 4 if regressions detected
python -m benchmarks compare baseline_old.jsonl baseline_new.jsonl
```

## Adding a benchmark

1. Add a fixture under `benchmarks/fixtures/<your_fixture>/` if your spec needs a workspace.
2. Add a `BenchmarkSpec(...)` to the right module under `benchmarks/specs/`.
3. Pick rubrics:
   * `CommandSucceeds(...)` — the gold standard; tests pass = the task is done.
   * `FileContains(...)` / `FileRegex(...)` — assert the agent's edit landed.
   * `FileNotContains(...)` — anti-patterns (anti-cheat, e.g. didn't delete the test).
   * `ResponseContains(...)` / `ResponseMatches(...)` — assert the explanation.
   * `MaxToolCalls(...)` / `MaxSeconds(...)` — efficiency budgets.
4. `python -m benchmarks list` to verify it appears.

## Scoring math

```
spec.score = sum(rubric.weight for rubric where rubric.passed) / sum(rubric.weight for all rubrics)
spec.passed = spec.score >= spec.pass_threshold AND run.status == "completed"
```

Most specs use `pass_threshold=1.0` (every rubric must pass). Loop-mode and
feature-mode specs use 0.70–0.85 to allow partial credit since their tasks
are more open-ended.

## Result files

```
benchmarks/baselines/openai__gpt-4o-mini__2026-05-11.jsonl
```

One JSON object per line. Append-only — multiple runs on the same date
accumulate. Use `summary` to aggregate, `compare` to diff.

## CI integration

```bash
# Run the suite, save the new baseline, fail the build if any regression
python -m benchmarks run --provider openai --model gpt-4o-mini \
    --baseline benchmarks/baselines/ci_head.jsonl
python -m benchmarks compare \
    benchmarks/baselines/ci_main.jsonl \
    benchmarks/baselines/ci_head.jsonl
# exit code 4 = regressions detected (score drop >0.05 OR pass rate drop)
```

## Testing the machinery

The harness itself is unit-tested with scripted fake providers, so the
benchmark code is covered by the regular pytest suite:

```bash
make test  # includes tests/test_benchmarks_{rubrics,recorder,harness}.py
```
