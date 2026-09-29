# Verified Scheduler

A learning project for specifying a leased-job protocol in TLA+, implementing it in Rust, and testing the running system from Python with Hypothesis. Phase 0 contains only the contract, workspace scaffolding, and a coordinator health endpoint. Scheduler transitions are deliberately not implemented yet.

## Prerequisites

- Rust 1.98.1 with Cargo (installed automatically by `rustup` from `rust-toolchain.toml`)
- Python 3.11 or newer
- Java 11 or newer for the TLA+ tools

## Run the coordinator

```sh
cargo run --manifest-path services/coordinator/Cargo.toml -- --bind 127.0.0.1:8080
curl http://127.0.0.1:8080/health
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

The smoke test starts the coordinator on an OS-assigned loopback port and always terminates that process. Each test therefore receives isolated server state. Its coordinator logs, client-observed HTTP transcript, and JUnit report are written to `artifacts/test-runs/latest/`; each run replaces the previous one.

The newline-delimited `http-transcript.jsonl` records each request method and path plus the response status and JSON body. It intentionally omits the ephemeral server address.

To preserve a named run instead, set `TEST_RUN_NAME`:

```sh
TEST_RUN_NAME=phase-0-baseline scripts/test-system.sh
```

## Current scope

The protocol is in [`docs/protocol.md`](docs/protocol.md), and requirement-to-evidence mappings are in [`docs/traceability.md`](docs/traceability.md). The Phase 1 model checks S1-S5 over a finite safety configuration and L1 over a smaller configuration with explicit fairness assumptions. The stale mutation command succeeds only when TLC finds the expected S2 violation. The script downloads TLA+ Tools 1.7.4 and verifies its published checksum.

Rust scheduler transitions, worker behavior, test/debug routes, and stateful Hypothesis tests remain future work. The original project guide is in [`verified_scheduler_project.md`](verified_scheduler_project.md).
