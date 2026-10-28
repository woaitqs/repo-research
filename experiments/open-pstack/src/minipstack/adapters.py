"""Provider adapters: everything that differs between CLIs, and nothing that is policy.

Mirrors `runner/providers/*.ts`. An adapter builds argv, classifies its preflight, parses the
CLI's output and states how the served model is proven. It has no field for a timeout, a retry
of model execution, or another provider: those are the runner's policy, kept in one place.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

BASE_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "TERM", "TZ",
                  "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
                  "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS")
BASE_PREFIXES = ("LC_", "XDG_")
AUTH_FAILURE = re.compile(r"not logged in|not authenticated|unauthenticated|authentication|sign in|login required", re.I)
MODEL_FAILURE = re.compile(r"model.{0,40}(not found|unknown|unavailable|unsupported|not supported|invalid)|invalid.{0,20}model", re.I)


@dataclass(frozen=True)
class Parsed:
    kind: str  # complete | provider-failure | malformed
    text: str | None = None
    reported_model: str | None = None
    session_id: str | None = None
    usage: dict | None = None
    cost_usd: float | None = None
    status: str | None = None  # for provider-failure: cancelled | child-failed
    message: str | None = None


def model_failure_status(text: str) -> str:
    return "unavailable-model" if MODEL_FAILURE.search(text) else "child-failed"


def failed_preflight(output: str, fallback: str) -> str:
    if AUTH_FAILURE.search(output):
        return "unauthenticated"
    return "unavailable-model" if MODEL_FAILURE.search(output) else fallback


def _usage(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    keys = {"input_tokens": "inputTokens", "output_tokens": "outputTokens",
            "cache_read_input_tokens": "cachedInputTokens", "cached_input_tokens": "cachedInputTokens"}
    out = {new: value[old] for old, new in keys.items() if isinstance(value.get(old), (int, float))}
    return out or None


def _exact_or_suffixed(lane, reported: str) -> bool:
    return reported == lane.model or reported.startswith(f"{lane.model}-")


class Adapter:
    id = ""
    executable = ""
    effort_in = "flag"
    supports: tuple[str, ...] = ("read-only",)
    prompt = "stdin"
    proof = "required"  # required: provider-report; absent: pinned argv
    retry_after_unauthenticated_s: float | None = None

    def needs_shared_git(self, access) -> bool:
        return False

    def environment(self, access, source: dict) -> dict:
        return dict(source)

    def matches(self, lane, reported: str) -> bool:
        return _exact_or_suffixed(lane, reported)


class Claude(Adapter):
    id, executable, supports = "claude", "claude", ("read-only", "isolated-write")

    def needs_shared_git(self, access) -> bool:
        return access.mode == "isolated-write"

    def preflight_argv(self, lane) -> list[str]:
        return ["auth", "status", "--json"]

    def classify(self, exit_code: int, output: str, lane) -> str:
        try:
            ok = json.JSONDecoder().raw_decode(output.lstrip())[0].get("loggedIn") is True  # the real CLI pretty-prints
        except (ValueError, IndexError, AttributeError):
            ok = False
        return "passed" if exit_code == 0 and ok else failed_preflight(output, "unauthenticated")

    def argv(self, lane, shared_git_dir: str | None) -> list[str]:
        denied = ["Agent", "Task", "WebSearch", "WebFetch"] + (["Edit", "Write", "NotebookEdit"] if lane.access.mode == "read-only" else [])
        if lane.access.mode == "read-only":
            permissions = ["--permission-mode", "plan"]
            tools = "Read,Grep,Glob,Bash"
        else:
            sandbox = {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True}
            if shared_git_dir is not None:
                sandbox["filesystem"] = {"denyWrite": [shared_git_dir]}
            permissions = ["--permission-mode", "acceptEdits", "--settings", json.dumps({"sandbox": sandbox})]
            tools = "Read,Write,Edit,Grep,Glob,Bash"
        effort = [] if lane.effort == "default" else ["--effort", lane.effort]
        return ["-p", "--model", lane.cli_model, *effort, *permissions, "--setting-sources", "project", "--strict-mcp-config",
                "--tools", tools, "--no-session-persistence", "--disable-slash-commands", "--disallowed-tools", ",".join(denied),
                "--output-format", "json"]

    def parse(self, stdout: str, lane) -> Parsed:
        try:
            value = json.loads(stdout)
        except ValueError:
            return Parsed("malformed", message="claude did not emit valid JSON")
        if isinstance(value, list):
            value = next((e for e in reversed(value) if isinstance(e, dict) and e.get("type") == "result"), None)
        if not isinstance(value, dict):
            return Parsed("malformed", message="claude result did not contain a terminal event")
        if value.get("is_error") is True or not value.get("result"):
            return Parsed("malformed", message="claude reported an error or no final text")
        models = list((value.get("modelUsage") or {}).keys())
        reported = next((m for m in models if self.matches(lane, m)), models[0] if models else None)
        return Parsed("complete", text=value["result"], reported_model=reported, session_id=value.get("session_id"),
                      usage=_usage(value.get("usage")), cost_usd=value.get("total_cost_usd"))

    def matches(self, lane, reported: str) -> bool:
        prefix = lane.family.rolling_prefix if lane.family else None
        if prefix is None:
            return _exact_or_suffixed(lane, reported)
        return reported.startswith(prefix) and re.fullmatch(r"[0-9]+(?:-[0-9]+)*", reported[len(prefix):]) is not None

    def environment(self, access, source: dict) -> dict:
        if access.mode == "read-only":
            return dict(source)
        return {k: v for k, v in source.items() if not k.startswith("GIT_")}


class Codex(Adapter):
    id, executable, proof = "codex", "codex", "absent"
    supports = ("read-only", "isolated-write", "isolated-write-full-access")

    def preflight_argv(self, lane) -> list[str]:
        return ["login", "status"]

    def classify(self, exit_code: int, output: str, lane) -> str:
        return "passed" if exit_code == 0 and re.search(r"logged in", output, re.I) and not re.search(r"not logged in", output, re.I) \
            else failed_preflight(output, "unauthenticated")

    def argv(self, lane, shared_git_dir: str | None) -> list[str]:
        sandbox = "read-only" if lane.access.mode == "read-only" else (
            "danger-full-access" if lane.access.sandbox == "full-access" else "workspace-write")
        effort = [] if lane.effort == "default" else ["--config", f"model_reasoning_effort={json.dumps(lane.effort)}"]
        return ["exec", "--model", lane.cli_model, *effort, "--sandbox", sandbox, "--cd", lane.cwd, "--skip-git-repo-check",
                "--ephemeral", "--disable", "plugins", "--disable", "multi_agent", "--disable", "hooks", "--disable", "memories",
                "--json", "-"]

    def parse(self, stdout: str, lane) -> Parsed:
        text = session = usage = None
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                return Parsed("malformed", message="codex emitted a non-JSON event")
            kind = event.get("type")
            if kind == "thread.started":
                session = event.get("thread_id") or session
            elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
                text = event["item"].get("text") or text
            elif kind == "turn.completed":
                usage = _usage(event.get("usage")) or usage
            elif kind == "turn.failed":
                return Parsed("malformed", message=(event.get("error") or {}).get("message") or "codex reported a failed turn")
        if text is None:
            return Parsed("malformed", message="codex result did not contain a final agent message")
        return Parsed("complete", text=text, session_id=session, usage=usage)


class Grok(Adapter):
    id, executable, prompt = "grok", "grok", "file"
    supports = ("read-only", "isolated-write")
    retry_after_unauthenticated_s = 5.0

    def preflight_argv(self, lane) -> list[str]:
        return ["models"]

    def _listed(self, output: str, model: str) -> bool:
        lines = output.splitlines()
        header = next((i for i, line in enumerate(lines) if re.match(r"\s*Available models:", line)), None)
        pattern = re.compile(rf"(^|[^A-Za-z0-9_.-]){re.escape(model)}($|[^A-Za-z0-9_.-])")
        return header is not None and any(pattern.search(line) for line in lines[header + 1:])

    def classify(self, exit_code: int, output: str, lane) -> str:
        logged_in = re.search(r"\blogged in\b|You are using XAI_API_KEY\.", output, re.I) and not AUTH_FAILURE.search(output)
        if exit_code == 0 and logged_in and self._listed(output, lane.cli_model):
            return "passed"
        return failed_preflight(output, "unauthenticated" if self._listed(output, lane.cli_model) else "unavailable-model")

    def argv(self, lane, shared_git_dir: str | None) -> list[str]:
        tools = ["read_file", "grep", "list_dir", "run_terminal_cmd"] + (["search_replace"] if lane.access.mode == "isolated-write" else [])
        effort = [] if lane.effort == "default" else ["--reasoning-effort", lane.effort]
        return ["--prompt-file", lane.prompt_path, "--model", lane.cli_model, *effort, "--permission-mode", "auto",
                "--sandbox", "read-only" if lane.access.mode == "read-only" else "workspace", "--tools", ",".join(tools),
                "--disallowed-tools", "Agent,search_tool,use_tool", "--output-format", "streaming-messages-json",
                "--cwd", lane.cwd, "--no-subagents", "--disable-web-search"]

    def parse(self, stdout: str, lane) -> Parsed:
        result = None
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                return Parsed("malformed", message="grok emitted a non-JSON event")
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
        if result is None:
            return Parsed("malformed", message="grok result did not contain a terminal event")
        models = list((result.get("modelUsage") or {}).keys())
        reported = next((m for m in models if self.matches(lane, m)), models[0] if models else None)
        if result.get("is_error") or result.get("subtype") != "success":
            message = result.get("error") or f"grok reported {result.get('subtype')}"
            status = "cancelled" if result.get("subtype") in ("cancelled", "canceled") else "child-failed"
            return Parsed("provider-failure", status=status, message=str(message), reported_model=reported)
        if not result.get("result"):
            return Parsed("malformed", message="grok result did not contain final text")
        return Parsed("complete", text=result["result"], reported_model=reported, session_id=result.get("session_id"),
                      usage=_usage(result.get("usage")))

    def environment(self, access, source: dict) -> dict:
        prefixes = BASE_PREFIXES + ("GROK_", "XAI_")
        return {k: v for k, v in source.items() if k in BASE_ALLOWLIST or k.startswith(prefixes)}


class Devin(Adapter):
    """Read-only only: its approval policy, not an OS sandbox, rejects writes, so no mode is a bounded writer."""

    id, executable, effort_in, prompt = "devin", "devin", "model-id", "file"

    def preflight_argv(self, lane) -> list[str]:
        return ["auth", "status"]

    def classify(self, exit_code: int, output: str, lane) -> str:
        if exit_code == 0 and output.startswith("Logged in (via Devin)."):
            return "passed"
        return "unauthenticated" if output.startswith("Not logged in.") else "inconclusive"

    def argv(self, lane, shared_git_dir: str | None) -> list[str]:
        return ["-p", "--prompt-file", lane.prompt_path, "--model", lane.cli_model, "--permission-mode", "auto"]

    def parse(self, stdout: str, lane) -> Parsed:
        try:
            value = json.loads(stdout)
        except ValueError:
            return Parsed("malformed", message="devin export is not valid JSON")
        steps = value.get("steps") or []
        if not steps or steps[-1].get("source") != "agent" or not steps[-1].get("message"):
            return Parsed("provider-failure", status="child-failed", message="devin ended without an agent reply")
        last = steps[-1]
        return Parsed("complete", text=last["message"], reported_model=(last.get("extra") or {}).get("generation_model"))

    def matches(self, lane, reported: str) -> bool:
        return reported == lane.cli_model

    def environment(self, access, source: dict) -> dict:
        return {k: v for k, v in source.items() if k in BASE_ALLOWLIST or k.startswith(BASE_PREFIXES)}


ADAPTERS = {a.id: a for a in (Claude(), Codex(), Grok(), Devin())}
