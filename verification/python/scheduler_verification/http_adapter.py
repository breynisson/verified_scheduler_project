import json
import pathlib
import subprocess
import time
import urllib.error
import urllib.request

from .adapter import Outcome


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
COORDINATOR_MANIFEST = REPOSITORY_ROOT / "services" / "coordinator" / "Cargo.toml"


class CoordinatorProcess:
    def __init__(
        self,
        artifact_dir: pathlib.Path,
        name: str,
        fault: bool = False,
        database_path: pathlib.Path | None = None,
    ):
        self.artifact_dir = artifact_dir
        self.name = name
        self.fault = fault
        self.database_path = database_path
        self.process = None
        self.url = None

    def __enter__(self):
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = self.artifact_dir / f"{self.name}.stdout.log"
        stderr_path = self.artifact_dir / f"{self.name}.stderr.log"
        self.stdout_log = stdout_path.open("w")
        self.stderr_log = stderr_path.open("w")
        command = [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(COORDINATOR_MANIFEST),
            "--",
            "--bind",
            "127.0.0.1:0",
            "--test-mode",
        ]
        if self.fault:
            command.append("--fault-accept-stale-token")
        if self.database_path is not None:
            command.extend(["--db", str(self.database_path)])
        self.process = subprocess.Popen(
            command,
            cwd=REPOSITORY_ROOT,
            stdout=self.stdout_log,
            stderr=self.stderr_log,
            text=True,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            lines = stdout_path.read_text().splitlines()
            if lines and lines[0].startswith("LISTENING "):
                self.url = f"http://{lines[0].removeprefix('LISTENING ')}"
                return self
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"coordinator exited before startup: {stderr_path.read_text()}"
                )
            time.sleep(0.02)
        raise RuntimeError("coordinator did not start within 30 seconds")

    def __exit__(self, *_):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.stdout_log.close()
        self.stderr_log.close()

    def adapter(self, transcript_path: pathlib.Path | None = None):
        return HttpAdapter(self.url, transcript_path)


class HttpAdapter:
    def __init__(self, base_url: str, transcript_path: pathlib.Path | None = None):
        self.base_url = base_url
        self.transcript_path = transcript_path

    def _request(self, method: str, path: str, body=None) -> Outcome:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            outcome = Outcome(
                response.status,
                None if response.status == 204 else json.load(response),
            )
        if self.transcript_path is not None:
            record = {
                "method": method,
                "path": path,
                "status": outcome.status,
                "response": outcome.body,
            }
            with self.transcript_path.open("a") as transcript:
                transcript.write(json.dumps(record, sort_keys=True) + "\n")
        return outcome

    def submit(self, job_id: str, payload: dict) -> Outcome:
        return self._request("POST", "/jobs", {"id": job_id, "payload": payload})

    def acquire(self, worker_id: str) -> Outcome:
        return self._request("POST", f"/workers/{worker_id}/acquire")

    def complete(self, worker_id: str, job_id: str, token: int) -> Outcome:
        return self._request(
            "POST",
            f"/jobs/{job_id}/complete",
            {"worker_id": worker_id, "token": token},
        )

    def crash(self, worker_id: str) -> Outcome:
        return self._request("POST", f"/test/workers/{worker_id}/crash")

    def advance_time(self, delta: int) -> Outcome:
        return self._request("POST", "/test/advance-time", {"delta": delta})

    def fail_next_persist(self) -> Outcome:
        return self._request("POST", "/test/fail-next-persist")

    def fail_next_persist_after_write(self) -> Outcome:
        return self._request("POST", "/test/fail-next-persist-after-write")

    def state(self) -> dict:
        outcome = self._request("GET", "/debug/state")
        assert outcome.status == 200
        return outcome.body
