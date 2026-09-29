#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mode=${1:-safety}
tool_version=1.7.4
tool_sha1=bee4a54f3ee3d4afc347c3240ec2d9e93b075104
tool_dir="$repository_root/.tools/tla-$tool_version"
tool_jar="$tool_dir/tla2tools.jar"
output_dir="$repository_root/artifacts/tlc/latest"

if [ ! -f "$tool_jar" ]; then
    mkdir -p "$tool_dir"
    curl --fail --location --silent --show-error \
        "https://github.com/tlaplus/tlaplus/releases/download/v$tool_version/tla2tools.jar" \
        --output "$tool_jar"
fi

actual_sha1=$(shasum "$tool_jar" | awk '{print $1}')
if [ "$actual_sha1" != "$tool_sha1" ]; then
    echo "unexpected SHA-1 for $tool_jar" >&2
    exit 1
fi

if [ -n "${JAVA_BIN:-}" ]; then
    java_bin=$JAVA_BIN
elif java -version >/dev/null 2>&1; then
    java_bin=java
elif [ -x /opt/homebrew/opt/openjdk@21/bin/java ]; then
    java_bin=/opt/homebrew/opt/openjdk@21/bin/java
else
    echo "Java 11 or newer is required; set JAVA_BIN to its executable" >&2
    exit 1
fi

case "$mode" in
    safety)
        module=Scheduler.tla
        config=Scheduler.cfg
        log_name=Scheduler.log
        expected_violation=
        ;;
    stale-mutation)
        module=SchedulerStaleCompletion.tla
        config=SchedulerStaleCompletion.cfg
        log_name=SchedulerStaleCompletion.log
        expected_violation="Invariant S2_FencedCompletion is violated"
        ;;
    liveness)
        module=Scheduler.tla
        config=SchedulerLiveness.cfg
        log_name=SchedulerLiveness.log
        expected_violation=
        ;;
    *)
        echo "usage: $0 [safety|liveness|stale-mutation]" >&2
        exit 2
        ;;
esac

mkdir -p "$output_dir"
log_file="$output_dir/$log_name"

cd "$repository_root/spec/tla"
set +e
"$java_bin" -XX:+UseParallelGC -jar "$tool_jar" \
    -cleanup -deadlock -workers 1 -config "$config" "$module" \
    >"$log_file" 2>&1
status=$?
set -e
cat "$log_file"

if [ -z "$expected_violation" ]; then
    exit "$status"
fi

if [ "$status" -ne 0 ] && grep -F "$expected_violation" "$log_file" >/dev/null; then
    echo "Observed expected mutation failure: $expected_violation"
    exit 0
fi

echo "Expected mutation failure was not observed" >&2
exit 1
