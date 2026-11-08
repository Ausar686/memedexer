# Architecture

Product-level decisions that span modules. Module contracts live in each
module's `README.md`; this file links to them rather than restating them.

## Goal
Caption image and video memes posted in Telegram chats so they can later be
found by the text on them. Search itself is out of scope for now; captions are posted
back into the chat (searchable with Telegram's own search) and stored for a
future search feature.

## Flow
```
update (photo / video / GIF / image or video document) in an approved chat
  → topic has captioning enabled?           no → ignore
  → enqueue job (persisted, survives restarts)
  → worker: resolve provider/model (topic → chat → env default)
  → budget check (chat's daily/weekly/monthly spend)   over → budget_exceeded
  → dedup by file_unique_id: reuse latest caption, or download + caption via provider
    (a video: frames sampled in a separate process, all sent in one request)
  → post silent reply: the same media + formatted caption
```

## Decisions
- **Telegram:** Bot API via aiogram 3. The bot only sees messages sent after it
  joins (no backfill). It needs privacy mode disabled or admin rights to see
  all messages.
- **Media:** photos, image documents (png/jpg/webp), videos, GIFs (Telegram's
  `animation`) and video documents (`video/*`). Stickers and round video
  notes are ignored. Each item of an album is handled on its own.
- **Videos:** captioned from frames at 10%, 50% and 90% of the duration
  (skipping fade-ins and end cards), sent as a list in one request so the
  model writes text that stays on screen once and describes the video as a
  whole. Near-identical frames are dropped, so a still picture with music
  costs one frame. Spoken words aren't captioned. Frames are decoded in a
  separate, short-lived process per video with no secrets in its environment
  and memory, CPU and time limits: a malformed file fails one job, not the
  bot. Videos over the Bot API's 20 MB download limit are captioned from
  Telegram's small thumbnail instead; the job is marked `thumbnail_only` and
  the owner gets a DM.
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
  The owner must `/start` the bot once so it can DM them. Removing the bot
  revokes the chat, so re-adding it asks again (at most one DM per chat per
  day); a rejected chat can't ask again, and only the owner can approve it
  later from `/chats`. Commands in a chat that isn't approved get one
  "waiting for approval" reply per hour. A group upgraded to a supergroup
  keeps its settings.
- **Providers:** Anthropic and OpenAI via their official SDKs. A future local
  service plugs in as an OpenAI-compatible endpoint.
- **Caption schema:** verbatim on-image `text` (original languages and line
  breaks), a one-sentence `description` (in the chat's configured language,
  defaulting to the meme's language), 0–4 `tags` (Telegram hashtags),
  `languages`, `kind` (meme / screenshot / photo / other). Every image is
  captioned; `kind` is stored for later filtering.
- **Reply:** a silent reply to the original message containing the same media
  and the caption: a photo (also for image documents), a video (also for
  video documents; one too big to re-upload goes back as a document) or a
  GIF. Telegram limits a
  photo caption to 1024 characters: tags are always kept, the description is
  cut to its first sentence, then the OCR text is truncated with `…`, then the
  description. The full caption is always stored in the database.
- **Spoilers:** a reply to media sent as a spoiler is a spoiler too: the
  media is blurred and the OCR text, description and tags are hidden, since
  they give it away just as much.
- **Edits:** when an edit replaces the media, the new media is captioned and a
  new reply posted; the previous reply stays. Edits that keep the file are
  ignored, except one that adds a spoiler: the replies already posted are
  blurred. Removing a spoiler leaves the replies hidden.
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
- **Shutdown:** on stop, polling ends, the worker finishes its current job
  (up to 15 s), queued replies are posted (up to 10 s), then the database
  closes; together this fits compose's 30 s stop grace period. Anything cut
  off is resumed on the next start, unless a job was interrupted on its last
  attempt: it fails instead, so a file that crashes the process can't take
  the bot down on every restart.
- **Failures:** silent in the chat; refusals, exhausted retries and replies
  that couldn't be posted are reported to the owner by DM (identical alerts at
  most hourly). Replies interrupted by a restart are retried if they finished
  within the last 24 h.
- **Storage:** SQLite (WAL) in a Docker volume via SQLAlchemy async; images are
  not stored, only Telegram `file_id`s.
