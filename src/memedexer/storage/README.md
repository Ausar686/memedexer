# storage

SQLAlchemy 2 async persistence on SQLite (aiosqlite). Schema in
[models.py](models.py), query helpers in [repo.py](repo.py), engine and
migrations in [db.py](db.py).

## Tables
| Table | Key | Holds |
| --- | --- | --- |
| `chats` | Telegram chat id | title, forum flag, approval `status`, who added the bot, chat-level `provider`/`model`/`description_language` overrides |
| `topics` | (`chat_id`, `thread_id`) | `captioning_enabled` and topic-level `provider`/`model` overrides |
| `media` | `file_unique_id` | latest `file_id` and dimensions; images themselves are never stored |
| `captions` | id | one row per captioning run: provider, model, `text`, `description`, `tags`, `languages`, `kind` |
| `jobs` | id | one row per captioning request: source message, `trigger`, `status`, attempts, error, resulting caption, posted `reply_message_id`, token usage and `cost_microusd` |

## Invariants
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
- A crashed process may leave jobs `running`; `requeue_interrupted_jobs` puts
  them back to `pending` at startup.
- Helpers flush but never commit; the caller owns the transaction.

## Migrations
Alembic scripts live in [migrations/](migrations). The app applies them at
startup with `db.upgrade(engine)`. After changing `models.py`, generate a
revision with the next sequential id from the repo root and review it:

```bash
uv run alembic revision --autogenerate --rev-id <NNNN> -m "<change>"
```

The CLI reads `DATABASE_URL` from the environment or `.env`.
`tests/storage/test_migrations.py` fails if the models and migrations drift
apart.
