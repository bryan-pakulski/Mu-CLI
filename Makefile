.PHONY: format test

TEST_MUCLI_HOME ?= /tmp/mucli-test
TEST_ENV = MUCLI_HOME=$(TEST_MUCLI_HOME) PYTHONPATH=.

format:
	black .

test:
	rm -rf $(TEST_MUCLI_HOME)
	$(TEST_ENV) pytest tests

run:
	./mucli
