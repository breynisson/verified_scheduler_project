import json
import os
import pathlib
import subprocess
import time
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


@pytest.fixture
def coordinator_url():
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    artifact_dir.mkdir(parents=True, exist_ok=True)
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


def test_health(coordinator_url):
    with urllib.request.urlopen(f"{coordinator_url}/health", timeout=5) as response:
        assert response.status == 200
        assert json.load(response) == {"status": "ok"}
