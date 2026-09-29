# Stale completion counterexample

## Purpose

`SchedulerStaleCompletion.tla` adds one deliberately faulty action to the correct scheduler model. `StaleComplete` accepts a completion associated with a historical lease even when another worker owns the current lease with a newer fencing token.

The mutation is not part of `Scheduler.Next`. It exists only to demonstrate that `S2_FencedCompletion` detects the fault and to preserve a concrete regression trace for later Rust and Hypothesis work.

## Reproduce

```sh
scripts/check-spec.sh stale-mutation
```

The wrapper returns success only when TLC exits with an error and reports:

```text
Invariant S2_FencedCompletion is violated
```

Tool: TLA+ Tools 1.7.4, TLC 2.19, revision `5a47802`

Configuration: one job, two workers, `MaxAttempts = 3`, `LeaseLength = 1`, `MaxTime = 3`

Search: breadth-first, one TLC worker

## TLC counterexample

TLC reported a seven-state behavior at depth seven:

| State | Action | Relevant state after action |
| --- | --- | --- |
| 1 | `Init` | `j1` is unsubmitted; `now = 0` |
| 2 | `Submit(j1)` | `j1` is pending |
| 3 | `Acquire(w1, j1)` | `w1` owns token 1; expiry is 1 |
| 4 | `Tick` | `now = 1`, so token 1 is expired because `now >= expiry` |
| 5 | `Expire(j1)` | `j1` returns to pending; attempt count is 1 |
| 6 | `Acquire(w2, j1)` | `w2` owns current token 2; expiry is 2 |
| 7 | faulty `StaleComplete(w1, j1, 1)` | completion for expired token 1 is accepted while the job's current token is 2 |

At state 7, the accepted-commit sequence contains:

```text
[job |-> j1, worker |-> w1, token |-> 1, acceptedAt |-> 1, expiry |-> 1]
```

but the job's monotonically increasing token is 2. The commit is also accepted exactly at its old expiry boundary. It therefore violates both essential checks represented by S2: the commit token is not current, and `acceptedAt < expiry` is false.

TLC generated 232 states and found 142 distinct states before reporting the violation. There were 66 states left on the queue, so this was a counterexample discovery rather than a completed state-space check.

## Correction

The real `Complete(w, j, token)` action in `Scheduler.tla` requires all of the following before it records a commit:

```text
leaseOwner[j] = w
leaseToken[j] = token
now < leaseExpiry[j]
```

The normal safety configuration excludes `StaleComplete`. With the guarded action, TLC completed the configured two-job/two-worker state graph without finding an S1-S5 violation. That result applies only to the finite model and constants recorded in `docs/traceability.md`; it does not prove the future Rust implementation correct.
