# Verified Scheduler

A learning project for specifying a leased-job protocol in TLA+, implementing it in Rust, and testing the running system from Python with Hypothesis. The workspace currently includes the contract, TLA+ model, a pure `scheduler-core` transition crate, and a coordinator health endpoint.

## Prerequisites

- Rust 1.98.1 with Cargo (installed automatically by `rustup` from `rust-toolchain.toml`)
- Python 3.11 or newer
- Java 11 or newer for the TLA+ tools

## Run the coordinator

```sh
cargo run --manifest-path services/coordinator/Cargo.toml -- --bind 127.0.0.1:8080
curl http://127.0.0.1:8080/health
```

Add `--db scheduler.sqlite3` to persist state across coordinator restarts. To run a worker for one acquisition/completion cycle:

```sh
cargo run --manifest-path services/worker/Cargo.toml -- \
  --coordinator 127.0.0.1:8080 --id worker-1 --once
```

Expected response:

```json
{"status":"ok"}
```

## Run checks from a fresh checkout

```sh
cargo fmt --all -- --check
cargo test --workspace
scripts/check-spec.sh
scripts/check-spec.sh liveness
scripts/check-spec.sh stale-mutation

python3 -m venv .venv
.venv/bin/python -m pip install --requirement verification/python/requirements-dev.lock
scripts/test-system.sh
```

The black-box tests start the coordinator in test mode on an OS-assigned loopback port and always terminate that process. Each test therefore receives isolated server state. Its coordinator logs, client-observed HTTP transcript, and JUnit report are written to `artifacts/test-runs/latest/`; each run replaces the previous one. Test/debug routes require the explicit `--test-mode` flag.

The newline-delimited `http-transcript.jsonl` records each request method and path plus the response status and JSON body. It intentionally omits the ephemeral server address.

To preserve a named run instead, set `TEST_RUN_NAME`:

```sh
TEST_RUN_NAME=phase-0-baseline scripts/test-system.sh
```

## Current scope

Start with [`docs/context.md`](docs/context.md) for a concise project map. The normative protocol is in [`docs/protocol.md`](docs/protocol.md), and requirement-to-evidence mappings are in [`docs/traceability.md`](docs/traceability.md). The Phase 1 model checks S1-S5 over a finite safety configuration and L1 over a smaller configuration with explicit fairness assumptions. The stale mutation command succeeds only when TLC finds the expected S2 violation. The script downloads TLA+ Tools 1.7.4 and verifies its published checksum.

The coordinator supports optional SQLite persistence, a replaceable worker process, restart recovery tests, and bounded competing-acquisition scenarios. The Hypothesis state machine compares the running service with an independent Python reference model after every generated command. Failure injection and state inspection are gated by `--test-mode`. Multi-coordinator behavior and general linearizability checking remain future work. The original project guide is in [`verified_scheduler_project.md`](verified_scheduler_project.md).
