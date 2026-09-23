#!/usr/bin/env python3
"""Rebuild a campaign's results table from the site's own records.

    python3 scripts/campaign_report.py [--site URL] [--runs 3] [--models slug,slug,...]

The site is the source of truth: for every model and run number it lists the handle's attempts
(a challenger may sign in more than once) and reports the newest decided one, plus how many
attempts it took. Useful when the runner's own bookkeeping was wrong or the campaign was cut short.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import MODELS, PERSONAS, site_handle  # noqa: E402
from pratique.challenger import DEFAULT_SITE  # noqa: E402


def attempts(site: str, handle: str) -> list:
    with urllib.request.urlopen(site.rstrip("/") + "/api/attempts?handle=" + urllib.parse.quote(site_handle(handle)), timeout=20) as r:
        return json.load(r).get("attempts") or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=os.environ.get("PRATIQUE_SITE") or DEFAULT_SITE)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--models", default=",".join(s for s, _ in MODELS))
    args = ap.parse_args()
    site = args.site.rstrip("/")
    rows, tally = [], {}
    for slug, model in MODELS:
        if slug not in set(args.models.split(",")):
            continue
        for n in range(1, args.runs + 1):
            mine = attempts(site, f"{slug} #{n}")
            decided = [x for x in mine if x.get("state") == "decided"]
            x = (decided or mine or [None])[0]
            persona = PERSONAS[(n - 1) % len(PERSONAS)][0]
            if not x:
                rows.append((slug, n, persona, "never signed in", "", "", "", "", 0, ""))
                tally.setdefault(slug, []).append("none")
                continue
            door = x.get("door") or x.get("state")
            rows.append((slug, n, persona, door, x.get("kind") or "", x.get("ruled_by") or "", x.get("turns"), x.get("cost_usd"),
                         len(mine), f"{site}/a/{x['id']}"))
            tally.setdefault(slug, []).append(door)
    lines = ["| model | run | persona | door | kind | ruled by | turns | site $ | attempts | replay |", "|---|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | {r[8]} | {r[9]} |" for r in rows]
    lines += ["", "| model | granted | agent-door | refused | other |", "|---|---|---|---|---|"]
    for slug, doors in tally.items():
        lines.append(f"| {slug} | {doors.count('granted')} | {doors.count('agent-door')} | {doors.count('refused')} | "
                     f"{sum(1 for d in doors if d not in ('granted', 'agent-door', 'refused'))} |")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
