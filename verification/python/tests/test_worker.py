import os
import pathlib
import subprocess
import time

from scheduler_verification.http_adapter import CoordinatorProcess


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKER_MANIFEST = REPOSITORY_ROOT / "services" / "worker" / "Cargo.toml"
DEFAULT_ARTIFACT_DIR = REPOSITORY_ROOT / "artifacts/test-runs/latest"


def worker_command(address, worker_id, *extra):
    return [
        "cargo",
        "run",
        "--quiet",
        "--manifest-path",
        str(WORKER_MANIFEST),
        "--",
        "--coordinator",
        address,
        "--id",
        worker_id,
        *extra,
    ]


def worker_binary_command(address, worker_id, *extra):
    return [
        str(REPOSITORY_ROOT / "target" / "debug" / "worker"),
        "--coordinator",
        address,
        "--id",
        worker_id,
        *extra,
    ]


def test_worker_rejects_test_controls_without_test_mode():
    result = subprocess.run(
        worker_command(
            "127.0.0.1:1",
            "worker-1",
            "--once",
            "--test-pause-before-complete-ms",
            "1",
        ),
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "usage: worker" in result.stderr


def test_crashed_worker_lease_expires_and_replacement_completes():
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    worker_stdout = artifact_dir / "worker-crashed.stdout.log"
    worker_stderr = artifact_dir / "worker-crashed.stderr.log"

    with CoordinatorProcess(artifact_dir, "worker-coordinator") as process:
        adapter = process.adapter()
        assert adapter.submit("job-1", {"task": "example"}).status == 201
        address = process.url.removeprefix("http://")

        with worker_stdout.open("w") as stdout, worker_stderr.open("w") as stderr:
            crashed_worker = subprocess.Popen(
                worker_command(
                    address,
                    "worker-1",
                    "--once",
                    "--test-mode",
                    "--test-pause-before-complete-ms",
                    "30000",
                ),
                cwd=REPOSITORY_ROOT,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = adapter.state()
                if state["jobs"][0]["status"] == "leased":
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("worker did not acquire the job")
            crashed_worker.terminate()
            crashed_worker.wait(timeout=5)

        assert adapter.advance_time(1).status == 200
        replacement = subprocess.run(
            worker_command(address, "worker-2", "--once"),
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert replacement.returncode == 0, replacement.stderr

        state = adapter.state()
        assert state["jobs"][0]["status"] == "completed"
        assert state["jobs"][0]["attempts"] == 2
        assert state["commits"][0]["worker_id"] == "worker-2"
        assert state["commits"][0]["token"] == 2


def test_five_real_workers_compete_for_three_jobs(tmp_path):
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    gate = tmp_path / "start-workers"
    subprocess.run(
        ["cargo", "build", "--quiet", "--manifest-path", str(WORKER_MANIFEST)],
        cwd=REPOSITORY_ROOT,
        check=True,
        timeout=30,
    )

    with CoordinatorProcess(artifact_dir, "multi-worker-coordinator") as process:
        adapter = process.adapter()
        for job_id in ["job-a", "job-b", "job-c"]:
            assert adapter.submit(job_id, {"job": job_id}).status == 201

        address = process.url.removeprefix("http://")
        workers = [
            subprocess.Popen(
                worker_binary_command(
                    address,
                    f"worker-{number}",
                    "--once",
                    "--test-mode",
                    "--test-start-gate",
                    str(gate),
                ),
                cwd=REPOSITORY_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for number in range(1, 6)
        ]
        try:
            gate.write_text("")
            results = [worker.communicate(timeout=10) for worker in workers]
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.terminate()
                    worker.wait(timeout=5)

        assert all(worker.returncode == 0 for worker in workers), results
        stdout_lines = [line for stdout, _ in results for line in stdout.splitlines()]
        assert sum(line.startswith("COMPLETED ") for line in stdout_lines) == 3
        assert sum(line.startswith("NO_JOB ") for line in stdout_lines) == 2

        state = adapter.state()
        assert [job["status"] for job in state["jobs"]] == [
            "completed",
            "completed",
            "completed",
        ]
        assert [job["attempts"] for job in state["jobs"]] == [1, 1, 1]
        assert len(state["commits"]) == 3
        assert len({commit["job_id"] for commit in state["commits"]}) == 3
        assert len({commit["worker_id"] for commit in state["commits"]}) == 3
