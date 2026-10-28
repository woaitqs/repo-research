"""Scriptable stand-ins for `claude`, `codex`, `grok` and `devin`, driven by control.json in their bin directory."""

import json
import os
import sys
import time

BIN, NAME, ARGS = sys.argv[1], sys.argv[2], sys.argv[3:]


def control():
    path = os.path.join(BIN, "control.json")
    return json.load(open(path)).get(NAME, {}) if os.path.exists(path) else {}


def log(entry):
    with open(os.path.join(BIN, "calls.jsonl"), "a") as f:
        f.write(json.dumps(entry) + "\n")


def count(key):
    path = os.path.join(BIN, f"{key}.count")
    n = int(open(path).read()) + 1 if os.path.exists(path) else 1
    open(path, "w").write(str(n))
    return n


ctl = control()
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
preflight = (NAME == "claude" and ARGS[:2] == ["auth", "status"]) or (NAME == "codex" and ARGS[:2] == ["login", "status"]) \
    or (NAME == "grok" and ARGS[:1] == ["models"]) or (NAME == "devin" and ARGS[:2] == ["auth", "status"])
log({"name": NAME, "phase": "preflight" if preflight else "model", "argv": ARGS, "stdin": stdin, "env": dict(os.environ),
     "ts": time.time(), "cwd": os.getcwd()})

if preflight:
    time.sleep(ctl.get("preflight_delay", 0))
    state = ctl.get("preflight", "ok")
    if state == "logged-out-once":
        state = "logged-out" if count("preflight") == 1 else "ok"
    if NAME == "claude":
        print(json.dumps({"loggedIn": state == "ok", "authMethod": "claude.ai"}, indent=2))  # multi-line, as the real CLI prints it
    elif NAME == "codex":
        print("Logged in using ChatGPT" if state == "ok" else "Not logged in")
    elif NAME == "grok":
        print("You are logged in" if state == "ok" else "Not logged in")
        print("Available models:")
        for model in ctl.get("models", ["grok-4.7"]):
            print(f"  {model}")
    elif NAME == "devin":
        print("Logged in (via Devin)." if state == "ok" else "Not logged in.")
    sys.exit(0 if state == "ok" else 1)

if ctl.get("started"):
    open(ctl["started"], "w").write(str(os.getpid()))
if ctl.get("partial"):
    sys.stdout.write(ctl["partial"])
    sys.stdout.flush()
time.sleep(ctl.get("delay", 0))
if ctl.get("stderr"):
    sys.stderr.write(ctl["stderr"])
if "raw" in ctl:
    sys.stdout.write(ctl["raw"])
    sys.exit(ctl.get("exit", 0))
if ctl.get("exit"):
    sys.exit(ctl["exit"])
text = ctl.get("text", "ANSWER")
if NAME == "claude":
    print(json.dumps({"type": "result", "is_error": False, "result": text, "session_id": "s", "total_cost_usd": 0.01,
                      "usage": {"input_tokens": 3, "output_tokens": 2}, "modelUsage": {ctl.get("reported", "claude-opus-5-5"): {}}}))
elif NAME == "codex":
    for event in ({"type": "thread.started", "thread_id": "t"}, {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
                  {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}}):
        print(json.dumps(event))
elif NAME == "grok":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                      "modelUsage": {ctl.get("reported", "grok-4.7-build"): {}}}))
elif NAME == "devin":
    print(json.dumps({"steps": [{"source": "agent", "message": text, "extra": {"generation_model": ctl.get("reported", "swe-2-max")}}]}))
