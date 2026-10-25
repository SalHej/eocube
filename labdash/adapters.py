"""
labdash.adapters
================

Everything that talks to the outside world: the local repository, the test
suite, the SSH connection, Slurm, and the results directory.

Two principles run through this module.

**Degrade, never crash.** The dashboard has to be useful before the cluster
account exists. Every adapter returns a structured result with an explicit
``available`` flag and a human-readable reason when something is unreachable,
rather than raising. A panel that says "no SSH host configured" is informative;
a stack trace is not.

**Report staleness.** Every reading carries the time it was taken and how long
it took. A dashboard that shows a number without saying when it was measured
invites you to trust a value that may be minutes old.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "Probe", "run_local", "run_remote",
    "git_status", "test_status", "ssh_status", "slurm_queue",
    "slurm_history", "disk_usage", "list_results", "read_result", "tail_file",
]

TIMEOUT = 25


@dataclass
class Probe:
    """One measurement, with provenance."""

    name: str
    available: bool
    data: Any = None
    reason: str | None = None
    took_ms: int = 0
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "available": self.available, "data": self.data,
            "reason": self.reason, "took_ms": self.took_ms, "at": self.at,
        }


def _timed(fn, name: str) -> Probe:
    t0 = time.perf_counter()
    try:
        ok, data, reason = fn()
    except FileNotFoundError as exc:
        ok, data, reason = False, None, f"command not found: {exc.filename}"
    except subprocess.TimeoutExpired:
        ok, data, reason = False, None, f"timed out after {TIMEOUT}s"
    except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
        ok, data, reason = False, None, f"{type(exc).__name__}: {exc}"
    return Probe(name, ok, data, reason, int((time.perf_counter() - t0) * 1000))


# ---------------------------------------------------------------- local shell
def run_local(cmd: list[str], cwd: str | Path | None = None,
              timeout: int = TIMEOUT) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def run_remote(host: str, user: str, command: str,
               timeout: int = TIMEOUT) -> tuple[int, str, str]:
    """Run a command over SSH using the system client and existing keys.

    No password handling and no credential storage: if key-based auth is not
    configured, this fails and the dashboard says so. Prompting for a password
    from a web UI would mean holding it, which is not a trade worth making.
    """
    if not shutil.which("ssh"):
        raise FileNotFoundError("ssh")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
           "-o", "StrictHostKeyChecking=accept-new", f"{user}@{host}", command]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------- repository
def git_status(repo: Path) -> Probe:
    def _go():
        if not (repo / ".git").exists():
            return False, None, "not a git repository"
        out: dict[str, Any] = {}
        rc, branch, _ = run_local(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
        out["branch"] = branch.strip() if rc == 0 else "?"
        rc, log, _ = run_local(
            ["git", "log", "-1", "--pretty=%h|%s|%an|%ar"], repo)
        if rc == 0 and "|" in log:
            h, s, a, w = log.strip().split("|", 3)
            out["last_commit"] = {"sha": h, "subject": s, "author": a, "when": w}
        rc, porcelain, _ = run_local(["git", "status", "--porcelain"], repo)
        dirty = [l for l in porcelain.splitlines() if l.strip()]
        out["dirty_count"] = len(dirty)
        out["dirty"] = dirty[:20]
        rc, counts, _ = run_local(
            ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"], repo)
        if rc == 0 and counts.strip():
            behind, ahead = counts.split()
            out["behind"], out["ahead"] = int(behind), int(ahead)
        rc, tags, _ = run_local(["git", "tag", "--sort=-creatordate"], repo)
        out["tags"] = tags.split()[:5] if rc == 0 else []
        return True, out, None
    return _timed(_go, "git")


def test_status(repo: Path, cache: dict[str, Any]) -> Probe:
    """Run pytest. Cached, because it is the slowest probe here."""
    def _go():
        rc, out, err = run_local(
            ["python", "-m", "pytest", "-q", "--no-header", "-x", "--tb=no"],
            repo, timeout=240)
        text = out + err
        m = re.search(r"(\d+) passed", text)
        f = re.search(r"(\d+) failed", text)
        data = {
            "passed": int(m.group(1)) if m else 0,
            "failed": int(f.group(1)) if f else 0,
            "ok": rc == 0,
            "tail": text.strip().splitlines()[-6:],
        }
        cache["tests"] = (time.time(), data)
        return True, data, None
    return _timed(_go, "tests")


# ---------------------------------------------------------------------- HPC
def ssh_status(host: str, user: str) -> Probe:
    def _go():
        if not host or not user:
            return False, None, "no SSH host or user configured"
        rc, out, err = run_remote(host, user, "echo ok; hostname; id -un", timeout=15)
        if rc != 0:
            hint = err.strip().splitlines()[-1] if err.strip() else f"exit {rc}"
            return False, None, hint
        lines = out.strip().splitlines()
        return True, {"hostname": lines[1] if len(lines) > 1 else host,
                      "user": lines[2] if len(lines) > 2 else user}, None
    return _timed(_go, "ssh")


def slurm_queue(host: str, user: str) -> Probe:
    def _go():
        if not host or not user:
            return False, None, "no SSH host configured"
        fmt = "%i|%j|%T|%M|%l|%D|%R|%P"
        rc, out, err = run_remote(
            host, user, f"squeue -u {user} -h -o '{fmt}'")
        if rc != 0:
            return False, None, (err.strip().splitlines() or ["squeue failed"])[-1]
        jobs = []
        for line in out.strip().splitlines():
            if not line.strip():
                continue
            p = line.split("|")
            if len(p) >= 8:
                jobs.append(dict(zip(
                    ["id", "name", "state", "elapsed", "limit",
                     "nodes", "reason", "partition"], p)))
        return True, jobs, None
    return _timed(_go, "queue")


def slurm_history(host: str, user: str, days: int = 7) -> Probe:
    def _go():
        if not host or not user:
            return False, None, "no SSH host configured"
        cmd = (f"sacct -u {user} --starttime now-{days}days -X -P -n "
               f"--format=JobID,JobName,State,Elapsed,AllocCPUS,ReqTRES,End")
        rc, out, err = run_remote(host, user, cmd)
        if rc != 0:
            return False, None, (err.strip().splitlines() or ["sacct failed"])[-1]
        rows = []
        for line in out.strip().splitlines():
            p = line.split("|")
            if len(p) >= 7:
                rows.append(dict(zip(
                    ["id", "name", "state", "elapsed", "cpus", "tres", "end"], p)))
        return True, rows[-40:], None
    return _timed(_go, "history")


def disk_usage(host: str, user: str, path: str) -> Probe:
    def _go():
        if not host or not user or not path:
            return False, None, "no SSH host or project path configured"
        rc, out, err = run_remote(host, user, f"du -sh {path} 2>/dev/null; df -h {path} | tail -1")
        if rc != 0:
            return False, None, (err.strip().splitlines() or ["df failed"])[-1]
        lines = out.strip().splitlines()
        used = lines[0].split()[0] if lines else "?"
        fs = lines[-1].split() if len(lines) > 1 else []
        return True, {
            "project_used": used,
            "filesystem_size": fs[1] if len(fs) > 1 else "?",
            "filesystem_used_pct": fs[4] if len(fs) > 4 else "?",
        }, None
    return _timed(_go, "disk")


# ------------------------------------------------------------------ results
def list_results(results_dir: Path) -> Probe:
    def _go():
        if not results_dir.exists():
            return True, [], None
        items = []
        for p in sorted(results_dir.glob("*.json"), key=lambda x: -x.stat().st_mtime):
            items.append({
                "name": p.name,
                "size_kb": round(p.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(
                    p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            })
        return True, items[:50], None
    return _timed(_go, "results")


def read_result(results_dir: Path, name: str) -> Probe:
    def _go():
        safe = Path(name).name                      # no traversal
        path = results_dir / safe
        if not path.exists():
            return False, None, f"{safe} not found"
        return True, json.loads(path.read_text(encoding="utf-8")), None
    return _timed(_go, f"result:{name}")


def tail_file(path: Path, lines: int = 200) -> Probe:
    def _go():
        if not path.exists():
            return False, None, f"{path} not found"
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return True, content[-lines:], None
    return _timed(_go, "log")
