# delivery

Posts captions back into the chat and keeps the owner informed. `Delivery`
([service.py](service.py)) is the worker's `JobListener`; formatting
([format.py](format.py)) and throttling ([throttle.py](throttle.py)) are pure
helpers.

## Replies
- Every `done` job gets a silent reply to its source message, in the same
  topic. The message holds the same media and the formatted caption, and
  never posts if the original is gone (`allow_sending_without_reply=False`).
  - This covers new posts, `/recaption` and deduplicated reposts.
- An `edit` job (the source message's media was replaced) edits the reply
  already posted for that message in place (`editMessageMedia`), so editing
  back and forth can't make the bot post again and again. Several jobs then
  share one `reply_message_id`. "Message is not modified" counts as done; if
  the old reply is gone or can't be edited, a new reply is posted.
- A job whose message already has a reply from a newer job (an edit can be
  captioned before the original) posts nothing and takes that reply's id,
  so a stale reply with the pre-edit media never appears.
- Photo, video, GIF: re-sent by `media.reply_file_id` (`sendPhoto`,
  `sendVideo`, `sendAnimation`). Documents can't be re-sent as photos or
  videos by their own id, so they are downloaded and uploaded once (an image
  prepared like for captioning, a video as is); the resulting file id is
  stored so later replies reuse it. A video document over the download limit
  goes back as a document (`sendDocument`), and so does an image document
  Telegram refuses as a photo (`PHOTO_INVALID_DIMENSIONS`: photos are
  limited to a 20:1 side ratio, and long strips exceed it).
- A spoiler that would go back as a file gets a text-only reply with the
  hidden caption instead (link previews off): a file can't be blurred, and
  its preview would show what the spoiler hides.
- Spoilers: a job with `has_spoiler` gets a blurred photo, and every caption
  part is wrapped in `<tg-spoiler>` (the markup doesn't count towards the
  limit below). `hide_replies(chat_id, message_id)` handles a source that
  became a spoiler after it was captioned: it flags the message's jobs and
  edits the replies already posted (`editMessageMedia`), each once, with the
  media of its latest job. A reply that was being sent at that moment is
  blurred right after it posts. The edit uses the input media type matching
  the reply (photo, video or GIF); a reply that went back as a document
  can't be blurred and is skipped.
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
- `RetryAfter` waits the time Telegram asks for, up to
  `MAX_RETRY_AFTER_SECONDS` (60 s); a longer flood wait gives up and alerts
  the owner rather than hold the chat's queue. Network/server errors and
  retryable download errors back off (5 s × attempt, up to
  `MAX_SEND_ATTEMPTS`), then give up and alert the owner.
- A bad request whose target is gone (`TARGET_GONE`: the original, its topic
  or the reply to edit was deleted, the topic is closed, the chat is gone) or
  lost access gives up quietly. Any other bad request (a malformed caption, a
  wrong file id, missing rights) gives up and alerts the owner.
- Any other exception while posting is recorded as `delivery_error` and
  alerted too, so the reply isn't retried on every restart.
- Hiding a reply uses the same retries; when they run out the owner is
  alerted, and a reply that is gone is skipped quietly.

## Owner alerts (DM)
- A `thumbnail_only` job (a video over the download limit): a notice that the
  caption came from the small preview, with a link.
- Captioning `failed` / `refused` jobs and abandoned replies: the error and a
  link to the message (supergroups only).
- Each kind of alert above is sent at most once an hour per chat
  (`ALERT_INTERVAL_SECONDS`), whatever the error text (it can differ for every
  file), so an outage or a flood of bad files doesn't flood the DM; the next
  alert says how many were held back.
- Over budget: one DM per chat and period, sent for the period's first
  `budget_exceeded` job (lowest id, so concurrent jobs can't both skip it).
  Over the total limit, one DM per period for all chats.
