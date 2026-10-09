# pipeline

Turns queued jobs into stored captions. [worker.py](worker.py) runs the loop;
[resolve.py](resolve.py), [budget.py](budget.py) and [images.py](images.py)
are pure helpers it calls.

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
6. Downloads the file, fits it to provider limits (≤1568 px per side,
   ≤3.75 MB raw, otherwise re-encoded as JPEG), and calls the provider.
7. Records usage and `cost_microusd` on the job (also for billed refusals
   and failures), stores the `Caption`, and finishes the job.

## Retries
- Retryable download or provider errors put the job back to `pending` with
  `available_at` delayed by 30 s × 4^(attempt−1), capped at 15 min.
- After `MAX_ATTEMPTS` claims the job is `failed`.
- An unexpected exception fails the job instead of killing the worker.
- `run()` puts jobs left `running` by a crash back to `pending` at startup.

## Concurrency
`WORKER_CONCURRENCY` loops share the queue; claiming is a single atomic
`UPDATE … RETURNING`. The budget check happens before the call, so a chat can
overshoot its limit by at most one call per concurrent loop (fractions of a
cent at default models). Handlers call `Worker.notify()` after enqueueing so
idle loops wake immediately instead of waiting for the 5 s poll.

## Extension point
`JobListener.job_finished(job)` runs after every terminal job (not after a
retry). It's a no-op for now; caption delivery and owner alerts will plug in
here. A failing listener is logged and never affects the job.
