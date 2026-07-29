"""Tests for the per-host Grafana alert-rules override (DRAYVE-W-014).

Alert rule thresholds/labels/host-names are inherently host-specific --
unlike the framework-fixed contact-point/policy pair just above this task in
``roles/monitoring/tasks/main.yml``, which the same file for every consumer
would make sense for. Before this fix there was no consumer-override path
for Grafana alert *rules* at all (``ops_deploy_root`` always resolves to the
vendored framework checkout, never a per-consumer directory), so the only
way a consumer got custom alerting was hand-editing the deployed file
directly on the host -- outside any repo, invisible to a rebuild, and never
touched by ``deploy-prod`` (found live: prod-cloud's
``monitoring/grafana/provisioning/alerting/rules.yaml``).

Fix under test: an optional per-host override at
``deploy/<host>/grafana-alert-rules.yaml`` under ``ops_secrets_root`` (the
same idiom ``roles/deploy/tasks/main.yml`` already uses for
``compose.override.yml``) -- copied if present, removed if not, so a
consumer without one sees zero behavior change.

The wrapper below inlines the exact task sequence (same approach as
``test_monitoring_prometheus_force_recreate.py``).

Run: ``pytest tests/python/test_monitoring_grafana_alert_rules_override.py``
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

WRAPPER_TASKS = """\
            # --- roles/monitoring/tasks/main.yml (relevant subset, DRAYVE-W-014) ---
            - name: Check for host Grafana alert rules override
              ansible.builtin.stat:
                path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/grafana-alert-rules.yaml"
              register: _grafana_alert_rules_override
              tags: [monitoring, alerting]

            - name: Deploy host Grafana alert rules
              ansible.builtin.copy:
                src: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/grafana-alert-rules.yaml"
                dest: "{{ drayve_deploy_dir }}/monitoring/grafana/provisioning/alerting/rules.yaml"
              when: _grafana_alert_rules_override.stat.exists | default(false)
              tags: [monitoring, alerting]

            - name: Remove host Grafana alert rules if not present locally
              ansible.builtin.file:
                path: "{{ drayve_deploy_dir }}/monitoring/grafana/provisioning/alerting/rules.yaml"
                state: absent
              when: not (_grafana_alert_rules_override.stat.exists | default(false))
              tags: [monitoring, alerting]
"""


def _make_wrapper(tmp_path: Path) -> Path:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        "---\n"
        "- name: Test DRAYVE-W-014 Grafana alert-rules override\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n" + textwrap.indent(WRAPPER_TASKS, "    ")
    )
    return wrapper


def _run(wrapper: Path, ops_secrets_root: Path, deploy_dir: Path, host: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "ansible-playbook",
        "-i",
        f"{host},",
        str(wrapper),
        "-e",
        f"inventory_hostname={host}",
        "-e",
        f"ops_secrets_root={ops_secrets_root}",
        "-e",
        f"drayve_deploy_dir={deploy_dir}",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def test_override_present_gets_deployed(ansible_available, tmp_path):
    """Consumer with a per-host override -> it lands at the provisioning path."""
    wrapper = _make_wrapper(tmp_path)
    host = "myhost"
    override_dir = tmp_path / "site" / "deploy" / host
    override_dir.mkdir(parents=True)
    rule_content = "apiVersion: 1\ngroups: []\n"
    (override_dir / "grafana-alert-rules.yaml").write_text(rule_content)
    deploy_dir = tmp_path / "drayve-deploy"
    deploy_dir.mkdir()
    (deploy_dir / "monitoring/grafana/provisioning/alerting").mkdir(parents=True)

    result = _run(wrapper, ops_secrets_root=tmp_path / "site", deploy_dir=deploy_dir, host=host)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    deployed = deploy_dir / "monitoring/grafana/provisioning/alerting/rules.yaml"
    assert deployed.exists(), combined
    assert deployed.read_text() == rule_content


def test_override_absent_removes_stale_file(ansible_available, tmp_path):
    """No consumer override -> any previously-deployed rules.yaml is removed
    (drift protection: a consumer that drops its override doesn't leave a
    stale file lingering, same as compose.override.yml's cleanup task)."""
    wrapper = _make_wrapper(tmp_path)
    host = "myhost"
    (tmp_path / "site" / "deploy" / host).mkdir(parents=True)
    deploy_dir = tmp_path / "drayve-deploy"
    alerting_dir = deploy_dir / "monitoring/grafana/provisioning/alerting"
    alerting_dir.mkdir(parents=True)
    (alerting_dir / "rules.yaml").write_text("stale content from a prior deploy\n")

    result = _run(wrapper, ops_secrets_root=tmp_path / "site", deploy_dir=deploy_dir, host=host)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (alerting_dir / "rules.yaml").exists(), f"stale rules.yaml must be removed: {combined}"


def test_no_override_no_op_on_fresh_host(ansible_available, tmp_path):
    """No override, nothing previously deployed -> stays absent, no error."""
    wrapper = _make_wrapper(tmp_path)
    host = "myhost"
    (tmp_path / "site" / "deploy" / host).mkdir(parents=True)
    deploy_dir = tmp_path / "drayve-deploy"
    (deploy_dir / "monitoring/grafana/provisioning/alerting").mkdir(parents=True)

    result = _run(wrapper, ops_secrets_root=tmp_path / "site", deploy_dir=deploy_dir, host=host)

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (deploy_dir / "monitoring/grafana/provisioning/alerting/rules.yaml").exists(), combined
