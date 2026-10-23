# Developer commands. `make` lists them.
# Non-developers: double-click start.command (or `bash start.command`) instead.

.DEFAULT_GOAL := help
.PHONY: help setup db db-down migrate migration api web dev lint fmt typecheck test e2e e2e-ui eval eval-live eval-gate eval-ltm-live eval-mem0 check check-env build stack stack-down clean

BACKEND = cd backend &&
FRONTEND = cd frontend &&

help: ## List the commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

setup: ## Install backend, frontend and E2E dependencies and the git hooks
	$(BACKEND) uv sync
	$(FRONTEND) npm ci --no-audit --no-fund
	cd e2e && npm ci --no-audit --no-fund && npx playwright install chromium
	uvx pre-commit install

db: ## Start Postgres 17 + pgvector in Docker
	docker compose up -d --wait db

db-down: ## Stop Postgres (data is kept)
	docker compose stop db

migrate: ## Apply database migrations
	$(BACKEND) uv run alembic upgrade head

migration: ## New migration from model changes: make migration m="add contacts"
	@test -n "$(m)" || (echo 'Usage: make migration m="what changed"' && exit 1)
	$(BACKEND) uv run alembic revision --autogenerate -m "$(m)"

api: ## Backend API with auto-reload on http://127.0.0.1:8000
	$(BACKEND) uv run superaide serve --reload

web: ## Frontend (Next.js) dev server on http://localhost:3000
	$(FRONTEND) npm run dev

dev: ## Everything at once (database, API, frontend), like start.command
	bash start.command

lint: ## Lint backend and frontend
	$(BACKEND) uv run ruff check .
	$(BACKEND) uv run ruff format --check .
	$(FRONTEND) npm run lint

fmt: ## Format and auto-fix the backend
	$(BACKEND) uv run ruff check --fix .
	$(BACKEND) uv run ruff format .

typecheck: ## Type-check backend (mypy) and frontend (tsc)
	$(BACKEND) uv run mypy
	$(FRONTEND) npm run typecheck

check-env: ## Every environment variable the app reads is in the .env.example files
	python3 scripts/check_env_example.py

test: ## Backend tests (needs TEST_DATABASE_URL, see backend/.env.example)
	$(BACKEND) uv run pytest

e2e: build ## Browser end-to-end tests on an isolated stack (E2E_SLOT=n for another one)
	cd e2e && npx playwright test

e2e-ui: build ## The same, in Playwright's interactive UI
	cd e2e && npx playwright test --ui

eval: ## Scenario evals, offline (fake model, free): scorecard in backend/evals/results/
	$(BACKEND) uv run python -m superaide.evals run

eval-gate: ## Long-term-memory regression gate, offline and free: fails if a scenario regresses (CI runs it via pytest)
	$(BACKEND) uv run python -m superaide.evals gate

eval-ltm-live: ## Memory scenarios on the real model (~US$0.10, hard cap US$0.25): scorecard in backend/evals/results/
	$(BACKEND) uv run python -m superaide.evals run --live --cap 0.25 --name scorecard-ltm-live --env-file $(or $(env),.env) $(foreach c,ltm_inputs ltm_update ltm_temporal ltm_multisession ltm_abstain ltm_canary ltm_ingest,--category $(c))

eval-mem0: ## OPT-IN, never in CI: Ava's recall questions against open-source Mem0 on the same notes (needs `uv sync --extra eval-mem0`)
	$(BACKEND) uv run --extra eval-mem0 python evals/baselines/mem0_baseline.py

eval-live: ## Scenario evals on the real model, 3 runs each (costs ~US$0.15; hard cap US$1): k=1 for one run
	$(BACKEND) uv run python -m superaide.evals run --live --k $(or $(k),3)

check: lint typecheck check-env test build ## Lint, types, unit/API tests and the build (CI also runs e2e)

build: ## Production build of the frontend (Next.js standalone server)
	$(FRONTEND) npm run build

stack: ## The whole stack in Docker: app :3000, API :8000
	docker compose --profile app up --build

stack-down: ## Stop the Docker stack
	docker compose --profile app down

clean: ## Remove caches and build output
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache backend/htmlcov backend/.coverage frontend/.next
