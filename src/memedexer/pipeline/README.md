# pipeline

Turns queued jobs into stored captions. [worker.py](worker.py) runs the loop;
[resolve.py](resolve.py), [budget.py](budget.py) and [images.py](images.py)
are pure helpers it calls; [video.py](video.py) samples video frames through
the child process in [frames.py](frames.py).

## Job lifecycle
```
pending ──claim──▶ running ──▶ done | failed | refused | budget_exceeded
   ▲                  │
   └──retry (delay)───┘
```
For each claimed job the worker, in order:
1. Fails it if the chat is no longer approved.
2. Resolves provider/model (topic → chat → env default, skipping overrides
   that are no longer allowlisted) and the chat's description language.
3. **Dedup:** reuses the latest caption of the same `file_unique_id`, from any
   chat and any model, at no cost. `recaption` jobs skip this step.
4. **Budget:** marks the job `budget_exceeded` if the chat has reached its
   daily, weekly or monthly limit, or all chats together have reached the
   total monthly limit (UTC calendar periods, ISO weeks).
5. Fails it if the resolved provider has no API key.
6. Downloads the file, fits it to provider limits (≤1568 px per side,
   ≤3.75 MB raw, otherwise re-encoded as JPEG), and calls the provider. A
   video becomes its sampled frames (see below), all in one call; a video
   over the Bot API's 20 MB download limit becomes its thumbnail and the job
   is marked `thumbnail_only` (without a thumbnail it fails).
7. Records usage and `cost_microusd` on the job (also for billed refusals
   and failures), stores the `Caption`, and finishes the job.

## Video frames
- Positions: `video.FRAME_POSITIONS` (10%, 50%, 90% of the duration). The
  duration comes from the container, else from Telegram; with neither, the
  first frame only. Each frame is the first one shown at or after its
  position, or the last frame when the video ends earlier.
- Frames are turned upright (phone videos store a display rotation), scaled
  to `MAX_SIDE` while converting, and re-encoded as JPEG.
- Near-identical frames (mean colour difference of 16×16 thumbnails below
  `frames.DUPLICATE_THRESHOLD`) are dropped, so a still picture with music
  costs one frame.
- Inputs that can't seek (raw streams) are read in one forward pass, with
  frame times from the frame rate when frames carry no timestamps.
- Isolation: decoding untrusted video runs FFmpeg's native code (via PyAV),
  so it happens in a fresh `python -I -m memedexer.pipeline.frames` process
  per video, with an empty environment (no tokens), `RLIMIT_AS` 1 GiB (Linux),
  `RLIMIT_CPU` 10 s and a 15 s wall-clock timeout. The child is killed on
  timeout or when the job is cancelled; its output is length-framed JPEGs
  that the parent checks (count, size) before use. Any failure is a
  non-retryable `VideoError`. The bot process never imports PyAV.

## Retries
- Retryable download or provider errors put the job back to `pending` with
  `available_at` delayed by 30 s × 4^(attempt−1), capped at 15 min.
- After `MAX_ATTEMPTS` claims the job is `failed`.
- An unexpected exception fails the job instead of killing the worker.
- `run()` puts jobs left `running` by a crash back to `pending` at startup,
  except those already on their last attempt: they fail (and the owner is
  alerted), so a file that crashes the process can't do it on every restart.

## Stopping
`stop()` lets each loop finish its current job and exit; `run()` then
returns. Don't cancel the worker to stop it: a cancellation that lands
mid-query aborts SQLAlchemy's connection reset and can leave an aiosqlite
close that never completes, hanging the event loop's shutdown. The app only
cancels a worker that overruns its stop deadline.

## Concurrency
`WORKER_CONCURRENCY` loops share the queue; claiming is a single atomic
`UPDATE … RETURNING`. The next job comes from the chat with the fewest jobs
running (oldest first), so a chat flooding the queue can't starve the others,
while a lone busy chat still gets every loop. The budget check happens before
the call, so a chat can
overshoot its limit by at most one call per concurrent loop (fractions of a
cent at default models). Handlers call `Worker.notify()` after enqueueing so
idle loops wake immediately instead of waiting for the 5 s poll.

## Extension point
`JobListener.job_finished(job)` runs after every terminal job (not after a
retry). The app plugs in `delivery.Delivery`, which posts replies and alerts
the owner. A failing listener is logged and never affects the job.
