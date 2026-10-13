#!/usr/bin/env python3
"""Render crowdsec-whitelist.yaml.j2 for a JSON ``security`` dict (argv[1]).

Standalone (jinja2 only): Ansible's ``to_json`` is mapped to ``json.dumps``
and ``trim_blocks`` is on, as in Ansible's template engine.
"""

import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "ansible" / "roles" / "traefik" / "templates"
)

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
)
env.filters["to_json"] = json.dumps
sys.stdout.write(
    env.get_template("crowdsec-whitelist.yaml.j2").render(
        security=json.loads(sys.argv[1])
    )
)
