# telegram

aiogram 3 integration: chat approval, group commands, handlers that turn
group images into jobs, and the Bot API downloader the worker uses. Routers
are built per dispatcher (`build_*_router`) and wired in `app.build_dispatcher`.

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

## Chat lifecycle ([membership.py](membership.py))
| Event | Effect |
| --- | --- |
| Bot added (`my_chat_member` not-member → member) | Chat saved as `pending`, owner gets a DM with Approve / Reject. An already `approved` chat stays approved without a new DM. |
| Owner presses Approve | `approved`; the bot posts a short notice in the chat (how to enable captions, and that images go to a third-party AI provider). |
| Owner presses Reject / Revoke (from `/chats`) | `rejected` / `revoked`; the bot leaves the chat. |
| Bot removed from the chat | `approved` or `pending` becomes `revoked`, so re-adding asks the owner again. |
| Group upgraded to supergroup | The chat row (with topics and jobs) moves to the new id; a row the bot created for the new id is dropped. |

Only `OWNER_USER_ID` can press the buttons or use `/chats`. Telegram doesn't
let bots DM someone first, so the owner must send `/start` once; startup logs
a warning if the owner can't be reached ([setup.py](setup.py)).

## Group commands ([admin.py](admin.py))
| Command | Who | Effect |
| --- | --- | --- |
| `/settings` | anyone | Captioning on/off here, effective model and where it's set, description language, spend vs limits, available models |
| `/captions on\|off` | admins | Toggle captioning for the current topic |
| `/model [chat] <model>\|reset` | admins | Override the model for the topic (or with `chat`, the whole chat); model ids come from the allowlist and need a configured key |
| `/language <language>\|reset` | admins | Description language for the whole chat; `reset` means each meme's own language |
| `/recaption` (as a reply to an image) | admins | Enqueue a `recaption` job, skipping dedup |

"Admins" means the chat's creator or administrators (checked with
`getChatMember`), anonymous admins posting as the group, and the owner.
Commands in a chat that isn't approved get a short "waiting for approval"
reply. Command menus are registered at startup.

## Bot setup requirements
- Turn privacy mode off in BotFather (or make the bot an admin). Otherwise
  it never sees ordinary group messages.
- The bot can't see messages sent before it joined, nor messages from other
  bots.

## Downloads ([downloader.py](downloader.py))
`TelegramBadRequest` (e.g. file too big or gone) is permanent. Other API,
network and timeout errors are retryable.
