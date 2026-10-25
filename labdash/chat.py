"""
labdash.chat
============

The assistant: an Anthropic Messages API client with tool use, written against
the standard library so the platform keeps its no-dependency property.

How a turn works
----------------

1. Your message goes to the model with the tool schema and a system prompt
   describing this project.
2. The model replies with text, tool calls, or both.
3. Tool calls are classified (:mod:`labdash.tools`). ``AUTO`` ones run
   immediately. ``REVIEW`` ones stop the turn and wait for your approval.
   ``BLOCKED`` ones are refused and the refusal is sent back as the result, so
   the model learns it cannot do that and proposes something else.
4. Results go back; the model continues until it has nothing left to call.

The pause for approval is the point. An assistant that can run PowerShell on a
machine with cluster credentials should not be able to do so without a human
seeing the exact command first.

Credentials
-----------
The API key is read from the ``ANTHROPIC_API_KEY`` environment variable, or
from ``.labdash_secrets.json`` in the project root — which must be gitignored.
It is never written to ``dashboard.json``, never logged, and never returned to
the browser.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tools import TOOL_SCHEMA, Tier, ToolCall, classify, execute

__all__ = ["Conversation", "load_api_key", "DEFAULT_MODEL", "MODELS"]

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

#: Sensible default: capable enough for this work, far cheaper than Opus.
DEFAULT_MODEL = "claude-sonnet-5-5"

#: Offered in the model picker. Verify current names against the API docs —
#: model identifiers change and this list will age.
MODELS = [
    ("claude-opus-5-5", "Opus 5.5 — hardest reasoning, most expensive"),
    ("claude-opus-5", "Opus 5 — strong reasoning"),
    ("claude-sonnet-5-5", "Sonnet 5.5 — the sensible default"),
    ("claude-haiku-5-5", "Haiku 5.5 — fast and cheap, simple tasks"),
]

SYSTEM = """You are the assistant inside a custom research workbench built by \
Salah Al-Heejawi for the eocube project at the Artificial Intelligence Methods \
Laboratory, Vilnius University.

The project implements machine-checkable contract gates for multi-source Earth \
Observation data cubes, accompanying the preprint doi:10.31223/X5TN59.

Rules that matter more than convenience:

- Never copy Earth Observation raster data to the local machine. TerraSAR-X and \
EnMAP are licensed by third parties and must stay on the cluster. If a task \
appears to need imagery locally, stop and say so.
- Use the `labdash` tool for cluster operations rather than raw ssh or scp. It \
validates job ids and quotes paths; a raw command does not.
- Tests must pass before any commit. Run them; do not assume.
- Never use a random train/test split on EO data. Spatial autocorrelation makes \
it meaningless. Use spatial_block_split.
- The GPU allocation is 1,000 hours shared with the group. Say what a job will \
cost before submitting it.

Working style: be concise. Say what you are about to do and why, in one line, \
before calling a tool. If a number is not available, say so rather than \
estimating. If you are unsure whether something is safe, ask."""


def load_api_key(repo: Path) -> str | None:
    """Environment variable first, then a gitignored secrets file."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    secrets = repo / ".labdash_secrets.json"
    if secrets.exists():
        try:
            return json.loads(secrets.read_text(encoding="utf-8")).get("anthropic_api_key")
        except json.JSONDecodeError:
            return None
    return None


def _post(key: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": API_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except json.JSONDecodeError:
            msg = body
        raise RuntimeError(f"API error {e.code}: {msg}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach the API: {e.reason}") from None


@dataclass
class Conversation:
    """One chat session, with its message history and pending approvals."""

    repo: Path
    cfg: dict[str, Any]
    model: str = DEFAULT_MODEL
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})

    # ------------------------------------------------------------------ util
    def _log(self, kind: str, **kw) -> None:
        self.transcript.append({
            "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **kw,
        })

    def estimated_cost_usd(self) -> float:
        """Rough running cost. Indicative only — rates change."""
        rates = {"opus": (15.0, 75.0), "sonnet": (3.0, 15.0), "haiku": (0.80, 4.0)}
        tier = next((k for k in rates if k in self.model), "sonnet")
        inp, out = rates[tier]
        return round(self.usage["input"] / 1e6 * inp + self.usage["output"] / 1e6 * out, 4)

    def reset(self) -> None:
        self.messages.clear()
        self.pending.clear()
        self.transcript.clear()
        self.usage = {"input": 0, "output": 0}

    # ------------------------------------------------------------------ turn
    def send(self, key: str, text: str) -> dict[str, Any]:
        self.messages.append({"role": "user", "content": text})
        self._log("user", text=text)
        return self._loop(key)

    def approve(self, key: str, approved_ids: list[str],
                denied_ids: list[str]) -> dict[str, Any]:
        """Execute approved calls, refuse denied ones, then continue the turn."""
        results = []
        for item in self.pending:
            call = ToolCall(item["id"], item["name"], item["args"],
                            Tier(item["tier"]), item.get("reason", ""))
            if item["id"] in approved_ids:
                out = execute(call, self.repo, self.cfg)
                self._log("tool", name=call.name, args=call.args,
                          approved=True, result=out)
            else:
                out = {"ok": False,
                       "error": "the operator declined this action"}
                self._log("tool", name=call.name, args=call.args, approved=False)
            results.append({"type": "tool_result", "tool_use_id": item["id"],
                            "content": json.dumps(out, default=str)[:30000]})
        self.pending.clear()
        if results:
            self.messages.append({"role": "user", "content": results})
        return self._loop(key)

    def _loop(self, key: str, max_rounds: int = 12) -> dict[str, Any]:
        for _ in range(max_rounds):
            resp = _post(key, {
                "model": self.model,
                "max_tokens": 4096,
                "system": SYSTEM,
                "tools": TOOL_SCHEMA,
                "messages": self.messages,
            })
            u = resp.get("usage", {})
            self.usage["input"] += u.get("input_tokens", 0)
            self.usage["output"] += u.get("output_tokens", 0)

            content = resp.get("content", [])
            self.messages.append({"role": "assistant", "content": content})

            said = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            if said.strip():
                self._log("assistant", text=said)

            calls = [b for b in content if b.get("type") == "tool_use"]
            if not calls:
                return self._state(done=True, text=said)

            auto_results, needs_approval = [], []
            for b in calls:
                tier, reason = classify(b["name"], b.get("input", {}))
                call = ToolCall(b["id"], b["name"], b.get("input", {}), tier, reason)
                if tier is Tier.AUTO:
                    out = execute(call, self.repo, self.cfg)
                    self._log("tool", name=call.name, args=call.args,
                              approved=True, auto=True, result=out)
                    auto_results.append({"type": "tool_result", "tool_use_id": b["id"],
                                         "content": json.dumps(out, default=str)[:30000]})
                elif tier is Tier.BLOCKED:
                    out = {"ok": False, "error": f"blocked by policy: {reason}"}
                    self._log("tool", name=call.name, args=call.args,
                              approved=False, blocked=True, reason=reason)
                    auto_results.append({"type": "tool_result", "tool_use_id": b["id"],
                                         "content": json.dumps(out)})
                else:
                    needs_approval.append({
                        "id": b["id"], "name": b["name"], "args": b.get("input", {}),
                        "tier": tier.value, "reason": reason,
                    })

            if needs_approval:
                if auto_results:
                    self.messages.append({"role": "user", "content": auto_results})
                self.pending = needs_approval
                return self._state(done=False, text=said, awaiting=True)

            self.messages.append({"role": "user", "content": auto_results})

        return self._state(done=True, text="(stopped: too many tool rounds)")

    def _state(self, *, done: bool, text: str = "",
               awaiting: bool = False) -> dict[str, Any]:
        return {
            "done": done, "awaiting_approval": awaiting, "text": text,
            "pending": self.pending, "transcript": self.transcript[-80:],
            "usage": self.usage, "cost_usd": self.estimated_cost_usd(),
            "model": self.model,
        }
