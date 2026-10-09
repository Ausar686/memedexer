FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.13.15-slim-trixie AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_DEV=1
WORKDIR /app
# Dependencies first, in their own layer, so code changes don't reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM python:3.13.15-slim-trixie
RUN groupadd --system --gid 10001 memedexer \
    && useradd --system --uid 10001 --gid memedexer --no-create-home memedexer \
    && install -d -o memedexer -g memedexer /data
COPY --from=builder --chown=memedexer:memedexer /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/memedexer.db
WORKDIR /data
VOLUME /data
USER memedexer
CMD ["python", "-m", "memedexer"]
