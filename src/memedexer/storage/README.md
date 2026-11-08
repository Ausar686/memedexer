# storage

SQLAlchemy 2 async persistence on SQLite (aiosqlite). Schema in
[models.py](models.py), query helpers in [repo.py](repo.py), engine and
migrations in [db.py](db.py).

## Tables
| Table | Key | Holds |
| --- | --- | --- |
| `chats` | Telegram chat id | title, forum flag, approval `status`, who added the bot, chat-level `provider`/`model`/`description_language` overrides |
| `topics` | (`chat_id`, `thread_id`) | `captioning_enabled` and topic-level `provider`/`model` overrides |
| `media` | `file_unique_id` | latest `file_id`, `media_type` (`photo`, `image_document`, `video`, `video_document`, `animation`), `mime_type`, dimensions (unknown for documents), `duration`, Telegram's `thumbnail_file_id`, and `reply_file_id` (what the reply's send method accepts: set at ingestion for photos, videos and GIFs, and after the first reply for documents); the files themselves are never stored |
| `captions` | id | one row per captioning run: provider, model, `text`, `description`, `tags`, `languages`, `kind` |
| `jobs` | id | one row per captioning request: source message (and whether its media is a spoiler, `has_spoiler`), `trigger`, `status`, attempts, error, resulting caption, whether it was captioned from a too-big video's thumbnail (`thumbnail_only`), posted `reply_message_id` or `delivery_error`, token usage, `cost_microusd`, and `available_at` (when a retried job may be claimed again) |

## Invariants
- Chat `status`: `pending` (waiting for the owner), `approved`, `rejected`
  (owner said no), `revoked` (owner withdrew approval, or the bot was
  removed). Only `approved` chats are captioned.
- `thread_id = NO_TOPIC` (0) means "no topic": a chat without topics, or a
  forum's General topic.
- `None` in an override column means "inherit from the next level".
- Spend is the sum of `jobs.cost_microusd` by `finished_at`, so refused or
  failed calls that were billed still count. Money is stored as integer
  micro-dollars to stay exact on SQLite.
- Datetimes are stored as naive UTC and returned as aware UTC
  (`UTCDateTime`); binding a naive datetime raises.
- `file_id` changes over time for the same file; `upsert_media` keeps the
  latest one.
- A crashed process may leave jobs `running`; at startup
  `fail_exhausted_interrupted_jobs` fails those on their last attempt, then
  `requeue_interrupted_jobs` puts the rest back to `pending`.
- Helpers flush but never commit; the caller owns the transaction.

## Migrations
Alembic scripts live in [migrations/](migrations). The app applies them at
startup with `db.upgrade(engine)`. After changing `models.py`, generate a
revision with the next sequential id from the repo root and review it:

```bash
uv run alembic revision --autogenerate --rev-id <NNNN> -m "<change>"
```

The CLI reads `DATABASE_URL` from the environment or `.env`.

SQLite can't alter columns in place, so autogenerate emits batch operations
that rebuild tables. Foreign-key enforcement is suspended while migrations
run and restored afterwards, failing if any reference dangles
(`db.suspend_foreign_keys` / `db.restore_foreign_keys`). When a migration adds
a non-null column, add it as nullable, backfill it, then tighten it, since
existing rows have no value (see `0002`).
`tests/storage/test_migrations.py` fails if the models and migrations drift
apart.
