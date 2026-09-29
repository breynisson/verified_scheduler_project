import json
import pathlib
import select
import subprocess
import urllib.request

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
COORDINATOR_MANIFEST = REPOSITORY_ROOT / "services" / "coordinator" / "Cargo.toml"


@pytest.fixture
def coordinator_url():
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([process.stdout], [], [], 30)
        if not ready:
            raise RuntimeError("coordinator did not announce its address within 30 seconds")

        line = process.stdout.readline().strip()
        if not line.startswith("LISTENING "):
            stderr = process.stderr.read() if process.poll() is not None else ""
            raise RuntimeError(f"unexpected coordinator output: {line!r}; stderr: {stderr}")

        yield f"http://{line.removeprefix('LISTENING ')}"
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
