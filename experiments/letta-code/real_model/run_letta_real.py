"""Drive the real letta-code CLI (local backend) against a real model.

What it runs: the *published* bundle (`letta.js`, built from the pinned commit)
in headless mode, `--backend local`, with an OpenAI-compatible provider that
points at `ark_shim.py`. The shim forwards to Volcano Engine Ark and logs every
request body, so each scenario can check what actually entered the model's
context on every provider call.

Usage:
  ARK_API_KEY=... python3 run_letta_real.py --letta-js /path/to/letta.js --out results_run1.json

Everything happens in a throwaway HOME / LETTA_LOCAL_BACKEND_DIR; nothing
touches the operator's ~/.letta.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("ARK_MODEL", "deepseek-v4-1-flash-260910")
HANDLE = f"openai-compatible/{MODEL}"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class Env:
    def __init__(self, letta_js: str):
        self.letta_js = letta_js
        self.root = tempfile.mkdtemp(prefix="letta-real-")
        self.home = os.path.join(self.root, "home")
        self.store = os.path.join(self.root, "store")
        self.work = os.path.join(self.root, "work")
        os.makedirs(self.work)
        os.makedirs(self.home)
        subprocess.run(["git", "init", "-q"], cwd=self.work, check=True)
        with open(os.path.join(self.work, "notes.txt"), "w") as fh:
            fh.write("project notes\nmagic number: 4217\n")
        self.shim_log = os.path.join(self.root, "shim.jsonl")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.shim = subprocess.Popen(
            [sys.executable, "-I", os.path.join(HERE, "ark_shim.py")],
            env={**os.environ, "SHIM_PORT": str(self.port), "SHIM_LOG": self.shim_log, "ARK_MODEL": MODEL},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_") and k != "BUN_OPTIONS"}
        self.env.update({"HOME": self.home, "LETTA_LOCAL_BACKEND_DIR": self.store,
                         "LETTA_LOCAL_BACKEND_EXPERIMENTAL": "1", "LETTA_DISABLE_TELEMETRY": "1",
                         "DO_NOT_TRACK": "1", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        self.cli(["connect", "openai-compatible", "--base-url", f"http://127.0.0.1:{self.port}/v1",
                  "--api-key", "unused"])

    def cli(self, args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
        return subprocess.run(["node", self.letta_js, *args], cwd=self.work, env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def shim_rows(self) -> list[dict]:
        if not os.path.exists(self.shim_log):
            return []
        with open(self.shim_log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def turn(self, prompt: str, *flags: str) -> dict:
        before = len(self.shim_rows())
        started = time.time()
        proc = self.cli(["--backend", "local", "-m", HANDLE, "--yolo", "--output-format", "stream-json",
                         *flags, "-p", prompt])
        events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
        calls = self.shim_rows()[before:]
        init = next((e for e in events if e.get("type") == "system"), {})
        result = next((e for e in events if e.get("type") == "result"), {})
        return {"exit": proc.returncode, "seconds": round(time.time() - started, 1),
                "conversation_id": init.get("conversation_id"), "agent_id": init.get("agent_id"),
                "result": result.get("result"), "events": events, "calls": calls,
                "stderr_tail": [l for l in proc.stderr.splitlines() if l.strip()][-5:]}

    def close(self) -> None:
        self.shim.terminate()


def call_summary(call: dict) -> dict:
    msgs = call["request"]["messages"]
    system = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    return {"n_messages": len(msgs), "roles": [m["role"] for m in msgs], "system_sha": sha(system),
            "system_chars": len(system), "tool_names": [t["function"]["name"] for t in call["request"].get("tools", [])],
            "tools_chars": len(json.dumps(call["request"].get("tools", [])))}


def tool_calls(turn: dict) -> list[dict]:
    return [e["tool_call"] for e in turn["events"] if e.get("message_type") == "tool_call_message"]


def contains(call: dict, needle: str, where: str) -> bool:
    msgs = call["request"]["messages"]
    if where == "system":
        return needle in msgs[0]["content"]
    return needle in json.dumps(msgs[1:], ensure_ascii=False)


# -- scenarios -------------------------------------------------------------------
def s1_tool_loop(env: Env) -> dict:
    t = env.turn("Read notes.txt, then create answer.txt containing only the magic number. Reply with the number.",
                 "--new-agent")
    answer = open(os.path.join(env.work, "answer.txt")).read().strip() if os.path.exists(
        os.path.join(env.work, "answer.txt")) else None
    stops = [e["stop_reason"] for e in t["events"] if e.get("message_type") == "stop_reason"]
    calls = [call_summary(c) for c in t["calls"]]
    first_user = t["calls"][0]["request"]["messages"][1]["content"] if t["calls"] else []
    parts = first_user if isinstance(first_user, list) else [{"text": first_user}]
    checks = {
        "answer_file_is_4217": answer == "4217",
        "each_tool_step_is_a_separate_run_ending_requires_approval": stops[:-1] and all(s == "requires_approval" for s in stops[:-1]) and stops[-1] == "end_turn",
        "system_prompt_byte_identical_across_calls": len({c["system_sha"] for c in calls}) == 1,
        "tool_schemas_sent_on_every_call": all(c["tool_names"] == calls[0]["tool_names"] for c in calls),
        "volatile_context_in_user_message_reminders": sum("<system-reminder>" in p.get("text", "") for p in parts) >= 2,
    }
    env.agent_id = t["agent_id"]
    return {"checks": checks, "stop_reasons": stops, "provider_calls": calls,
            "tool_calls": [c["name"] for c in tool_calls(t)], "result": t["result"], "seconds": t["seconds"],
            "first_user_message_parts": [p.get("text", "")[:160] for p in parts]}


def s2_memory(env: Env) -> dict:
    t1 = env.turn("Please remember this permanently in your memory: my favorite color is teal. "
                  "Update your memory files now and commit them.", "--agent", env.agent_id, "--new")
    memdir = os.path.join(env.store, "memfs", env.agent_id, "memory")
    log = subprocess.run(["git", "log", "--oneline"], cwd=memdir, capture_output=True, text=True).stdout
    committed = subprocess.run(["git", "grep", "-il", "teal", "HEAD"], cwd=memdir, capture_output=True, text=True).stdout
    t1_calls = [{"system_sha": call_summary(c)["system_sha"], "teal_in_system": contains(c, "teal", "system"),
                 "memory_update_in_system": contains(c, "<memory_update>", "system"),
                 "memory_update_in_messages": contains(c, "<memory_update>", "messages")} for c in t1["calls"]]
    t2 = env.turn("Run `date` in the shell, then tell me: what is my favorite color?", "-C", t1["conversation_id"])
    t2_calls = [{"system_sha": call_summary(c)["system_sha"], "teal_in_system": contains(c, "teal", "system"),
                 "memory_update_anywhere": contains(c, "<memory_update>", "system") or contains(c, "<memory_update>", "messages"),
                 "teal_in_transcript": contains(c, "teal", "messages")} for c in t2["calls"]]
    t3 = env.turn("Without using any tools: what is my favorite color? If you don't know, say UNKNOWN.",
                  "--agent", env.agent_id, "--new")
    t3_calls = [{"teal_in_system": contains(c, "teal", "system")} for c in t3["calls"]]
    checks = {
        "agent_committed_the_fact_to_memfs": bool(committed.strip()),
        "a_memory_update_reached_the_model_in_turn1": any(c["memory_update_in_system"] or c["memory_update_in_messages"] for c in t1_calls),
        "same_conversation_later_calls_do_not_carry_the_memory": bool(t2_calls) and not any(c["teal_in_system"] or c["memory_update_anywhere"] for c in t2_calls),
        "new_conversation_prompt_contains_the_memory": bool(t3_calls) and all(c["teal_in_system"] for c in t3_calls),
        "new_conversation_answers_teal_without_tools": "teal" in (t3["result"] or "").lower() and not tool_calls(t3),
    }
    return {"checks": checks, "memfs_log": log.strip().splitlines(), "turn1_calls": t1_calls,
            "turn1_tools": [c["name"] for c in tool_calls(t1)], "turn2_calls": t2_calls,
            "turn2_tools": [c["name"] for c in tool_calls(t2)], "turn2_result": t2["result"],
            "turn3_result": t3["result"], "turn3_tools": [c["name"] for c in tool_calls(t3)]}


SUBAGENT_PROMPT = ("Use the Agent tool to launch a general-purpose subagent with this exact task: "
                   "'Count the lines in notes.txt in the current directory using the shell and report the number.' "
                   "Wait for its report, then tell me the number it reported.")


def _child_view(calls: list[dict]) -> dict:
    parent_sha = call_summary(calls[0])["system_sha"] if calls else None
    child = [c for c in calls if call_summary(c)["system_sha"] != parent_sha]
    child_first = child[0]["request"]["messages"] if child else []
    first_user = json.dumps(child_first[1]["content"], ensure_ascii=False) if len(child_first) > 1 else ""
    return {"parent_sha": parent_sha, "child": child, "child_first": child_first, "first_user": first_user}


def s3a_subagent_one_shot(env: Env) -> dict:
    """One-shot `-p`: a depth-0 Agent call returns at once (background task)."""
    t = env.turn(SUBAGENT_PROMPT, "--agent", env.agent_id, "--new")
    v = _child_view(t["calls"])
    checks = {
        "parent_called_Agent": any(c["name"] == "Agent" for c in tool_calls(t)),
        "child_ran_as_a_separate_agent_with_a_different_system_prompt": bool(v["child"]),
        "child_first_request_is_only_the_brief": len(v["child_first"]) == 2 and "notes.txt" in v["first_user"],
        "child_got_no_shared_environment_reminders": "Device Information" not in v["first_user"],
        "child_has_no_parent_memory_block": bool(v["child"]) and "<human>" not in v["child_first"][0]["content"],
        "(observed) one-shot parent ends its turn before the report arrives": "2" not in (t["result"] or ""),
    }
    agent_results = [e for e in t["events"] if e.get("message_type") == "tool_return_message"]
    return {"checks": checks, "parent_tools": [c["name"] for c in tool_calls(t)],
            "agent_tool_return": str(agent_results[0].get("tool_return"))[:400] if agent_results else None,
            "child_calls_logged": len(v["child"]), "child_tool_names": call_summary(v["child"][0])["tool_names"] if v["child"] else [],
            "child_first_user_message": v["first_user"][:500],
            "result": t["result"], "seconds": t["seconds"]}


def s3b_subagent_bidirectional(env: Env) -> dict:
    """Bidirectional stream-json (the SDK/Desktop host): the report comes back as a later turn."""
    before = len(env.shim_rows())
    proc = subprocess.Popen(["node", env.letta_js, "--backend", "local", "-m", HANDLE, "--yolo", "--agent", env.agent_id,
                             "--new", "--input-format", "stream-json", "--output-format", "stream-json"],
                            cwd=env.work, env=env.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": SUBAGENT_PROMPT}}) + "\n")
    proc.stdin.flush()
    events, results, deadline = [], [], time.time() + 420
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        events.append(event)
        if event.get("type") == "result":
            results.append(event)
            if len(results) >= 2:
                break
    proc.stdin.close()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    calls = env.shim_rows()[before:]
    v = _child_view(calls)
    parent_calls = [c for c in calls if call_summary(c)["system_sha"] == v["parent_sha"]]
    notification_seen = any("task-notification" in json.dumps(c["request"]["messages"][1:]) for c in parent_calls)
    final = results[-1].get("result") if results else None
    checks = {
        "two_turns_first_launch_then_notification": len(results) >= 2,
        "parent_context_received_a_task_notification": notification_seen,
        "child_context_never_enters_parent_context": bool(v["child"]) and not any(
            v["child_first"][0]["content"][:300] in json.dumps(c["request"]["messages"], ensure_ascii=False)
            for c in parent_calls),
        "final_answer_mentions_2": "2" in (final or ""),
    }
    return {"checks": checks, "turn_results": [str(r.get("result"))[:200] for r in results],
            "child_calls_logged": len(v["child"]), "parent_calls": len(parent_calls)}


def _write_big_files(env: Env) -> None:
    for name, seed in (("big1.txt", "alpha"), ("big2.txt", "omega")):
        with open(os.path.join(env.work, name), "w") as fh:
            fh.write("".join(f"{seed} filler line {i:05d} " + "x" * 60 + "\n" for i in range(400)))
            fh.write(f"LAST LINE OF {name}: {seed.upper()}-END\n")


def _limit_window(env: Env, conv: str, tokens: int) -> int:
    """Test setup: `letta model set --model-settings` writes only model_settings, while the
    conversation's top-level context_window_limit takes precedence (fake-headless-backend.ts:146-149).
    Write both fields - the equivalent of the TUI's /context-limit."""
    setp = env.cli(["model", "set", "--conversation", conv, "--model-settings",
                    json.dumps({"context_window_limit": tokens})])
    for d in os.listdir(os.path.join(env.store, "conversations")):
        path = os.path.join(env.store, "conversations", d, "conversation.json")
        if os.path.exists(path):
            with open(path) as fh:
                record = json.load(fh)
            if record.get("id") == conv:
                record["context_window_limit"] = tokens
                record.setdefault("model_settings", {})["context_window_limit"] = tokens
                with open(path, "w") as fh:
                    json.dump(record, fh, indent=2)
    return setp.returncode


def _transcript_rows(env: Env, conv: str) -> list[dict]:
    for d in os.listdir(os.path.join(env.store, "conversations")):
        base = os.path.join(env.store, "conversations", d)
        meta = os.path.join(base, "conversation.json")
        rows = os.path.join(base, "messages.jsonl")
        if os.path.exists(meta) and os.path.exists(rows) and json.load(open(meta)).get("id") == conv:
            with open(rows) as fh:
                return [json.loads(line) for line in fh if line.strip()]
    return []


def _call_rows(turn: dict) -> list[dict]:
    out = []
    for c in turn["calls"]:
        msgs = c["request"]["messages"]
        raw = c.get("response_raw", "")
        finish = [part.split('"')[0] for part in raw.split('"finish_reason":"')[1:]]
        tokens = [int(part.split(",")[0].split("}")[0]) for part in raw.split('"prompt_tokens":')[1:]]
        out.append({"n_messages": len(msgs), "system_sha": call_summary(c)["system_sha"],
                    "first_is_system_alert": len(msgs) > 1 and "system_alert" in json.dumps(msgs[1]),
                    "is_summarizer_call": not c["request"].get("tools"),
                    "max_completion_tokens": c["request"].get("max_completion_tokens"),
                    "prompt_tokens": tokens[-1] if tokens else None, "finish": finish[-1] if finish else None})
    return out


def _stops(turn: dict) -> list[str]:
    return [e.get("stop_reason") for e in turn["events"] if e.get("message_type") == "stop_reason"]


def _compactions(turn: dict) -> list[dict]:
    return [e.get("event_data") for e in turn["events"]
            if e.get("message_type") == "event_message" and e.get("event_type") == "compaction"]


def s4a_compaction_multi_turn(env: Env) -> dict:
    """Realistic: reads land in separate turns, so the earlier read becomes evictable history."""
    _write_big_files(env)
    t0 = env.turn("Reply with just OK.", "--agent", env.agent_id, "--new")
    conv = t0["conversation_id"]
    rc = _limit_window(env, conv, 45_000)
    ta = env.turn("Read big1.txt fully and tell me its last line.", "-C", conv)
    tb = env.turn("Now read big2.txt fully and tell me its last line.", "-C", conv)
    tc = env.turn("Without using any tools: what were the last lines of big1.txt and big2.txt?", "-C", conv)
    rows = _transcript_rows(env, conv)
    turns = {"A": ta, "B": tb, "C": tc}
    checks = {
        "window_set": rc == 0,
        "compaction_happened": any(_compactions(t) for t in turns.values()),
        "transcript_append_only_with_compaction_row": any(r["type"] == "compaction" for r in rows)
        and sum(r["type"] == "message" for r in rows) >= 8,
        "a_later_request_starts_with_system_alert_summary": any(c["first_is_system_alert"] for t in turns.values() for c in _call_rows(t)),
        "turn_C_answers_both_without_tools": all(x in (tc["result"] or "") for x in ("ALPHA-END", "OMEGA-END")) and not tool_calls(tc),
    }
    return {"checks": checks, "window": 45_000,
            "turns": {k: {"stops": _stops(t), "compactions": _compactions(t), "tools": [c["name"] for c in tool_calls(t)],
                          "calls": _call_rows(t), "result": (t["result"] or "")[:300]} for k, t in turns.items()},
            "transcript_row_types": [r["type"] for r in rows]}


def s4b_compaction_stress(env: Env) -> dict:
    """Edge case: both 30k-char reads in ONE step under a 40k window. The big results sit in the
    protected tail (after the last assistant message), so compaction cannot evict them."""
    _write_big_files(env)
    t0 = env.turn("Reply with just OK.", "--agent", env.agent_id, "--new")
    conv = t0["conversation_id"]
    rc = _limit_window(env, conv, 40_000)
    t = env.turn("Read big1.txt fully, then read big2.txt fully, then tell me the LAST LINE of each file.", "-C", conv)
    calls = _call_rows(t)
    return {"checks": {"window_set": rc == 0, "compaction_happened": bool(_compactions(t)),
                       "(observed) answer survives": all(x in (t["result"] or "") for x in ("ALPHA-END", "OMEGA-END"))},
            "stops": _stops(t), "compactions": _compactions(t), "calls": calls,
            "result": (t["result"] or "")[:300], "transcript_row_types": [r["type"] for r in _transcript_rows(env, conv)]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--letta-js", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    env = Env(args.letta_js)
    out: dict = {"model": MODEL, "letta_js_sha": sha(open(args.letta_js, encoding="utf-8", errors="replace").read()),
                 "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "scenarios": {}}
    try:
        for name, fn in (("S1_tool_loop", s1_tool_loop), ("S2_memory", s2_memory),
                         ("S3a_subagent_one_shot", s3a_subagent_one_shot),
                         ("S3b_subagent_bidirectional", s3b_subagent_bidirectional),
                         ("S4a_compaction_multi_turn", s4a_compaction_multi_turn),
                         ("S4b_compaction_stress", s4b_compaction_stress)):
            if args.only and name not in args.only:
                continue
            try:
                out["scenarios"][name] = fn(env)
            except Exception as err:  # noqa: BLE001 - record and continue
                out["scenarios"][name] = {"error": repr(err)}
            print(name, json.dumps(out["scenarios"][name].get("checks", out["scenarios"][name]), indent=None)[:600], flush=True)
    finally:
        env.close()
        out["provider_calls_total"] = len(env.shim_rows())
        out["workdir"] = "<temp>"
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
        print("wrote", args.out, "| temp root:", env.root)


if __name__ == "__main__":
    main()
