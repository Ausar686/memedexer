# Architecture

Product-level decisions that span modules. Module contracts live in each
module's `README.md`; this file links to them rather than restating them.

## Goal
Caption image memes posted in Telegram chats so they can later be found by the
text on the image. Search itself is out of scope for now; captions are posted
back into the chat (searchable with Telegram's own search) and stored for a
future search feature.

## Flow
```
update (photo / image document) in an approved chat
  → topic has captioning enabled?           no → ignore
  → enqueue job (persisted, survives restarts)
  → worker: resolve provider/model (topic → chat → env default)
  → budget check (chat's daily/weekly/monthly spend)   over → budget_exceeded
  → dedup by file_unique_id: reuse latest caption, or download + caption via provider
  → post silent reply: the image + formatted caption
```

## Decisions
- **Telegram:** Bot API via aiogram 3. The bot only sees messages sent after it
  joins (no backfill). It needs privacy mode disabled or admin rights to see
  all messages.
- **Media:** photos and image documents (png/jpg/webp). Stickers, GIFs and
  videos are ignored. Each image of an album is handled on its own.
- **Topics:** captioning is a per-topic flag, off by default, toggled by chat
  admins. Separating meme topics from discussion topics is the chat admins'
  job. A chat without topics, and a forum's General topic, use
  `NO_TOPIC` (see [storage](../src/memedexer/storage/README.md)). A message
  counts as in a topic only when `is_topic_message` is set.
- **Settings:** chat admins can set provider, model and description language
  per topic or per chat. Lookup order: topic → chat → env default
  (`DEFAULT_PROVIDER` / `DEFAULT_MODEL`, Anthropic `claude-haiku-5-5`). Models
  must be on an allowlist; a provider is usable only if its API key is set.
- **Chat approval:** the owner (`OWNER_USER_ID`) gets a DM with Approve/Reject
  when the bot is added to a chat. Unapproved chats are ignored; rejecting
  makes the bot leave. The owner can list and revoke approved chats via DM.
  The owner must `/start` the bot once so it can DM them.
- **Providers:** Anthropic and OpenAI via their official SDKs. A future local
  service plugs in as an OpenAI-compatible endpoint.
- **Caption schema:** verbatim on-image `text` (original languages and line
  breaks), a one-sentence `description` (in the chat's configured language,
  defaulting to the meme's language), 0–4 `tags` (Telegram hashtags),
  `languages`, `kind` (meme / screenshot / photo / other). Every image is
  captioned; `kind` is stored for later filtering.
- **Reply:** a silent reply to the original message containing the image (sent
  as a photo, also for image documents) and the caption. Telegram limits a
  photo caption to 1024 characters: tags are always kept, the description is
  cut to its first sentence, then the OCR text is truncated with `…`, then the
  description. The full caption is always stored in the database.
- **Edits:** when an edit replaces the image, the new image is captioned and a
  new reply posted; the previous reply stays. Edits that keep the image are
  ignored.
- **Rate limits:** replies go through a per-chat send queue, since Telegram
  allows bots about 20 messages per minute per group.
- **Budgets:** per-chat spend limits from `.env` (defaults: $20 daily, $100
  weekly, $200 monthly), in UTC calendar periods (ISO weeks start on Monday).
  Over the limit, jobs are marked `budget_exceeded` and skipped; the owner
  gets one DM per period. Nothing is posted in the chat.
- **Dedup:** an image already captioned anywhere (same `file_unique_id`) reuses
  its latest caption for free; `/recaption` forces a new one.
- **Process health:** if the worker dies, polling stops and the process exits
  with the error, so the container restart policy brings it back.
- **Failures:** silent in the chat; refusals and exhausted retries are reported
  to the owner by DM.
- **Storage:** SQLite (WAL) in a Docker volume via SQLAlchemy async; images are
  not stored, only Telegram `file_id`s.
