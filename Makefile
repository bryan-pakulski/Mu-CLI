PYTHON ?= python3
UVICORN ?= uvicorn
APP_MODULE ?= server.app.main:app
HOST ?= 0.0.0.0
PORT ?= 8000

.PHONY: help install-dev run-server test-server test lint format check run-cli run-gui

help:
	@echo "Mu-CLI developer targets"
	@echo "  install-dev  Install project + dev dependencies"
	@echo "  run-server   Start FastAPI server"
	@echo "  test-server  Run server test suite"
	@echo "  test         Run full test suite"
	@echo "  lint         Run Ruff linting"
	@echo "  check        Run lint + tests"
	@echo "  run-cli      Launch CLI component (placeholder scaffold)"
	@echo "  run-gui      Launch GUI component (placeholder scaffold)"

install-dev:
	$(PYTHON) -m pip install -e .[dev] --no-build-isolation

run-server:
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT) --reload

test-server:
	pytest -q server/tests || test $$? -eq 5

test:
	pytest -q || test $$? -eq 5

lint:
	ruff check .

check: lint test

run-cli:
	@echo "CLI scaffold is present; runtime command will be added in a future phase."
	@cat cli/README.md

run-gui:
	@echo "GUI scaffold is present; runtime command will be added in a future phase."
	@cat gui/README.md
