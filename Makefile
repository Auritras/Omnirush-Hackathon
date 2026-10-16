PYTHON ?= python3

.PHONY: test run demo fixture-reset export

DATA_ROOT ?= .proofix
HOST ?= 127.0.0.1
PORT ?= 8000
FORMAT ?= markdown
OUTPUT ?= exports/report.md

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

run:
	PYTHONPATH=src $(PYTHON) -m proofix.cli run --data-root "$(DATA_ROOT)" --host "$(HOST)" --port "$(PORT)"

demo:
	PYTHONPATH=src $(PYTHON) -m proofix.cli demo --data-root "$(DATA_ROOT)" --host "$(HOST)" --port "$(PORT)"

fixture-reset:
	sh fixtures/broken-parser-repo/reset.sh

export:
	@test -n "$(RUN_ID)" || (printf '%s\n' 'RUN_ID is required' >&2; exit 2)
	PYTHONPATH=src $(PYTHON) -m proofix.cli export "$(RUN_ID)" "$(OUTPUT)" --format "$(FORMAT)" --data-root "$(DATA_ROOT)"
