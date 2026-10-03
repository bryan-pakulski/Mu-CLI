.PHONY: format test test-nvim run

TEST_MUCLI_HOME ?= /tmp/mucli-test
TEST_ENV = MUCLI_HOME=$(TEST_MUCLI_HOME) PYTHONPATH=.

format:
	black .

test:
	rm -rf $(TEST_MUCLI_HOME)
	$(TEST_ENV) pytest tests

run:
	./mucli

test-nvim:
	NVIM_LOG_FILE=/tmp/mucli-nvim-test.log nvim --headless -u NONE -i NONE -l integrations/nvim/tests/smoke.lua
	NVIM_LOG_FILE=/tmp/mucli-nvim-test.log nvim --headless -u NONE -i NONE -l integrations/nvim/tests/context.lua
	NVIM_LOG_FILE=/tmp/mucli-nvim-test.log nvim --headless -u NONE -i NONE -l integrations/nvim/tests/conversation.lua
	NVIM_LOG_FILE=/tmp/mucli-nvim-test.log nvim --headless -u NONE -i NONE -l integrations/nvim/tests/answers.lua
	NVIM_LOG_FILE=/tmp/mucli-nvim-test.log nvim --headless -u NONE -i NONE -l integrations/nvim/tests/input.lua
