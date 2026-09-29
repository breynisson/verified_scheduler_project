# Protocol traceability

This table tracks how the written requirements map into the abstract model and, later, the running implementation. A checked box means that the named tool ran over its documented finite configuration; it does not mean that arbitrary implementations or executions have been proved correct.

| Requirement | TLA+ model | Rust behavior | Python/Hypothesis evidence | Current evidence |
| --- | --- | --- | --- | --- |
| S1: each job has at most one accepted terminal completion | `Complete`, `commits`, `S1_UniqueAcceptedCommit` | `Scheduler::complete`; completed jobs reject later transitions | Stateful invariant requires unique job IDs in the accepted-commit log | TLC safety run: no violation in configured bounds; Rust and generated HTTP checks pass |
| S2: only the current owner and fencing token may complete an unexpired lease | `Acquire`, `Complete`, `leaseHistory`, `S2_FencedCompletion` | `Scheduler::complete` checks expiry, owner, then token before mutation | Reference-model comparison plus commit time/token invariant; stale mutation and replay | TLC safety run: no violation; deliberate TLA+ and service mutations are detected |
| S3: completed and permanently failed jobs stay terminal | `terminalJobs`, `S3_TerminalMonotonicity` | Terminal statuses cannot be acquired, completed, or expired | Stateful invariant retains the observed terminal-job set across every command | TLC safety run: no violation in configured bounds; Rust and generated HTTP checks pass |
| S4: each job has at most one current lease | Single lease fields per job, `S4_LeaseUniqueness` | Each `Job` contains at most one optional `Lease`; acquire requires `pending` | Stateful invariant requires exactly leased jobs to have one lease object | TLC safety run: no violation in configured bounds; Rust and generated HTTP checks pass |
| S5: attempts do not exceed three and exhaustion fails permanently | `MaxAttempts`, `Acquire`, `Expire`, `S5_RetryBound` | `MAX_ATTEMPTS`; acquire increments attempts; third expiry fails the job | Stateful invariant bounds attempts and requires failed jobs to have three | TLC safety run: no violation in configured bounds; Rust and generated HTTP checks pass |
| Lease is expired exactly when `now >= expiry` | `Complete` requires `now < leaseExpiry[j]`; `Expire` requires `now >= leaseExpiry[j]` | `advance_time` atomically expires every due lease using `now >= expiry` | Stale-trace regression advances from `now = 0` to expiry 1 and observes `pending` | Exercised by TLC boundary; Rust and HTTP exact-expiry tests pass |
| Crashed workers cannot acquire or complete | `Crash`; guards in `Acquire` and `Complete` | Test-support `Scheduler::crash`; acquire and complete reject crashed workers | `test_crashed_worker_cannot_complete_current_lease`; production-mode gate test | TLC safety run: no violation in configured bounds; Rust and HTTP crash tests pass |
| L1: a submitted job eventually becomes terminal under progress assumptions | `ProgressSpec`, `L1_EventualTerminal` | Pure transitions only; no runtime progress mechanism or liveness claim | Not implemented | Bounded TLC liveness run: no violation under documented assumptions |

## Model-to-interface differences

- The TLA+ model represents `Tick` and `Expire` as separate atomic actions. The future `POST /test/advance-time` operation will advance logical time and process all resulting expirations before returning. HTTP clients cannot observe the intermediate abstract state.
- A TLA+ `Acquire(w, j)` action chooses a particular eligible job. The future HTTP acquisition endpoint chooses a job internally; its selection policy remains undecided.
- Rejected HTTP requests are represented by stuttering in the abstract specification rather than named error transitions. Error-code precedence remains an implementation and black-box-test obligation.
- Payloads are omitted from the model because no current property depends on their contents. Job identity is retained.
- `leaseHistory` and `terminalJobs` are specification history variables used to express safety properties. They need not become production state with the same representation.

## Liveness boundary

`L1` is not part of the safety configuration. `SchedulerLiveness.cfg` checks it separately for one job and two workers. `ProgressSpec` excludes `Crash`, representing the assumption that eligible workers remain available, and applies weak fairness to acquisition, completion, clock advancement, and expiry. Submission is not required to occur; the property begins to apply if a job leaves `unsubmitted`.

The logical clock is bounded by `MaxTime = 3`. Once the clock reaches that bound, a valid final lease cannot expire, but weak fairness of completion requires its continuously enabled completion action eventually to occur. Before the bound, fair ticking and expiry allow attempts to be exhausted. This is a bounded abstraction of the stated progress assumptions, not a universal liveness result and not evidence about the future Rust implementation. Finite black-box tests will not establish universal liveness either.

## Recorded model-checking bounds

The initial `Scheduler.cfg` run used two jobs, two workers, `MaxAttempts = 3`, `LeaseLength = 2`, and logical time `0..6`. TLA+ Tools 1.7.4 with one TLC worker completed the state graph with 2,112,449 states generated, 1,147,008 distinct states, depth 23, and no invariant violation. Fingerprint collision estimates and the full local output are retained under `artifacts/tlc/latest/` when the check runs; generated logs are not committed.

The `SchedulerLiveness.cfg` run used one job, two workers, `MaxAttempts = 3`, `LeaseLength = 1`, and logical time `0..3`. TLC completed temporal-property checking over 244 distinct states at depth 11 without finding a violation of `L1_EventualTerminal` under `ProgressSpec`.

The deliberate stale-completion configuration used one job, two workers, a lease length of one, and logical time `0..3`. Its checked-in counterexample is in [`spec/tla/counterexamples/stale-completion.md`](../spec/tla/counterexamples/stale-completion.md).

## Phase 2 core evidence

On 2026-09-29, `cargo test -p scheduler-core` passed eight focused unit tests covering submission, explicit and deterministic acquisition, the exact expiry boundary, retry exhaustion, terminal monotonicity, stale fencing tokens, and crashed-worker guards. `cargo test --workspace` also passed. These are implementation tests, not proofs. Failure injection and complete state lookup are available only to crate tests or consumers that explicitly enable the `test-support` feature.

The same change was checked against the existing artifacts: `scripts/check-spec.sh safety` completed all 1,147,008 distinct configured states without finding an invariant violation; `scripts/check-spec.sh liveness` completed temporal checking over 244 distinct configured states without finding a violation; and `scripts/check-spec.sh stale-mutation` reproduced the expected seven-state `S2_FencedCompletion` violation.

`TEST_RUN_NAME=phase-2-http scripts/test-system.sh` passed five black-box tests against isolated coordinator processes. The suite covers health, the recorded stale-completion trace and rejection-without-mutation, exact expiry and reacquisition, retry exhaustion, crashed-worker completion rejection, successful current-token completion, and production-mode rejection of every test/debug route. These finite examples provide implementation evidence; they do not prove the TLA+ properties for the Rust service.

## Phase 3 generated evidence

On 2026-09-29, `TEST_RUN_NAME=phase-3-final scripts/test-system.sh` passed nine tests in 12.68 seconds. The `RuleBasedStateMachine` used two bounded job IDs, two worker IDs, tokens `1..4`, positive time deltas `1..2`, 20 examples, and 20 state-machine steps. Submission, acquisition, completion, clock advancement, and crashes include valid, duplicate, invalid-state, wrong-owner, and stale-token histories. After every command, the harness compares the normalized HTTP outcome and full debug snapshot with an independent Python reference model, then checks non-tautological observable forms of S1-S5.

An explicit `--fault-accept-stale-token` mutation is available only with test mode. A targeted Hypothesis state machine detected it, shrank the supplied stale token to 1, and saved the minimized five-command trace in `verification/python/tests/regressions/stale-token.json`. Regression tests replay that trace successfully against the corrected coordinator and confirm that it diverges against the intentional mutation. Generated examples and replay tests are finite implementation evidence, not a proof of the Rust service or universal liveness.
