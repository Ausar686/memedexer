# captioning — Agent Instructions

> Scope: `src/memedexer/captioning/`. Inherits the root and package
> `AGENTS.md`; the contract is in [README.md](README.md).

## Adding or changing a model
- Add it to `catalog.MODELS` with prices from the provider's official pricing
  page. Confirm it accepts image input and structured outputs before adding it.
- Set `effort=None` if the model rejects a reasoning-effort parameter.

## Adding a provider
- Add a `config.Provider` member, an API-key setting, a branch in
  `registry.build_providers`, and an adapter under `providers/` using the
  provider's official SDK.
- Map errors onto `CaptionError` with `base.is_retryable_status`, keep `usage`
  on every failure that returned a response, and raise `CaptionRefused` for
  refusals. Never let an SDK exception escape the adapter.
- Read SDK signatures from the installed package; don't rely on memory, since
  these SDKs change often.

## Testing
- Unit tests fake the SDK client (see `tests/captioning/fakes.py`) and build
  real SDK response objects with `model_validate`, so a renamed field fails
  loudly.
- `tests/live/` calls the real APIs on `tests/fixtures/meme.png`; run it with
  `uv run pytest -m live` after prompt or adapter changes.
