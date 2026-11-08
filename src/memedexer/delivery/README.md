# delivery

Posts captions back into the chat and keeps the owner informed. `Delivery`
([service.py](service.py)) is the worker's `JobListener`; formatting
([format.py](format.py)) and throttling ([throttle.py](throttle.py)) are pure
helpers.

## Replies
- Every `done` job gets a silent reply to its source message, in the same
  topic. The message holds the same media and the formatted caption, and
  never posts if the original is gone (`allow_sending_without_reply=False`).
  - This covers new posts, edits, `/recaption` and deduplicated reposts.
- Photo, video, GIF: re-sent by `media.reply_file_id` (`sendPhoto`,
  `sendVideo`, `sendAnimation`). Documents can't be re-sent as photos or
  videos by their own id, so they are downloaded and uploaded once (an image
  prepared like for captioning, a video as is); the resulting file id is
  stored so later replies reuse it. A video document over the download limit
  goes back as a document (`sendDocument`).
- Spoilers: a job with `has_spoiler` gets a blurred photo, and every caption
  part is wrapped in `<tg-spoiler>` (the markup doesn't count towards the
  limit below). `hide_replies(chat_id, message_id)` handles a source that
  became a spoiler after it was captioned: it flags the message's jobs and
  edits the replies already posted (`editMessageMedia`). A reply that was
  being sent at that moment is blurred right after it posts. The edit uses
  the input media type matching the reply (photo, video or GIF); a reply
  that went back as a document can't be blurred and is skipped.
- Caption layout (HTML): OCR text in an expandable quote, the description,
  then the hashtags. It must fit Telegram's 1024-character photo caption limit, which
  counts the visible text in UTF-16 units. Tags are always kept; over the
  limit the description is cut to its first sentence, then the OCR text is
  truncated with `…`, then the description. The full caption stays in the
  database.

## Queueing and failures
- One queue and task per chat: replies keep their order, and a chat that hits
  Telegram's ~20 messages/minute group limit (`Throttle`) waits without
  delaying other chats or the captioning workers.
- A job counts as delivered once `reply_message_id` is set, and as abandoned once
  `delivery_error` is. `start()` re-queues jobs with neither that finished in
  the last 24 h, so a restart doesn't lose replies.
- `close(timeout)` lets queued replies finish before stopping the idle queue
  tasks; replies still pending at the deadline are retried by `start()`.
- `RetryAfter` waits the time Telegram asks for. Network/server errors and
  retryable download errors back off (5 s × attempt, up to
  `MAX_SEND_ATTEMPTS`), then give up and alert the owner.
- A bad request (e.g. the original was deleted) or lost access gives up quietly.
- Hiding a reply uses the same retries; when they run out the owner is
  alerted, and a reply that is gone is skipped quietly.

## Owner alerts (DM)
- A `thumbnail_only` job (a video over the download limit): a notice that the
  caption came from the small preview, with a link.
- Captioning `failed` / `refused` jobs and abandoned replies: the error and a
  link to the message (supergroups only). Identical alerts for the same chat
  are sent at most once an hour, so an outage doesn't flood the DM.
- Over budget: one DM per chat and period, sent for the period's first
  `budget_exceeded` job (lowest id, so concurrent jobs can't both skip it).
