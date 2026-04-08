.PHONY: format test eval eval-simulate eval-swebench

TEST_MUCLI_HOME ?= /tmp/mucli-test
TEST_ENV = MUCLI_HOME=$(TEST_MUCLI_HOME) PYTHONPATH=.

format:
	black .

test:
	rm -rf $(TEST_MUCLI_HOME)
	$(TEST_ENV) pytest tests

eval:
	PYTHONPATH=. python -m evals.harness \
	  --seed 1337 \
	  --corpus evals/corpus/tasks.json \
	  --execution-mode execute \
	  --output evals/artifacts/eval_run_latest.json \
	  --trend evals/artifacts/trend_report.md \
	  --digest evals/artifacts/eval_digest_latest.md

eval-simulate:
	PYTHONPATH=. python -m evals.harness \
	  --seed 1337 \
	  --corpus evals/corpus/tasks.json \
	  --execution-mode simulate \
	  --output evals/artifacts/eval_run_simulated.json \
	  --trend evals/artifacts/trend_report.md \
	  --digest evals/artifacts/eval_digest_simulated.md

# Usage: make eval-swebench SWEBENCH_PATH=/path/to/swebench_lite.jsonl SWEBENCH_LIMIT=100
eval-swebench:
	@if [ -z "$(SWEBENCH_PATH)" ]; then \
		echo "SWEBENCH_PATH is required (path to SWE-bench JSONL)"; \
		exit 1; \
	fi
	PYTHONPATH=. python -m evals.harness \
	  --seed 1337 \
	  --corpus "$(SWEBENCH_PATH)" \
	  --corpus-format swebench-lite \
	  --execution-mode execute \
	  --swebench-limit $${SWEBENCH_LIMIT:-100} \
	  --swebench-root "$${SWEBENCH_ROOT:-}" \
	  --output evals/artifacts/eval_run_swebench.json \
	  --trend evals/artifacts/trend_report.md \
	  --digest evals/artifacts/eval_digest_swebench.md

run:
	./mucli
