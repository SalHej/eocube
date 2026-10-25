"""
labdash.ops
===========

The verbs. Every action this project performs against the cluster or the
repository lives here, so that both the dashboard and the command line drive
identical code, and an agent working through the CLI cannot invent its own
variant of a dangerous operation.

Three safety properties are built in rather than left to discipline:

**Destructive operations are explicit.** Nothing here deletes remote data.
``fetch_results`` copies an allow-list of extensions and will not transfer
imagery even if asked.

**Paths are not interpolated from user input.** The project path comes from the
config; job identifiers are validated as integers before reaching a shell.

**Every operation returns a structured result.** Agents and panels both need to
know what happened, not parse prose.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import adapters

__all__ = [
    "OpResult", "submit_job", "job_status", "tail_job", "cancel_job",
    "sync_code", "fetch_results", "publish_results", "run_pipeline",
    "RESULT_EXTENSIONS",
]

#: Only these come back from the cluster. An allow-list, not a block-list:
#: licensed imagery must not transfer even by mistake.
RESULT_EXTENSIONS = (".json", ".csv", ".txt", ".log", ".md", ".out", ".err")

_JOBID = re.compile(r"^\d+(_\d+)?$")


@dataclass
class OpResult:
    op: str
    ok: bool
    message: str = ""
    data: Any = None
    stdout: str = ""
    stderr: str = ""
    took_ms: int = 0
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op, "ok": self.ok, "message": self.message,
            "data": self.data, "stdout": self.stdout[-20000:],
            "stderr": self.stderr[-8000:], "took_ms": self.took_ms, "at": self.at,
        }

    def __str__(self) -> str:  # pragma: no cover
        mark = "ok " if self.ok else "FAIL"
        out = f"[{mark}] {self.op}: {self.message}"
        if self.stdout.strip():
            out += "\n" + self.stdout.rstrip()
        if self.stderr.strip():
            out += "\n[stderr] " + self.stderr.rstrip()
        return out


def _hpc(cfg: dict[str, Any]) -> tuple[str, str, str]:
    h = cfg.get("hpc", {})
    return h.get("host", ""), h.get("user", ""), h.get("project_path", "")


def _timed(op: str, fn) -> OpResult:
    t0 = time.perf_counter()
    try:
        res = fn()
    except Exception as exc:  # noqa: BLE001
        res = OpResult(op, False, f"{type(exc).__name__}: {exc}")
    res.took_ms = int((time.perf_counter() - t0) * 1000)
    return res


# ------------------------------------------------------------------ cluster
def submit_job(cfg: dict[str, Any], script: str = "scripts/train.sbatch",
               env: dict[str, str] | None = None) -> OpResult:
    """Submit a Slurm job and return its id.

    ``script`` is resolved relative to the repository on the cluster and is
    quoted; ``env`` values are quoted individually.
    """
    def _go():
        host, user, project = _hpc(cfg)
        if not (host and user and project):
            return OpResult("submit", False, "HPC host, user or project_path not configured")
        repo = f"{project}/{Path(cfg.get('repo_name', 'eocube')).name}"
        exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in (env or {}).items())
        cmd = (f"cd {shlex.quote(repo)} && mkdir -p logs results && "
               f"{exports} sbatch {shlex.quote(script)}")
        rc, out, err = adapters.run_remote(host, user, cmd, timeout=60)
        if rc != 0:
            return OpResult("submit", False, "sbatch failed", stdout=out, stderr=err)
        m = re.search(r"(\d+)", out)
        jid = m.group(1) if m else None
        return OpResult("submit", True, f"submitted job {jid}",
                        data={"job_id": jid}, stdout=out, stderr=err)
    return _timed("submit", _go)


def job_status(cfg: dict[str, Any], job_id: str | None = None) -> OpResult:
    """Queue state, or one job's state if ``job_id`` is given."""
    def _go():
        host, user, _ = _hpc(cfg)
        if not (host and user):
            return OpResult("status", False, "HPC not configured")
        if job_id:
            if not _JOBID.match(str(job_id)):
                return OpResult("status", False, f"invalid job id {job_id!r}")
            cmd = (f"squeue -j {job_id} -h -o '%i|%j|%T|%M|%l|%R' || "
                   f"sacct -j {job_id} -X -P -n --format=JobID,JobName,State,Elapsed")
        else:
            cmd = f"squeue -u {user} -h -o '%i|%j|%T|%M|%l|%R'"
        rc, out, err = adapters.run_remote(host, user, cmd)
        if rc != 0:
            return OpResult("status", False, "squeue failed", stdout=out, stderr=err)
        rows = [dict(zip(["id", "name", "state", "elapsed", "limit", "reason"],
                         l.split("|")))
                for l in out.strip().splitlines() if "|" in l]
        return OpResult("status", True, f"{len(rows)} job(s)", data=rows, stdout=out)
    return _timed("status", _go)


def tail_job(cfg: dict[str, Any], job_id: str, lines: int = 120) -> OpResult:
    """Tail a running job's stdout log on the cluster."""
    def _go():
        host, user, project = _hpc(cfg)
        if not (host and user and project):
            return OpResult("tail", False, "HPC not configured")
        if not _JOBID.match(str(job_id)):
            return OpResult("tail", False, f"invalid job id {job_id!r}")
        repo = f"{project}/{Path(cfg.get('repo_name', 'eocube')).name}"
        cmd = (f"tail -n {int(lines)} {shlex.quote(repo)}/logs/*-{job_id}.out "
               f"2>/dev/null || echo '(no log yet for {job_id})'")
        rc, out, err = adapters.run_remote(host, user, cmd, timeout=40)
        return OpResult("tail", rc == 0, f"tail of job {job_id}",
                        data=out.splitlines(), stdout=out, stderr=err)
    return _timed("tail", _go)


def cancel_job(cfg: dict[str, Any], job_id: str) -> OpResult:
    """Cancel a job. The only destructive remote operation here."""
    def _go():
        host, user, _ = _hpc(cfg)
        if not (host and user):
            return OpResult("cancel", False, "HPC not configured")
        if not _JOBID.match(str(job_id)):
            return OpResult("cancel", False, f"invalid job id {job_id!r}")
        rc, out, err = adapters.run_remote(host, user, f"scancel {job_id}")
        return OpResult("cancel", rc == 0,
                        f"cancelled {job_id}" if rc == 0 else "scancel failed",
                        stdout=out, stderr=err)
    return _timed("cancel", _go)


# --------------------------------------------------------------------- code
def sync_code(cfg: dict[str, Any], repo: Path, message: str = "",
              run_tests: bool = True) -> OpResult:
    """Test, commit, push, then pull on the cluster.

    Tests run first and a failure aborts the whole operation. Pushing code that
    does not pass its own tests to a machine that will spend GPU hours on it is
    a waste that is trivial to prevent.
    """
    def _go():
        if run_tests:
            rc, out, err = adapters.run_local(
                ["python", "-m", "pytest", "-q", "--tb=line"], repo, timeout=300)
            if rc != 0:
                return OpResult("sync", False, "tests failed; nothing pushed",
                                stdout=out, stderr=err)
        if message:
            adapters.run_local(["git", "add", "-A"], repo)
            rc, out, err = adapters.run_local(["git", "commit", "-m", message], repo)
            if rc != 0 and "nothing to commit" not in (out + err):
                return OpResult("sync", False, "commit failed", stdout=out, stderr=err)
        rc, out, err = adapters.run_local(["git", "push"], repo, timeout=180)
        if rc != 0:
            return OpResult("sync", False, "push failed", stdout=out, stderr=err)

        host, user, project = _hpc(cfg)
        if not (host and user and project):
            return OpResult("sync", True, "pushed to GitHub (cluster not configured)",
                            stdout=out, stderr=err)
        repo_remote = f"{project}/{Path(cfg.get('repo_name', 'eocube')).name}"
        rc2, out2, err2 = adapters.run_remote(
            host, user, f"cd {shlex.quote(repo_remote)} && git pull --ff-only",
            timeout=120)
        return OpResult("sync", rc2 == 0,
                        "pushed and pulled on cluster" if rc2 == 0 else "cluster pull failed",
                        stdout=out + "\n" + out2, stderr=err + err2)
    return _timed("sync", _go)


def fetch_results(cfg: dict[str, Any], repo: Path) -> OpResult:
    """Copy result files back. Allow-list only; imagery never transfers."""
    def _go():
        host, user, project = _hpc(cfg)
        if not (host and user and project):
            return OpResult("fetch", False, "HPC not configured")
        repo_remote = f"{project}/{Path(cfg.get('repo_name', 'eocube')).name}"
        local = repo / cfg.get("results_dir", "results")
        local.mkdir(parents=True, exist_ok=True)

        pattern = " -o ".join(f"-name '*{e}'" for e in RESULT_EXTENSIONS)
        listing = (f"find {shlex.quote(repo_remote)}/results -maxdepth 1 -type f "
                   f"\\( {pattern} \\) -printf '%f\\n' 2>/dev/null")
        rc, out, err = adapters.run_remote(host, user, listing, timeout=60)
        names = [n.strip() for n in out.splitlines() if n.strip()]
        if not names:
            return OpResult("fetch", True, "no result files on the cluster",
                            data=[], stderr=err)

        copied = []
        for n in names:
            if not n.lower().endswith(RESULT_EXTENSIONS):
                continue                       # belt and braces
            rc2, _, err2 = adapters.run_local(
                ["scp", "-o", "BatchMode=yes",
                 f"{user}@{host}:{repo_remote}/results/{n}", str(local / n)],
                timeout=180)
            if rc2 == 0:
                copied.append(n)
        return OpResult("fetch", True, f"fetched {len(copied)} file(s)",
                        data=copied)
    return _timed("fetch", _go)


def publish_results(cfg: dict[str, Any], repo: Path,
                    message: str = "") -> OpResult:
    """Commit fetched results and push them to GitHub.

    Result files are small, textual and worth versioning: they are the record
    of what was run and what came out. Imagery is excluded by .gitignore and by
    the allow-list in :func:`fetch_results`.
    """
    def _go():
        results = cfg.get("results_dir", "results")
        rc, out, _ = adapters.run_local(
            ["git", "status", "--porcelain", results], repo)
        if not out.strip():
            return OpResult("publish", True, "no new results to publish")
        adapters.run_local(["git", "add", results], repo)
        msg = message or f"results: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
        rc, out2, err = adapters.run_local(["git", "commit", "-m", msg], repo)
        if rc != 0 and "nothing to commit" not in (out2 + err):
            return OpResult("publish", False, "commit failed", stdout=out2, stderr=err)
        rc, out3, err3 = adapters.run_local(["git", "push"], repo, timeout=180)
        return OpResult("publish", rc == 0,
                        "results published" if rc == 0 else "push failed",
                        stdout=out2 + out3, stderr=err3)
    return _timed("publish", _go)


# ----------------------------------------------------------------- pipeline
def run_pipeline(cfg: dict[str, Any], repo: Path, message: str,
                 script: str = "scripts/train.sbatch",
                 poll_seconds: int = 60, max_wait_minutes: int = 240,
                 progress=None) -> OpResult:
    """Test, push, submit, wait, fetch, publish — the whole loop.

    Blocking by design: it is meant to be left running in a terminal or started
    from the dashboard and watched. ``progress`` is called with each step name
    so a caller can report as it goes.
    """
    def say(msg: str) -> None:
        if progress:
            progress(msg)

    def _go():
        steps: list[dict[str, Any]] = []

        say("syncing code")
        s = sync_code(cfg, repo, message)
        steps.append(s.as_dict())
        if not s.ok:
            return OpResult("pipeline", False, f"stopped at sync: {s.message}",
                            data=steps)

        say("submitting job")
        sub = submit_job(cfg, script)
        steps.append(sub.as_dict())
        if not sub.ok:
            return OpResult("pipeline", False, f"stopped at submit: {sub.message}",
                            data=steps)
        jid = (sub.data or {}).get("job_id")

        say(f"waiting for job {jid}")
        deadline = time.time() + max_wait_minutes * 60
        state = "PENDING"
        while time.time() < deadline:
            time.sleep(poll_seconds)
            st = job_status(cfg, jid)
            rows = st.data or []
            if not rows:
                state = "COMPLETED"
                break
            state = rows[0].get("state", "?")
            say(f"job {jid}: {state}")
            if state in {"FAILED", "CANCELLED", "TIMEOUT"}:
                break
        else:
            return OpResult("pipeline", False,
                            f"job {jid} still running after {max_wait_minutes} min",
                            data=steps)

        say("fetching results")
        f = fetch_results(cfg, repo)
        steps.append(f.as_dict())

        say("publishing")
        p = publish_results(cfg, repo, f"results from job {jid}: {message}")
        steps.append(p.as_dict())

        ok = state not in {"FAILED", "CANCELLED", "TIMEOUT"}
        return OpResult("pipeline", ok,
                        f"job {jid} finished {state}; "
                        f"{len(f.data or [])} result file(s) published",
                        data=steps)
    return _timed("pipeline", _go)
