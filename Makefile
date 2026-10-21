# Developer commands. `make` lists them.
# Non-developers: double-click start.command (or `bash start.command`) instead.

.DEFAULT_GOAL := help
.PHONY: help setup db db-down migrate migration api web dev lint fmt typecheck test check build stack stack-down clean

BACKEND = cd backend &&
FRONTEND = cd frontend &&

help: ## List the commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies and the git hooks
	$(BACKEND) uv sync
	$(FRONTEND) npm ci --no-audit --no-fund
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

test: ## Backend tests (needs TEST_DATABASE_URL, see backend/.env.example)
	$(BACKEND) uv run pytest

check: lint typecheck test build ## Everything CI runs

build: ## Production build of the frontend (Next.js standalone server)
	$(FRONTEND) npm run build

stack: ## The whole stack in Docker: app :3000, API :8000
	docker compose --profile app up --build

stack-down: ## Stop the Docker stack
	docker compose --profile app down

clean: ## Remove caches and build output
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache backend/htmlcov backend/.coverage frontend/.next
