# Makefile for open-webui-honcho
#
# Quality/security suite:
#   make lint      - ruff check (package + Filter + scripts + tests)
#   make format    - ruff format (writes) ; format-check verifies only
#   make typecheck - mypy on the package + Filter + scripts
#   make test      - pytest with coverage (term + xml + html)
#   make codeql    - build a CodeQL database (scoped to our code) and analyze it (SARIF)
#   make opengrep  - run OpenGrep static analysis (JSON)
#   make scan      - codeql + opengrep
#   make all       - lint + typecheck + test + scan (full suite)
#   make clean     - remove generated reports and caches
#
# lint, typecheck, and test failures DO fail the build. Security scans (codeql,
# opengrep) are informational and never fail the target — review their reports.

SHELL := /bin/bash

PYTHON      ?= .venv/bin/python
PKG         := src/open_webui_honcho
FILTER      := functions/honcho_memory_filter.py
SCRIPTS     := scripts
TESTS       := tests
LINT_PATHS  := $(PKG) $(FILTER) $(SCRIPTS) $(TESTS)
MYPY_PATHS  := $(PKG) $(FILTER) $(SCRIPTS)
REPORTS_DIR := reports

RUFF        ?= $(PYTHON) -m ruff
MYPY        ?= $(PYTHON) -m mypy

CODEQL         ?= codeql
CODEQL_DB      := $(REPORTS_DIR)/codeql-db
CODEQL_SARIF   := $(REPORTS_DIR)/codeql.sarif
CODEQL_QUERIES := codeql/python-queries

OPENGREP        ?= opengrep
OPENGREP_CONFIG := p/python
OPENGREP_JSON   := $(REPORTS_DIR)/opengrep.json

.DEFAULT_GOAL := help
.PHONY: all lint format format-check typecheck test coverage codeql opengrep scan clean help

help:
	@echo "open-webui-honcho quality/security targets:"
	@echo "  make lint         - ruff check on $(LINT_PATHS)"
	@echo "  make format       - ruff format (writes changes)"
	@echo "  make format-check - ruff format --check (verify only)"
	@echo "  make typecheck    - mypy on $(MYPY_PATHS)"
	@echo "  make test         - pytest + coverage (term/xml/html)"
	@echo "  make codeql       - CodeQL database build + analysis -> $(CODEQL_SARIF)"
	@echo "  make opengrep     - OpenGrep static analysis -> $(OPENGREP_JSON)"
	@echo "  make scan         - codeql + opengrep"
	@echo "  make all          - lint + typecheck + test + scan (full suite)"
	@echo "  make clean        - remove $(REPORTS_DIR)/ and caches"

$(REPORTS_DIR):
	@mkdir -p $(REPORTS_DIR)

# --- Lint / format ----------------------------------------------------------
lint:
	$(RUFF) check $(LINT_PATHS)

format:
	$(RUFF) format $(LINT_PATHS)

format-check:
	$(RUFF) format --check $(LINT_PATHS)

# --- Type check -------------------------------------------------------------
typecheck:
	$(MYPY) $(MYPY_PATHS)

# --- Tests + coverage -------------------------------------------------------
test coverage: | $(REPORTS_DIR)
	$(PYTHON) -m pytest \
		--cov=open_webui_honcho \
		--cov-report=term-missing \
		--cov-report=xml:$(REPORTS_DIR)/coverage.xml \
		--cov-report=html:$(REPORTS_DIR)/htmlcov
	@echo ">> Coverage written to $(REPORTS_DIR)/coverage.xml and $(REPORTS_DIR)/htmlcov/"

# --- CodeQL -----------------------------------------------------------------
# Index only our own code (LGTM_INDEX_FILTERS keeps .venv / deps out of the DB),
# then run the standard Python security+quality query suite.
codeql: | $(REPORTS_DIR)
	@echo ">> Building CodeQL database (scoped to our code) ..."
	LGTM_INDEX_FILTERS=$$'exclude:**/*\ninclude:src/**\ninclude:functions/**\ninclude:scripts/**' \
		$(CODEQL) database create $(CODEQL_DB) \
			--language=python --source-root=. --overwrite --threads=0
	@echo ">> Analyzing with $(CODEQL_QUERIES) ..."
	$(CODEQL) database analyze $(CODEQL_DB) $(CODEQL_QUERIES) \
		--download --format=sarif-latest --output=$(CODEQL_SARIF) --threads=0
	@$(PYTHON) -c 'import json; r=json.load(open("$(CODEQL_SARIF)"))["runs"][0]; print(">> CodeQL:", len(r.get("results", [])), "result(s) ->", "$(CODEQL_SARIF)")'

# --- OpenGrep ---------------------------------------------------------------
# Leading "-" so findings (non-zero exit) do not fail the target.
opengrep: | $(REPORTS_DIR)
	@echo ">> Running OpenGrep ($(OPENGREP_CONFIG)) ..."
	-$(OPENGREP) scan --config $(OPENGREP_CONFIG) --quiet \
		--json --output $(OPENGREP_JSON) $(PKG) $(FILTER) $(SCRIPTS)
	@test -f $(OPENGREP_JSON) \
		&& $(PYTHON) -c 'import json; d=json.load(open("$(OPENGREP_JSON)")); print(">> OpenGrep:", len(d.get("results", [])), "finding(s),", len(d.get("errors", [])), "error(s) ->", "$(OPENGREP_JSON)")' \
		|| echo ">> OpenGrep: no JSON output produced"

# --- Aggregates -------------------------------------------------------------
scan: codeql opengrep

all: lint typecheck test scan
	@echo ">> All checks complete. Reports in $(REPORTS_DIR)/"

clean:
	rm -rf $(REPORTS_DIR) .coverage .coverage.* .pytest_cache .mypy_cache .ruff_cache
