API_IMAGE := stackfuel-learning-api
PIPELINE_IMAGE := stackfuel-pipeline
API_CONTAINER := stackfuel-api
API_NETWORK := stackfuel-pipeline-net
API_PORT ?= 9000
.DEFAULT_GOAL := build

.PHONY: build build-api build-pipeline api-start api-up api-stop run pipeline-run pipeline-run-container \
	pipeline-local dbt-setup dbt-debug dbt-build dbt-full-refresh dbt-test dbt-results dbt-rerun-check \
	dbt-incremental-check test pytest docs all \
	dashboard-setup dashboard setup pipeline-setup demo db-flush

# --- dbt (transformation, marts and reporting on top of the raw curated layer) -------------
# Run `python pipeline.py` (or `make run` / `make pipeline-local`) first: dbt reads raw.*_curated
# from db/duckdb/stackfuel.duckdb. DuckDB has a single writer: close other sessions on the file.
DBT_VENV ?= .venv-dbt
DBT_DB ?= $(CURDIR)/db/duckdb/stackfuel.duckdb
DBT = cd dbt && DBT_PROFILES_DIR=. STACKFUEL_DUCKDB_PATH=$(DBT_DB) $(CURDIR)/$(DBT_VENV)/bin/dbt
# Pipeline interpreter: .venv (created by `make pipeline-setup`), the author's local venv, or python3
PYTHON ?= $(firstword $(wildcard .venv/bin/python stackfuel/bin/python) python3)
LOCAL_API_PORT ?= 8000

dbt-setup:
	python3 -m venv $(DBT_VENV)
	$(DBT_VENV)/bin/pip install -q -r dbt/requirements.txt

dbt-debug:
	$(DBT) debug

# Incremental staging (progress events, survey responses): the first run builds the complete history,
# later runs only read the newest curated snapshot day(s). Everything else is rebuilt as tables.
dbt-build:
	$(DBT) build

# Rebuilds the incremental models from the complete curated history. Schedule it weekly (the pipeline
# also re-reads every source completely every 7 days) and after any change of an incremental model.
dbt-full-refresh:
	$(DBT) build --full-refresh

dbt-test:
	$(DBT) test

# Executes dbt/analyses/*.sql against the reporting models and writes dbt/reports/*.csv + results.md
dbt-results:
	$(DBT) compile --select path:analyses
	$(CURDIR)/$(DBT_VENV)/bin/python dbt/scripts/export_results.py --database $(DBT_DB)

# Builds twice and proves that the second build neither duplicates nor changes any row
dbt-rerun-check:
	$(CURDIR)/$(DBT_VENV)/bin/python dbt/scripts/rerun_check.py --database $(DBT_DB) --dbt-dir dbt --dbt $(CURDIR)/$(DBT_VENV)/bin/dbt

# Scratch copies of the database: injects a new snapshot day and proves incremental == full refresh
dbt-incremental-check:
	$(CURDIR)/$(DBT_VENV)/bin/python dbt/scripts/incremental_check.py --database $(DBT_DB) --dbt-dir dbt --dbt $(CURDIR)/$(DBT_VENV)/bin/dbt

# Generate the static dbt documentation site under dbt/target/
dbt-docs:
	$(DBT) docs generate

# Generate docs if needed, then serve them locally
DBT_DOCS_PORT ?= 9001

dbt-docs-serve: dbt-docs
	$(DBT) docs serve --port $(DBT_DOCS_PORT)

# Backward-compatible alias
docs: dbt-docs

# --- Fresh machine: one-time setup and a complete local demo ---------------------------------
pipeline-setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt

# Three small venvs: pipeline (.venv), dbt (.venv-dbt), dashboard (.venv-dashboard)
setup: pipeline-setup dbt-setup dashboard-setup

# Starts the bundled source API, runs the pipeline, `dbt build` and saves the results of the five
# questions to dbt/reports/. The API is stopped afterwards, also when the pipeline fails.
demo:
	@if lsof -nP -iTCP:$(LOCAL_API_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Port $(LOCAL_API_PORT) is already in use. An old API server there would feed the pipeline stale data."; \
		echo "Stop it, or run: make demo LOCAL_API_PORT=<free port>"; \
		exit 1; \
	fi
	@echo "Starting the local source API on port $(LOCAL_API_PORT) ..."
	@$(PYTHON) api/server.py --port $(LOCAL_API_PORT) > .api.log 2>&1 & echo $$! > .api.pid
	@sleep 2
	@API_BASE_URL=http://127.0.0.1:$(LOCAL_API_PORT) $(PYTHON) pipeline.py; status=$$?; \
		kill $$(cat .api.pid) 2>/dev/null; rm -f .api.pid; \
		if [ $$status -ne 0 ]; then \
			echo "Pipeline exited with code $$status (1 = failed, 2 = partial success): dbt build and reports are skipped."; \
			exit $$status; \
		fi; \
		$(MAKE) dbt-build dbt-results

# Local, non-docker pipeline run against an already running API (API_BASE_URL, default :8000)
pipeline-local:
	$(PYTHON) pipeline.py

# Flush the stackfuel database on demand: deletes the DuckDB file (raw, curated, meta and any dbt
# schemas in it) and its write-ahead log. The next pipeline run recreates everything from the sources.
# Make sure no other process (pipeline, dbt, dashboard, DuckDB shell) has the file open.
db-flush:
	@rm -f "$(DBT_DB)" "$(DBT_DB).wal"
	@echo "Flushed $(DBT_DB)"

pytest:
	$(PYTHON) -m pytest -q

# Python tests plus all dbt tests
test: pytest dbt-test

# --- Streamlit dashboard (reads the dbt `reporting` models, read-only) --------------------
DASHBOARD_VENV ?= .venv-dashboard
DASHBOARD_PORT ?= 8501

dashboard-setup:
	python3 -m venv $(DASHBOARD_VENV)
	$(DASHBOARD_VENV)/bin/pip install -q -r requirements-dashboard.txt

# Needs `make dbt-build` first. http://localhost:$(DASHBOARD_PORT)
dashboard:
	STACKFUEL_DUCKDB_PATH=$(DBT_DB) $(DASHBOARD_VENV)/bin/streamlit run dashboard.py --server.port $(DASHBOARD_PORT)

# Full reproducible sequence once the API is up: pipeline -> dbt build -> saved results
all: pipeline-local dbt-build dbt-results

build: build-api build-pipeline

build-api:
	docker build -f api/Dockerfile -t $(API_IMAGE) api

build-pipeline:
	docker build -f Dockerfile -t $(PIPELINE_IMAGE) .

api-start: build-api api-up

api-up:
	@docker network inspect $(API_NETWORK) >/dev/null 2>&1 || docker network create $(API_NETWORK)
	@docker rm -f $(API_CONTAINER) >/dev/null 2>&1 || true
	docker run -d --rm --name $(API_CONTAINER) --network $(API_NETWORK) -p $(API_PORT):8000 $(API_IMAGE)

pipeline-run: build-pipeline pipeline-run-container

pipeline-run-container:
	docker run --rm --network $(API_NETWORK) \
		-e API_BASE_URL=http://$(API_CONTAINER):8000 \
		$(if $(SNAPSHOT_DATE),-e SNAPSHOT_DATE=$(SNAPSHOT_DATE)) \
		$(if $(LAYER),-e LAYER=$(LAYER)) \
		$(if $(FULL_REFRESH),-e FULL_REFRESH=$(FULL_REFRESH)) \
		$(if $(REBUILD_CURATED),-e REBUILD_CURATED=$(REBUILD_CURATED)) \
		-v "$(CURDIR)/data:/app/data:ro" \
		-v "$(CURDIR)/db:/app/db" \
		$(PIPELINE_IMAGE)

run: build
	@set -e; \
		$(MAKE) api-up; \
		trap '$(MAKE) api-stop' 0 INT TERM; \
		$(MAKE) pipeline-run-container

api-stop:
	@docker rm -f $(API_CONTAINER) >/dev/null 2>&1 || true