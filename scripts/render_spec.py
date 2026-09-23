#!/usr/bin/env python3
"""Print the gatekeeper's environment spec as YAML (for doctl, or to read on GitHub).

    python3 scripts/render_spec.py > gatekeeper/agents.yaml
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pratique.gatekeeper import render_spec  # noqa: E402

sys.stdout.write(render_spec())
