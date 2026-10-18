"""Shipped defaults must not point at one operator's infrastructure.

A missing value that a feature needs must stop the run early with a clear
message instead of silently falling back to someone's private endpoint.

Covered:

1. ``backup_kedge_openbao_base_url`` has no default; the backup role asserts
   it is set when ``backup_kedge_use_openbao`` is true.
2. The Grafana Slack contact point takes its bot display name from
   ``grafana_alert_slack_username`` (neutral default) instead of a hard-coded
   name.
3. ``scripts/notify-ci-failure.sh`` has no built-in Slack channel and a
   neutral message text; without ``SLACK_CHANNEL`` it refuses.

Run: ``pytest tests/python/test_neutral_defaults.py``
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DEFAULTS = REPO_ROOT / "ansible" / "roles" / "backup" / "defaults" / "main.yml"
BACKUP_TASKS = REPO_ROOT / "ansible" / "roles" / "backup" / "tasks" / "main.yml"
MONITORING_DEFAULTS = REPO_ROOT / "ansible" / "roles" / "monitoring" / "defaults" / "main.yml"
MONITORING_TASKS = REPO_ROOT / "ansible" / "roles" / "monitoring" / "tasks" / "main.yml"
NOTIFY = REPO_ROOT / "scripts" / "notify-ci-failure.sh"

OPENBAO_ASSERT_TASK = "Assert OpenBao base URL is set when OpenBao-backed password is enabled"


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


# ============================================================
# 1. backup: OpenBao base URL
# ============================================================


def test_openbao_base_url_has_no_default():
    defaults = yaml.safe_load(BACKUP_DEFAULTS.read_text())
    assert defaults["backup_kedge_openbao_base_url"] == ""


def _real_task(path: Path, name: str) -> dict:
    tasks = yaml.safe_load(path.read_text())
    hits = [t for t in tasks if t.get("name") == name]
    assert hits, f"task {name!r} not found in {path}"
    return hits[0]


def _run_backup_assert(tmp_path: Path, extra_vars: str) -> subprocess.CompletedProcess[str]:
    """Run the *real* assert task from the backup role against the real defaults."""
    task = _real_task(BACKUP_TASKS, OPENBAO_ASSERT_TASK)
    play = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars_files": [str(BACKUP_DEFAULTS)],
            "tasks": [task],
        }
    ]
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(yaml.safe_dump(play))
    return subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            str(wrapper),
            "-e",
            "stack_name=t backup_target=kedge backup_enabled=true " + extra_vars,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_openbao_enabled_without_base_url_fails_fast(ansible_available, tmp_path):
    res = _run_backup_assert(tmp_path, "backup_kedge_use_openbao=true")
    out = res.stdout + res.stderr
    assert res.returncode != 0, out
    assert "backup_kedge_openbao_base_url" in out, out


def test_openbao_enabled_with_base_url_passes(ansible_available, tmp_path):
    res = _run_backup_assert(
        tmp_path,
        "backup_kedge_use_openbao=true backup_kedge_openbao_base_url=https://bao.example.com/v1",
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_openbao_disabled_needs_no_base_url(ansible_available, tmp_path):
    res = _run_backup_assert(tmp_path, "backup_kedge_use_openbao=false")
    assert res.returncode == 0, res.stdout + res.stderr


# ============================================================
# 2. monitoring: Slack bot display name
# ============================================================


def test_slack_username_default_is_neutral():
    defaults = yaml.safe_load(MONITORING_DEFAULTS.read_text())
    assert defaults["grafana_alert_slack_username"] == "drayve"


def test_contact_point_uses_username_variable():
    text = MONITORING_TASKS.read_text()
    assert "username: '{{ grafana_alert_slack_username }}'" in text
    assert "reggi_woos_ki" not in text


# ============================================================
# 3. notify-ci-failure.sh
# ============================================================


def _notify(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SLACK_", "CI_", "DRY_"))}
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(NOTIFY)], env=env, capture_output=True, text=True, check=False
    )


def test_notify_without_channel_refuses():
    res = _notify({"DRY_RUN": "1", "CI_REPO": "o/r"})
    assert res.returncode != 0, res.stdout
    assert "SLACK_CHANNEL" in res.stderr


def test_notify_with_channel_posts_neutral_text():
    res = _notify(
        {
            "DRY_RUN": "1",
            "SLACK_CHANNEL": "CTESTCHAN1",
            "CI_REPO": "o/r",
            "CI_REF": "main",
            "CI_SHA": "0123456789abcdef",
            "CI_RUN_NUMBER": "7",
            "CI_RUN_URL": "https://ci.example.com/run/7",
        }
    )
    assert res.returncode == 0, res.stderr
    assert "channel=CTESTCHAN1" in res.stdout
    assert "CI failed on main" in res.stdout
    assert "byrd" not in res.stdout.lower()


def test_notify_script_has_no_house_channel_or_name():
    text = NOTIFY.read_text()
    assert "C0BAJ39L0SH" not in text
    assert "wooki" not in text.lower()
    assert "byrd" not in text.lower()
    assert "Stephan" not in text


def test_workflow_passes_channel_from_vars():
    wf = (REPO_ROOT / ".gitea" / "workflows" / "test.yml").read_text()
    assert "SLACK_CHANNEL: ${{ vars.SLACK_CHANNEL }}" in wf
    assert "C0BAJ39L0SH" not in wf

