import os
import pathlib

from scheduler_verification.http_adapter import CoordinatorProcess


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT_DIR = REPOSITORY_ROOT / "artifacts/test-runs/latest"


def test_restart_preserves_lease_token_and_rejects_stale_worker(tmp_path):
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    database_path = tmp_path / "scheduler.sqlite3"

    with CoordinatorProcess(
        artifact_dir, "restart-before", database_path=database_path
    ) as process:
        adapter = process.adapter()
        assert adapter.submit("job-1", {"task": "example"}).status == 201
        first = adapter.acquire("worker-1")
        assert first.status == 200
        assert first.body["lease"]["token"] == 1
        assert adapter.crash("worker-3").status == 204
        before_restart = adapter.state()

    with CoordinatorProcess(
        artifact_dir, "restart-after", database_path=database_path
    ) as process:
        adapter = process.adapter()
        assert adapter.state() == before_restart
        assert adapter.advance_time(1).status == 200
        second = adapter.acquire("worker-2")
        assert second.status == 200
        assert second.body["lease"]["token"] == 2

        before_stale = adapter.state()
        stale = adapter.complete("worker-1", "job-1", 1)
        assert stale.status == 409
        assert stale.body["error"]["code"] == "lease_owner_mismatch"
        assert adapter.state() == before_stale

        completed = adapter.complete("worker-2", "job-1", 2)
        assert completed.status == 200
        assert completed.body["status"] == "completed"

    with CoordinatorProcess(
        artifact_dir, "restart-completed", database_path=database_path
    ) as process:
        state = process.adapter().state()
        assert state["jobs"][0]["status"] == "completed"
        assert state["jobs"][0]["attempts"] == 2
        assert state["crashed_workers"] == ["worker-3"]
        assert len(state["commits"]) == 1


def test_failed_persist_rolls_back_memory_and_durable_state(tmp_path):
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    database_path = tmp_path / "scheduler.sqlite3"

    with CoordinatorProcess(
        artifact_dir, "persist-failure-before", database_path=database_path
    ) as process:
        adapter = process.adapter()
        assert adapter.submit("job-1", {"task": "durable"}).status == 201
        before_failure = adapter.state()

        assert adapter.fail_next_persist().status == 204
        failed = adapter.submit("job-2", {"task": "must-roll-back"})
        assert failed.status == 500
        assert failed.body["error"]["code"] == "internal_error"
        assert adapter.state() == before_failure

    with CoordinatorProcess(
        artifact_dir, "persist-failure-after", database_path=database_path
    ) as process:
        assert process.adapter().state() == before_failure


def test_failed_persist_after_transactional_write_rolls_back_database(tmp_path):
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    database_path = tmp_path / "scheduler.sqlite3"

    with CoordinatorProcess(
        artifact_dir, "transaction-failure-before", database_path=database_path
    ) as process:
        adapter = process.adapter()
        assert adapter.submit("job-1", {"task": "durable"}).status == 201
        before_failure = adapter.state()

        assert adapter.fail_next_persist_after_write().status == 204
        failed = adapter.submit("job-2", {"task": "must-roll-back"})
        assert failed.status == 500
        assert failed.body["error"]["code"] == "internal_error"
        assert adapter.state() == before_failure

    with CoordinatorProcess(
        artifact_dir, "transaction-failure-after", database_path=database_path
    ) as process:
        assert process.adapter().state() == before_failure
