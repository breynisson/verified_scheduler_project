import os
import pathlib

import pytest

from scheduler_verification.http_adapter import CoordinatorProcess
from scheduler_verification.reference_model import ReferenceScheduler
from scheduler_verification.traces import load_trace, replay


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT_DIR = REPOSITORY_ROOT / "artifacts/test-runs/latest"
STALE_TOKEN_TRACE = pathlib.Path(__file__).parent / "regressions/stale-token.json"


def test_minimized_stale_token_regression_replays_against_correct_service():
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    commands = load_trace(STALE_TOKEN_TRACE)
    with CoordinatorProcess(artifact_dir, "regression-correct") as process:
        replay(process.adapter(), ReferenceScheduler(), commands)


def test_minimized_stale_token_regression_detects_intentional_fault():
    artifact_dir = pathlib.Path(os.environ.get("TEST_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    commands = load_trace(STALE_TOKEN_TRACE)
    with CoordinatorProcess(artifact_dir, "regression-faulty", fault=True) as process:
        with pytest.raises(AssertionError):
            replay(process.adapter(), ReferenceScheduler(), commands)
