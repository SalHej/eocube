"""
labdash.tools
=============

The capabilities the assistant is allowed to use, and the rules governing them.

This is the security boundary of the whole platform. An assistant that can run
PowerShell on a machine holding SSH keys to a university cluster is, in effect,
a remote operator. The design assumption here is not that the model is
malicious — it is that models misread intent, that a prompt can be
misinterpreted, and that the cost of a mistake is asymmetric: a wrong edit is
recoverable from git, a wrong ``Remove-Item -Recurse`` on a Lustre filesystem
is not.

Three tiers
-----------

``AUTO``
    Read-only and reversible. Runs without asking: listing files, reading
    source, ``git status``, ``labdash status``.

``REVIEW``
    Changes state. Shown to the operator with the exact command and executed
    only on explicit approval: writing files, committing, pushing, submitting
    jobs, arbitrary shell.

``BLOCKED``
    Refused outright, with no approval path. Recursive deletes at filesystem
    roots, disk formatting, shutdown, credential files, anything touching the
    cluster's project directory destructively.

The classifier is deliberately conservative and pattern-based. It will
sometimes send a harmless command to REVIEW. That is the correct direction to
fail in.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = ["Tier", "classify", "TOOL_SCHEMA", "execute", "ToolCall"]


class Tier(str, Enum):
    AUTO = "auto"
    REVIEW = "review"
    BLOCKED = "blocked"


# Patterns that are never run, whatever the context or justification.
_BLOCKED = [
    (r"\bRemove-Item\b[^|]*-Recurse[^|]*\b[A-Za-z]:\\?\s*$", "recursive delete of a drive root"),
    (r"\brm\s+-rf\s+/\s*$", "recursive delete of /"),
    (r"\brm\s+-rf\s+~", "recursive delete of home"),
    (r"\bformat\b.*\b[A-Za-z]:", "disk format"),
    (r"\b(shutdown|Restart-Computer|Stop-Computer)\b", "power state change"),
    (r"\bdel\b.*\/s.*[A-Za-z]:\\", "recursive delete"),
    (r"\b(id_rsa|id_ed25519|\.ssh/|\.aws/|credentials)\b", "credential file access"),
    (r"ANTHROPIC_API_KEY", "API key access"),
    (r"\.labdash_secrets", "secrets file access"),
    (r"\bgit\s+push\b.*--force", "force push"),
    (r"\bscancel\s+-u\b", "cancelling all of a user's jobs"),
    (r"\bRemove-Item\b.*scratch.*lustre", "deletion on the cluster filesystem"),
    (r"\brm\b.*scratch/lustre", "deletion on the cluster filesystem"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
]

# Read-only commands that may run without asking.
_AUTO = [
    r"^\s*(Get-ChildItem|dir|ls)\b", r"^\s*(Get-Content|cat|type)\b",
    r"^\s*(Get-Location|pwd)\b", r"^\s*git\s+(status|log|diff|branch|remote|show)\b",
    r"^\s*python\s+-m\s+pytest\b", r"^\s*labdash\s+(status|jobs)\b",
    r"^\s*python\s+-m\s+labdash\.cli\s+(status|jobs)\b",
    r"^\s*(Test-Path|Measure-Object)\b", r"^\s*python\s+--version",
    r"^\s*(Select-String|findstr)\b", r"^\s*git\s+rev-parse\b",
]

# A command may only be classified AUTO if it is a *single* command. The
# allow-list patterns are anchored at the start, so without this check
# "git status; Remove-Item -Recurse ." would match the git pattern and then be
# handed to PowerShell in full. Any separator, substitution or redirection
# forces the command to REVIEW regardless of how it begins.
_COMPOUND = re.compile(r"""
    [;&|]            # ; && || |  — command chaining or piping
  | \n               # newline
  | `                # PowerShell escape / line continuation
  | \$\(             # $( ) subshell or subexpression
  | \$\{             # ${ } expansion
  | \$               # any variable: $env:USERPROFILE can hide where a path points
  | [(){}]           # PowerShell runs (...) and @(...) even as an argument,
                     # so "cat (Remove-Item -Recurse x)" deletes before it reads
  | <\(              # process substitution
  | \bIEX\b | \bInvoke-Expression\b
  | \bStart-Process\b
  | >                # redirection can overwrite files
""", re.IGNORECASE | re.VERBOSE)

# Forms of the allow-listed git subcommands that are not read-only. "git branch"
# lists, but "git branch -D main" deletes; "git remote" lists, but "git remote
# set-url" silently redirects every later push. --output writes a file and
# --ext-diff runs whatever program the config names.
_GIT_WRITES = re.compile(r"""
    ^\s*git\s+branch\b.*\s(-[dDmMcCfu]\b|--(delete|move|copy|force|set-upstream-to|
        unset-upstream|edit-description)\b)
  | ^\s*git\s+remote\s+(add|remove|rm|rename|set-url|set-head|set-branches|prune|update)\b
  | ^\s*git\b.*\s--(output|ext-diff)\b
""", re.IGNORECASE | re.VERBOSE)

# Read-only is not the same as harmless if the read is outside the project: the
# model's context goes to the API, so "cat C:\Users\...\notes.txt" ships a
# private file off the machine without anyone approving it. Absolute paths,
# home-relative paths, UNC shares and parent traversal all go to REVIEW.
_OUTSIDE_PROJECT = re.compile(
    r"(^|\s|=)([A-Za-z]:|[/\\]|~)|\.\.", re.IGNORECASE)

# Paths that must never be read, even by the read-only tools.
_SECRET_PATHS = re.compile(
    r"(\.labdash_secrets\.json|\.env\b|id_rsa|id_ed25519|\.ssh[/\\]|"
    r"\.aws[/\\]|credentials|\.netrc|\.git-credentials)", re.IGNORECASE)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
    tier: Tier
    reason: str = ""


def classify(name: str, args: dict[str, Any]) -> tuple[Tier, str]:
    """Decide how a proposed tool call should be handled."""
    if name in ("read_file", "list_files"):
        target = str(args.get("path", "") or args.get("pattern", ""))
        if _SECRET_PATHS.search(target):
            return Tier.BLOCKED, "credential or secrets file"
        return Tier.AUTO, "read-only"

    if name == "write_file":
        if _SECRET_PATHS.search(str(args.get("path", ""))):
            return Tier.BLOCKED, "writing to a credential file"
        return Tier.REVIEW, "writes to disk"

    if name == "labdash":
        op = str(args.get("operation", ""))
        if op in {"status", "jobs", "tail"}:
            return Tier.AUTO, "read-only"
        if op == "cancel":
            return Tier.REVIEW, "cancels a running job"
        return Tier.REVIEW, f"changes state ({op})"

    if name == "run_command":
        cmd = str(args.get("command", ""))
        for pat, why in _BLOCKED:
            if re.search(pat, cmd, re.IGNORECASE):
                return Tier.BLOCKED, why
        if _SECRET_PATHS.search(cmd):
            return Tier.BLOCKED, "touches a credential or secrets file"
        # A compound command is reviewed even if it starts with something safe.
        if _COMPOUND.search(cmd):
            return Tier.REVIEW, "compound command (chaining, substitution or redirection)"
        if _GIT_WRITES.search(cmd):
            return Tier.REVIEW, "git command that changes state"
        if _OUTSIDE_PROJECT.search(cmd):
            return Tier.REVIEW, "path outside the project"
        for pat in _AUTO:
            if re.match(pat, cmd, re.IGNORECASE):
                return Tier.AUTO, "single read-only command"
        return Tier.REVIEW, "shell command"

    return Tier.REVIEW, "unknown tool"


TOOL_SCHEMA = [
    {
        "name": "run_command",
        "description": (
            "Run a PowerShell command in the project directory. Read-only commands "
            "run immediately; anything that changes state is shown to the operator "
            "for approval first. Prefer the 'labdash' tool for cluster operations — "
            "it validates inputs that a raw command does not."),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "the command to run"},
                "why": {"type": "string",
                        "description": "one short sentence on why this is needed"},
            },
            "required": ["command", "why"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the project directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path relative to the project root"},
                "max_lines": {"type": "integer", "description": "optional line cap"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write a file in the project directory. Always requires approval. "
            "Show the full intended content; the operator sees it before it lands."),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "why": {"type": "string"},
            },
            "required": ["path", "content", "why"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in the project, optionally matching a glob.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob, e.g. '**/*.py'"},
            },
        },
    },
    {
        "name": "labdash",
        "description": (
            "Infrastructure operations against the HPC cluster and the repository. "
            "Use this rather than raw ssh or scp: it validates job ids, quotes "
            "paths, and will not transfer imagery. Operations: status, jobs, "
            "submit, tail, cancel, sync, fetch, publish, pipeline."),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["status", "jobs", "submit", "tail", "cancel",
                             "sync", "fetch", "publish", "pipeline"],
                },
                "job_id": {"type": "string"},
                "message": {"type": "string", "description": "commit message"},
                "why": {"type": "string"},
            },
            "required": ["operation"],
        },
    },
]


def _safe_path(repo: Path, rel: str) -> Path:
    """Resolve a path and refuse anything outside the project.

    Uses real path containment rather than a string prefix: comparing prefixes
    lets a sibling directory such as ``..\\eocube2`` pass, because its resolved
    path starts with the same characters as ``eocube``.
    """
    root = repo.resolve()
    # Normalise separators before resolving. On POSIX a backslash is a legal
    # filename character, so "..\\sibling\\x" would not be seen as traversal
    # at all; on Windows it would. Treating both as separators everywhere
    # makes the check behave the same on either platform.
    rel_norm = str(rel).replace("\\", "/")
    p = (root / rel_norm).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise PermissionError(f"path escapes the project directory: {rel}") from None
    if _SECRET_PATHS.search(str(p)):
        raise PermissionError(f"refused: credential or secrets file ({rel})")
    return p


def execute(call: ToolCall, repo: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    """Run an approved tool call and return a result for the model."""
    name, a = call.name, call.args

    if call.tier is Tier.BLOCKED:
        return {"ok": False, "error": f"blocked: {call.reason}"}

    try:
        if name == "read_file":
            p = _safe_path(repo, a["path"])
            if not p.exists():
                return {"ok": False, "error": f"{a['path']} not found"}
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            cap = a.get("max_lines") or 1200
            truncated = len(lines) > cap
            return {"ok": True, "path": a["path"], "truncated": truncated,
                    "content": "\n".join(lines[:cap])}

        if name == "list_files":
            pat = a.get("pattern") or "**/*"
            skip = {".git", "__pycache__", ".pytest_cache", "data", "node_modules"}
            found = [str(p.relative_to(repo)) for p in repo.glob(pat)
                     if p.is_file() and not (skip & set(p.parts))]
            return {"ok": True, "count": len(found), "files": sorted(found)[:400]}

        if name == "write_file":
            p = _safe_path(repo, a["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            existed = p.exists()
            p.write_text(a["content"], encoding="utf-8")
            return {"ok": True, "path": a["path"],
                    "action": "overwrote" if existed else "created",
                    "bytes": len(a["content"])}

        if name == "run_command":
            cmd = a["command"]
            shell = ["powershell", "-NoProfile", "-Command", cmd] \
                if os.name == "nt" else ["bash", "-lc", cmd]
            r = subprocess.run(shell, cwd=str(repo), capture_output=True,
                               text=True, timeout=600)
            return {"ok": r.returncode == 0, "returncode": r.returncode,
                    "stdout": r.stdout[-16000:], "stderr": r.stderr[-6000:]}

        if name == "labdash":
            from . import ops
            op = a["operation"]
            if op == "status":
                from . import adapters
                h = cfg.get("hpc", {})
                return {"ok": True, "data": {
                    "git": adapters.git_status(repo).as_dict(),
                    "ssh": adapters.ssh_status(h.get("host", ""), h.get("user", "")).as_dict(),
                    "allocation": h.get("allocation", {})}}
            fn = {
                "jobs": lambda: ops.job_status(cfg),
                "submit": lambda: ops.submit_job(cfg),
                "tail": lambda: ops.tail_job(cfg, a.get("job_id", "")),
                "cancel": lambda: ops.cancel_job(cfg, a.get("job_id", "")),
                "sync": lambda: ops.sync_code(cfg, repo, a.get("message", "")),
                "fetch": lambda: ops.fetch_results(cfg, repo),
                "publish": lambda: ops.publish_results(cfg, repo, a.get("message", "")),
                "pipeline": lambda: ops.run_pipeline(cfg, repo, a.get("message", "run")),
            }[op]
            return fn().as_dict()

        return {"ok": False, "error": f"unknown tool {name}"}

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out after 600s"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
