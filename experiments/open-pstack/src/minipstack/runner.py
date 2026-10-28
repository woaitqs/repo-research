"""Run exactly one external model lane and leave a receipt, whatever happens.

Mirrors `runner/run.ts`. The policy lives here and nowhere else: exclusive private reservation of
the output and receipt, a child environment without the parent's identity markers, a preflight
with at most one retry and only after `unauthenticated`, exactly one model execution, one
absolute deadline across every phase, cancellation that reaches the live child, model proof
before an answer counts, and no fallback of any kind.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from .adapters import model_failure_status
from .registry import PARENTS, UsageError, resolve_lane

EXIT_CODES = {"complete": 0, "cancelled": 130, "malformed-output": 65, "unavailable-cli": 69, "unavailable-model": 69,
              "child-failed": 70, "unauthenticated": 77, "timed-out": 124}
EVIDENCE_LIMIT = 4000


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _evidence(text: str) -> str:
    return text.strip()[:EVIDENCE_LIMIT]


def child_environment(lane, source: dict | None = None) -> dict:
    env = lane.adapter.environment(lane.access, dict(os.environ if source is None else source))
    for key in PARENTS[lane.parent]["identity"]:
        env.pop(key, None)
    return env


def reserve(lane) -> tuple[int, int]:
    paths = [lane.output_path, lane.receipt_path, lane.receipt_path + ".stdout", lane.receipt_path + ".stderr"]
    resolved = [os.path.realpath(p) for p in paths]
    if len(set(resolved)) != len(paths) or os.path.realpath(lane.prompt_path) in resolved:
        raise UsageError("prompt, output, receipt, and stream paths must be distinct")
    created, fds = [], []
    try:
        for path in paths:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created.append(path)
            fds.append(fd)
        os.close(fds[0])
        os.close(fds[1])
        return fds[2], fds[3]
    except FileExistsError as error:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass
        for path in created:
            os.unlink(path)
        raise UsageError(f"refusing to overwrite an existing path: {error.filename}") from None


class Process:
    """One child process with captured streams, bounded by a deadline and a cancellation event."""

    def __init__(self, argv, cwd, env, stdin_text, deadline, cancel, sinks=(None, None)):
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.chunks = ([], [])
        self.readers = [threading.Thread(target=self._pump, args=(stream, i, sinks[i]), daemon=True)
                        for i, stream in enumerate((self.proc.stdout, self.proc.stderr))]
        for reader in self.readers:
            reader.start()
        if stdin_text is not None:
            try:
                self.proc.stdin.write(stdin_text.encode())
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        self.deadline, self.cancel = deadline, cancel
        self.outcome, self.signal_sent = self._wait()

    def _pump(self, stream, index, sink):
        for chunk in iter(lambda: stream.read1(65536), b""):
            self.chunks[index].append(chunk)
            if sink is not None:
                os.write(sink, chunk)

    def _wait(self) -> tuple[str, str | None]:
        while self.proc.poll() is None:
            if self.cancel.is_set():
                return "cancelled", self._terminate(signal.SIGTERM)
            if self.deadline is not None and time.time() >= self.deadline:
                return "timed-out", self._terminate(signal.SIGTERM)
            time.sleep(0.02)
        for reader in self.readers:
            while reader.is_alive():
                if self.cancel.is_set():
                    return "cancelled", None
                if self.deadline is not None and time.time() >= self.deadline:
                    return "timed-out", None
                reader.join(0.02)
        return "exited", None

    def _terminate(self, sig) -> str | None:
        if self.proc.poll() is not None:
            return None
        self.proc.send_signal(sig)
        try:
            self.proc.wait(1.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        return signal.Signals(sig).name

    @property
    def exit_code(self):
        return self.proc.returncode

    @property
    def stdout(self) -> str:
        return b"".join(self.chunks[0]).decode(errors="replace")

    @property
    def stderr(self) -> str:
        return b"".join(self.chunks[1]).decode(errors="replace")


def shared_git_dir(cwd: str, env: dict) -> tuple[str | None, str | None]:
    """The linked worktree's shared git directory to deny, or a refusal message."""
    git = shutil.which("git", path=env.get("PATH"))
    if git is None:
        return None, "git executable not found, so the shared git directory cannot be found"
    run = subprocess.run([git, "rev-parse", "--git-common-dir"], cwd=cwd, env={**env, "LC_ALL": "C"}, capture_output=True, text=True)
    if run.returncode == 128 and "not a git repository" in run.stderr:
        return None, None
    if run.returncode != 0 or not run.stdout.strip():
        return None, f"git rev-parse --git-common-dir exited with status {run.returncode}"
    root = os.path.realpath(cwd)
    shared = os.path.realpath(os.path.join(root, run.stdout.strip()))
    if shared == root or shared.startswith(root + os.sep):
        return None, None
    if root.startswith(shared + os.sep):
        return None, f"cwd {root} is inside its shared git directory {shared}, so the writer sandbox cannot deny it"
    return shared, None


def run_lane(lane, *, source_env: dict | None = None, cancel: threading.Event | None = None,
             started: float | None = None, retry_delay_s: float | None = None) -> tuple[int, dict]:
    started = time.time() if started is None else started
    cancel = cancel or threading.Event()
    deadline = None if lane.timeout_s is None else started + lane.timeout_s
    if not os.path.isfile(lane.prompt_path):
        raise UsageError(f"prompt is not a file: {lane.prompt_path}")
    if not os.path.isdir(lane.cwd):
        raise UsageError(f"cwd is not a directory: {lane.cwd}")
    out_fd, err_fd = reserve(lane)
    adapter = lane.adapter
    env = child_environment(lane, source_env)
    receipt = {"schemaVersion": 1, "parent": lane.parent, "provider": adapter.id, "model": lane.model, "effort": lane.effort,
               "mode": lane.access.mode, "writerSandbox": None if lane.access.mode == "read-only" else lane.access.sandbox,
               "cwd": lane.cwd, "promptPath": lane.prompt_path, "outputPath": lane.output_path,
               "stdoutPath": lane.receipt_path + ".stdout", "stderrPath": lane.receipt_path + ".stderr",
               "startedAt": _iso(started), "executable": None,
               "preflight": {"argv": [adapter.executable, *adapter.preflight_argv(lane)], "status": "not-run", "evidence": ""},
               "argv": [], "exitCode": None, "signal": None, "reportedModel": None, "modelVerified": False,
               "modelEvidence": None, "sessionId": None, "usage": None, "costUsd": None, "error": None}

    def finish(status: str, message: str | None = None, evidence: str = "") -> tuple[int, dict]:
        now = time.time()
        receipt.update(status=status, completedAt=_iso(now), elapsedMs=int((now - started) * 1000))
        if status != "complete":
            receipt["error"] = {"message": message or status, "evidence": _evidence(evidence)}
            if os.path.exists(lane.output_path):
                os.unlink(lane.output_path)
        with open(lane.receipt_path, "w") as f:
            json.dump(receipt, f, indent=2)
        os.chmod(lane.receipt_path, 0o600)
        return EXIT_CODES[status], receipt

    try:
        executable = shutil.which(adapter.executable, path=env.get("PATH"))
        receipt["executable"] = executable
        receipt["argv"] = [executable or adapter.executable, *adapter.argv(lane, None)]
        if cancel.is_set():
            return finish("cancelled", "launcher was cancelled before authentication preflight")
        if executable is None:
            return finish("unavailable-cli", f"{adapter.executable} executable not found")
        shared = None
        if adapter.needs_shared_git(lane.access):
            shared, refusal = shared_git_dir(lane.cwd, env)
            if refusal is not None:
                return finish("child-failed", refusal)
        launch_argv = adapter.argv(lane, shared)
        receipt["argv"] = [executable, *launch_argv]

        def preflight() -> tuple[Process, str]:
            run = Process([executable, *adapter.preflight_argv(lane)], lane.cwd, env, None, deadline, cancel)
            return run, adapter.classify(run.exit_code or 0, f"{run.stdout}\n{run.stderr}", lane)

        run, verdict = preflight()
        evidence = _evidence(f"{run.stdout}\n{run.stderr}")
        if run.outcome == "exited" and verdict == "unauthenticated" and adapter.retry_after_unauthenticated_s is not None:
            delay = adapter.retry_after_unauthenticated_s if retry_delay_s is None else retry_delay_s
            wake = time.time() + delay
            while time.time() < wake and not cancel.is_set() and (deadline is None or time.time() < deadline):
                time.sleep(0.02)
            if cancel.is_set():
                receipt["preflight"].update(status="cancelled", evidence=evidence)
                return finish("cancelled", "launcher was cancelled during the preflight retry delay")
            if deadline is not None and time.time() >= deadline:
                receipt["preflight"].update(status="timed-out", evidence=evidence)
                return finish("timed-out", "explicit deadline elapsed during the preflight retry delay")
            first = evidence
            run, verdict = preflight()
            passed = run.outcome == "exited" and verdict == "passed"
            evidence = f"attempt 1 failed:\n{first}\n\nattempt 2 {'passed' if passed else 'failed'}:\n{_evidence(run.stdout + run.stderr)}"
        if run.outcome != "exited":
            receipt["preflight"].update(status=run.outcome, evidence=evidence)
            return finish(run.outcome, f"preflight ended: {run.outcome}")
        receipt["preflight"].update(status="passed" if verdict == "passed" else "failed", evidence=evidence)
        if verdict != "passed":
            status = {"unavailable-model": "unavailable-model", "inconclusive": "child-failed"}.get(verdict, "unauthenticated")
            return finish(status, "authentication or model preflight failed", evidence)

        prompt = open(lane.prompt_path).read()
        model = Process([executable, *launch_argv], lane.cwd, env, prompt if adapter.prompt == "stdin" else None,
                        deadline, cancel, (out_fd, err_fd))
        receipt.update(exitCode=model.exit_code, signal=model.signal_sent)
        raw = f"{model.stderr}\n{model.stdout}"
        if model.outcome == "cancelled":
            return finish("cancelled", "launcher was cancelled; signal was sent to child" if model.signal_sent else
                          "launcher was cancelled after the child exited", raw)
        if model.outcome == "timed-out":
            return finish("timed-out", f"launcher exceeded the explicit {lane.timeout_s}s deadline", raw)
        parsed = adapter.parse(model.stdout, lane)
        if parsed.kind == "provider-failure":
            receipt.update(reportedModel=parsed.reported_model)
            return finish(parsed.status, parsed.message, f"{parsed.message}\n{raw}")
        if model.exit_code != 0:
            return finish(model_failure_status(raw), f"child exited with status {model.exit_code}", raw)
        if parsed.kind != "complete":
            return finish("malformed-output", parsed.message, raw)
        if parsed.reported_model is not None and adapter.matches(lane, parsed.reported_model):
            proof = {"reportedModel": parsed.reported_model, "modelVerified": True, "modelEvidence": "provider-report"}
        elif parsed.reported_model is None and adapter.proof == "absent":
            proof = {"reportedModel": None, "modelVerified": False, "modelEvidence": "pinned-argv"}
        else:
            return finish("malformed-output", f"requested model {lane.model} was not reported by {adapter.id}", raw)
        receipt.update(proof, sessionId=parsed.session_id, usage=parsed.usage, costUsd=parsed.cost_usd)
        with open(lane.output_path, "w") as f:
            f.write(parsed.text)
        return finish("complete")
    except Exception as error:  # a launcher failure after reservation still leaves a receipt
        status = "cancelled" if cancel.is_set() else ("timed-out" if deadline is not None and time.time() >= deadline else "child-failed")
        return finish(status, "launcher failed after reserving output paths", repr(error))
    finally:
        os.close(out_fd)
        os.close(err_fd)


def main(argv: list[str] | None = None) -> int:
    started = time.time()
    parser = argparse.ArgumentParser(prog="minipstack-runner")
    for name in ("parent", "provider", "model", "effort", "mode", "prompt", "cwd", "output", "receipt"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--writer-sandbox", default="bounded")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args(argv)
    cancel = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: cancel.set())
    try:
        lane = resolve_lane(parent=args.parent, provider=args.provider, model=args.model, effort=args.effort, mode=args.mode,
                            prompt_path=os.path.abspath(args.prompt), cwd=os.path.abspath(args.cwd),
                            output_path=os.path.abspath(args.output), receipt_path=os.path.abspath(args.receipt),
                            writer_sandbox=args.writer_sandbox, timeout_s=args.timeout)
        code, receipt = run_lane(lane, cancel=cancel, started=started)
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return 64
    print(json.dumps(receipt), file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
