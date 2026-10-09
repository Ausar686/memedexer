# memedexer — Agent Instructions

A Telegram meme indexer: it watches chats, writes text captions for
image-based memes (the text on the image plus a short description), and
indexes them so a meme can later be found by searching for that text.

## Project overview
- Package: [src/memedexer/](src/memedexer). **See [src/memedexer/AGENTS.md](src/memedexer/AGENTS.md).**
- Runtime: Python 3.13 (`requires-python` in [pyproject.toml](pyproject.toml)).
- Product decisions (flow, topics, approval, budgets, reply format): **see
  [docs/architecture.md](docs/architecture.md)** — read it before changing
  behavior and update it when a decision changes.

## Environment & setup
- Dependency manager: `uv`; lock file `uv.lock`. The project is a src-layout
  package installed into the local `.venv` in editable mode.
- First-time setup:
  1. Install `uv` (once per machine): `curl -LsSf https://astral.sh/uv/install.sh | sh`
  2. Create `.venv` with runtime + dev deps: `uv sync` (the `dev` group is a
     default group).
  3. Run commands with the venv activated (`source .venv/bin/activate`) or
     prefix each with `uv run`.
- After **any** dependency change: `uv lock`, then `uv sync`; verify with
  `uv lock --check`.
- Secrets and runtime settings come from `.env` (template:
  [.env.example](.env.example)). Never read or commit a real `.env`.

## Build / test / lint commands
> Run from the repo root inside `.venv` (or prefix with `uv run`). Tests + lint
> must pass before a task is done.
- Tests with coverage: `coverage run -m pytest` then `coverage report`
- Quick tests: `pytest -q`
- Lint: `ruff check .`
- Import sort: `isort .`
- Security: `bandit -c pyproject.toml -r src --severity-level medium` and
  `semgrep scan --config .semgrep.yml --error src`
- Git hooks: `pre-commit install` once; `pre-commit run --all-files` on demand.

## Code style & conventions
- Python 3.13 idioms: builtin generics (`list`, `dict`, `tuple`), PEP 604
  unions (`X | None`), `import typing as t` instead of `from typing import ...`.
- Keep logic modular and unit-testable: small pure functions and explicit
  boundaries around I/O (Telegram, captioning models, storage).
- **DRY — one source of truth per fact.** A contract (schema, config
  semantics, file layout) lives in exactly one place — a code constant or the
  owning module's `README.md` — and everything else links to it.
- Give every function an explicit `return` where a value is expected.
- Line length, naming and import order are owned by `ruff` + `isort` (config in
  `pyproject.toml`) — make them pass instead of hand-styling.
- Don't over-engineer: no speculative config, fallbacks or validation for
  states that can't occur. Validate only at real boundaries (user input, I/O).

### Comments & docstrings
Write code that needs as little prose as possible; add prose only where the
code can't speak.
1. **Self-explanatory code (default).** Clear names, type hints and small
   functions are the primary documentation.
2. **A one-line `#` comment** — only for the non-obvious: a rationale,
   trade-off, invariant, API quirk or deliberate deviation. Explain *why*,
   never restate *what*.
3. **A short docstring** — one-line module docstring stating the file's role;
   one-line contract on public or non-trivial functions/classes.

Never write: comments that paraphrase code, docstrings that echo the name,
param prose that repeats annotations, commented-out code, change-log
narration, or dated TODOs. Match the comment density of neighboring code.

## Architecture map
- [src/memedexer/](src/memedexer) — the application package.
  - [config.py](src/memedexer/config.py) — `Settings` from env/`.env`; the
    single source of env variable names and defaults (template:
    [.env.example](.env.example)).
  - [storage/](src/memedexer/storage) — SQLite schema, query helpers and
    Alembic migrations (`README.md`).
  - [captioning/](src/memedexer/captioning) — provider adapters, prompt,
    model allowlist and prices. **See
    [captioning/AGENTS.md](src/memedexer/captioning/AGENTS.md).**
- [tests/](tests) — pytest suite. **See [tests/AGENTS.md](tests/AGENTS.md).**
- [.docker/](.docker) + [docker-compose.yaml](docker-compose.yaml) — container build and
  local orchestration.
- [.claude/](.claude) — agent tooling: settings and skills. `.agents/skills`
  and `.cursor/skills` are symlinks to `.claude/skills` so Codex and Cursor
  share the same skills.
- Read a module's `README.md` (when present) before editing that module.

## Testing instructions
- Add or update tests for every behavior change; assert the stated behavior,
  not just "it runs".
- Fake only external boundaries (Telegram API, captioning/OCR models, network).
- All tests must be green before a task is done.

## Security & safety
- No `eval`/`exec`, no `subprocess(..., shell=True)`, no unsafe YAML or
  untrusted deserialization (enforced by `.semgrep.yml`).
- Never hardcode, log or commit secrets; load them from the environment.
- New dependencies must be justified, pinned with `==` and locked in `uv.lock`.

## Git conventions
- No issue tracker: branches and commits use Conventional Commits types —
  `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `perf`, `build`.
- Branch: `<type>/<short-kebab-slug>` (e.g. `feat/ocr-captions`,
  `fix/album-dedup`). Never commit directly to `main`.
- Commit: `<type>(<optional scope>): <imperative summary>` (e.g.
  `feat(captioning): add OCR pass for image memes`). One logical change per
  commit.
- PR title follows the commit format; write the body with the
  `/pr-description` skill (concise bulleted changelog).
- Commit messages and PR bodies are plain changelogs — no co-authorship,
  "generated by" or tool-attribution trailers.
- Keep changes minimal and scoped; avoid drive-by refactors.

## Gotchas
- Don't paste pinned versions into docs — reference `pyproject.toml` /
  `uv.lock`.
