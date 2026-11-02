# Nora — essential ops. No vendor names, no amounts: safe to keep public.
# Local data/config it drives (providers/, .env, importer/config, dumps) stays gitignored.

SHELL := /bin/bash
FIREFLY_URL ?= http://localhost:8066
DB := docker exec firefly-db psql -U firefly -d firefly

.DEFAULT_GOAL := help

.PHONY: help up down restart ps logs health net-worth verify \
        sync import db-preflight db-backup db-restore db-shell \
        api ui test

help: ## List targets
	@grep -hE '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

## --- stack ---
up: ## Start the stack (detached)
	docker compose up -d
down: ## Stop the stack (keeps volumes/data)
	docker compose down
restart: down up ## Restart the stack
ps: ## Container status
	docker compose ps
logs: ## Tail all logs (S=firefly-iii to scope)
	docker compose logs -f $(S)

## --- health / verify ---
health: ## Assert all containers healthy + API reachable
	@docker compose ps --format '{{.Name}} {{.Status}}' | grep -qi 'firefly-db.*healthy'       && echo "db   ok" || { echo "db   DOWN"; exit 1; }
	@docker compose ps --format '{{.Name}} {{.Status}}' | grep -qi 'firefly-iii.*healthy'      && echo "core ok" || { echo "core DOWN"; exit 1; }
	@docker compose ps --format '{{.Name}} {{.Status}}' | grep -qi 'importer.*healthy'         && echo "imp  ok" || { echo "imp  DOWN"; exit 1; }
	@code=$$(curl -s -o /dev/null -w '%{http_code}' $(FIREFLY_URL)/login); [ "$$code" = 200 ] && echo "http ok" || { echo "http $$code"; exit 1; }

net-worth: ## Print current net worth (reads token from .env)
	@tok=$$(grep '^FIREFLY_III_ACCESS_TOKEN=' .env | cut -d= -f2); \
	end=$$(date +%F); start=$$(date -d "$$end -6 days" +%F 2>/dev/null || date +%F); \
	curl -s -H "Authorization: Bearer $$tok" -H "Accept: application/json" \
	  "$(FIREFLY_URL)/api/v1/summary/basic?start=$$start&end=$$end" \
	  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Net worth:', d.get('net-worth-in-EUR',{}).get('value_parsed','?'))" \
	  2>/dev/null || echo "no net-worth (check token / API)"

verify: health net-worth ## End-to-end smoke: stack healthy + net worth returns

## --- import ---
sync: ## Convert + import every provider with files waiting (provider-agnostic)
	bash scripts/auto-sync.sh
import: ## Import one standard-format CSV:  make import CSV=path/to.csv
	@test -n "$(CSV)" || { echo "usage: make import CSV=<path>"; exit 2; }
	bash scripts/import-csv-firefly.sh "$(CSV)"

## --- database ---
db-preflight: ## Tag canary — proves import won't 500 on schema drift
	@$(DB) -c 'BEGIN; INSERT INTO tags (user_id,user_group_id,tag,"tagMode",created_at,updated_at) VALUES (1,1,'"'"'__canary__'"'"','"'"'nothing'"'"',now(),now()); ROLLBACK;' \
	  && echo "preflight ok" || { echo "SCHEMA DRIFT — see docs-local/db-import-runbook.md"; exit 1; }
db-backup: ## Dump the DB to data/backup/ (timestamped)
	@mkdir -p data/backup; f=data/backup/firefly_$$(date +%Y%m%d-%H%M%S).sql.gz; \
	docker exec firefly-db pg_dump -U firefly firefly | gzip > "$$f" && echo "wrote $$f"
db-restore: ## Restore a dump (DROPS current data):  make db-restore DUMP=path.sql.gz
	@test -n "$(DUMP)" || { echo "usage: make db-restore DUMP=<file.sql.gz>"; exit 2; }
	docker compose stop firefly-iii data-importer
	$(DB) -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='firefly' AND pid<>pg_backend_pid();" >/dev/null
	$(DB) -d postgres -c "DROP DATABASE firefly;" && $(DB) -d postgres -c "CREATE DATABASE firefly OWNER firefly;"
	zcat "$(DUMP)" | docker exec -i firefly-db psql -U firefly -d firefly >/dev/null
	docker compose up -d
	@echo "restored — run 'make db-preflight' before importing"
db-shell: ## psql shell into the DB
	$(DB)

## --- dashboard (dev) ---
# Jeton local par défaut, partagé par l'API et le proxy Vite.
dev api ui: export NORA_API_TOKEN ?= dev
dev: ## Run api (:8068) + ui (:5173) in background; stop with 'make dev-stop'
	@mkdir -p .run
	@setsid bash -c 'echo $$$$ > .run/api.pid; cd web/api && exec ./.venv/bin/uvicorn main:app --reload --port 8068' > .run/api.log 2>&1 &
	@setsid bash -c 'echo $$$$ > .run/ui.pid; cd web/ui && exec npm run dev' > .run/ui.log 2>&1 &
	@sleep 1; echo "api :8068 · ui :5173 · logs in .run/ · stop: make dev-stop"
dev-stop: ## Stop both dev servers (kills reloader/vite children too)
	@for p in .run/api.pid .run/ui.pid; do \
	  [ -f $$p ] && kill -TERM -$$(cat $$p) 2>/dev/null; rm -f $$p; done
	@fuser -k 8068/tcp 5173/tcp 2>/dev/null || true; echo "dev stopped"
api: ## Run only the FastAPI backend, foreground (:8068)
	cd web/api && ./.venv/bin/uvicorn main:app --reload --port 8068
ui: ## Run only the Vite frontend, foreground (:5173)
	cd web/ui && npm run dev

## --- tests ---
test-unit: ## Pure-logic self-checks (no docker needed)
	python3 web/api/scores.py
	cd web/api && python3 test_forecast.py
	cd web/api && python3 test_import_status.py
	cd web/api && python3 test_recurrences.py
	cd web/api && python3 test_snapshots.py
	cd web/api && python3 test_categorization.py
	cd web/api && python3 test_amortization.py
	cd web/api && python3 test_drift.py
	cd providers/example && python3 test_convert.py
test: test-unit db-preflight ## Unit self-checks + schema canary
	@echo "all tests ok"
