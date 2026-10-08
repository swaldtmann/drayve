"""Tests for the deploy role's `docker compose up` diagnostics and the
CrowdSec double-recreate guard (roles/deploy/tasks/main.yml).

1. Diagnostics: a CI flake showed Compose recreating `traefik` in the second
   (idempotence) run without any template change. The reason was not
   recoverable because the stderr of `Docker compose up` was never logged.
   A debug task directly after it now prints `compose_up.stderr_lines`
   whenever the step reports a change.

2. On a first rollout `config.yaml.local` is newly created (changed), `compose
   up` creates the crowdsec container anyway, and the unconditional
   "Force-recreate CrowdSec" task then rebuilt it a second time (~30 s of
   HTTP 403 on all services, traefik depends_on crowdsec: service_healthy).
   The force-recreate is now skipped when `compose up` already created or
   recreated crowdsec in the same run.

The `when` expression is taken from the real task file and evaluated by
ansible-playbook (Jinja, not string comparison).

Run: ``pytest tests/python/test_deploy_compose_up_diagnostics.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_TASKS = REPO_ROOT / "ansible" / "roles" / "deploy" / "tasks" / "main.yml"

COMPOSE_STDERR_CROWDSEC_RECREATED = (
    " Container crowdsec  Recreate\n"
    " Container crowdsec  Recreated\n"
    " Container traefik  Running\n"
)
COMPOSE_STDERR_CROWDSEC_CREATED = (
    " Container crowdsec  Creating\n"
    " Container crowdsec  Created\n"
    " Container crowdsec  Starting\n"
)
COMPOSE_STDERR_CROWDSEC_UNTOUCHED = (
    " Container crowdsec  Running\n"
    " Container traefik  Recreated\n"
    " Container landing  Running\n"
)

CROWDSEC_TASK = "Force-recreate CrowdSec on whitelist or config.yaml.local change"


def _tasks() -> list[dict]:
    return yaml.safe_load(DEPLOY_TASKS.read_text())


def _task(name: str) -> dict:
    for t in _tasks():
        if t.get("name") == name:
            return t
    raise AssertionError(f"task not found: {name}")


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


# --- 1. diagnostics -------------------------------------------------------


def test_debug_task_directly_after_compose_up():
    tasks = _tasks()
    names = [t.get("name") for t in tasks]
    idx = names.index("Docker compose up")
    nxt = tasks[idx + 1]
    assert "ansible.builtin.debug" in nxt, f"task after compose up is: {nxt}"
    assert "compose_up.stderr_lines" in json.dumps(nxt["ansible.builtin.debug"])
    assert nxt.get("when") == "compose_up is changed"
    assert "deploy" in nxt.get("tags", [])


# --- 2. CrowdSec double-recreate guard ------------------------------------


def _eval_when(tmp_path: Path, extra_vars: dict) -> bool:
    """Run the REAL `when` of the CrowdSec task in a debug task; True = would run."""
    when = _task(CROWDSEC_TASK)["when"]
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "probe",
                            "ansible.builtin.debug": {"msg": "WOULD_RUN_MARKER"},
                            "when": when,
                        }
                    ],
                }
            ]
        )
    )
    vars_file = tmp_path / "vars.json"
    vars_file.write_text(json.dumps(extra_vars))
    res = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", f"@{vars_file}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    return "WOULD_RUN_MARKER" in res.stdout


def test_recreate_command_unchanged():
    assert "docker compose up -d --force-recreate crowdsec" in _task(CROWDSEC_TASK)[
        "ansible.builtin.command"
    ]["cmd"]


def test_config_changed_and_compose_recreated_crowdsec_skips(ansible_available, tmp_path):
    assert not _eval_when(
        tmp_path,
        {
            "crowdsec_local_config": {"changed": True},
            "compose_up": {"stderr": COMPOSE_STDERR_CROWDSEC_RECREATED},
        },
    )


def test_config_changed_and_compose_created_crowdsec_skips(ansible_available, tmp_path):
    assert not _eval_when(
        tmp_path,
        {
            "crowdsec_whitelist": {"changed": True},
            "compose_up": {"stderr": COMPOSE_STDERR_CROWDSEC_CREATED},
        },
    )


def test_config_changed_and_compose_did_not_touch_crowdsec_runs(ansible_available, tmp_path):
    assert _eval_when(
        tmp_path,
        {
            "crowdsec_local_config": {"changed": True},
            "compose_up": {"stderr": COMPOSE_STDERR_CROWDSEC_UNTOUCHED},
        },
    )


def test_config_unchanged_skips(ansible_available, tmp_path):
    assert not _eval_when(
        tmp_path,
        {
            "crowdsec_local_config": {"changed": False},
            "crowdsec_whitelist": {"changed": False},
            "compose_up": {"stderr": COMPOSE_STDERR_CROWDSEC_UNTOUCHED},
        },
    )


def test_compose_up_undefined_tag_run_and_config_changed_runs(ansible_available, tmp_path):
    assert _eval_when(tmp_path, {"crowdsec_whitelist": {"changed": True}})


def test_similar_container_name_does_not_suppress(ansible_available, tmp_path):
    """`crowdsec-foo Recreated` must not be mistaken for the crowdsec container."""
    assert _eval_when(
        tmp_path,
        {
            "crowdsec_local_config": {"changed": True},
            "compose_up": {"stderr": " Container crowdsec-foo  Recreated\n"},
        },
    )
