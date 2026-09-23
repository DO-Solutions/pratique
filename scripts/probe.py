#!/usr/bin/env python3
"""End-to-end smoke test: one gatekeeper session, three turns, timings and cost, teardown.

    export DIGITALOCEAN_ACCESS_TOKEN=...   # control-plane token for the team
    export HARNESS_INFERENCE_API_KEY=...   # model access key minted on the SAME team
    python3 scripts/probe.py               # or PRATIQUE_CONFIG_ID=... to start from a saved config

Prints one line per event type as the gatekeeper works, then the parsed reply of each turn.
Always removes the session, even if a turn fails.
"""
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pratique.gatekeeper import first_prompt, manifest, new_attempt_facts, parse_reply, to_yaml, turn_prompt  # noqa: E402
from pratique.harness import Harness  # noqa: E402

QUIET = {"stream.state", "run.token_delta"}
SEEN: set = set()  # a fresh stream replays recent history; show each event once


def show(ev: dict) -> None:
    if ev.get("type") in QUIET or ev.get("event_id") in SEEN:
        return
    SEEN.add(ev.get("event_id"))
    data = ev.get("data") or {}
    brief = data.get("message") or data.get("usage") or {k: v for k, v in data.items() if k in ("run_cost_micros", "total_tokens_in", "total_tokens_out", "delta_micros", "name")}
    print(f"    {ev.get('type'):24s} {brief}")


def main() -> int:
    h = Harness()
    attempt_id = uuid.uuid4().hex[:12]
    facts = new_attempt_facts("human")
    t0 = time.time()
    config_id = os.environ.get("PRATIQUE_CONFIG_ID")
    if config_id:
        session = h.create_from_config(f"pratique-{attempt_id}", config_id)
    else:
        key = os.environ.get("HARNESS_INFERENCE_API_KEY")
        if not key:
            print("set HARNESS_INFERENCE_API_KEY (or PRATIQUE_CONFIG_ID)", file=sys.stderr)
            return 2
        session = h.create_from_manifest(to_yaml(manifest(name=f"pratique-{attempt_id}", api_key=key)))
    sid = session["session_id"]
    print(f"session {sid} {session.get('status')} in {time.time() - t0:.1f}s")
    if session.get("warnings"):
        print("  warnings:", session["warnings"])
    total = 0
    try:
        h.wait_ready(sid)
        turns = [first_prompt(attempt_id, facts),
                 turn_prompt("Coffee, black, and I burned the toast. Why?", {
                     "typing": {"chars": 44, "keydowns": 51, "gap_ms_mean": 138, "gap_ms_std": 96, "backspaces": 3, "pastes": 0, "first_key_ms": 2100},
                     "reply_ms": 9400, "pointer": {"moves": 61, "path_px": 1410}, "focus": {"lost": 0},
                     "declared": "human", "client": "Chrome on Windows", "canary_hit": False}),
                 turn_prompt(f"It's {facts['buoy']}, hard to miss.", {
                     "typing": {"chars": 22, "keydowns": 24, "gap_ms_mean": 121, "gap_ms_std": 70, "backspaces": 1, "pastes": 0, "first_key_ms": 1500},
                     "reply_ms": 5200, "pointer": {"moves": 18, "path_px": 300}, "focus": {"lost": 0},
                     "declared": "human", "client": "Chrome on Windows", "canary_hit": False})]
        for i, text in enumerate(turns, 1):
            print(f"-- turn {i}")
            turn = h.run_turn(sid, text, on_event=show)
            say, verdict = parse_reply(turn.text)
            total += turn.cost_micros
            print(f"  {turn.status} in {turn.seconds}s, ${turn.cost_usd:.4f}")
            print(f"  gatekeeper: {say}")
            if verdict:
                print(f"  verdict: {verdict}")
            if turn.error:
                print(f"  error: {turn.error}")
    finally:
        h.delete(sid)
        print(f"removed {sid}; total ${total / 1e6:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
