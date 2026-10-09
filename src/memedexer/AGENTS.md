# memedexer package — Agent Instructions

> Scope: `src/memedexer/`. Inherits all repo-wide rules from the root
> [AGENTS.md](../../AGENTS.md); this file adds package-specific conventions.

## Layout
- One subpackage per domain (e.g. Telegram ingestion, captioning, index/search,
  storage). Each subpackage owns a `README.md` describing its contract; read it
  before editing and update it when the contract changes.
- Keep external boundaries (Telegram client, captioning/OCR backends, database)
  behind small interfaces so the core logic is testable without them.

## Conventions
- Settings are read from the environment once, at the edge, and passed in —
  no module-level reads of `os.environ` deep in the code.
- Async code: never block the event loop with CPU-bound or synchronous I/O
  work; offload it explicitly.
- New runtime dependencies go in `[project].dependencies` pinned with `==`.
