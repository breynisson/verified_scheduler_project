#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
run_name=${TEST_RUN_NAME:-latest}

case "$run_name" in
    ""|*[!A-Za-z0-9._-]*)
        echo "TEST_RUN_NAME may contain only letters, numbers, dot, underscore, and hyphen" >&2
        exit 2
        ;;
esac

artifact_dir="$repository_root/artifacts/test-runs/$run_name"
rm -rf "$artifact_dir"
mkdir -p "$artifact_dir"

export TEST_ARTIFACT_DIR="$artifact_dir"
export PYTHONPATH="$repository_root/verification/python${PYTHONPATH:+:$PYTHONPATH}"
exec "$repository_root/.venv/bin/python" -m pytest \
    "$repository_root/verification/python/tests" \
    --junitxml="$artifact_dir/pytest.xml"
