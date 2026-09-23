#!/usr/bin/env python3
"""Spin up a challenger agent in its own sandbox and send it at a Pratique site.

    export DIGITALOCEAN_ACCESS_TOKEN=...   # control-plane token for the team
    export HARNESS_INFERENCE_API_KEY=...   # model access key minted on the SAME team
    python3 scripts/challenge.py --model glm-5.3 --posture incognito          # over the JSON API
    python3 scripts/challenge.py --channel browser --posture incognito        # through the real page

The browser channel needs Chromium in the sandbox, which the stock image lacks: the script installs
Playwright + Chromium with `doctl harness-runtime exec` before the model is handed the task (a few
minutes). `--setup-only` stops after that and prints the session id; `--session <id>` resumes on
it, so the long part can run separately from the interview.

Streams the challenger's tool calls and text as it works, then prints the verdict, the door, the
replay URL and what the session cost. Removes the session unless `--setup-only` or `--keep`.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pratique.challenger import (BROWSER_SETUP, DEFAULT_CHALLENGER_MODEL, DEFAULT_SITE, challenge_prompt,  # noqa: E402
                                 challenger_manifest)
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
        brief = args.get("command") if isinstance(args, dict) and "command" in args else (json.dumps(args) if args else "")
        print(f"    tool {data.get('name') or data.get('tool') or '?'}: {str(brief)[:300]}", flush=True)
    elif kind == "run.failed":
        print(f"    {kind}: {data.get('message')}", flush=True)


def setup_browser(session_id: str, site: str) -> bool:
    doctl = shutil.which("doctl")
    if not doctl:
        print("doctl not found: the challenger will have to install a browser itself", flush=True)
        return False
    cmd = [doctl, "harness-runtime", "exec", session_id, "--timeout", "600", "--", "sh", "-c", BROWSER_SETUP.format(site=site.rstrip("/"))]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=660)
    tail = (r.stdout + r.stderr).strip().splitlines()[-4:]
    print(f"browser setup in {time.time() - t0:.0f}s (exit {r.returncode}):", flush=True)
    for line in tail:
        print("   ", line[:200], flush=True)
    return r.returncode == 0 and "browser ok" in r.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=os.environ.get("PRATIQUE_SITE") or DEFAULT_SITE)
    ap.add_argument("--model", default=DEFAULT_CHALLENGER_MODEL)
    ap.add_argument("--posture", choices=("incognito", "honest"), default="incognito")
    ap.add_argument("--channel", choices=("api", "browser"), default="api")
    ap.add_argument("--handle", default=None)
    ap.add_argument("--session", default=None, help="reuse an existing challenger session")
    ap.add_argument("--setup-only", action="store_true", help="create the session (and install the browser), then stop")
    ap.add_argument("--keep", action="store_true", help="do not remove the session at the end")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    handle = args.handle or f"{args.model} {args.channel} {args.posture}"
    h = Harness()
    sid = args.session
    if not sid:
        key = os.environ.get("HARNESS_INFERENCE_API_KEY")
        if not key:
            print("set HARNESS_INFERENCE_API_KEY", file=sys.stderr)
            return 2
        name = f"challenger-{uuid.uuid4().hex[:8]}"
        t0 = time.time()
        s = h.create_from_manifest(to_yaml(challenger_manifest(name=name, site=args.site, model=args.model, api_key=key, channel=args.channel)))
        sid = s["session_id"]
        print(f"challenger {name} ({args.model}, {args.channel}, {args.posture}) session {sid} {s.get('status')} in {time.time() - t0:.1f}s", flush=True)
        h.wait_ready(sid)
        if args.channel == "browser":
            setup_browser(sid, args.site)
        if args.setup_only:
            print(f"session ready: {sid}")
            return 0
    keep = args.keep
    try:
        turn = h.run_turn(sid, challenge_prompt(site=args.site, posture=args.posture, handle=handle, channel=args.channel),
                          timeout=args.timeout, on_event=show)
        print(f"run {turn.status} in {turn.seconds}s, ${turn.cost_usd:.4f}" + (f", error: {turn.error}" if turn.error else ""))
        print("challenger said:", turn.text[-700:].strip())
        m = re.search(r"RESULT\s+door=(\S+)\s+kind=(\S+)\s+replay=(\S+)", turn.text)
        if m:
            print(f"RESULT door={m.group(1)} kind={m.group(2)} replay={m.group(3)}")
        try:  # the site's own record is the truth, whatever the challenger claims
            with urllib.request.urlopen(args.site.rstrip("/") + "/api/leaderboard", timeout=20) as r:
                board = json.load(r)
            mine = [x for x in board.get("recent", []) if x.get("handle") == handle]
            if mine:
                x = mine[0]
                print(f"site record: door={x['door']} kind={x['kind']} ruled_by={x.get('ruled_by')} turns={x['turns']} cost=${x['cost_usd']} replay={args.site.rstrip('/')}/a/{x['id']}")
        except Exception as e:  # noqa: BLE001
            print("leaderboard check failed:", e)
    finally:
        if not keep:
            h.delete(sid)
            print(f"removed {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
