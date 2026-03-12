PYTHON ?= python3
UVICORN ?= uvicorn
APP_MODULE ?= server.app.main:app
HOST ?= 0.0.0.0
PORT ?= 8000
GUI_URL ?= http://127.0.0.1:$(PORT)/gui
GUI_STATIC_PORT ?= 4173

.PHONY: help install-dev run-server run-server-no-reload test-server test lint format check run-cli run-gui run-gui-static

help:
	@echo "Mu-CLI developer targets"
	@echo "  install-dev     Install project + dev dependencies"
	@echo "  run-server      Start FastAPI server with reload"
	@echo "  run-server-no-reload Start FastAPI server without reload"
	@echo "  test-server     Run server test suite"
	@echo "  test            Run full test suite"
	@echo "  lint            Run Ruff linting"
	@echo "  check           Run lint + tests"
	@echo "  run-cli         Show CLI help"
	@echo "  run-gui         Start server and serve GUI at $(GUI_URL)"
	@echo "  run-gui-static  Serve gui/ statically on localhost:$(GUI_STATIC_PORT)"

install-dev:
	$(PYTHON) -m pip install -e .[dev] --no-build-isolation

run-server:
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT) --reload

run-server-no-reload:
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT)

test-server:
	pytest -q server/tests || test $$? -eq 5

test:
	pytest -q || test $$? -eq 5

lint:
	ruff check . --fix

check: lint test

run-cli:
	$(PYTHON) cli/mu_cli.py --help

run-gui:
	@echo "Starting Mu-CLI server with GUI available at $(GUI_URL)"
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT) --reload

run-gui-static:
	@echo "Serving static GUI preview at http://127.0.0.1:$(GUI_STATIC_PORT)"
	$(PYTHON) -m http.server $(GUI_STATIC_PORT) --directory gui
