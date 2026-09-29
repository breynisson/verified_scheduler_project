# Verified Scheduler

A learning project for specifying a leased-job protocol in TLA+, implementing it in Rust, and testing the running system from Python with Hypothesis. Phase 0 contains only the contract, workspace scaffolding, and a coordinator health endpoint. Scheduler transitions are deliberately not implemented yet.

## Prerequisites

- Rust 1.98.1 with Cargo (installed automatically by `rustup` from `rust-toolchain.toml`)
- Python 3.11 or newer

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

python3 -m venv .venv
.venv/bin/python -m pip install --requirement verification/python/requirements-dev.lock
.venv/bin/python -m pytest verification/python/tests/test_health.py
```

The smoke test starts the coordinator on an OS-assigned loopback port and always terminates that process. Each test therefore receives isolated server state.

## Current scope

The future protocol and explicit Phase 0 decisions are in [`docs/protocol.md`](docs/protocol.md). The original project guide remains in [`verified_scheduler_project.md`](verified_scheduler_project.md). TLA+, scheduler transitions, worker behavior, test/debug routes, and stateful Hypothesis tests begin in later phases.
