PYTHON ?= python
PYTHONPATH ?= agents

PROVIDER ?= echo
MODEL ?=
API_KEY ?=
SYSTEM ?= You are a helpful coding assistant. Keep responses concise.

.PHONY: test test-verbose run run-echo run-openai run-gemini help

help:
	@echo "Targets:"
	@echo "  make test            - Run unit tests"
	@echo "  make test-verbose    - Run unit tests (verbose)"
	@echo "  make run-echo        - Start CLI with echo provider"
	@echo "  make run-openai      - Start CLI with openai provider (uses OPENAI_API_KEY)"
	@echo "  make run-gemini      - Start CLI with gemini provider (uses GEMINI_API_KEY/GOOGLE_API_KEY)"
	@echo "  make run PROVIDER=<provider> MODEL=<model> [API_KEY=<key>]"

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s agents/tests

test-verbose:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s agents/tests -v

run:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m mu_cli.cli \
		--provider "$(PROVIDER)" \
		$(if $(MODEL),--model "$(MODEL)") \
		$(if $(API_KEY),--api-key "$(API_KEY)") \
		--system "$(SYSTEM)"

run-echo:
	$(MAKE) run PROVIDER=echo

run-openai:
	$(MAKE) run PROVIDER=openai MODEL=$${MODEL:-gpt-4o-mini}

run-gemini:
	$(MAKE) run PROVIDER=gemini MODEL=$${MODEL:-gemini-2.0-flash}
