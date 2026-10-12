# memedexer

Meme indexer for Telegram chats: it captions image-based memes (the text on the
image plus a short description) so they can later be found by searching for
that text.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/memedexer/` | Application package |
| `tests/` | pytest suite |
| `.docker/`, `docker-compose.yaml`, `.dockerignore` | Container build and local orchestration |
| `.claude/` | Agent settings and skills (`.agents/skills`, `.cursor/skills` link here) |
| `AGENTS.md` (+ nested) | Agent instructions; each `CLAUDE.md` imports its sibling `AGENTS.md` |
| `.env.example` | Environment variable template |

## Deployment

```bash
cp .env.example .env   # bot token, owner id, provider key
docker compose up -d --build
```

Bot setup in BotFather, chat approval and operations are covered in
[docs/deployment.md](docs/deployment.md).

## Development

The project uses [`uv`](https://docs.astral.sh/uv/) and Python 3.13.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
cp .env.example .env
```

Fill in `.env` (at least `TELEGRAM_BOT_TOKEN`, `OWNER_USER_ID` and a provider
key), turn the bot's privacy mode off in BotFather, send `/start` to the bot
from the owner's account, then start it:

```bash
uv run python -m memedexer
```

Add the bot to a group, approve the chat from the owner's DM, then send
`/captions on` in each topic that should be captioned. The full command list
is in [src/memedexer/telegram/README.md](src/memedexer/telegram/README.md).

Run any command in the environment with `uv run <cmd>` or after
`source .venv/bin/activate`.

```bash
uv run pytest -q
uv run coverage run -m pytest && uv run coverage report
uv run ruff check .
uv run isort .
uv run bandit -c pyproject.toml -r src --severity-level medium
uv run semgrep scan --config .semgrep.yml --error src
uv run pre-commit install
```

After changing dependencies: `uv lock && uv sync`.

| Tool | Purpose | Config |
| --- | --- | --- |
| `ruff` | Linting | `[tool.ruff]` in `pyproject.toml` |
| `isort` | Import sorting | `[tool.isort]` |
| `pytest`, `pytest-asyncio` | Tests | `[tool.pytest.ini_options]` |
| `coverage` | Test coverage | `[tool.coverage.*]` |
| `bandit` | Security scanning | `[tool.bandit]` |
| `semgrep` | Pattern-based scanning | `.semgrep.yml` |
| `pre-commit` | Git hooks | `.pre-commit-config.yaml` |

CI (`.github/workflows/ci.yml`) runs lint, tests and security scans on pull
requests and pushes to `main`.

## Contributing

Branches and commits follow Conventional Commits types (`feat`, `fix`,
`chore`, `docs`, …): branch `<type>/<slug>`, commit `<type>(<scope>): <summary>`.
See [AGENTS.md](AGENTS.md) for the full conventions.

## License

[MIT](LICENSE)
