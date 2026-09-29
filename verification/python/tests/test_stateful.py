import itertools
import os
import pathlib

from hypothesis import HealthCheck, settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from scheduler_verification.http_adapter import CoordinatorProcess
from scheduler_verification.reference_model import MAX_ATTEMPTS, ReferenceScheduler
from scheduler_verification.strategies import DELTAS, JOB_IDS, TOKENS, WORKER_IDS
from scheduler_verification.traces import execute, normalized


DEFAULT_ARTIFACT_DIR = pathlib.Path(__file__).resolve().parents[3] / "artifacts/test-runs/latest"
MACHINE_IDS = itertools.count()


class SchedulerStateMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        machine_id = next(MACHINE_IDS)
        self.process = CoordinatorProcess(artifact_dir, f"stateful-{machine_id}")
        self.process.__enter__()
        self.adapter = self.process.adapter(artifact_dir / "stateful-transcript.jsonl")
        self.model = ReferenceScheduler()
        self.last_state = self.adapter.state()
        self.terminal_jobs = set()

    def teardown(self):
        self.process.__exit__(None, None, None)

    def apply(self, command):
        actual = execute(self.adapter, command)
        expected = execute(self.model, command)
        assert normalized(actual) == normalized(expected)
        self.last_state = self.adapter.state()
        assert self.last_state == self.model.state()

    @rule(job_id=JOB_IDS)
    def submit(self, job_id):
        self.apply(
            {"operation": "submit", "job_id": job_id, "payload": {"job": job_id}}
        )

    @rule(worker_id=WORKER_IDS)
    def acquire(self, worker_id):
        self.apply({"operation": "acquire", "worker_id": worker_id})

    @rule(worker_id=WORKER_IDS, job_id=JOB_IDS, token=TOKENS)
    def complete(self, worker_id, job_id, token):
        self.apply(
            {
                "operation": "complete",
                "worker_id": worker_id,
                "job_id": job_id,
                "token": token,
            }
        )

    @rule(delta=DELTAS)
    def advance_time(self, delta):
        self.apply({"operation": "advance_time", "delta": delta})

    @rule(worker_id=WORKER_IDS)
    def crash(self, worker_id):
        self.apply({"operation": "crash", "worker_id": worker_id})

    @invariant()
    def safety_properties_hold_in_observable_state(self):
        jobs = {job["id"]: job for job in self.last_state["jobs"]}
        commits = self.last_state["commits"]

        committed_jobs = [commit["job_id"] for commit in commits]
        assert len(committed_jobs) == len(set(committed_jobs))  # S1
        assert all(
            commit["accepted_at"] < commit["expiry"]
            and commit["token"] == jobs[commit["job_id"]]["attempts"]
            for commit in commits
        )  # S2

        current_terminal = {
            job_id
            for job_id, job in jobs.items()
            if job["status"] in {"completed", "failed"}
        }
        assert self.terminal_jobs <= current_terminal  # S3
        self.terminal_jobs = current_terminal

        for job in jobs.values():
            assert (job["status"] == "leased") == (job["lease"] is not None)  # S4
            assert job["attempts"] <= MAX_ATTEMPTS  # S5
            if job["status"] == "failed":
                assert job["attempts"] == MAX_ATTEMPTS


TestSchedulerStateMachine = SchedulerStateMachine.TestCase
TestSchedulerStateMachine.settings = settings(
    max_examples=20,
    stateful_step_count=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
