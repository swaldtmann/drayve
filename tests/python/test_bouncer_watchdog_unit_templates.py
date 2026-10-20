"""Regression test: the bouncer-watchdog systemd unit templates must keep
`Description=` and `Documentation=` on separate lines.

The timer template once had both glued together on one line
(`Description=... TimerDocumentation=...`), which systemd reads as a single
Description and silently drops the Documentation directive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "traefik" / "templates"

UNIT_TEMPLATES = ["bouncer-watchdog.service.j2", "bouncer-watchdog.timer.j2"]


@pytest.mark.parametrize("name", UNIT_TEMPLATES)
def test_description_and_documentation_on_separate_lines(name: str) -> None:
    lines = (TEMPLATES / name).read_text(encoding="utf-8").splitlines()

    desc = [ln for ln in lines if ln.startswith("Description=")]
    doc = [ln for ln in lines if ln.startswith("Documentation=")]

    assert len(desc) == 1, f"{name}: expected exactly one Description= line"
    assert len(doc) == 1, f"{name}: expected a Documentation= line at line start"
    assert "Documentation=" not in desc[0], (
        f"{name}: Documentation= is glued onto the Description= line"
    )
