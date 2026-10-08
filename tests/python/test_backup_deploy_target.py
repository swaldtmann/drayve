"""consumer.mk must offer a `backup-deploy` target.

deploy.yml does not run the backup role, so the kedge checkout on a host is
only moved by running backup.yml. `make backup-deploy` is the consumer entry
point for that. It changes backup configuration only, hence no pre-snapshot.

Run: ``pytest tests/python/test_backup_deploy_target.py``
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONSUMER_MK = ROOT / "ansible/consumer.mk"
SNAPSHOT_MARKER = "SNAPSHOT-MARKER-XYZ"


@pytest.fixture()
def site(tmp_path):
    (tmp_path / "Makefile").write_text(
        "NAME := testhost\n"
        "DRAYVE_REF := v9.9.9\n"
        "DRAYVE_DIR := vendor/drayve\n"
        "vendor:\n"
        "\t@true\n"
        f"include {CONSUMER_MK}\n"
    )
    return tmp_path


def _make(site, *args):
    return subprocess.run(
        ["make", "-n", f"PROD_SNAPSHOT={SNAPSHOT_MARKER}", *args],
        cwd=site,
        capture_output=True,
        text=True,
    )


def test_backup_deploy_runs_backup_playbook_with_deploy_args(site):
    res = _make(site, "backup-deploy", "CONFIRM=y", "EXTRA_ANSIBLE_VARS=--check --diff")
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if "ansible-playbook" in ln]
    assert len(lines) == 1, res.stdout
    line = lines[0]
    assert "playbooks/backup.yml" in line
    assert "-l testhost" in line
    assert "deploy_ref=v9.9.9" in line
    assert f"ops_secrets_root={site}" in line
    assert f"-e @{site}/stack.yaml" in line
    assert line.rstrip().endswith("--check --diff")


def test_backup_deploy_has_no_pre_snapshot(site):
    res = _make(site, "backup-deploy", "CONFIRM=y")
    assert res.returncode == 0, res.stderr
    assert SNAPSHOT_MARKER not in res.stdout
    assert "deploy.yml" not in res.stdout


def test_backup_deploy_asks_for_confirmation_like_deploy_prod(site):
    res = _make(site, "backup-deploy")
    assert res.returncode == 0, res.stderr
    assert "[y/N]" in res.stdout


def test_make_help_lists_backup_deploy(site):
    res = subprocess.run(["make", "help"], cwd=site, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "backup-deploy" in res.stdout
    assert "--check --diff" in res.stdout
