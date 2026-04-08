.PHONY: format test eval eval-swebench

TEST_MUCLI_HOME ?= /tmp/mucli-test
TEST_ENV = MUCLI_HOME=$(TEST_MUCLI_HOME) PYTHONPATH=.

format:
	black .

test:
	rm -rf $(TEST_MUCLI_HOME)
	$(TEST_ENV) pytest tests

eval:
	@if [ -z "$(EVAL_PROVIDER)" ] || [ -z "$(EVAL_MODEL)" ]; then \
		echo "EVAL_PROVIDER and EVAL_MODEL are required (example: make eval EVAL_PROVIDER=openai EVAL_MODEL=gpt-4o-mini)"; \
		exit 1; \
	fi
	PYTHONPATH=. python -m evals.harness \
	  --seed 1337 \
	  --corpus evals/corpus/tasks.json \
	  --provider "$(EVAL_PROVIDER)" \
	  --model "$(EVAL_MODEL)" \
	  --ollama-host "$${OLLAMA_HOST:-}" \
	  --agent-mode "$${EVAL_AGENT_MODE:-feature}" \
	  --output evals/artifacts/eval_run_latest.json \
	  --trend evals/artifacts/trend_report.md \
	  --digest evals/artifacts/eval_digest_latest.md

# Usage: make eval-swebench SWEBENCH_PATH=/path/to/swebench_lite.jsonl SWEBENCH_LIMIT=100 EVAL_PROVIDER=openai EVAL_MODEL=gpt-4o-mini
eval-swebench:
	@if [ -z "$(SWEBENCH_PATH)" ]; then \
		echo "SWEBENCH_PATH is required (path to SWE-bench JSONL)"; \
		exit 1; \
	fi
	@if [ -z "$(EVAL_PROVIDER)" ] || [ -z "$(EVAL_MODEL)" ]; then \
		echo "EVAL_PROVIDER and EVAL_MODEL are required (example: make eval-swebench ... EVAL_PROVIDER=openai EVAL_MODEL=gpt-4o-mini)"; \
		exit 1; \
	fi
	PYTHONPATH=. python -m evals.harness \
	  --seed 1337 \
	  --corpus "$(SWEBENCH_PATH)" \
	  --corpus-format swebench-lite \
	  --swebench-limit $${SWEBENCH_LIMIT:-100} \
	  --swebench-root "$${SWEBENCH_ROOT:-}" \
	  --provider "$(EVAL_PROVIDER)" \
	  --model "$(EVAL_MODEL)" \
	  --ollama-host "$${OLLAMA_HOST:-}" \
	  --agent-mode "$${EVAL_AGENT_MODE:-feature}" \
	  --output evals/artifacts/eval_run_swebench.json \
	  --trend evals/artifacts/trend_report.md \
	  --digest evals/artifacts/eval_digest_swebench.md

run:
	./mucli
