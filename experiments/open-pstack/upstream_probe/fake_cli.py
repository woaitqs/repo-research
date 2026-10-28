"""A scriptable stand-in for the provider CLIs that pstack-runner launches.

The probe installs one wrapper per name (`claude`, `codex`, `grok`, `devin`) in a
private bin directory. Every wrapper runs `python3 fake_cli.py <bin dir> <name> <args>`. The
behaviour comes from `control.json` next to the wrappers, not from the environment,
because the runner filters the environment differently per provider. Every call is
appended to `calls.jsonl`: argv, cwd, stdin and the environment the child received.
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = sys.argv[1]  # the bin directory that holds the wrappers, control.json and calls.jsonl


def _control(name: str) -> dict:
    path = os.path.join(HERE, "control.json")
    try:
        with open(path) as f:
            return json.load(f).get(name, {})
    except FileNotFoundError:
        return {}


def _log(entry: dict) -> None:
    with open(os.path.join(HERE, "calls.jsonl"), "a") as f:
        f.write(json.dumps(entry) + "\n")


def _bump(counter: str) -> int:
    path = os.path.join(HERE, f"{counter}.count")
    n = int(open(path).read()) + 1 if os.path.exists(path) else 1
    with open(path, "w") as f:
        f.write(str(n))
    return n


def _sleep_ms(value) -> None:
    if value:
        time.sleep(float(value) / 1000)


def _model_phase(name: str, ctl: dict, argv: list[str]) -> int:
    marker = ctl.get("started_marker")
    if marker:
        with open(marker, "w") as f:
            f.write(str(os.getpid()))
    if ctl.get("partial_stdout"):
        sys.stdout.write(ctl["partial_stdout"])
        sys.stdout.flush()
    _sleep_ms(ctl.get("model_delay_ms"))
    if ctl.get("model_stderr"):
        sys.stderr.write(ctl["model_stderr"])
    if "model_stdout_raw" in ctl:
        sys.stdout.write(ctl["model_stdout_raw"])
        return int(ctl.get("model_exit", 0))
    if int(ctl.get("model_exit", 0)) != 0:
        return int(ctl["model_exit"])
    text = ctl.get("text", "FAKE-ANSWER")
    if name == "claude":
        reported = ctl.get("reported_model", "claude-opus-5-5")
        sys.stdout.write(json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "result": text,
            "session_id": "fake-claude-session", "total_cost_usd": 0.0123,
            "usage": {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 3},
            "modelUsage": {reported: {"inputTokens": 11}},
        }))
    elif name == "codex":
        for event in (
            {"type": "thread.started", "thread_id": "fake-codex-thread"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            {"type": "turn.completed", "usage": {"input_tokens": 5, "cached_input_tokens": 1, "output_tokens": 2}},
        ):
            sys.stdout.write(json.dumps(event) + "\n")
    elif name == "grok":
        reported = ctl.get("reported_model", "grok-4.7-build")
        sys.stdout.write(json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "result": text,
            "session_id": "fake-grok-session", "usage": {"input_tokens": 9, "output_tokens": 4},
            "modelUsage": {reported: {}},
        }) + "\n")
    return 0


def _preflight(name: str, ctl: dict, argv: list[str]) -> int:
    _sleep_ms(ctl.get("preflight_delay_ms"))
    state = ctl.get("preflight", "ok")
    if state == "logged-out-once":
        state = "logged-out" if _bump(f"{name}-preflight") == 1 else "ok"
    if name == "claude":
        print(json.dumps({"loggedIn": state == "ok"}))
        return 0 if state == "ok" else 1
    if name == "codex":
        print("Logged in using ChatGPT" if state == "ok" else "Not logged in")
        return 0 if state == "ok" else 1
    if name == "grok":
        if state != "ok":
            print("Not logged in. Run grok login.")
            return 1
        models = ctl.get("models", ["grok-4.7", "grok-4.6"])
        print("You are logged in as probe@example.com")
        print(f"Default model: {ctl.get('default_model', 'grok-4.7')}")
        print("Available models:")
        for model in models:
            print(f"  {model}")
        return 0
    if name == "devin":
        print("Logged in (via Devin).")
        return 0
    return 0


def main() -> int:
    name, argv = sys.argv[2], sys.argv[3:]
    ctl = _control(name)
    stdin = sys.stdin.read() if not sys.stdin.isatty() else ""
    preflight = (
        (name == "claude" and argv[:2] == ["auth", "status"])
        or (name == "codex" and argv[:2] == ["login", "status"])
        or (name == "grok" and argv[:1] == ["models"])
        or (name == "devin" and argv[:2] == ["auth", "status"])
    )
    _log({"name": name, "phase": "preflight" if preflight else "model", "argv": argv,
          "cwd": os.getcwd(), "stdin": stdin, "env": dict(os.environ), "ts": time.time()})
    if preflight:
        return _preflight(name, ctl, argv)
    return _model_phase(name, ctl, argv)


if __name__ == "__main__":
    sys.exit(main())
