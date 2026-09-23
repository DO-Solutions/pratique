#!/usr/bin/env python3
"""Spin up a challenger agent in its own sandbox and send it at a Pratique site.

    export DIGITALOCEAN_ACCESS_TOKEN=...   # control-plane token for the team
    export HARNESS_INFERENCE_API_KEY=...   # model access key minted on the SAME team
    python3 scripts/challenge.py --model glm-5.3 --posture incognito --handle "glm-5.3 incognito"
    python3 scripts/challenge.py --posture honest

Streams the challenger's tool calls and text as it works, then prints the verdict, the door, the
replay URL and what the session cost. Always removes the session.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pratique.challenger import DEFAULT_CHALLENGER_MODEL, DEFAULT_SITE, challenge_prompt, challenger_manifest  # noqa: E402
from pratique.gatekeeper import to_yaml  # noqa: E402
from pratique.harness import Harness  # noqa: E402

SEEN: set = set()


def show(ev: dict) -> None:
    if ev.get("event_id") in SEEN:
        return
    SEEN.add(ev.get("event_id"))
    kind = ev.get("type", "")
    data = ev.get("data") or {}
    if kind == "run.tool_call_started":
        args = data.get("input") or data.get("arguments") or data.get("args") or {}
        brief = args.get("command") if isinstance(args, dict) else args
        print(f"    tool {data.get('name') or data.get('tool') or '?'}: {str(brief or json.dumps(data))[:220]}", flush=True)
    elif kind == "run.tool_call_completed":
        out = data.get("output") or data.get("result") or ""
        if out:
            print(f"      -> {str(out)[:160].replace(chr(10), ' ')}", flush=True)
    elif kind in ("run.log", "run.failed"):
        print(f"    {kind}: {data.get('message')}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=os.environ.get("PRATIQUE_SITE") or DEFAULT_SITE)
    ap.add_argument("--model", default=DEFAULT_CHALLENGER_MODEL)
    ap.add_argument("--posture", choices=("incognito", "honest"), default="incognito")
    ap.add_argument("--handle", default=None)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    handle = args.handle or f"{args.model} {args.posture}"
    key = os.environ.get("HARNESS_INFERENCE_API_KEY")
    if not key:
        print("set HARNESS_INFERENCE_API_KEY", file=sys.stderr)
        return 2
    h = Harness()
    name = f"challenger-{uuid.uuid4().hex[:8]}"
    t0 = time.time()
    s = h.create_from_manifest(to_yaml(challenger_manifest(name=name, site=args.site, model=args.model, api_key=key)))
    sid = s["session_id"]
    print(f"challenger {name} ({args.model}, {args.posture}) session {sid} {s.get('status')} in {time.time() - t0:.1f}s", flush=True)
    if s.get("warnings"):
        print("  warnings:", s["warnings"])
    try:
        h.wait_ready(sid)
        turn = h.run_turn(sid, challenge_prompt(site=args.site, posture=args.posture, handle=handle), timeout=args.timeout, on_event=show)
        print(f"run {turn.status} in {turn.seconds}s, ${turn.cost_usd:.4f}" + (f", error: {turn.error}" if turn.error else ""))
        print("challenger said:", turn.text[-600:].strip())
        m = re.search(r"RESULT\s+door=(\S+)\s+kind=(\S+)\s+replay=(\S+)", turn.text)
        if m:
            print(f"RESULT door={m.group(1)} kind={m.group(2)} replay={m.group(3)}")
        # the site's own record is the truth, whatever the challenger claims
        try:
            with urllib.request.urlopen(args.site.rstrip("/") + "/api/leaderboard", timeout=20) as r:
                board = json.load(r)
            mine = [x for x in board.get("recent", []) if x.get("handle") == handle]
            if mine:
                x = mine[0]
                print(f"site record: door={x['door']} kind={x['kind']} turns={x['turns']} cost=${x['cost_usd']} replay={args.site.rstrip('/')}/a/{x['id']}")
        except Exception as e:  # noqa: BLE001
            print("leaderboard check failed:", e)
    finally:
        h.delete(sid)
        print(f"removed {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
