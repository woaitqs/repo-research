"""Logging OpenAI-compatible shim in front of Volcano Engine Ark.

Why: Ark's plan endpoint returns an empty /models list, so letta-code's
OpenAI-compatible provider cannot discover the model. The shim serves a
one-entry /v1/models and forwards /v1/chat/completions unchanged. It also
records every request body letta-code sends, which shows exactly what enters
the model context on each provider call.

Env: ARK_API_KEY (required), ARK_BASE_URL, ARK_MODEL, SHIM_PORT, SHIM_LOG.
The Authorization header is never logged.
"""

import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3").rstrip("/")
MODEL = os.environ.get("ARK_MODEL", "deepseek-v4-1-flash-260910")
PORT = int(os.environ.get("SHIM_PORT", "18080"))
LOG = os.environ.get("SHIM_LOG", "shim-requests.jsonl")
CA = "/root/.ccr/ca-bundle.crt"
_lock = threading.Lock()
_seq = 0


def _ssl_context():
    return ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def _log(record):
    with _lock:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # close-delimited bodies keep SSE passthrough simple

    def log_message(self, *_args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "ark"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        global _seq
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        with _lock:
            _seq += 1
            seq = _seq
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {"_unparsed": raw.decode("utf-8", "replace")}
        started = time.time()
        req = urllib.request.Request(
            f"{BASE}/chat/completions",
            data=raw,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['ARK_API_KEY']}"},
        )
        response_text = []
        status = 0
        try:
            with urllib.request.urlopen(req, context=_ssl_context(), timeout=300) as upstream:
                status = upstream.status
                self.send_response(status)
                self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
                self.end_headers()
                while True:
                    chunk = upstream.read1(4096) if hasattr(upstream, "read1") else upstream.read(4096)
                    if not chunk:
                        break
                    response_text.append(chunk.decode("utf-8", "replace"))
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as err:
            status = err.code
            payload = err.read()
            response_text.append(payload.decode("utf-8", "replace"))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        _log({
            "seq": seq,
            "status": status,
            "elapsed_s": round(time.time() - started, 2),
            "request": body,
            "response_raw": "".join(response_text),
        })


if __name__ == "__main__":
    if "ARK_API_KEY" not in os.environ:
        sys.exit("ARK_API_KEY is required")
    print(f"shim on http://127.0.0.1:{PORT}/v1 -> {BASE} (model {MODEL}), log {LOG}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
