"""Regression test: bouncer-watchdog deploy-tasks must be gated on
both crowdsec_enabled AND bouncer_watchdog_enabled (AFKI-W-119).

Beifang #11 (S322 v0.3.x sweep) hypothesized: "What happens when
crowdsec_enabled=False but bouncer_watchdog_enabled=True (default)?"

Two findings from the code review:

1. **Ansible side:** all five deploy tasks AND the start-timer task carry
   `when: crowdsec_enabled and bouncer_watchdog_enabled`, and a sixth task
   stops + disables the timer when `not (crowdsec_enabled and
   bouncer_watchdog_enabled)`. So a host with `crowdsec_enabled=false`
   never gets the watchdog deployed (and any pre-existing timer is torn
   down).

2. **Script side:** `drayve-bouncer-watchdog.sh` does not talk to CrowdSec
   at all — it only probes the Traefik health URL and restarts Traefik on
   failure. There is no CrowdSec dependency to break.

So the failure mode the beifang worried about is not present today. This
test exists to keep it that way: if anyone removes/changes the gating
guard on the deploy tasks, the test fails loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_TASKS = REPO_ROOT / "ansible" / "roles" / "traefik" / "tasks" / "main.yml"

REQUIRED_CONDITION = "crowdsec_enabled and bouncer_watchdog_enabled"
DISABLE_CONDITION = "not (crowdsec_enabled and bouncer_watchdog_enabled)"

# Names of tasks that must be gated by REQUIRED_CONDITION.
GATED_TASK_NAMES = {
    "Deploy bouncer-watchdog script",
    "Deploy bouncer-watchdog systemd service unit",
    "Deploy bouncer-watchdog systemd timer unit",
    "Enable + start bouncer-watchdog timer",
}

DISABLE_TASK_NAME = "Stop + disable bouncer-watchdog when CrowdSec/Watchdog disabled"

RELOAD_TASK_NAME = "Reload systemd if watchdog units changed"


@pytest.fixture(scope="module")
def traefik_tasks() -> list[dict]:
    raw = yaml.safe_load(TRAEFIK_TASKS.read_text())
    assert isinstance(raw, list), "traefik tasks file must be a YAML list"
    return raw


def _task_by_name(tasks: list[dict], name: str) -> dict:
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(f"task not found: {name!r}")


@pytest.mark.parametrize("name", sorted(GATED_TASK_NAMES))
def test_gated_task_has_required_when(traefik_tasks, name):
    task = _task_by_name(traefik_tasks, name)
    when = task.get("when")
    assert when is not None, f"task {name!r} has no `when` clause"
    if isinstance(when, list):
        assert REQUIRED_CONDITION in when, (
            f"task {name!r} when-list missing {REQUIRED_CONDITION!r}: {when!r}"
        )
    else:
        assert when == REQUIRED_CONDITION, (
            f"task {name!r} has when={when!r}, expected {REQUIRED_CONDITION!r}"
        )


def test_reload_task_carries_required_condition(traefik_tasks):
    task = _task_by_name(traefik_tasks, RELOAD_TASK_NAME)
    when = task.get("when")
    assert isinstance(when, list), f"reload task `when` must be a list, got {when!r}"
    assert REQUIRED_CONDITION in when, (
        f"reload task missing {REQUIRED_CONDITION!r} in when-list: {when!r}"
    )


def test_disable_task_inverts_condition(traefik_tasks):
    task = _task_by_name(traefik_tasks, DISABLE_TASK_NAME)
    when = task.get("when")
    assert when == DISABLE_CONDITION, (
        f"disable task has when={when!r}, expected {DISABLE_CONDITION!r}"
    )
    assert task.get("failed_when") is False, (
        "disable task must use failed_when: false (timer may already be absent)"
    )


def test_watchdog_script_has_no_crowdsec_dependency():
    """The script restarts traefik on health-check failure. It must not
    touch CrowdSec — otherwise the gating test above would not be enough.
    """
    script = REPO_ROOT / "ansible" / "roles" / "traefik" / "files" / "drayve-bouncer-watchdog.sh"
    text = script.read_text().lower()
    forbidden = ("cscli", "crowdsec ", "/crowdsec", "crowdsec:")
    for token in forbidden:
        assert token not in text, (
            f"watchdog script must not reference {token!r} — "
            "CrowdSec independence is part of the design"
        )
