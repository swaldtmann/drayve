"""Tests for DRAYVE-W-012 — Prometheus config reload didn't survive a
single-file bind mount.

Bug: Prometheus mounts ``prometheus.yml`` as a single-file bind mount.
``roles/monitoring/tasks/main.yml`` ("Deploy Prometheus config") replaces it
via ``ansible.builtin.copy`` (atomic write + rename -> new inode). Docker
binds a single-file mount to the inode at container-create time, not the
path, so a running container keeps serving the OLD file after the rename.
The old deploy-role task ("Reload Prometheus config via API", a POST to
``/-/reload``) makes Prometheus re-read what it thinks is its config file --
which, from the container's point of view, never changed. Scrape-job/alert
changes (e.g. AFKI-W-238's cadvisor gating) were silently never applied
until someone manually force-recreated the container.

Found live on prod-genua's AFKI-W-241 rollout: the rendered host file had 0
``cadvisor`` scrape jobs, ``docker exec prometheus cat .../prometheus.yml``
still showed 2, host/container inodes differed (683893 vs 526301).

Fix under test: register the "Deploy Prometheus config" task
(``prometheus_config``) and, in the deploy role, force-recreate the
Prometheus container when ``prometheus_config.changed`` -- the same pattern
already used for Traefik/Authelia/CrowdSec config changes just above it in
``roles/deploy/tasks/main.yml``.

The wrapper below inlines the exact task sequence (same approach as
``test_authelia_db_secrets_scope_guard.py``) so the test runs against
``hosts: localhost`` without a real target host or docker daemon -- a fake
``docker`` shim on ``PATH`` records invocations instead.

Run: ``pytest tests/python/test_monitoring_prometheus_force_recreate.py``
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

WRAPPER_TASKS = """\
            # --- roles/monitoring/tasks/main.yml (relevant subset) ---
            - name: Simulate "Deploy Prometheus config" result
              ansible.builtin.set_fact:
                prometheus_config: "{{ {'changed': prometheus_config_changed | bool} }}"
              tags: [monitoring]

            # --- roles/deploy/tasks/main.yml (relevant subset, DRAYVE-W-012) ---
            - name: Force-recreate Prometheus on config change
              ansible.builtin.command:
                cmd: docker compose up -d --force-recreate --no-deps prometheus
                chdir: "{{ drayve_deploy_dir }}"
              register: force_recreate_result
              failed_when: false
              when: prometheus_config.changed | default(false)
              tags: [deploy, monitoring]
"""


def _make_wrapper(tmp_path: Path) -> Path:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        "---\n"
        "- name: Test DRAYVE-W-012 Prometheus force-recreate\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n" + textwrap.indent(WRAPPER_TASKS, "    ")
    )
    return wrapper


def _make_fake_docker(tmp_path: Path, log_file: Path) -> Path:
    """A `docker` shim on PATH that records its argv instead of touching a
    real daemon -- proves whether the recreate command actually ran."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log_file}"\n'
        "exit 0\n"
    )
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run(
    wrapper: Path,
    deploy_dir: Path,
    prometheus_config_changed: bool,
    fake_bin_dir: Path,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin_dir}:{env['PATH']}"
    cmd = [
        "ansible-playbook",
        "-i",
        "localhost,",
        str(wrapper),
        "-e",
        f"drayve_deploy_dir={deploy_dir}",
        "-e",
        f"prometheus_config_changed={'true' if prometheus_config_changed else 'false'}",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def test_config_changed_triggers_force_recreate(ansible_available, tmp_path):
    """The bug's fix: a real config change must force-recreate the
    container, not just POST /-/reload (which the old task did and which
    doesn't survive the single-file bind-mount's stale inode)."""
    wrapper = _make_wrapper(tmp_path)
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    log_file = tmp_path / "docker-calls.log"
    bin_dir = _make_fake_docker(tmp_path, log_file)

    result = _run(wrapper, deploy_dir, prometheus_config_changed=True, fake_bin_dir=bin_dir)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert log_file.exists(), "docker was never invoked when config changed"
    calls = log_file.read_text()
    assert "compose up -d --force-recreate --no-deps prometheus" in calls, calls


def test_config_unchanged_skips_recreate(ansible_available, tmp_path):
    """No-op deploy (config identical) must not bounce Prometheus -- avoids
    an unnecessary restart/scrape-gap on every routine deploy."""
    wrapper = _make_wrapper(tmp_path)
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    log_file = tmp_path / "docker-calls.log"
    bin_dir = _make_fake_docker(tmp_path, log_file)

    result = _run(wrapper, deploy_dir, prometheus_config_changed=False, fake_bin_dir=bin_dir)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not log_file.exists(), f"docker must not be invoked when config is unchanged: {combined}"
