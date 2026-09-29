import os
import pathlib

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, precondition, rule, run_state_machine_as_test

from scheduler_verification.http_adapter import CoordinatorProcess
from scheduler_verification.reference_model import ReferenceScheduler
from scheduler_verification.traces import execute, normalized, save_trace


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT_DIR = REPOSITORY_ROOT / "artifacts/test-runs/latest"
CHECKED_IN_TRACE = pathlib.Path(__file__).parent / "regressions/stale-token.json"


def test_state_machine_finds_and_shrinks_intentional_stale_token_fault():
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    discovered_path = artifact_dir / "minimized-stale-token.json"

    class FaultDiscoveryMachine(RuleBasedStateMachine):
        def __init__(self):
            super().__init__()
            self.process = CoordinatorProcess(artifact_dir, "mutation", fault=True)
            self.process.__enter__()
            self.adapter = self.process.adapter()
            self.model = ReferenceScheduler()
            self.commands = []
            self.phase = 0

        def teardown(self):
            self.process.__exit__(None, None, None)

        def apply(self, command):
            self.commands.append(command)
            actual = execute(self.adapter, command)
            expected = execute(self.model, command)
            if normalized(actual) != normalized(expected):
                save_trace(discovered_path, self.commands)
            assert normalized(actual) == normalized(expected)
            assert self.adapter.state() == self.model.state()

        @precondition(lambda self: self.phase == 0)
        @rule()
        def submit(self):
            self.apply(
                {"operation": "submit", "job_id": "job-a", "payload": {}}
            )
            self.phase = 1

        @precondition(lambda self: self.phase == 1)
        @rule()
        def first_acquire(self):
            self.apply({"operation": "acquire", "worker_id": "worker-1"})
            self.phase = 2

        @precondition(lambda self: self.phase == 2)
        @rule()
        def expire(self):
            self.apply({"operation": "advance_time", "delta": 1})
            self.phase = 3

        @precondition(lambda self: self.phase == 3)
        @rule()
        def second_acquire(self):
            self.apply({"operation": "acquire", "worker_id": "worker-2"})
            self.phase = 4

        @precondition(lambda self: self.phase == 4)
        @rule(token=st.integers(min_value=1, max_value=4).filter(lambda token: token != 2))
        def stale_complete(self, token):
            self.apply(
                {
                    "operation": "complete",
                    "worker_id": "worker-2",
                    "job_id": "job-a",
                    "token": token,
                }
            )

    with pytest.raises(AssertionError):
        run_state_machine_as_test(
            FaultDiscoveryMachine,
            settings=settings(max_examples=5, stateful_step_count=5, deadline=None),
        )

    assert discovered_path.read_text() == CHECKED_IN_TRACE.read_text()
