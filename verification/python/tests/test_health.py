import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
COORDINATOR_MANIFEST = REPOSITORY_ROOT / "services" / "coordinator" / "Cargo.toml"
DEFAULT_ARTIFACT_DIR = REPOSITORY_ROOT / "artifacts" / "test-runs" / "latest"


def wait_for_listening_address(process, stdout_path, stderr_path):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        lines = stdout_path.read_text().splitlines()
        if lines:
            line = lines[0]
            if line.startswith("LISTENING "):
                return line.removeprefix("LISTENING ")
            raise RuntimeError(f"unexpected coordinator output: {line!r}")
        if process.poll() is not None:
            raise RuntimeError(
                f"coordinator exited before startup; stderr: {stderr_path.read_text()}"
            )
        time.sleep(0.05)
    raise RuntimeError("coordinator did not announce its address within 30 seconds")


@pytest.fixture(scope="session")
def artifact_dir():
    path = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    path.mkdir(parents=True, exist_ok=True)
    (path / "http-transcript.jsonl").write_text("")
    return path


def record_http_exchange(artifact_dir, method, path, status, response):
    record = {
        "method": method,
        "path": path,
        "status": status,
        "response": response,
    }
    with (artifact_dir / "http-transcript.jsonl").open("a") as transcript:
        transcript.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


@pytest.fixture
def coordinator_url(artifact_dir):
    stdout_path = artifact_dir / "coordinator.stdout.log"
    stderr_path = artifact_dir / "coordinator.stderr.log"

    with stdout_path.open("w") as stdout_log, stderr_path.open("w") as stderr_log:
        process = subprocess.Popen(
            [
                "cargo",
                "run",
                "--quiet",
                "--manifest-path",
                str(COORDINATOR_MANIFEST),
                "--",
                "--bind",
                "127.0.0.1:0",
                "--test-mode",
            ],
            cwd=REPOSITORY_ROOT,
            stdout=stdout_log,
            stderr=stderr_log,
            text=True,
        )
        try:
            address = wait_for_listening_address(process, stdout_path, stderr_path)
            yield f"http://{address}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def production_coordinator_url(artifact_dir):
    stdout_path = artifact_dir / "production-coordinator.stdout.log"
    stderr_path = artifact_dir / "production-coordinator.stderr.log"

    with stdout_path.open("w") as stdout_log, stderr_path.open("w") as stderr_log:
        process = subprocess.Popen(
            [
                "cargo",
                "run",
                "--quiet",
                "--manifest-path",
                str(COORDINATOR_MANIFEST),
                "--",
                "--bind",
                "127.0.0.1:0",
            ],
            cwd=REPOSITORY_ROOT,
            stdout=stdout_log,
            stderr=stderr_log,
            text=True,
        )
        try:
            address = wait_for_listening_address(process, stdout_path, stderr_path)
            yield f"http://{address}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_health(coordinator_url, artifact_dir):
    with urllib.request.urlopen(f"{coordinator_url}/health", timeout=5) as response:
        status = response.status
        body = json.load(response)

    record_http_exchange(artifact_dir, "GET", "/health", status, body)
    assert status == 200
    assert body == {"status": "ok"}


def request_json(coordinator_url, artifact_dir, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{coordinator_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error

    with response:
        status = response.status
        response_body = json.load(response) if status != 204 else None
    record_http_exchange(artifact_dir, method, path, status, response_body)
    return status, response_body


def test_stale_completion_trace_is_rejected_without_mutation(
    coordinator_url, artifact_dir
):
    status, submitted = request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/jobs",
        {"id": "job-1", "payload": {"task": "example"}},
    )
    assert status == 201
    assert submitted["status"] == "pending"

    status, first_lease = request_json(
        coordinator_url, artifact_dir, "POST", "/workers/worker-1/acquire"
    )
    assert status == 200
    assert first_lease["lease"] == {
        "worker_id": "worker-1",
        "token": 1,
        "expiry": 1,
    }

    status, advanced = request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/test/advance-time",
        {"delta": 1},
    )
    assert status == 200
    assert advanced == {
        "now": 1,
        "expired": [{"job_id": "job-1", "status": "pending"}],
    }

    status, second_lease = request_json(
        coordinator_url, artifact_dir, "POST", "/workers/worker-2/acquire"
    )
    assert status == 200
    assert second_lease["lease"] == {
        "worker_id": "worker-2",
        "token": 2,
        "expiry": 2,
    }

    status, before = request_json(
        coordinator_url, artifact_dir, "GET", "/debug/state"
    )
    assert status == 200

    status, rejected = request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/jobs/job-1/complete",
        {"worker_id": "worker-1", "token": 1},
    )
    assert status == 409
    assert rejected["error"]["code"] == "lease_owner_mismatch"

    status, after = request_json(
        coordinator_url, artifact_dir, "GET", "/debug/state"
    )
    assert status == 200
    assert after == before

    status, rejected = request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/jobs/job-1/complete",
        {"worker_id": "worker-2", "token": 1},
    )
    assert status == 409
    assert rejected["error"]["code"] == "stale_fencing_token"

    status, completed = request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/jobs/job-1/complete",
        {"worker_id": "worker-2", "token": 2},
    )
    assert status == 200
    assert completed["status"] == "completed"
    assert completed["lease"] is None


def test_debug_and_failure_injection_require_test_mode(
    production_coordinator_url, artifact_dir
):
    status, response = request_json(
        production_coordinator_url, artifact_dir, "GET", "/debug/state"
    )
    assert status == 503
    assert response["error"]["code"] == "test_mode_disabled"

    status, response = request_json(
        production_coordinator_url,
        artifact_dir,
        "POST",
        "/test/advance-time",
        {"delta": 1},
    )
    assert status == 503
    assert response["error"]["code"] == "test_mode_disabled"

    status, response = request_json(
        production_coordinator_url,
        artifact_dir,
        "POST",
        "/test/workers/worker-1/crash",
    )
    assert status == 503
    assert response["error"]["code"] == "test_mode_disabled"


def test_crashed_worker_cannot_complete_current_lease(coordinator_url, artifact_dir):
    request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/jobs",
        {"id": "job-1", "payload": {}},
    )
    _, leased = request_json(
        coordinator_url, artifact_dir, "POST", "/workers/worker-1/acquire"
    )

    status, response = request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/test/workers/worker-1/crash",
    )
    assert status == 204
    assert response is None

    status, response = request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/jobs/job-1/complete",
        {"worker_id": "worker-1", "token": leased["lease"]["token"]},
    )
    assert status == 409
    assert response["error"]["code"] == "worker_crashed"

    _, state = request_json(coordinator_url, artifact_dir, "GET", "/debug/state")
    assert state["jobs"][0]["status"] == "leased"
    assert state["crashed_workers"] == ["worker-1"]


def test_third_expiry_fails_job_permanently(coordinator_url, artifact_dir):
    request_json(
        coordinator_url,
        artifact_dir,
        "POST",
        "/jobs",
        {"id": "job-1", "payload": {}},
    )

    for expected_token in range(1, 4):
        status, leased = request_json(
            coordinator_url, artifact_dir, "POST", "/workers/worker-1/acquire"
        )
        assert status == 200
        assert leased["lease"]["token"] == expected_token
        status, _ = request_json(
            coordinator_url,
            artifact_dir,
            "POST",
            "/test/advance-time",
            {"delta": 1},
        )
        assert status == 200

    _, state = request_json(coordinator_url, artifact_dir, "GET", "/debug/state")
    assert state["jobs"][0]["status"] == "failed"
    assert state["jobs"][0]["attempts"] == 3

    status, response = request_json(
        coordinator_url, artifact_dir, "POST", "/workers/worker-2/acquire"
    )
    assert status == 204
    assert response is None
