# syntax=docker/dockerfile:1
# Superaide backend API: FastAPI on port 8000.
#   docker build -t superaide-api backend/
# Migrations run separately (see the "migrate" service in compose.yaml):
#   docker run --rm superaide-api alembic upgrade head

ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim AS runtime
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /home/app/.config/superaide /home/app/.cache/superaide \
    && chown -R app:app /home/app/.config /home/app/.cache
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
# Inside a container the API listens on all interfaces; publish it to 127.0.0.1
# on the host (compose.yaml does) or put it behind a TLS-terminating proxy.
CMD ["uvicorn", "superaide.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
