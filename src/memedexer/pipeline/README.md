# pipeline

Turns queued jobs into stored captions. [worker.py](worker.py) runs the loop;
[resolve.py](resolve.py) and [budget.py](budget.py) are pure helpers it
calls; [sandbox.py](sandbox.py) decodes images and videos through the child
process in [decoder.py](decoder.py), which runs [images.py](images.py) and
[frames.py](frames.py).

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
   daily, weekly or monthly limit (UTC calendar periods, ISO weeks).
5. Fails it if the resolved provider has no API key.
6. Downloads the file, fits it to provider limits in the sandbox (see
   below), and calls the provider. A video becomes its sampled frames, all
   in one call; a video over the Bot API's 20 MB download limit (by its
   stated size, or because Telegram refuses it as too big) becomes its
   thumbnail and the job is marked `thumbnail_only` (without a thumbnail it
   fails).
7. Records usage and `cost_microusd` on the job (also for billed refusals
   and failures), stores the `Caption`, and finishes the job.

## Images
- Only JPEG, PNG and WebP are opened (`images.FORMATS`), whatever type the
  sender declared, and the type sent to the provider is the detected one.
- Images over `images.MAX_PIXELS` (64 MP) are refused before decoding, so a
  decompression bomb (a few KB that decode to gigabytes) costs nothing.
- Small ones (≤1568 px per side, ≤3.75 MB) are passed through untouched once
  their structure checks out; the rest are shrunk (palettes smoothly) and
  re-encoded as JPEG, flattened onto white.

## Video frames
- Positions: `sandbox.FRAME_POSITIONS` (10%, 50%, 90% of the duration),
  counted from the stream's first timestamp (not always 0, e.g. in MPEG-TS).
  The duration comes from the container, else from Telegram; with neither,
  the first frame only. Each frame is the first one shown at or after its
  position, or the last frame when the video ends earlier.
- Frames are turned upright (phone videos store a display rotation), shown at
  their display shape (anamorphic video stores non-square pixels; ratios
  beyond 4:1 are clamped), scaled to `MAX_SIDE` while converting, and
  re-encoded as JPEG.
- Near-identical frames (mean colour difference of 16×16 thumbnails below
  `frames.DUPLICATE_THRESHOLD`) are dropped, so a still picture with music
  costs one frame.
- Inputs that can't seek (raw streams), seek imprecisely (a seek that lands
  past its target, as in MPEG streams) or lack timestamps are read in one
  forward pass instead, with frame times from the frame rate when frames
  carry none.
- Only the demuxers in `frames.DEMUXERS` are opened (MP4/MOV, Matroska/WebM,
  AVI, MPEG-TS/PS, FLV, ASF, GIF, raw H.264/HEVC), and only the decoders in
  `frames.DECODERS` run (H.264, HEVC, VP8/9, AV1, MPEG-1/2/4, H.263, FLV,
  WMV/VC-1, MJPEG, GIF), also while FFmpeg probes the streams.

## Decoding sandbox
Pillow and FFmpeg (via PyAV) are native code parsing files anyone in a chat
can send, so the bot never decodes media itself. Each image or video goes to
a fresh `python -I -m memedexer.pipeline.decoder image|video` process, in its
own process group, with:
- an empty environment (no tokens); the bot process is also non-dumpable
  (`app.forbid_inspection`), so the child can't read the bot's environment or
  memory through `/proc` either;
- `RLIMIT_AS` 1 GiB (Linux), `RLIMIT_CPU` 10 s, `RLIMIT_FSIZE` 0 (no file
  writes) and a small `RLIMIT_NOFILE`, plus a 15 s wall-clock timeout.

The whole group is killed on timeout or when the job is cancelled. The parent
reads at most `sandbox.MAX_OUTPUT_BYTES` of output and the tail of stderr;
the output is length-framed pictures that it checks (count, size, image
signature) before use. Bad input exits with a one-line reason; any failure is
a non-retryable `ImageError` or `VideoError`. The image mode doesn't load
PyAV, and the bot process loads neither.

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
`UPDATE … RETURNING`. The budget check happens before the call, so a chat can
overshoot its limit by at most one call per concurrent loop (fractions of a
cent at default models). Handlers call `Worker.notify()` after enqueueing so
idle loops wake immediately instead of waiting for the 5 s poll.

## Extension point
`JobListener.job_finished(job)` runs after every terminal job (not after a
retry). The app plugs in `delivery.Delivery`, which posts replies and alerts
the owner. A failing listener is logged and never affects the job.
