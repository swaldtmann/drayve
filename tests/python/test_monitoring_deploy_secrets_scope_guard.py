"""Tests for the destructive tag-scope guards in the monitoring and deploy roles.

Bug 1: ``roles/monitoring/tasks/main.yml`` ("Remove Grafana alert policy when
neither SMTP nor Slack is configured") deletes ``policies.yaml`` whenever
``grafana_smtp_host`` and ``grafana_slack_token`` are empty. Both come
exclusively from ``roles/secrets`` (tag ``[secrets]``). A ``--tags monitoring``
run (no ``secrets`` tag) leaves them empty and deletes an already-deployed,
correctly-configured alert routing setup -- worse than just templating ``.env``
empty, because this actively removes something.

Bug 2: ``roles/deploy/tasks/main.yml``'s CrowdSec bouncer registration tasks
(tag ``[deploy, crowdsec]``) read ``crowdsec_bouncer_key``/``crowdsec_lapi_key``,
also only ever set by ``roles/secrets``. With an empty key the registration's
``when`` clause (``key | length > 0``) just silently skips -- no crash, but the
bouncer stays unregistered. Because Ansible runs a task if *any* of its tags is
requested, a bare ``--tags crowdsec`` run (no ``deploy``, no ``secrets``) also
reaches these tasks -- the deploy-role guard (tagged ``[deploy]`` only) does not
cover that case.

Fix under test: both spots re-stat ``secrets.yml`` (cheap, independent of
whether ``roles/secrets`` ran this pass) and use an ``ansible.builtin.assert``
that fails hard when the file exists but ``drayve_secrets_facts_loaded`` (the
sentinel set by ``roles/secrets``) was never set. The Grafana guard
additionally checks whether the target file already exists, so a host that
genuinely never configured alerting (and never had alert-config files
deployed) is not blocked.

The wrappers below inline the exact task sequences from
``roles/monitoring/tasks/main.yml`` and ``roles/deploy/tasks/main.yml`` so the
tests run against ``hosts: localhost`` without a real target host, docker, or
the full role dependency chain (dashboards, compose, cscli, ...).

Run: ``pytest tests/python/test_monitoring_deploy_secrets_scope_guard.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SECRETS_SENTINEL_TASKS = """\
            # --- roles/secrets/tasks/main.yml (relevant subset) ---
            - name: Check for host secrets file
              ansible.builtin.stat:
                path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
              register: secrets_file
              tags: [secrets]

            - name: Load host secrets (plaintext/quickstart)
              ansible.builtin.include_vars:
                file: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
                name: host_secrets
              when: secrets_file.stat.exists
              tags: [secrets]

            - name: Set secret variables
              ansible.builtin.set_fact:
                grafana_smtp_host: "{{ host_secrets.grafana_smtp_host | default('') }}"
                grafana_slack_token: "{{ host_secrets.grafana_slack_token | default('') }}"
                crowdsec_bouncer_key: "{{ host_secrets.crowdsec_bouncer_key | default('') }}"
                crowdsec_lapi_key: "{{ host_secrets.crowdsec_lapi_key | default('') }}"
              when: secrets_file.stat.exists
              no_log: true
              tags: [secrets]

            - name: Mark secret facts as loaded (cross-tag guard sentinel)
              ansible.builtin.set_fact:
                drayve_secrets_facts_loaded: true
              when: secrets_file.stat.exists
              tags: [secrets]
"""

GRAFANA_ALERT_GUARD_TASKS = """\
            # --- roles/monitoring/tasks/main.yml ---
            - name: Check for host secrets file (cross-tag guard)
              ansible.builtin.stat:
                path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
              register: _mon_secrets_file_check
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Check for existing Grafana alert policy file (cross-tag guard)
              ansible.builtin.stat:
                path: "{{ drayve_deploy_dir }}/alerting/policies.yaml"
              register: _mon_alert_policies_precheck
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Guard — fail instead of deleting Grafana alert config when secrets weren't loaded
              ansible.builtin.assert:
                that:
                  - >-
                    not (
                      _mon_secrets_file_check.stat.exists and
                      drayve_secrets_facts_loaded is not defined and
                      (grafana_smtp_host | default('') | length == 0) and
                      (grafana_slack_token | default('') | length == 0) and
                      (_mon_alert_policies_precheck.stat.exists | default(false))
                    )
                fail_msg: |
                  Secrets not loaded -- monitoring deploy run without the `secrets` tag?
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Remove Grafana alert policy when neither SMTP nor Slack is configured
              ansible.builtin.file:
                path: "{{ drayve_deploy_dir }}/alerting/policies.yaml"
                state: absent
              when: >-
                _mon_grafana and
                (grafana_smtp_host | default('') | length == 0) and
                (grafana_slack_token | default('') | length == 0)
              tags: [monitoring, alerting]
"""

CROWDSEC_GUARD_TASKS = """\
            # --- roles/deploy/tasks/main.yml ---
            - name: Check for host secrets file (cross-tag guard, crowdsec)
              ansible.builtin.stat:
                path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
              register: _deploy_crowdsec_secrets_file_check
              when: crowdsec_enabled
              tags: [deploy, crowdsec]

            - name: Guard — fail if secrets.yml exists but its facts were never loaded before CrowdSec bouncer registration
              ansible.builtin.assert:
                that:
                  - not (_deploy_crowdsec_secrets_file_check.stat.exists and drayve_secrets_facts_loaded is not defined)
                fail_msg: |
                  Secrets not loaded -- CrowdSec bouncer registration run without the `secrets` tag?
              when: crowdsec_enabled
              tags: [deploy, crowdsec]

            - name: Simulate bouncer registration reached (marker for the test)
              ansible.builtin.file:
                path: "{{ crowdsec_marker_path }}"
                state: touch
              when:
                - crowdsec_enabled
                - crowdsec_bouncer_key | default('') | length > 0
              tags: [deploy, crowdsec]
"""


def _make_wrapper(tmp_path: Path, guard_tasks: str) -> Path:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        "---\n"
        "- name: Test cross-tag guard\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n"
        + textwrap.indent(SECRETS_SENTINEL_TASKS, "    ")
        + textwrap.indent(guard_tasks, "    ")
    )
    return wrapper


def _layout(tmp_path: Path, host: str, secrets_body: str | None) -> Path:
    root = tmp_path / "site"
    deploy = root / "deploy" / host
    deploy.mkdir(parents=True)
    if secrets_body is not None:
        (deploy / "secrets.yml").write_text(secrets_body)
    return root


def _run(
    wrapper: Path,
    root: Path,
    host: str,
    extra_vars: dict[str, str],
    tags: str | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "ansible-playbook",
        "-i",
        f"{host},",
        str(wrapper),
        "-e",
        f"inventory_hostname={host}",
        "-e",
        f"ops_secrets_root={root}",
    ]
    if extra_vars:
        # A single JSON --extra-vars blob (rather than repeated key=value
        # args) so booleans/paths are typed correctly instead of landing as
        # raw strings (Ansible's key=value CLI form never type-coerces).
        cmd.extend(["-e", json.dumps(extra_vars)])
    if tags:
        cmd.extend(["--tags", tags])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


# ============================================================
# Bug 1 — Grafana alert policy delete guard
# ============================================================


def _grafana_layout(tmp_path: Path, host: str, secrets_body: str | None) -> tuple[Path, Path]:
    root = _layout(tmp_path, host, secrets_body)
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    (deploy_dir / "alerting" / "policies.yaml").write_text("apiVersion: 1\n")
    return root, deploy_dir


def test_monitoring_only_tag_fires_guard_instead_of_deleting_alert_config(
    ansible_available, tmp_path
):
    """--tags monitoring (no secrets) must not delete an
    existing, correctly deployed Grafana alert config."""
    wrapper = _make_wrapper(tmp_path, GRAFANA_ALERT_GUARD_TASKS)
    root, deploy_dir = _grafana_layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "Secrets not loaded" in combined, combined
    assert (deploy_dir / "alerting" / "policies.yaml").exists(), (
        "policies.yaml must survive when the guard fires"
    )


def test_secrets_plus_monitoring_tags_with_no_alert_channel_deletes_as_designed(
    ansible_available, tmp_path
):
    """Legitimate case: secrets loaded, but this host never configured
    SMTP or Slack alerting — deletion is the correct, intended behavior and
    must not be blocked by the guard."""
    wrapper = _make_wrapper(tmp_path, GRAFANA_ALERT_GUARD_TASKS)
    root, deploy_dir = _grafana_layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (deploy_dir / "alerting" / "policies.yaml").exists()


def test_monitoring_tag_without_any_secrets_file_is_not_blocked(ansible_available, tmp_path):
    """Nicht-Ziel: a host with no secrets.yml at all and no alert files ever
    deployed must not be blocked by the guard (nothing to protect)."""
    wrapper = _make_wrapper(tmp_path, GRAFANA_ALERT_GUARD_TASKS)
    root = _layout(tmp_path, "myhost", secrets_body=None)
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined


# ============================================================
# Bug 2 — CrowdSec bouncer registration guard
# ============================================================


def test_crowdsec_tag_alone_fires_guard(ansible_available, tmp_path):
    """A bare --tags crowdsec run (no deploy, no secrets) must also fail hard
    -- this is the gap the deploy-role guard (tagged [deploy] only) missed."""
    wrapper = _make_wrapper(tmp_path, CROWDSEC_GUARD_TASKS)
    root = _layout(tmp_path, "myhost", secrets_body="crowdsec_bouncer_key: realkey\n")
    marker = tmp_path / "marker"
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"crowdsec_enabled": True, "crowdsec_marker_path": str(marker)},
        tags="crowdsec",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "Secrets not loaded" in combined, combined
    assert not marker.exists(), "registration must not be reached when the guard fires"


def test_deploy_plus_crowdsec_tags_fires_guard(ansible_available, tmp_path):
    """--tags deploy,crowdsec (no secrets) must fail hard
    instead of silently skipping the bouncer registration."""
    wrapper = _make_wrapper(tmp_path, CROWDSEC_GUARD_TASKS)
    root = _layout(tmp_path, "myhost", secrets_body="crowdsec_bouncer_key: realkey\n")
    marker = tmp_path / "marker"
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"crowdsec_enabled": True, "crowdsec_marker_path": str(marker)},
        tags="deploy,crowdsec",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "Secrets not loaded" in combined, combined
    assert not marker.exists()


def test_secrets_plus_deploy_plus_crowdsec_tags_pass_and_register(ansible_available, tmp_path):
    """Fix suggested in the guard message must actually resolve the failure."""
    wrapper = _make_wrapper(tmp_path, CROWDSEC_GUARD_TASKS)
    root = _layout(tmp_path, "myhost", secrets_body="crowdsec_bouncer_key: realkey\n")
    marker = tmp_path / "marker"
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"crowdsec_enabled": True, "crowdsec_marker_path": str(marker)},
        tags="secrets,deploy,crowdsec",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert marker.exists()


def test_crowdsec_tag_without_any_secrets_file_is_not_blocked(ansible_available, tmp_path):
    """Nicht-Ziel: a host with no secrets.yml at all (crowdsec configured
    without a bouncer key on purpose) must not be blocked by the guard."""
    wrapper = _make_wrapper(tmp_path, CROWDSEC_GUARD_TASKS)
    root = _layout(tmp_path, "myhost", secrets_body=None)
    marker = tmp_path / "marker"
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"crowdsec_enabled": True, "crowdsec_marker_path": str(marker)},
        tags="crowdsec",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not marker.exists(), "no key configured -> registration correctly not reached"
