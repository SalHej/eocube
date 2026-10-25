"""
labdash.server
==============

A dependency-free monitoring dashboard for computational research projects.

Runs on the Python standard library alone, serves a single page on localhost,
and is configured by one JSON file so the same tool works across projects.

On the command panel
--------------------
It runs a **whitelist**, not a shell. Commands are declared in the config file
and invoked by key; nothing the browser sends is passed to a shell. A browser
tab that can execute arbitrary commands is a remote-code-execution hole on a
machine holding SSH keys to a university cluster, and the convenience of typing
free-form commands is not worth that. Use a real terminal for ad-hoc work.

The server binds to 127.0.0.1 for the same reason.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import adapters, chat as chatmod

HERE = Path(__file__).parent
DEFAULT_CONFIG = {
    "project_name": "eocube",
    "repo_path": ".",
    "results_dir": "results",
    "log_dir": "logs",
    "hpc": {
        "host": "",
        "user": "",
        "project_path": "",
        "allocation": {"name": "", "cpu_hours": 0, "gpu_hours": 0, "expires": ""},
    },
    "commands": {
        "tests": {"label": "Run tests", "argv": ["python", "-m", "pytest", "-q"], "where": "local"},
        "ablation": {"label": "Ablation (synthetic)", "where": "local",
                     "argv": ["python", "examples/retrieval_experiment.py", "--seeds", "3"]},
        "git_status": {"label": "Git status", "argv": ["git", "status", "--short"], "where": "local"},
        "git_push": {"label": "Push to GitHub", "argv": ["git", "push"], "where": "local"},
        "queue": {"label": "Slurm queue", "where": "remote", "remote": "squeue -u $USER"},
        "sinfo": {"label": "Cluster partitions", "where": "remote",
                  "remote": "sinfo -o '%P %G %D %m %l'"},
    },
    "refresh_seconds": 30,
}


class State:
    def __init__(self, config: dict[str, Any], root: Path):
        self.config = config
        self.root = root
        self.repo = (root / config.get("repo_path", ".")).resolve()
        self.results = (self.repo / config.get("results_dir", "results")).resolve()
        self.logs = (self.repo / config.get("log_dir", "logs")).resolve()
        self.cache: dict[str, Any] = {}
        self.conv: Any = None
        self.history: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    @property
    def hpc(self) -> dict[str, Any]:
        return self.config.get("hpc", {})

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.history.insert(0, entry)
            del self.history[60:]


def _cached(state: State, key: str, ttl: int, fn):
    now = time.time()
    hit = state.cache.get(key)
    if hit and now - hit[0] < ttl:
        probe = hit[1]
        probe["cached"] = True
        return probe
    probe = fn().as_dict()
    probe["cached"] = False
    state.cache[key] = (now, probe)
    return probe


class Handler(BaseHTTPRequestHandler):
    state: State = None  # type: ignore[assignment]

    def log_message(self, *args):  # silence per-request logging
        pass

    # ------------------------------------------------------------------ util
    def _send(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        body = (HERE / "index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        s = self.state
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            return self._send_html()

        if p == "/api/config":
            cfg = json.loads(json.dumps(s.config))
            return self._send({
                "project_name": cfg.get("project_name"),
                "repo_path": str(s.repo),
                "hpc": {k: v for k, v in cfg.get("hpc", {}).items()},
                "commands": {k: {"label": v.get("label", k), "where": v.get("where", "local")}
                             for k, v in cfg.get("commands", {}).items()},
                "refresh_seconds": cfg.get("refresh_seconds", 30),
                "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

        if p == "/api/overview":
            h = s.hpc
            return self._send({
                "git": _cached(s, "git", 10, lambda: adapters.git_status(s.repo)),
                "ssh": _cached(s, "ssh", 45, lambda: adapters.ssh_status(
                    h.get("host", ""), h.get("user", ""))),
                "disk": _cached(s, "disk", 300, lambda: adapters.disk_usage(
                    h.get("host", ""), h.get("user", ""), h.get("project_path", ""))),
                "allocation": h.get("allocation", {}),
            })

        if p == "/api/jobs":
            h = s.hpc
            return self._send({
                "queue": _cached(s, "queue", 20, lambda: adapters.slurm_queue(
                    h.get("host", ""), h.get("user", ""))),
                "history": _cached(s, "hist", 120, lambda: adapters.slurm_history(
                    h.get("host", ""), h.get("user", ""))),
            })

        if p == "/api/tests":
            return self._send(_cached(s, "tests", 3600,
                                      lambda: adapters.test_status(s.repo, s.cache)))

        if p == "/api/results":
            return self._send(adapters.list_results(s.results).as_dict())

        if p == "/api/result":
            name = (q.get("name") or [""])[0]
            return self._send(adapters.read_result(s.results, name).as_dict())

        if p == "/api/logs":
            name = (q.get("name") or [""])[0]
            if not name:
                files = []
                if s.logs.exists():
                    files = sorted(
                        (f.name for f in s.logs.iterdir() if f.is_file()),
                        reverse=True)[:30]
                return self._send({"available": True, "data": files})
            return self._send(adapters.tail_file(s.logs / Path(name).name).as_dict())

        if p == "/api/chat/state":
            key = chatmod.load_api_key(s.repo)
            conv = s.conv
            return self._send({
                "key_present": bool(key),
                "models": chatmod.MODELS,
                "model": (conv.model if conv else chatmod.DEFAULT_MODEL),
                "transcript": (conv.transcript[-80:] if conv else []),
                "pending": (conv.pending if conv else []),
                "usage": (conv.usage if conv else {"input": 0, "output": 0}),
                "cost_usd": (conv.estimated_cost_usd() if conv else 0.0),
            })

        if p == "/api/activity":
            return self._send({"available": True, "data": s.history})

        return self._send({"error": "not found"}, 404)

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        s = self.state
        path = urlparse(self.path).path
        if path.startswith("/api/chat/"):
            return self._chat(path)
        if path != "/api/run":
            return self._send({"error": "not found"}, 404)

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send({"error": "bad json"}, 400)

        key = payload.get("command", "")
        spec = s.config.get("commands", {}).get(key)
        if spec is None:
            # Whitelist only. Anything not declared in the config is refused.
            return self._send(
                {"error": f"'{key}' is not a declared command. "
                          "Add it to the config file to allow it."}, 403)

        t0 = time.perf_counter()
        try:
            if spec.get("where") == "remote":
                h = s.hpc
                if not h.get("host") or not h.get("user"):
                    raise RuntimeError("no SSH host configured")
                rc, out, err = adapters.run_remote(
                    h["host"], h["user"], spec["remote"], timeout=120)
            else:
                rc, out, err = adapters.run_local(spec["argv"], s.repo, timeout=600)
            ok = rc == 0
        except Exception as exc:  # noqa: BLE001
            rc, out, err, ok = -1, "", f"{type(exc).__name__}: {exc}", False

        entry = {
            "command": key, "label": spec.get("label", key), "ok": ok,
            "returncode": rc, "stdout": out[-20000:], "stderr": err[-8000:],
            "took_ms": int((time.perf_counter() - t0) * 1000),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        s.record({k: entry[k] for k in ("command", "label", "ok", "took_ms", "at")})
        if key == "tests":
            s.cache.pop("tests", None)
        return self._send(entry)

    # ------------------------------------------------------------------ chat
    def _chat(self, path: str) -> None:
        s = self.state
        body = _read_json(self)
        key = chatmod.load_api_key(s.repo)

        if path == "/api/chat/reset":
            s.conv = None
            return self._send({"ok": True})

        if path == "/api/chat/model":
            if s.conv is None:
                s.conv = chatmod.Conversation(s.repo, s.config)
            s.conv.model = body.get("model", chatmod.DEFAULT_MODEL)
            return self._send({"ok": True, "model": s.conv.model})

        if not key:
            return self._send({"error":
                "No API key. Set ANTHROPIC_API_KEY, or create "
                ".labdash_secrets.json with {\"anthropic_api_key\": \"sk-...\"} "
                "in the project root. That file must stay gitignored."}, 400)

        if s.conv is None:
            s.conv = chatmod.Conversation(s.repo, s.config,
                                          body.get("model") or chatmod.DEFAULT_MODEL)
        try:
            if path == "/api/chat/send":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send({"error": "empty message"}, 400)
                return self._send(s.conv.send(key, text))
            if path == "/api/chat/approve":
                return self._send(s.conv.approve(
                    key, body.get("approved", []), body.get("denied", [])))
        except Exception as exc:  # noqa: BLE001
            return self._send({"error": f"{type(exc).__name__}: {exc}"}, 500)

        return self._send({"error": "not found"}, 404)


def _read_json(handler) -> dict:
    n = int(handler.headers.get("Content-Length", 0))
    try:
        return json.loads(handler.rfile.read(n) or b"{}")
    except json.JSONDecodeError:
        return {}


def load_config(path: Path | None) -> tuple[dict[str, Any], Path]:
    if path and path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        merged.update(cfg)
        for k in ("hpc", "commands"):
            if k in cfg:
                merged[k] = cfg[k]
        return merged, path.parent.resolve()
    return json.loads(json.dumps(DEFAULT_CONFIG)), Path.cwd()


def serve(config_path: Path | None = None, port: int = 8787,
          open_browser: bool = True) -> None:
    cfg, root = load_config(config_path)
    Handler.state = State(cfg, root)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  {cfg.get('project_name', 'project')} dashboard")
    print(f"  {url}")
    print(f"  repo: {Handler.state.repo}")
    host = cfg.get("hpc", {}).get("host") or "(not configured)"
    print(f"  hpc:  {host}")
    print("\n  Ctrl-C to stop.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        httpd.shutdown()
