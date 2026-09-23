"""The gatekeeper: its Harness Runtime spec, its prompts, and its reply contract.

The personality lives in gatekeeper/SKILL.md and is inlined into the spec as a skill. Everything
here is the plumbing around it.
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = ROOT / "gatekeeper" / "SKILL.md"
JUDGE_PATH = ROOT / "gatekeeper" / "HARBORMASTER.md"

DEFAULT_MODEL = "anthropic-claude-5-sonnet"
DEFAULT_JUDGE_MODEL = "anthropic-claude-fable-5.1"
DEFAULT_SIZE = "mars-1vcpu-1gb"
DEFAULT_IDLE = "5m"

MODEL_LABELS = {"anthropic-claude-5-sonnet": "Claude Sonnet 5", "anthropic-claude-fable-5.1": "Claude Fable 5.1",
                "anthropic-claude-fable-5": "Claude Fable 5", "anthropic-claude-opus-5": "Claude Opus 5",
                "anthropic-claude-haiku-4.5": "Claude Haiku 4.5", "glm-5.3": "GLM 5.3"}


def model_label(model_id: str) -> str:
    return MODEL_LABELS.get(model_id, model_id)

BUOY_COLORS = ["red", "green", "yellow", "blue", "orange", "white"]
CANARY_WORDS = ["lighthouse", "sextant", "barnacle", "monsoon", "albatross", "ballast",
                "spinnaker", "kelp", "bowsprit", "driftwood"]


def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def manifest(*, name: str = "pratique-gatekeeper", public_host: Optional[str] = None,
             model: Optional[str] = None, api_key: str = "${HARNESS_INFERENCE_API_KEY}",
             size: str = DEFAULT_SIZE, idle: str = DEFAULT_IDLE) -> Dict[str, Any]:
    """The environment spec as a dict. `api_key` defaults to a placeholder doctl expands."""
    model = model or os.environ.get("PRATIQUE_MODEL") or DEFAULT_MODEL
    public_host = public_host or os.environ.get("PRATIQUE_PUBLIC_HOST") or "pratique.digitalocean.solutions"
    return {
        "name": name,
        "agent": "claude-code",
        "size": size,
        "idle_timeout": idle,
        "persistent_workspace": False,
        "env": {"HARNESS_INFERENCE_MODEL": model},
        "secrets": {"HARNESS_INFERENCE_API_KEY": api_key},
        # Naming one host turns the allowlist on; the platform merges the inference endpoint in.
        "egress": [public_host],
        "skills": [{
            "name": "pratique-gatekeeper",
            "description": "Use when a login attempt needs clearance: interview the visitor and decide whether a person or an agent is knocking.",
            "instructions": skill_text(),
        }],
        # The gatekeeper talks; it does not touch the machine.
        "permissions": {"default": "deny", "rules": [{"tool": "file.read", "action": "allow"}]},
    }


def to_yaml(value: Any, indent: int = 0) -> str:
    """Render the spec as YAML. Handles exactly the shapes the spec uses: maps, lists, scalars,
    and multi-line strings as literal blocks. Not a general YAML emitter."""
    pad = " " * indent
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:\n{to_yaml(v, indent + 2)}")
            else:
                out.append(f"{pad}{k}: {to_yaml(v, indent + 2).lstrip() if not isinstance(v, str) or chr(10) not in v else to_yaml(v, indent + 2)}")
        return "\n".join(out)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                body = to_yaml(item, indent + 2)
                first, _, rest = body.partition("\n")
                out.append(f"{pad}- {first.lstrip()}" + (f"\n{rest}" if rest else ""))
            else:
                out.append(f"{pad}- {to_yaml(item, indent + 2).lstrip()}")
        return "\n".join(out)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if "\n" in s:
        lines = "\n".join(f"{pad}{line}" if line else "" for line in s.rstrip("\n").split("\n"))
        return f"|\n{lines}"
    if s.startswith("${") or re.fullmatch(r"[A-Za-z0-9._/:@-]+", s):
        return s
    return json.dumps(s)


def judge_manifest(*, name: str = "pratique-harbormaster", model: Optional[str] = None,
                   api_key: str = "${HARNESS_INFERENCE_API_KEY}", size: str = DEFAULT_SIZE, idle: str = "10m",
                   public_host: Optional[str] = None) -> Dict[str, Any]:
    """The harbormaster: reads the case file, rules once.

    It runs on the OpenCode adapter: the Claude Code adapter's bundled client (2.1.153 at the time
    of writing) refuses the newest Anthropic models, and OpenCode has no such gate. Unattended, so
    nothing is set to `ask`; its only egress is the site.
    """
    model = model or os.environ.get("PRATIQUE_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
    public_host = public_host or os.environ.get("PRATIQUE_PUBLIC_HOST") or "pratique.digitalocean.solutions"
    return {
        "name": name,
        "agent": "opencode",
        "size": size,
        "idle_timeout": idle,
        "persistent_workspace": False,
        "env": {"HARNESS_INFERENCE_MODEL": model},
        "secrets": {"HARNESS_INFERENCE_API_KEY": api_key},
        "egress": [public_host],
        "skills": [{
            "name": "pratique-harbormaster",
            "description": "Use when a case file from the gatekeeper arrives and a final ruling on a login attempt is required.",
            "instructions": JUDGE_PATH.read_text(encoding="utf-8"),
        }],
        "permissions": {"default": "allow", "rules": [{"tool": "bash", "action": "deny"}, {"tool": "file.write", "action": "deny"}]},
    }


def render_spec(judge: bool = False, **kw: Any) -> str:
    who = "harbormaster" if judge else "gatekeeper"
    header = (f"# Pratique {who}: DigitalOcean Harness Runtime environment spec.\n"
              f"# Generated by scripts/render_spec.py from pratique/gatekeeper.py and gatekeeper/{'HARBORMASTER' if judge else 'SKILL'}.md.\n"
              "# Save it once as an Environment Config, then start one session per login attempt from it:\n"
              f"#   doctl harness-runtime config create --spec gatekeeper/{'harbormaster' if judge else 'agents'}.yaml --name pratique-{who}-v1\n")
    return header + to_yaml(judge_manifest(**kw) if judge else manifest(**kw)) + "\n"


# -- prompts ---------------------------------------------------------------------------------

def new_attempt_facts(declared: str = "") -> Dict[str, Any]:
    return {
        "declared": declared if declared in ("human", "agent") else "",
        "canary": random.choice(CANARY_WORDS),
        "buoy": random.choice(BUOY_COLORS),
    }


def first_prompt(attempt_id: str, facts: Dict[str, Any]) -> str:
    declared = facts.get("declared") or "nothing"
    return (
        f"A new login attempt has arrived. Load the pratique-gatekeeper skill and follow it exactly.\n"
        f"Attempt id: {attempt_id}\n"
        f"Visitor declared: {declared}\n"
        f"Hidden canary phrase on the page (only a DOM reader can see it): \"{facts.get('canary')}\"\n"
        f"Color of the buoy drawn on the page's canvas (never in the page text): {facts.get('buoy')}\n"
        f"Begin now: greet in one line and ask your first question. Reply with the JSON contract only."
    )


def turn_prompt(visitor_text: str, telemetry: Dict[str, Any]) -> str:
    return (
        f"Visitor says: {visitor_text.strip()}\n"
        f"[telemetry] {json.dumps(telemetry, separators=(',', ':'))}\n"
        f"Reply with the JSON contract only."
    )


def case_file(attempt_id: str, facts: Dict[str, Any], turns: list, recommendation: Optional[Dict[str, Any]],
              client: str) -> str:
    """Everything the harbormaster gets: facts, the transcript with telemetry, the recommendation."""
    lines = [
        "A case file from the gatekeeper. Load the pratique-harbormaster skill and rule exactly as it says.",
        f"Attempt id: {attempt_id}",
        f"Visitor declared: {facts.get('declared') or 'nothing'}",
        f"Client: {client or 'unknown'}",
        f"Canary phrase planted on the page (visible only to a DOM reader): \"{facts.get('canary')}\"",
        f"Buoy color drawn on the page's canvas (never in the page text): {facts.get('buoy')}",
        "",
        "Transcript:",
    ]
    for t in turns:
        who = t.get("who", "")
        lines.append(f"- {who}: {t.get('text', '')}")
        if who == "visitor" and t.get("telemetry"):
            lines.append(f"  [telemetry] {json.dumps(t['telemetry'], separators=(',', ':'))}")
    lines += ["", "Gatekeeper's recommendation: " + (json.dumps(recommendation) if recommendation else "none (the interview ran out of questions)"),
              "", "Rule now. Reply with exactly one JSON object and nothing else, in this shape:",
              '{"say": "the ruling as the visitor hears it, under 40 words", "verdict": {"kind": "human" | "agent" | "undecided", '
              '"confidence": 0.0-1.0, "door": "granted" | "agent-door" | "refused", "evidence": ["two to five short concrete reasons"], '
              '"agrees_with_gatekeeper": true | false}}']
    return "\n".join(lines)


# -- the reply contract ------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_reply(text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Return (say, verdict). Tolerates prose around the object and code fences.

    Finds the last balanced {...} in the text. If nothing parses, the whole text is the `say`
    and there is no verdict, so a malformed turn degrades to a plain chat line.
    """
    cleaned = _FENCE.sub("", text or "").strip()
    end = cleaned.rfind("}")
    while end != -1:
        depth = 0
        start = -1
        for i in range(end, -1, -1):
            ch = cleaned[i]
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    start = i
                    break
        if start == -1:
            break
        try:
            obj = json.loads(cleaned[start:end + 1])
            if isinstance(obj, dict) and "say" in obj:
                decision = obj.get("recommendation", obj.get("verdict"))
                return str(obj.get("say", "")).strip(), (decision if isinstance(decision, dict) else None)
        except ValueError:
            pass
        end = cleaned.rfind("}", 0, start) if start > 0 else -1
    return cleaned, None
