.PHONY: format test

format:
	black .

test:
	PYTHONPATH=. pytest tests

run:
	./mucli

