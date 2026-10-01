# Phase 4 persistence and concurrency policy

Status: accepted for the single-coordinator Phase 4 implementation.

- SQLite is the durable authority when the coordinator receives `--db PATH`. Without the option, it uses an in-memory SQLite database and preserves the isolated behavior of earlier tests.
- The database stores one versioned JSON scheduler snapshot. Every accepted mutation writes the complete snapshot and accepted-commit log in an explicit SQLite transaction before the HTTP response is sent. This favors an auditable atomic boundary over a normalized schema in the first persistence phase.
- Logical time, active leases, fencing-token counters, crash markers, and accepted commits survive restart. Coordinator downtime does not advance logical time, so an active lease remains active until an explicit clock advance reaches its expiry.
- A crash before the database commit cannot produce a successful response. A crash after commit but before response may leave the client uncertain whether the operation committed; clients must inspect or retry according to the normal protocol.
- Connections may be read concurrently, but scheduler transitions are serialized by one process-local mutex. SQLite and the in-memory snapshot are updated while that mutex is held.
- Concurrent tests record invocation and response times and check specific outcomes. They do not claim a general linearizability proof.
- The Phase 4 worker is a replaceable client of the existing HTTP API. It does not access SQLite directly and does not weaken coordinator fencing.
- Deterministic fail-next-save controls are exposed only through the coordinator's test mode. One fails before opening a transaction; the other fails after the transactional write but before commit. Together they verify that failure returns `500`, rolls back the in-memory transition, and leaves restart state unchanged.
- Worker pause and start-gate controls require the worker's explicit test mode; normal worker operation cannot activate them.

## Deferred verification

A later phase should inject failure after SQLite commit but before the HTTP response and test the resulting ambiguous client outcome. That scenario requires an explicit retry or idempotency policy: unlike the pre-commit failures covered here, the mutation is durable even though the client did not observe success.
