FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.13.15-alpine3.24 AS builder
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

FROM python:3.13.15-alpine3.24
# Apply Alpine security fixes newer than the base tag, and drop the base image's pip (with its vendored
# urllib3/msgpack): the app runs from its own venv, so pip is only unused attack surface.
RUN apk upgrade --no-cache \
    && python -m pip uninstall --yes --quiet --root-user-action=ignore pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip \
    && addgroup -S -g 10001 memedexer \
    && adduser -S -D -H -u 10001 -G memedexer memedexer \
    && install -d -o memedexer -g memedexer /data
COPY --from=builder --chown=memedexer:memedexer /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/memedexer.db
WORKDIR /data
VOLUME /data
USER memedexer
CMD ["python", "-m", "memedexer"]
