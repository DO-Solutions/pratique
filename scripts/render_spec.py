#!/usr/bin/env python3
"""Print an environment spec as YAML (for doctl, or to read on GitHub).

    python3 scripts/render_spec.py > gatekeeper/agents.yaml            # the gatekeeper (interviews)
    python3 scripts/render_spec.py --judge > gatekeeper/harbormaster.yaml   # the harbormaster (rules)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pratique.gatekeeper import render_spec  # noqa: E402

sys.stdout.write(render_spec(judge="--judge" in sys.argv))
