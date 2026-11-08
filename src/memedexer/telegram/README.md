# telegram

aiogram 3 integration: chat approval, group commands, handlers that turn
group images into jobs, and the Bot API downloader the worker uses. Routers
are built per dispatcher (`build_*_router`) and wired in `app.build_dispatcher`.

## Ingestion ([handlers.py](handlers.py))
- Listens to `message` and `edited_message` updates with a photo, video,
  GIF (`animation`) or document in groups and supergroups (private chats are
  ignored).
- A job is enqueued only when the chat is `approved` **and** the message's
  topic has `captioning_enabled`. Everything else is ignored silently.
- A chat with `MAX_QUEUED_JOBS_PER_CHAT` (50) jobs waiting or running gets no
  new ones until they're done; the owner is told at most once an hour.
  `/recaption` answers that the queue is full.
- Topic: `message_thread_id` when `is_topic_message` is set, otherwise
  `NO_TOPIC`. Reply threads in ordinary groups also carry a thread id, so the
  flag matters.
- Each job records the message's `has_media_spoiler`, so its reply is a
  spoiler too (`/recaption` takes it from the replied message).
- Edits enqueue an `edit` job only when the image changed (a different
  `file_unique_id` than the message's latest job). An edit that keeps the
  image but adds a spoiler calls `delivery.hide_replies` instead.
- Handlers receive `sessionmaker`, `worker` and `delivery` from the dispatcher
  context (`app.build_dispatcher`).

## Which file ([media.py](media.py))
- Photos: the largest size whose longer side fits `pipeline.images.MAX_SIDE`,
  else the smallest. Dedup keys on that size's `file_unique_id`.
- Documents: `image/jpeg`, `image/png`, `image/webp` up to the Bot API's
  20 MB download limit, and any `video/*`. Their dimensions are unknown.
- Videos and GIFs: the file itself, with its duration and thumbnail; one over
  the download limit is still accepted and captioned from the thumbnail.
  Telegram also sets `document` on GIFs, so `animation` is checked first.
- Video notes (round videos) and stickers are ignored.

## Chat lifecycle ([membership.py](membership.py))
| Event | Effect |
| --- | --- |
| Bot added (`my_chat_member` not-member → member) | Chat saved as `pending`, owner gets a DM with Approve / Reject, at most once per chat per day (`APPROVAL_REQUEST_INTERVAL_SECONDS`; the chat is in `/chats` either way). An already `approved` chat stays approved without a new DM. A `rejected` chat stays rejected and the bot leaves it silently: only the owner can approve it, from `/chats`. |
| Owner presses Approve | `approved`; the bot posts a short notice in the chat (how to enable captions, and that images go to a third-party AI provider). |
| Owner presses Reject / Revoke (from `/chats`) | `rejected` / `revoked`; the bot leaves the chat. |
| Bot removed from the chat | `approved` or `pending` becomes `revoked`, so re-adding asks the owner again. |
| Group upgraded to supergroup | The chat row (with topics and jobs) moves to the new id; a row the bot created for the new id is dropped. |

`/chats` lists approved, pending and rejected chats with Revoke, Approve /
Reject and Approve buttons. Approving a chat the bot has left tells the owner
to add the bot again. Only `OWNER_USER_ID` can press the buttons or use
`/chats`. Telegram doesn't
let bots DM someone first, so the owner must send `/start` once; startup logs
a warning if the owner can't be reached ([setup.py](setup.py)).

## Group commands ([admin.py](admin.py))
| Command | Who | Effect |
| --- | --- | --- |
| `/settings` | anyone | Captioning on/off here, effective model and where it's set, description language, spend vs limits, available models |
| `/captions on\|off` | admins | Toggle captioning for the current topic |
| `/model [chat] <model>\|reset` | admins | Override the model for the topic (or with `chat`, the whole chat); model ids come from the allowlist and need a configured key |
| `/language <language>\|reset` | admins | Description language for the whole chat; `reset` means each meme's own language |
| `/recaption` (as a reply to an image or a video) | admins | Enqueue a `recaption` job, skipping dedup |

"Admins" means the chat's creator or administrators (checked with
`getChatMember`; a failed lookup counts as not an admin), anonymous admins
posting as the group, and the owner. Commands run only in approved chats
(`require_approved_chat`, checked first): elsewhere the chat gets a short
"waiting for approval" reply at most once an hour, and no usage text or admin
lookups. Command menus are registered at startup.

## Bot setup requirements
- Turn privacy mode off in BotFather (or make the bot an admin). Otherwise
  it never sees ordinary group messages.
- The bot can't see messages sent before it joined, nor messages from other
  bots.

## Downloads ([downloader.py](downloader.py))
`TelegramBadRequest` (e.g. file too big or gone) is permanent. Other API,
network and timeout errors are retryable. Each download may take up to
`DOWNLOAD_TIMEOUT_SECONDS` (120 s), enough for a 20 MB video on a slow link.
