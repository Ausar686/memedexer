# telegram

aiogram 3 integration: handlers that turn group images into jobs, and the
Bot API downloader the worker uses.

## Ingestion ([handlers.py](handlers.py))
- Listens to `message` and `edited_message` updates with a photo or document
  in groups and supergroups (private chats are ignored).
- A job is enqueued only when the chat is `approved` **and** the message's
  topic has `captioning_enabled`. Everything else is ignored silently.
- Topic: `message_thread_id` when `is_topic_message` is set, otherwise
  `NO_TOPIC`. Reply threads in ordinary groups also carry a thread id, so the
  flag matters.
- Edits enqueue an `edit` job only when the image changed (a different
  `file_unique_id` than the message's latest job).
- Handlers receive `sessionmaker` and `worker` from the dispatcher context
  (`app.build_dispatcher`).

## Which file ([media.py](media.py))
- Photos: the largest size whose longer side fits `pipeline.images.MAX_SIDE`,
  else the smallest. Dedup keys on that size's `file_unique_id`.
- Documents: only `image/jpeg`, `image/png`, `image/webp`, up to the Bot
  API's 20 MB download limit. Their dimensions are unknown until downloaded.

## Bot setup requirements
- Turn privacy mode off in BotFather (or make the bot an admin). Otherwise
  it never sees ordinary group messages.
- The bot can't see messages sent before it joined, nor messages from other
  bots.

## Downloads ([downloader.py](downloader.py))
`TelegramBadRequest` (e.g. file too big or gone) is permanent. Other API,
network and timeout errors are retryable.
