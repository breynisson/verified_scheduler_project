import json


def normalized(outcome):
    if outcome.status >= 400:
        return outcome.status, outcome.body["error"]["code"]
    return outcome.status, outcome.body


def execute(target, command):
    operation = command["operation"]
    if operation == "submit":
        return target.submit(command["job_id"], command["payload"])
    if operation == "acquire":
        return target.acquire(command["worker_id"])
    if operation == "complete":
        return target.complete(
            command["worker_id"], command["job_id"], command["token"]
        )
    if operation == "advance_time":
        return target.advance_time(command["delta"])
    if operation == "crash":
        return target.crash(command["worker_id"])
    raise ValueError(f"unknown operation: {operation}")


def save_trace(path, commands):
    path.write_text(json.dumps(commands, indent=2, sort_keys=True) + "\n")


def load_trace(path):
    return json.loads(path.read_text())


def replay(adapter, model, commands):
    for command in commands:
        actual = execute(adapter, command)
        expected = execute(model, command)
        assert normalized(actual) == normalized(expected)
        assert adapter.state() == model.state()
