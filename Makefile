PYTHON ?= python
PYTHONPATH ?= agents

PROVIDER ?= echo
MODEL ?=
API_KEY ?=
SYSTEM ?= You are a helpful coding assistant. Keep responses concise.
WORKSPACE ?=
PRICING_CONFIG ?= .mu_cli/pricing.json
SESSION ?= default
APPROVAL_MODE ?= ask
AGENTIC_PLANNING ?= 1
DEBUG ?= 0

.PHONY: test test-verbose run run-web run-echo run-openai run-gemini models docker-build docker-run-web docker-run-cli docker-models help

help:
	@echo "Targets:"
	@echo "  make test            - Run unit tests"
	@echo "  make test-verbose    - Run unit tests (verbose)"
	@echo "  make models          - Show supported model catalog"
	@echo "  make run-echo        - Start CLI with echo provider"
	@echo "  make run-openai      - Start CLI with openai provider (uses OPENAI_API_KEY)"
	@echo "  make run-gemini      - Start CLI with gemini provider (uses GEMINI_API_KEY/GOOGLE_API_KEY)"
	@echo "  make run PROVIDER=<provider> MODEL=<model> [API_KEY=<key>] [WORKSPACE=<path>] [AGENTIC_PLANNING=0|1] [DEBUG=0|1]"
	@echo "  make docker-build    - Build local container image (mu-cli:latest)"
	@echo "  make docker-run-web  - Run Flask GUI in container on http://localhost:5000"
	@echo "  make docker-run-cli  - Start interactive CLI in container"
	@echo "  make docker-models   - Print model catalog from container"

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s agents/tests

test-verbose:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s agents/tests -v

models:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m mu_cli.cli --list-models

run:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m mu_cli.cli \
		--provider "$(PROVIDER)" \
		$(if $(MODEL),--model "$(MODEL)") \
		$(if $(API_KEY),--api-key "$(API_KEY)") \
		$(if $(WORKSPACE),--workspace "$(WORKSPACE)") \
		--pricing-config "$(PRICING_CONFIG)" \
		--session "$(SESSION)" \
		--approval-mode "$(APPROVAL_MODE)" \
		$(if $(filter 0,$(AGENTIC_PLANNING)),--no-agentic-planning) \
		$(if $(filter 1,$(DEBUG)),--debug) \
		--system "$(SYSTEM)"

run-echo:
	$(MAKE) run PROVIDER=echo MODEL=echo

run-openai:
	$(MAKE) run PROVIDER=openai MODEL=$${MODEL:-gpt-4o-mini}

run-gemini:
	$(MAKE) run PROVIDER=gemini MODEL=$${MODEL:-gemini-2.0-flash}

run-web:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m mu_cli.web

docker-build:
	docker build -t mu-cli:latest -f Dockerfile .

docker-run-web:
	docker run --rm -p 5000:5000 \
		-v "$(CURDIR)/.mu_cli:/app/.mu_cli" \
		-v "$(CURDIR):/workspace" \
		-e OPENAI_API_KEY \
		-e GEMINI_API_KEY \
		-e GOOGLE_API_KEY \
		mu-cli:latest web

docker-run-cli:
	docker run --rm -it \
		-v "$(CURDIR)/.mu_cli:/app/.mu_cli" \
		-v "$(CURDIR):/workspace" \
		-e OPENAI_API_KEY \
		-e GEMINI_API_KEY \
		-e GOOGLE_API_KEY \
		mu-cli:latest cli --provider echo --model echo --workspace /workspace

docker-models:
	docker run --rm mu-cli:latest models
