PYTHON ?= python3

.PHONY: test run demo

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

run:
	PYTHONPATH=src $(PYTHON) -m proofix.cli run

demo:
	PYTHONPATH=src $(PYTHON) -m proofix.cli demo
