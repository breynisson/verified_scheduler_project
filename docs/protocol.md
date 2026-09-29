# Scheduler protocol contract

This document fixes the protocol choices that TLA+, Rust, and Python implementations must share. Phase 2 implements the scheduler operations below with an in-memory coordinator; later phases extend the verification harness, worker behavior, and persistence.

## State and lifecycle

A job has a caller-supplied stable string ID, a JSON object payload, a status (`pending`, `leased`, `completed`, or `failed`), an attempt count, and an optional lease. A lease contains a worker ID, a positive integer fencing token, and an expiry in coordinator-controlled logical time.

The permitted lifecycle is:

```text
pending -> leased -> completed
                  -> pending (expired lease with retries remaining)
                  -> failed  (expired lease after retry limit)
```

`completed` and `failed` are terminal. Rejected requests never mutate coordinator state.

## Fixed semantic choices

- **Lease boundary:** a lease is expired when `now >= expiry`. It is valid only while `now < expiry`. A completion arriving exactly at expiry is rejected as `lease_expired`.
- **Retry limit:** `max_attempts` is **3**. Each accepted acquisition increments the job's attempt count, so a job can receive at most three leases. Expiry of attempt 1 or 2 returns it to `pending`; expiry of attempt 3 makes it permanently `failed`. Rejected acquisition requests do not consume an attempt.
- **Fencing tokens:** the first accepted lease for a job has token 1. Every later accepted lease for that job increments the token by one. Tokens are never reused or supplied by clients. Expiry does not itself increment a token.
- **Time authority:** only the coordinator's non-negative integer logical clock is authoritative. Test mode may advance it by a positive integer delta; it never moves backward.

## HTTP interface

All bodies are JSON and successful JSON responses use `Content-Type: application/json`. Scheduler endpoints and debug/test endpoints listed here are reserved for later phases.

| Operation | Request | Successful response |
| --- | --- | --- |
| Health | `GET /health` | `200 {"status":"ok"}` |
| Submit | `POST /jobs` with `{"id": string, "payload": object}` | `201` with the job |
| Acquire | `POST /workers/{worker_id}/acquire` | `200` with the leased job and token, or `204` when none is eligible |
| Complete | `POST /jobs/{job_id}/complete` with `{"worker_id": string, "token": integer}` | `200` with the completed job |
| Advance clock (test mode) | `POST /test/advance-time` with `{"delta": positive integer}` | `200` with current time |
| Crash worker (test mode) | `POST /test/workers/{worker_id}/crash` | `204` |
| Inspect state (test mode) | `GET /debug/state` | `200` with the complete observable state |

Expiry processing is an atomic coordinator transition performed after logical time advances and before the advance-time response is returned. Later implementations must define a deterministic ordering for multiple expirations in their observable diagnostic log.

## Error responses

Errors use this stable envelope:

```json
{
  "error": {
    "code": "machine_readable_code",
    "message": "human-readable summary"
  }
}
```

Clients must branch on `code`, not `message`. No error response changes state.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | Malformed JSON, missing/invalid fields, or invalid time delta |
| 404 | `not_found` | Unknown route or unknown job ID |
| 405 | `method_not_allowed` | Known route used with the wrong HTTP method |
| 409 | `job_already_exists` | Submission reuses an existing job ID |
| 409 | `invalid_job_state` | Operation is not allowed from the job's current state |
| 409 | `worker_crashed` | A crashed worker attempts a new operation |
| 409 | `lease_owner_mismatch` | Completion worker is not the current lease owner |
| 409 | `stale_fencing_token` | Completion token is not the current lease token |
| 409 | `lease_expired` | Completion is received when `now >= expiry` |
| 503 | `test_mode_disabled` | A test/debug endpoint is unavailable outside test mode |

For completion validation, errors take this precedence: unknown job, terminal/otherwise invalid job state, expired lease, owner mismatch, then stale token. This makes one invalid request produce one deterministic response without exposing token details for an expired lease.

## Atomicity and scope

Each HTTP scheduler operation will be one atomic transition at a single coordinator. Concurrent and multi-coordinator behavior is outside the initial protocol. Coordinator fencing prevents an old lease from committing at the coordinator; it does not make external side effects exactly-once.

## Decisions intentionally left open

- Maximum job ID, worker ID, payload, and request sizes.
- Whether a crash marker can be cleared and how worker recovery is represented.

The Phase 2 choices for lease duration, job selection, duplicate submission, diagnostic ordering, and debug responses are recorded in [`decisions/0001-phase-2-http-policy.md`](decisions/0001-phase-2-http-policy.md).
