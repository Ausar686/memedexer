# delivery

Posts captions back into the chat and keeps the owner informed. `Delivery`
([service.py](service.py)) is the worker's `JobListener`; formatting
([format.py](format.py)) and throttling ([throttle.py](throttle.py)) are pure
helpers.

## Replies
- Every `done` job gets a silent photo reply to its source message, in the
  same topic. The message holds the image and the formatted caption, and
  never posts if the original is gone (`allow_sending_without_reply=False`).
  - This covers new posts, edits, `/recaption` and deduplicated reposts.
- Photo: re-sent by `media.photo_file_id`. Image document: downloaded,
  prepared like for captioning, uploaded once as a photo; the resulting file
  id is stored so later replies reuse it.
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
- `RetryAfter` waits the time Telegram asks for. Network/server errors and
  retryable download errors back off (5 s × attempt, up to
  `MAX_SEND_ATTEMPTS`), then give up and alert the owner.
- A bad request (e.g. the original was deleted) or lost access gives up quietly.

## Owner alerts (DM)
- Captioning `failed` / `refused` jobs and abandoned replies: the error and a
  link to the message (supergroups only). Identical alerts for the same chat
  are sent at most once an hour, so an outage doesn't flood the DM.
- Over budget: one DM per chat and period, sent for the period's first
  `budget_exceeded` job (lowest id, so concurrent jobs can't both skip it).
