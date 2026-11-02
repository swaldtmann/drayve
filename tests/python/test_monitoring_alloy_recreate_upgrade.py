"""Tests for the Alloy force-recreate guard on Promtail -> Alloy upgrades.

Bug: ``roles/monitoring/tasks/main.yml`` ("Force-recreate Alloy on config
change") runs before the deploy role re-renders ``docker-compose.yml``. On an
upgrade from <= v0.9.3 the old compose file exists but has no ``alloy``
service (it still has ``promtail``), so ``docker compose up -d
--force-recreate alloy`` failed with ``no such service: alloy`` (rc=1). Fresh
installs (no compose file) and idempotent re-runs (alloy already in the file)
never hit it.

Fix under test: the recreate only runs when the existing compose file
actually defines the ``alloy`` service. The full ``up -d --remove-orphans`` of
the deploy role creates alloy on upgrade anyway.

The tests extract the REAL tasks from the role by name (no inlined copy), run
them against ``hosts: localhost`` and use a fake ``docker`` shim on ``PATH``.

Run: ``pytest tests/python/test_monitoring_alloy_recreate_upgrade.py``
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROLE_TASKS = Path(__file__).resolve().parents[2] / "ansible/roles/monitoring/tasks/main.yml"

# Task names are matched by prefix so the guard may add helper tasks in between.
WANTED_PREFIXES = (
    "Check if compose file exists",
    "Check whether compose file defines",
    "Force-recreate Alloy",
)

COMPOSE_OLD = "services:\n  traefik:\n    image: x\n  promtail:\n    image: y\n"
COMPOSE_NEW = "services:\n  traefik:\n    image: x\n  alloy:\n    image: y\n"


def _real_tasks() -> list[dict]:
    tasks = yaml.safe_load(ROLE_TASKS.read_text())
    picked = [t for t in tasks if str(t.get("name", "")).startswith(WANTED_PREFIXES)]
    assert any(t["name"].startswith("Force-recreate Alloy") for t in picked)
    return picked


def _make_wrapper(tmp_path: Path) -> Path:
    play = [
        {
            "name": "Test Alloy force-recreate guard",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {"_mon_alloy": True},
            "tasks": [
                {
                    "name": "Simulate Alloy config result",
                    "ansible.builtin.set_fact": {
                        "_alloy_config": "{{ {'changed': alloy_config_changed | bool} }}"
                    },
                }
            ]
            + _real_tasks(),
        }
    ]
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(yaml.safe_dump(play))
    return wrapper


def _make_fake_docker(tmp_path: Path, log_file: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "docker"
    fake.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log_file}"\nexit 0\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run(tmp_path: Path, compose: str | None, config_changed: bool):
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    if compose is not None:
        (deploy_dir / "docker-compose.yml").write_text(compose)
    log_file = tmp_path / "docker-calls.log"
    bin_dir = _make_fake_docker(tmp_path, log_file)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    result = subprocess.run(
        [
            "ansible-playbook", "-i", "localhost,", str(_make_wrapper(tmp_path)),
            "-e", f"drayve_deploy_dir={deploy_dir}",
            "-e", f"drayve_user={os.environ.get('USER', 'root')}",
            "-e", f"alloy_config_changed={'true' if config_changed else 'false'}",
        ],
        capture_output=True, text=True, check=False, env=env,
    )
    return result, log_file


@pytest.fixture(scope="module", autouse=True)
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def test_upgrade_compose_without_alloy_skips_recreate(tmp_path):
    """Upgrade from Promtail era: compose exists, no alloy service."""
    result, log_file = _run(tmp_path, COMPOSE_OLD, config_changed=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not log_file.exists(), f"docker must not run: {log_file.read_text()}"


def test_compose_with_alloy_and_changed_config_recreates(tmp_path):
    result, log_file = _run(tmp_path, COMPOSE_NEW, config_changed=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "compose up -d --force-recreate alloy" in log_file.read_text()


def test_compose_with_alloy_unchanged_config_skips_recreate(tmp_path):
    result, log_file = _run(tmp_path, COMPOSE_NEW, config_changed=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not log_file.exists()


def test_fresh_install_without_compose_skips_recreate(tmp_path):
    result, log_file = _run(tmp_path, None, config_changed=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not log_file.exists()
