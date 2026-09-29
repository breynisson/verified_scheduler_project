# Phase 2 HTTP policy

Status: accepted for the in-memory Phase 2 coordinator.

- Leases last one coordinator-controlled logical tick. This matches the recorded stale-completion regression trace. Configuration can be introduced later without changing transition semantics.
- Acquisition selects the lexicographically smallest eligible job ID. Expirations are also reported in job-ID order because `scheduler-core` stores jobs in a `BTreeMap`.
- Job IDs and worker IDs must be non-empty strings. The coordinator rejects requests larger than 64 KiB. More specific size limits remain deferred.
- Resubmitting any existing ID remains a conflict, even when the payload is identical.
- `GET /debug/state` returns `now`, jobs ordered by ID, crashed workers ordered by ID, and an accepted-commit log used by Phase 3 verification. `POST /test/advance-time` returns `now` and the ordered jobs expired by that transition.
- Crash markers are permanent for the process lifetime. Test and debug routes require the coordinator's `--test-mode` flag; otherwise they return `test_mode_disabled`.

Phase 3 adds `--fault-accept-stale-token`, which is valid only together with `--test-mode`. It deliberately substitutes the current token when the current owner supplies a stale one while recording the stale supplied token. This mutation exists only so the verification harness can demonstrate discovery, shrinking, and replay of a real discrepancy.

The coordinator handles one connection at a time. Each request therefore performs one complete scheduler transition before another request can observe or modify state.
