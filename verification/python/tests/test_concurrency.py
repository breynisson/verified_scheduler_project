import concurrent.futures
import json
import os
import pathlib
import threading
import time

from scheduler_verification.http_adapter import CoordinatorProcess


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT_DIR = REPOSITORY_ROOT / "artifacts/test-runs/latest"


def test_competing_acquisitions_issue_only_one_lease():
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    history_path = artifact_dir / "concurrent-acquisition.json"
    barrier = threading.Barrier(2)

    with CoordinatorProcess(artifact_dir, "concurrent-acquire") as process:
        adapter = process.adapter()
        assert adapter.submit("job-1", {}).status == 201

        def acquire(worker_id):
            worker_adapter = process.adapter()
            barrier.wait(timeout=5)
            invoked_at = time.monotonic_ns()
            outcome = worker_adapter.acquire(worker_id)
            responded_at = time.monotonic_ns()
            return {
                "worker_id": worker_id,
                "invoked_at": invoked_at,
                "responded_at": responded_at,
                "status": outcome.status,
                "response": outcome.body,
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            events = list(
                executor.map(acquire, ["worker-1", "worker-2"])
            )

        history_path.write_text(json.dumps(events, indent=2, sort_keys=True) + "\n")
        assert sorted(event["status"] for event in events) == [200, 204]
        assert max(event["invoked_at"] for event in events) < min(
            event["responded_at"] for event in events
        )

        state = adapter.state()
        assert state["jobs"][0]["status"] == "leased"
        assert state["jobs"][0]["attempts"] == 1
        assert state["jobs"][0]["lease"]["token"] == 1
