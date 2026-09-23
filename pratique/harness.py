"""A small client for DigitalOcean Managed Agents / Harness Runtime sessions.

Only what Pratique needs: create a session (from a saved Environment Config or an inline
manifest), send a turn, follow the event stream, remove the session. Standard library only.

The API (verified live, September 2026):

    POST   /v2/agents/sessions                 application/x-yaml manifest, or JSON {name, config_id}
    GET    /v2/agents/sessions/{id}
    DELETE /v2/agents/sessions/{id}
    POST   /v2/agents/sessions/{id}/input      {"text": ...} -> {"run_id": ...}
    GET    /v2/agents/sessions/{id}/events     server-sent events; ?replay_only=true for history

Events look like {event_id, run_id, session_id, seq, timestamp, type, data}. The types this
client cares about are run.started, run.token_delta {text, is_reasoning}, run.usage_recorded,
run.cost_accrued, run.completed {run_cost_micros, ...} and run.failed {code, message}.
The server sends a ": keepalive" comment every 15 seconds.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional

DEFAULT_BASE = "https://api.digitalocean.com"
TERMINAL = {"run.completed", "run.failed", "run.error", "run.cancelled"}


class HarnessError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


@dataclass
class Turn:
    """One run: what the agent said and what it cost."""

    run_id: str
    text: str
    status: str  # completed | failed | timeout
    seconds: float
    usage: Dict[str, Any] = field(default_factory=dict)
    cost_micros: int = 0
    error: Optional[str] = None
    events: List[dict] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return self.cost_micros / 1_000_000


class EventStream:
    """Iterates the JSON payloads of a text/event-stream response. Close it when done."""

    def __init__(self, response):
        self._resp = response

    def __iter__(self) -> Iterator[dict]:
        for raw in self._resp:
            line = raw.rstrip(b"\r\n")
            if line.startswith(b":"):
                yield {"type": "stream.keepalive"}  # lets callers check deadlines on a silent session
                continue
            if not line.startswith(b"data:"):
                continue  # blank separators
            try:
                chunk = json.loads(line[5:].decode("utf-8").strip())
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                err = chunk["error"]
                raise HarnessError(int(err.get("http_code") or 0), str(err.get("message") or err))
            yield chunk.get("result") or chunk

    def close(self) -> None:
        try:
            self._resp.close()
        except Exception:  # noqa: BLE001 — best effort
            pass

    def __enter__(self) -> "EventStream":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class Harness:
    def __init__(self, token: Optional[str] = None, base: Optional[str] = None):
        self.token = token or os.environ.get("DIGITALOCEAN_ACCESS_TOKEN") or ""
        if not self.token:
            raise HarnessError(0, "DIGITALOCEAN_ACCESS_TOKEN is not set")
        self.base = (base or os.environ.get("DO_API_BASE") or DEFAULT_BASE).rstrip("/")

    # -- plumbing -----------------------------------------------------------------------------

    def _request(self, method: str, path: str, *, body: Any = None, raw: Optional[bytes] = None,
                 content_type: str = "application/json", accept: str = "application/json",
                 timeout: float = 120):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": accept}
        if data is not None:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, method=method, data=data, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise HarnessError(e.code, e.read().decode("utf-8", "replace")) from None

    def _json(self, method: str, path: str, **kw: Any) -> dict:
        with self._request(method, path, **kw) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text) if text.strip() else {}

    # -- sessions -----------------------------------------------------------------------------

    def create_from_config(self, name: str, config_id: str) -> dict:
        """Start a session from a saved Environment Config; secrets stay server-side."""
        d = self._json("POST", "/v2/agents/sessions", body={"name": name, "config_id": config_id}, timeout=600)
        return d.get("session") or d

    def create_from_manifest(self, manifest_yaml: str) -> dict:
        """Start a session from an inline spec (YAML or JSON text)."""
        d = self._json("POST", "/v2/agents/sessions", raw=manifest_yaml.encode("utf-8"),
                       content_type="application/x-yaml", timeout=600)
        return d.get("session") or d

    def get(self, session_id: str) -> dict:
        d = self._json("GET", f"/v2/agents/sessions/{session_id}")
        return d.get("session") or d

    def list_sessions(self) -> List[dict]:
        """Every session on the team (all pages)."""
        out: List[dict] = []
        token = ""
        while True:
            d = self._json("GET", "/v2/agents/sessions?page_size=200" + (f"&page_token={token}" if token else ""))
            out.extend(d.get("sessions") or [])
            token = d.get("next_page_token") or ""
            if not token:
                return out

    def delete(self, session_id: str) -> None:
        with self._request("DELETE", f"/v2/agents/sessions/{session_id}") as resp:
            resp.read()

    def wait_ready(self, session_id: str, timeout: float = 120) -> dict:
        deadline = time.time() + timeout
        while True:
            s = self.get(session_id)
            status = str(s.get("status", ""))
            if "READY" in status or "FAILED" in status or "DESTROY" in status or time.time() > deadline:
                return s
            time.sleep(0.5)

    # -- environment configs --------------------------------------------------------------------

    def list_configs(self) -> List[dict]:
        return list(self._json("GET", "/v2/agents/configs?page_size=200").get("configs") or [])

    def create_config(self, name: str, manifest_yaml: str) -> dict:
        """Save a spec as an immutable Environment Config; secrets in it stay server-side."""
        d = self._json("POST", "/v2/agents/configs", body={"name": name, "manifest_yaml": manifest_yaml})
        return d.get("config") or d

    def delete_config(self, config_id: str) -> None:
        with self._request("DELETE", f"/v2/agents/configs/{config_id}") as resp:
            resp.read()

    # -- runs ---------------------------------------------------------------------------------

    def send_input(self, session_id: str, text: str) -> str:
        return str(self._json("POST", f"/v2/agents/sessions/{session_id}/input", body={"text": text}).get("run_id", ""))

    def events(self, session_id: str, *, replay_only: bool = False, timeout: float = 900) -> EventStream:
        q = "?replay_only=true" if replay_only else ""
        return EventStream(self._request("GET", f"/v2/agents/sessions/{session_id}/events{q}",
                                         accept="text/event-stream", timeout=timeout))

    def run_turn(self, session_id: str, text: str, *, timeout: float = 120,
                 on_event: Optional[Callable[[dict], None]] = None) -> Turn:
        """Send one turn and block until its run finishes (or `timeout` seconds pass).

        The stream is opened before the input is posted so nothing is missed. `on_event` sees
        every event on the session as it arrives, which is how a UI relays the agent's work live.
        """
        started = time.time()
        stream = self.events(session_id)
        try:
            run_id = self.send_input(session_id, text)
            turn = Turn(run_id=run_id, text="", status="timeout", seconds=0.0)
            parts: List[str] = []
            for ev in stream:
                if ev.get("type") == "stream.keepalive":
                    if time.time() - started > timeout:
                        break
                    continue
                if on_event:
                    on_event(ev)
                if ev.get("run_id") != run_id:
                    continue
                turn.events.append(ev)
                kind = ev.get("type")
                data = ev.get("data") or {}
                if kind == "run.token_delta" and not data.get("is_reasoning"):
                    parts.append(str(data.get("text", "")))
                elif kind == "run.usage_recorded":
                    turn.usage = data.get("usage") or {}
                elif kind == "run.completed":
                    turn.status = "completed"
                    turn.cost_micros = int(data.get("run_cost_micros") or 0)
                    break
                elif kind in TERMINAL:
                    turn.status = "failed"
                    turn.error = str(data.get("message") or data.get("code") or kind)
                    break
                if time.time() - started > timeout:
                    break
            turn.text = "".join(parts).strip()
            turn.seconds = round(time.time() - started, 2)
            return turn
        finally:
            stream.close()
