# Project working rules

1. Read `docs/protocol.md` and `docs/traceability.md` before changing scheduler behavior. If a phase has not created one of those files yet, note that explicitly.
2. Keep the operation vocabulary and expiry boundary consistent across TLA+, Rust, and Python.
3. Make one protocol change at a time. Add or update a counterexample or regression trace when fixing a discovered bug.
4. Run the relevant TLC, Rust, and black-box checks and report their commands and results. Never report a property as proved from passing tests.
5. Treat AI-generated invariants and tests as hypotheses; reject tautologies, unreachable premises, and properties that contradict the contract.
6. Keep failure injection and state introspection restricted to test mode.
7. Prefer small, reviewable changes and preserve public APIs unless a change is explicit.
8. Do not add production dependencies without approval. Update tests when behavior changes, and run the narrowest relevant check first.
