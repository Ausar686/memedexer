# Tests — Agent Instructions

> Scope: `tests/`. Inherits all repo-wide rules from the root
> [AGENTS.md](../AGENTS.md); this file adds testing-specific conventions.

## Harness
- Framework: `pytest` with `pytest-asyncio` (`asyncio_mode = "auto"`), so
  `async def test_*` functions run without markers.
- The package is installed editable into `.venv`; import it as `memedexer`,
  never by manipulating `sys.path`.
- Run: `coverage run -m pytest` (with coverage) or `pytest -q` (fast). Subset:
  `pytest tests/test_smoke.py -q`.
- Tests marked `live` (in [live/](live)) call real provider APIs and are
  deselected by default; run them with `pytest -m live` and keys in `.env`.

## Writing tests
- Add or update a test for every behavior change.
- Assert the stated behavior, not merely that code runs; avoid weak
  assertions like `assert result` when a specific value is expected.
- Cover negative cases alongside positive ones.
- Tests must be order-independent and parallel-safe: use `tmp_path` and
  `monkeypatch`; never hit the real Telegram API, captioning models or network.
- Name tests for the behavior under test; keep them small and focused.
