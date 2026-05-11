.PHONY: format test run benchmark benchmark-list benchmark-report benchmark-clean

TEST_MUCLI_HOME ?= /tmp/mucli-test
TEST_ENV = MUCLI_HOME=$(TEST_MUCLI_HOME) PYTHONPATH=.

# --- Benchmark configuration -------------------------------------------
#
# Override on the command line, e.g.:
#   make benchmark BENCH_PROVIDER=openai BENCH_MODEL=gpt-4o
#   make benchmark BENCH_MODE=debug
#   make benchmark BENCH_NAME=default__fix_off_by_one
#
BENCH_PROVIDER ?= openai
BENCH_MODEL ?=
BENCH_MODE ?=
BENCH_NAME ?=
BENCH_BASELINE ?=
BENCH_ENV = PYTHONPATH=.

# Compose the run flags from whatever was set.
BENCH_FLAGS = --provider $(BENCH_PROVIDER)
ifneq ($(BENCH_MODEL),)
BENCH_FLAGS += --model $(BENCH_MODEL)
endif
ifneq ($(BENCH_MODE),)
BENCH_FLAGS += --mode $(BENCH_MODE)
endif
ifneq ($(BENCH_NAME),)
BENCH_FLAGS += --name $(BENCH_NAME)
endif
ifneq ($(BENCH_BASELINE),)
BENCH_FLAGS += --baseline $(BENCH_BASELINE)
endif


format:
	black .

test:
	rm -rf $(TEST_MUCLI_HOME)
	$(TEST_ENV) pytest tests

run:
	./mucli

# --- Benchmark targets -------------------------------------------------

# List every built-in benchmark spec.
benchmark-list:
	$(BENCH_ENV) python3 -m benchmarks list

# Run benchmarks against a real LLM. Writes a JSONL baseline + a
# human-parsable Markdown report side-by-side under benchmarks/baselines/.
# Requires the appropriate API key (OPENAI_API_KEY, GEMINI_API_KEY) or a
# running local Ollama instance.
#
# Examples:
#   make benchmark
#   make benchmark BENCH_PROVIDER=gemini BENCH_MODEL=gemini-2.5-flash
#   make benchmark BENCH_MODE=debug
#   make benchmark BENCH_NAME=default__fix_off_by_one
benchmark:
	$(BENCH_ENV) python3 -m benchmarks run $(BENCH_FLAGS) -v

# Render an existing baseline JSONL as a Markdown report. Pass
# BENCH_BASELINE=path/to.jsonl ; if omitted, lists baselines/.
benchmark-report:
	@if [ -z "$(BENCH_BASELINE)" ]; then \
		echo "Usage: make benchmark-report BENCH_BASELINE=<path-to-baseline.jsonl>"; \
		echo ""; \
		echo "Available baselines:"; \
		ls -1 benchmarks/baselines/*.jsonl 2>/dev/null || echo "  (none yet — run 'make benchmark' first)"; \
		exit 1; \
	fi
	$(BENCH_ENV) python3 -m benchmarks report $(BENCH_BASELINE) --print

# Compare two baselines. Set BENCH_A and BENCH_B to JSONL paths. Exit
# code 4 = regressions detected (suitable for CI gating).
benchmark-compare:
	@if [ -z "$(BENCH_A)" ] || [ -z "$(BENCH_B)" ]; then \
		echo "Usage: make benchmark-compare BENCH_A=<old.jsonl> BENCH_B=<new.jsonl>"; \
		exit 1; \
	fi
	$(BENCH_ENV) python3 -m benchmarks compare $(BENCH_A) $(BENCH_B)

# Remove every recorded baseline. Use sparingly.
benchmark-clean:
	rm -f benchmarks/baselines/*.jsonl benchmarks/baselines/*.md
