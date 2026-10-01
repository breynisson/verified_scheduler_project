# Project context

This document is a concise orientation map for contributors and future work sessions. It is not the normative protocol or evidence record. If documents disagree, use this precedence:

1. [`protocol.md`](protocol.md) defines scheduler semantics and the public contract.
2. [`decisions/`](decisions/) records accepted implementation and architecture choices.
3. [`traceability.md`](traceability.md) records which model and test evidence currently exists.
4. This document summarizes those sources for navigation.

The original milestones and longer-term plan are in [`verified_scheduler_project.md`](../verified_scheduler_project.md).

## Purpose and current phase

Verified Scheduler is a learning project for connecting four forms of reasoning about a leased-job protocol:

- a written contract;
- a finite TLA+ model checked with TLC;
- a Rust implementation; and
- black-box Python tests, including a Hypothesis state machine and independent reference model.

Phases 0–3 are complete and tagged `Phase0` through `Phase3`. Phase 4 adds a worker process, optional SQLite persistence, restart recovery, and selected concurrent-request scenarios while retaining deterministic logical time and a single coordinator.

No test result should be described as a proof. TLC results apply to the documented finite model configurations, while Rust and Python tests provide evidence about selected implementation executions.

## Protocol vocabulary

A job has a stable caller-supplied ID, an object payload, a status, an attempt count, and an optional lease. The shared status vocabulary is:

```text
pending -> leased -> completed
                  -> pending  (expired with retries remaining)
                  -> failed   (expired after retry exhaustion)
```

`completed` and `failed` are terminal.

A lease contains a worker ID, a positive fencing token, and an expiry in coordinator-controlled logical time. The main operations are `Submit`, `Acquire`, `Complete`, `Tick`/advance time, `Expire`, and `Crash`. Use these names and argument meanings consistently across TLA+, Rust, Python, HTTP, and serialized traces.

## Fixed semantic decisions

The most important protocol choices are:

- A lease is valid only while `now < expiry`; it is expired when `now >= expiry`.
- A job can receive at most three accepted leases.
- Every accepted acquisition increments both the attempt count and fencing token.
- Tokens begin at 1, increase monotonically per job, and are never reused.
- Expiry after attempts 1 or 2 returns a job to `pending`; expiry after attempt 3 makes it permanently `failed`.
- Completion requires the current owner, current token, and an unexpired lease.
- Rejected requests do not mutate scheduler state.
- Logical time belongs to the coordinator and never moves backward.
- Completion error precedence is unknown job, invalid state, expired lease, owner mismatch, then stale token.

The complete error vocabulary and HTTP contract live in [`protocol.md`](protocol.md).

## Current implementation decisions

The accepted in-memory coordinator policy is documented in [`0001-phase-2-http-policy.md`](decisions/0001-phase-2-http-policy.md). In summary:

- leases last one logical tick;
- acquisition chooses the lexicographically smallest eligible job ID;
- simultaneous expirations are reported in job-ID order;
- duplicate submission is always a conflict;
- crash markers last for the process lifetime;
- requests are limited to 64 KiB; and
- connections are handled concurrently while coordinator transitions and persistence commits serialize through one process-local mutex.

`scheduler-core` is a dependency-free Rust crate containing pure domain transitions and a validated snapshot/restore boundary. The coordinator owns HTTP parsing, JSON serialization, the scheduler instance, SQLite state snapshots, and the accepted-commit diagnostic log. Phase 4 persistence and concurrency choices are recorded in [`0002-phase-4-persistence-and-concurrency.md`](decisions/0002-phase-4-persistence-and-concurrency.md).

The TLA+ model represents `Tick` and `Expire` as separate actions. The HTTP test operation advances time and processes all resulting expirations atomically before returning, so clients cannot observe the intermediate state.

## Repository map

| Area | Responsibility |
| --- | --- |
| `spec/tla/` | Abstract scheduler, finite TLC configurations, and deliberate stale-completion mutation |
| `services/scheduler-core/` | Pure Rust state and transitions |
| `services/coordinator/` | Concurrent HTTP boundary, serialized transitions, and optional SQLite persistence |
| `services/worker/` | Replaceable HTTP worker process used in crash and replacement scenarios |
| `verification/python/scheduler_verification/` | Adapter, independent reference model, strategies, and trace replay |
| `verification/python/tests/` | Example-based, stateful, mutation, and regression tests |
| `scripts/check-spec.sh` | Reproducible TLC safety, liveness, and mutation runs |
| `scripts/test-system.sh` | Isolated process-level Python verification suite |
| `artifacts/` | Generated local evidence; not committed |
| `docs/article-notes/` | Untracked narrative working notes; do not include in commits |

## Verification layers

The project deliberately keeps different evidence sources separate:

### TLA+

[`Scheduler.tla`](../spec/tla/Scheduler.tla) models submission, leasing, completion, logical time, expiry, crashes, and retries. The safety configuration checks TypeOK and S1–S5 over finite constants. A separate bounded configuration checks L1 under explicit weak-fairness assumptions.

[`SchedulerStaleCompletion.tla`](../spec/tla/SchedulerStaleCompletion.tla) adds an intentional fault. Its checked-in [seven-state counterexample](../spec/tla/counterexamples/stale-completion.md) shows an expired token-1 worker completing after token 2 has been issued.

### Rust tests

`scheduler-core` has focused unit tests for submission, explicit and deterministic acquisition, exact expiry, retry exhaustion, terminal monotonicity, stale fencing, and crashed workers.

### Example-based black-box tests

The Python suite starts isolated coordinator processes and checks health, stale-completion rejection without mutation, exact expiry and reacquisition, retry exhaustion, crash handling, successful current-token completion, and test-mode gating.

### Hypothesis and replay

The Phase 3 `RuleBasedStateMachine` generates bounded histories over two jobs, two workers, tokens `1..4`, and time deltas `1..2`. After every command it compares the HTTP outcome and complete debug snapshot with an independent Python reference model, then checks observable forms of S1–S5.

An intentional test-only stale-token fault demonstrates discovery and shrinking. Its minimized five-command trace is stored in [`stale-token.json`](../verification/python/tests/regressions/stale-token.json) and replayed against both the corrected and faulty coordinator.

Exact commands, bounds, results, and limitations belong in [`traceability.md`](traceability.md).

## Test-only boundaries

Failure injection and complete state inspection must remain unavailable in normal operation.

The following routes require `--test-mode` and otherwise return `503 test_mode_disabled`:

```text
POST /test/advance-time
POST /test/workers/{worker_id}/crash
POST /test/fail-next-persist
POST /test/fail-next-persist-after-write
GET  /debug/state
```

The `--fault-accept-stale-token` mutation is valid only together with `--test-mode`. It is verification instrumentation, not an alternative production configuration.

The worker's `--test-pause-before-complete-ms` and `--test-start-gate` controls likewise require its `--test-mode` flag.

Debug state currently includes logical time, ordered jobs, ordered crashed workers, and an accepted-commit log. The commit log exists to make safety properties observable to the harness.

## Current limitations

The current system is intentionally bounded and incomplete:

- persistence uses one versioned JSON snapshot rather than normalized relational tables;
- concurrent transitions are serialized through one process-local mutex;
- there is no multi-coordinator protocol;
- no real-time clock participates in lease validity;
- generated histories use small finite domains and budgets;
- external side effects are not made exactly once; and
- liveness evidence depends on finite bounds and explicit fairness assumptions.

Coordinator fencing prevents an old lease from committing at this coordinator. It does not by itself prevent duplicated external work or make downstream effects idempotent.

## Reproduction commands

From the repository root:

```sh
cargo fmt --all -- --check
cargo test --workspace --locked

scripts/check-spec.sh safety
scripts/check-spec.sh liveness
scripts/check-spec.sh stale-mutation

scripts/test-system.sh
```

The system-test wrapper writes replaceable evidence under `artifacts/test-runs/latest/`. Set `TEST_RUN_NAME` to retain a named run.

## Documentation maintenance

When behavior changes:

- update [`protocol.md`](protocol.md) if normative semantics or public errors change;
- add or revise a decision record when an implementation choice has meaningful alternatives or consequences;
- update [`traceability.md`](traceability.md) only with evidence that actually ran;
- add a minimized regression trace for every confirmed generated or discovered protocol bug;
- keep TLA+, Rust, and Python operation vocabulary and expiry boundaries aligned; and
- update this document only when the project map, current phase, or high-level decisions change.

Do not turn this file into a changelog or duplicate detailed run statistics here. Its purpose is to help a reader find the authoritative source quickly.
