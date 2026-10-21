# 1. One repository with separate backend and frontend

Date: 2026-10-21. Status: accepted.

## Context

The product is a FastAPI JSON API and a React app that are always released together, plus database migrations and deployment files.

## Decision

Keep one repository with `backend/` (its own uv project and lockfile) and `frontend/` (its own npm project and lockfile). Each part builds its own Docker image. CI runs one job per part. Build output (`frontend/dist`) is not committed.

## Consequences

- One pull request can change the API and the screen that uses it.
- Each part keeps its own tooling, so it could move to its own repository later without rewrites.
- Developers run commands from the part's folder, or use the root `Makefile`.
