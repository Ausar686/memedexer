# captioning

Turns one image, or a video's sampled frames, into a structured caption
through a third-party model API.

## Contract
- Input: a sequence of `Image(data, media_type)` with `media_type` in
  `SUPPORTED_MEDIA_TYPES` (jpeg/png/gif/webp): one for a picture, several
  frames in playback order for a video, all sent in one request. Choosing
  the Telegram size, sampling frames and converting formats happen upstream.
- Output: `CaptionResult` (`text`, `description`, `tags`, `languages`, `kind`,
  `usage`), already normalized by `base.parse_payload`:
  - `tags` are Telegram hashtags: `#` prefix, word characters only, words
    joined by `_`, not all digits, deduplicated ignoring case, at most
    `MAX_TAGS`.
  - `languages` are lowercase and deduplicated.
- Failures raise `CaptionError(retryable=...)`; `CaptionRefused` is a
  non-retryable subclass. Both carry `usage` when the provider billed the
  call, so budgets count it.
  - Retryable: connection errors and HTTP 408/409/429/5xx (after the SDK's own
    retries).
  - Permanent: other HTTP errors, refusals, truncated output, and replies that
    don't match the schema.

## Pieces
| File | Role |
| --- | --- |
| [base.py](base.py) | Types, the `CaptionProvider` protocol, the JSON schema (`CaptionPayload`), normalization |
| [prompt.py](prompt.py) | System prompt and the per-call language instruction |
| [catalog.py](catalog.py) | Model allowlist with prices and reasoning effort; `cost_microusd` |
| [registry.py](registry.py) | Builds a provider per configured API key; fails fast on a default model that isn't allowlisted |
| [providers/](providers) | `AnthropicProvider` (Messages API) and `OpenAIProvider` (Chat Completions) |

## Behavior worth knowing
- Both adapters request schema-constrained JSON (`output_config.format` /
  `response_format` with `strict`), so the prompt describes field meaning,
  not format.
- Reasoning effort comes from the catalog (`low` by default) to keep OCR
  cheap. Claude's adaptive thinking is left on, since it can't be disabled on
  every current model.
- The OpenAI adapter uses Chat Completions rather than the Responses API so a
  future local OpenAI-compatible server can reuse it.
- Description language: the chat's setting, or the main language of the
  text on it (English if there's none).
- Videos: the system prompt asks for text that stays on screen to be written
  once and changing text in order of appearance, and for one description of
  the whole video; the user instruction states the frame count.

## Costs
Catalog prices are USD per million tokens, which equals micro-USD per token,
so `cost_microusd` is a plain multiply, rounded up. Update prices from the
providers' pricing pages when adding or changing a model.
