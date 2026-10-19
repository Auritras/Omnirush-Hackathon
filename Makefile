PYTHON ?= python3

-include .env

.PHONY: test run demo fixture-reset export

PROOFIX_DATA_ROOT ?= .proofix-data
PROOFIX_HOST ?= 127.0.0.1
PROOFIX_PORT ?= 8000
PROOFIX_ALLOWED_ROOTS ?= $(PROOFIX_DATA_ROOT)
FORMAT ?= markdown
OUTPUT ?= exports/report.md

DATA_ROOT ?= $(PROOFIX_DATA_ROOT)
ALLOWED_ROOTS ?= $(PROOFIX_ALLOWED_ROOTS)
HOST ?= $(PROOFIX_HOST)
PORT ?= $(PROOFIX_PORT)

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

run:
	PROOFIX_HOST="$(HOST)" PROOFIX_PORT="$(PORT)" PROOFIX_DATA_ROOT="$(DATA_ROOT)" PROOFIX_ALLOWED_ROOTS="$(ALLOWED_ROOTS)" PYTHONPATH=src $(PYTHON) -m proofix.cli run --data-root "$(DATA_ROOT)" --allowed-root "$(ALLOWED_ROOTS)" --host "$(HOST)" --port "$(PORT)"

demo:
	PROOFIX_HOST="$(HOST)" PROOFIX_PORT="$(PORT)" PROOFIX_DATA_ROOT="$(DATA_ROOT)" PROOFIX_ALLOWED_ROOTS="$(ALLOWED_ROOTS)" PYTHONPATH=src $(PYTHON) -m proofix.cli demo --data-root "$(DATA_ROOT)" --allowed-root "$(ALLOWED_ROOTS)" --host "$(HOST)" --port "$(PORT)"

fixture-reset:
	sh fixtures/broken-parser-repo/reset.sh

export:
	@test -n "$(RUN_ID)" || (printf '%s\n' 'RUN_ID is required' >&2; exit 2)
	PROOFIX_DATA_ROOT="$(DATA_ROOT)" PYTHONPATH=src $(PYTHON) -m proofix.cli export "$(RUN_ID)" "$(OUTPUT)" --format "$(FORMAT)" --data-root "$(DATA_ROOT)"
