"""Tests for KIG-W-054 Schritt 2b — Slack critical-alert contact point +
the root-policy routing gap it uncovered.

Background: drayve already had two independent, off-by-default Grafana
alert delivery channels: the legacy ``kigulls-api-webhook`` contact point
(gated on ``grafana_alert_api_token``) and the SMTP email contact point
(gated on ``grafana_smtp_host``, KIG-W-054, see
``test_monitoring_smtp_alerting.py``). Neither of them wires
severity=critical to Slack. This adds a third, independent contact point
(``type: slack``, gated on the new ``grafana_slack_token`` secret var) and
fixes a routing gap found while building it: ``policies.yaml`` (the file
that actually points Grafana's root policy at a receiver) was ONLY ever
deployed by the webhook block, gated on ``grafana_alert_api_token`` — a host
that migrated to SMTP-only (token unset) never got a ``policies.yaml`` at
all, silently falling back to Grafana's own built-in default receiver
instead of the configured ``grafana-smtp-email`` contact point.

Four things are covered here, each mirroring an existing test's approach:

1. ``docker-compose.yml.j2`` only emits ``SLACK_TOKEN`` for the grafana
   container when ``grafana_slack_token`` is set — independent of the
   webhook/SMTP gates (isolated-snippet + anchor-test approach, same as
   ``test_monitoring_smtp_alerting.py``).
2. ``ansible.builtin.assert`` fails fast when ``grafana_slack_token`` is set
   but ``grafana_alert_slack_channel`` is empty.
3. The AFKI-W-239 cross-tag delete-guard is extended to also protect the new
   ``contact-points-slack-critical.yaml``.
4. A new, independent ``policies.yaml`` deploy task (root receiver
   ``grafana-smtp-email`` when SMTP is set, else Grafana's built-in default
   ``grafana-default-email`` — never an undefined receiver; plus a
   severity=critical route to ``slack-critical`` with ``continue: true``
   when Slack is set) fires whenever SMTP or Slack is configured — not just
   the old webhook token. The existing webhook-path removal task is
   refined so it only deletes ``policies.yaml`` when webhook, SMTP AND
   Slack are all unset (it must not clobber a policies.yaml that SMTP/Slack
   still need), and the AFKI-W-239 guard is extended to match.

Run: ``pytest tests/python/test_monitoring_slack_alerting.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"
ENV_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "env.j2"
TASKS_FILE = REPO_ROOT / "ansible" / "roles" / "monitoring" / "tasks" / "main.yml"


# ============================================================
# 1. docker-compose.yml.j2 — SLACK_TOKEN follows grafana_slack_token
# ============================================================

_GRAFANA_ENV_SNIPPET = (
    "{% if grafana_alert_api_token | default('') | length > 0 %}\n"
    "      - KIGULLS_API_TOKEN=${KIGULLS_API_TOKEN:-}\n"
    "{% endif %}\n"
    "{% if grafana_smtp_host | default('') | length > 0 %}\n"
    "      - GF_SMTP_ENABLED=true\n"
    "{% endif %}\n"
    "{% if grafana_slack_token | default('') | length > 0 %}\n"
    "      - SLACK_TOKEN=${SLACK_TOKEN}\n"
    "{% endif %}\n"
)


def _render_env_snippet(
    *,
    grafana_slack_token: str = "",
    grafana_smtp_host: str = "",
    grafana_alert_api_token: str = "",
) -> str:
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    template = env.from_string(_GRAFANA_ENV_SNIPPET)
    return template.render(
        grafana_slack_token=grafana_slack_token,
        grafana_smtp_host=grafana_smtp_host,
        grafana_alert_api_token=grafana_alert_api_token,
    )


def test_no_slack_token_emits_no_slack_env_var():
    out = _render_env_snippet()
    assert "SLACK_TOKEN" not in out


def test_slack_token_set_emits_slack_env_var():
    out = _render_env_snippet(grafana_slack_token="xoxb-test")
    assert "SLACK_TOKEN=${SLACK_TOKEN}" in out


def test_slack_gate_is_independent_of_smtp_and_webhook_gates():
    """All three channels are additive — setting one must not imply or
    exclude the others."""
    out = _render_env_snippet(grafana_alert_api_token="tok123", grafana_smtp_host="smtp.example.com")
    assert "KIGULLS_API_TOKEN" in out
    assert "GF_SMTP_ENABLED=true" in out
    assert "SLACK_TOKEN" not in out

    out = _render_env_snippet(
        grafana_alert_api_token="tok123",
        grafana_smtp_host="smtp.example.com",
        grafana_slack_token="xoxb-test",
    )
    assert "KIGULLS_API_TOKEN" in out
    assert "GF_SMTP_ENABLED=true" in out
    assert "SLACK_TOKEN=${SLACK_TOKEN}" in out


def test_compose_template_actually_gates_slack_env_var():
    """Anchor test: the production template was changed, not just this snippet."""
    text = COMPOSE_TEMPLATE.read_text()
    assert "{% if grafana_slack_token | default('') | length > 0 %}" in text
    assert "- SLACK_TOKEN=${SLACK_TOKEN}" in text


def test_env_template_emits_slack_token_unconditionally_with_empty_default():
    """env.j2 always writes SLACK_TOKEN to .env, empty by default — same
    idiom as KIGULLS_API_TOKEN/GRAFANA_SMTP_*."""
    text = ENV_TEMPLATE.read_text()
    assert "SLACK_TOKEN={{ grafana_slack_token | default('') }}" in text


# ============================================================
# 2-4 — ansible-playbook wrapper tests (assert, deploy, root policy, guards)
# ============================================================

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
                grafana_alert_api_token: "{{ host_secrets.grafana_alert_api_token | default('') }}"
                grafana_smtp_host: "{{ host_secrets.grafana_smtp_host | default('') }}"
                grafana_slack_token: "{{ host_secrets.grafana_slack_token | default('') }}"
              when: secrets_file.stat.exists
              no_log: true
              tags: [secrets]

            - name: Mark secret facts as loaded (cross-tag guard sentinel)
              ansible.builtin.set_fact:
                drayve_secrets_facts_loaded: true
              when: secrets_file.stat.exists
              tags: [secrets]
"""

# Inlines the exact task sequence added to roles/monitoring/tasks/main.yml for
# KIG-W-054 Schritt 2b (Slack assert + deploy + root policy + AFKI-W-239
# guard + removal), same approach as test_monitoring_smtp_alerting.py.
SLACK_AND_ROOT_POLICY_TASKS = """\
            # --- roles/monitoring/tasks/main.yml (KIG-W-054 Schritt 2b) ---
            - name: Assert Grafana alert Slack channel is configured when Slack token is set
              ansible.builtin.assert:
                that:
                  - grafana_alert_slack_channel | default('') | length > 0
                fail_msg: "grafana_slack_token is set but grafana_alert_slack_channel is empty."
              when: _mon_grafana and (grafana_slack_token | default('') | length > 0)
              tags: [monitoring, alerting]

            - name: Deploy Grafana alert contact point (Slack critical)
              ansible.builtin.copy:
                dest: "{{ drayve_deploy_dir }}/alerting/contact-points-slack-critical.yaml"
                content: |
                  apiVersion: 1
                  contactPoints:
                    - orgId: 1
                      name: slack-critical
                      receivers:
                        - uid: slack-critical-1
                          type: slack
                          settings:
                            recipient: '{{ grafana_alert_slack_channel }}'
                            token: $SLACK_TOKEN
                            username: reggi_woos_ki
              when: _mon_grafana and (grafana_slack_token | default('') | length > 0)
              register: grafana_alert_slack_contact_point
              tags: [monitoring, alerting]

            - name: "Deploy Grafana alert policy (root: SMTP email + Slack critical)"
              ansible.builtin.copy:
                dest: "{{ drayve_deploy_dir }}/alerting/policies.yaml"
                content: |
                  apiVersion: 1

                  policies:
                    - orgId: 1
                      receiver: {{ 'grafana-smtp-email' if (grafana_smtp_host | default('') | length > 0) else 'grafana-default-email' }}
                      group_by: ['alertname', 'severity']
                      group_wait: 30s
                      group_interval: 5m
                      repeat_interval: 4h
                  {% if grafana_slack_token | default('') | length > 0 %}
                      routes:
                        - receiver: slack-critical
                          matchers:
                            - severity = critical
                          continue: true
                  {% endif %}
              when: >-
                _mon_grafana and
                ((grafana_smtp_host | default('') | length > 0) or
                 (grafana_slack_token | default('') | length > 0))
              register: grafana_alert_root_policy
              tags: [monitoring, alerting]

            - name: Check for host secrets file (cross-tag guard, AFKI-W-239)
              ansible.builtin.stat:
                path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
              register: _mon_secrets_file_check
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Check for existing Grafana alert policy file (cross-tag guard, AFKI-W-239)
              ansible.builtin.stat:
                path: "{{ drayve_deploy_dir }}/alerting/policies.yaml"
              register: _mon_alert_policies_precheck
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Check for existing Grafana alert contact-point file (Slack critical) (cross-tag guard, AFKI-W-239)
              ansible.builtin.stat:
                path: "{{ drayve_deploy_dir }}/alerting/contact-points-slack-critical.yaml"
              register: _mon_alert_slack_contact_point_precheck
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Guard — fail instead of deleting Grafana alert config when secrets weren't loaded (AFKI-W-239)
              ansible.builtin.assert:
                that:
                  - >-
                    not (
                      _mon_secrets_file_check.stat.exists and
                      drayve_secrets_facts_loaded is not defined and
                      (grafana_alert_api_token | default('') | length == 0) and
                      (grafana_smtp_host | default('') | length == 0) and
                      (grafana_slack_token | default('') | length == 0) and
                      (_mon_alert_policies_precheck.stat.exists | default(false))
                    )
                  - >-
                    not (
                      _mon_secrets_file_check.stat.exists and
                      drayve_secrets_facts_loaded is not defined and
                      (grafana_slack_token | default('') | length == 0) and
                      (_mon_alert_slack_contact_point_precheck.stat.exists | default(false))
                    )
                fail_msg: |
                  Secrets nicht geladen -- Monitoring-Deploy ohne `secrets`-Tag gefahren?
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Remove Grafana alert contact point/policy when token not configured
              ansible.builtin.file:
                path: "{{ drayve_deploy_dir }}/alerting/{{ item }}"
                state: absent
              loop:
                - contact-points.yaml
                - policies.yaml
              when: >-
                _mon_grafana and (grafana_alert_api_token | default('') | length == 0) and
                (item == 'contact-points.yaml' or
                 ((grafana_smtp_host | default('') | length == 0) and
                  (grafana_slack_token | default('') | length == 0)))
              tags: [monitoring, alerting]

            - name: Remove Grafana alert contact point (Slack critical) when Slack not configured
              ansible.builtin.file:
                path: "{{ drayve_deploy_dir }}/alerting/contact-points-slack-critical.yaml"
                state: absent
              when: _mon_grafana and (grafana_slack_token | default('') | length == 0)
              tags: [monitoring, alerting]
"""


def _make_wrapper(tmp_path: Path) -> Path:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        "---\n"
        "- name: Test KIG-W-054 Schritt 2b Slack + root-policy routing\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n"
        + textwrap.indent(SECRETS_SENTINEL_TASKS, "    ")
        + textwrap.indent(SLACK_AND_ROOT_POLICY_TASKS, "    ")
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
    extra_vars: dict[str, object],
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
        cmd.extend(["-e", json.dumps(extra_vars)])
    if tags:
        cmd.extend(["--tags", tags])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


# ---- assert: grafana_alert_slack_channel required when Slack gate is active ----


def test_slack_enabled_without_channel_fails_fast(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="grafana_slack_token: xoxb-test\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={
            "_mon_grafana": True,
            "drayve_deploy_dir": str(deploy_dir),
            "grafana_alert_slack_channel": "",
        },
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "grafana_alert_slack_channel is empty" in combined, combined
    assert not (deploy_dir / "alerting" / "contact-points-slack-critical.yaml").exists()


def test_slack_enabled_with_channel_deploys_contact_point(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="grafana_slack_token: xoxb-test\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={
            "_mon_grafana": True,
            "drayve_deploy_dir": str(deploy_dir),
            "grafana_alert_slack_channel": "#00_wooki",
        },
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    content = (deploy_dir / "alerting" / "contact-points-slack-critical.yaml").read_text()
    assert "#00_wooki" in content
    assert "type: slack" in content


def test_slack_disabled_by_default_no_contact_point_file(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
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
    assert not (deploy_dir / "alerting" / "contact-points-slack-critical.yaml").exists()


# ---- AFKI-W-239 delete guard extended to the Slack contact point ----


def test_monitoring_only_tag_fires_guard_instead_of_deleting_slack_contact_point(
    ansible_available, tmp_path
):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    (deploy_dir / "alerting" / "contact-points-slack-critical.yaml").write_text("apiVersion: 1\n")
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "Secrets nicht geladen" in combined, combined
    assert (deploy_dir / "alerting" / "contact-points-slack-critical.yaml").exists()


def test_secrets_plus_monitoring_tags_with_slack_unset_deletes_contact_point_as_designed(
    ansible_available, tmp_path
):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    (deploy_dir / "alerting" / "contact-points-slack-critical.yaml").write_text("apiVersion: 1\n")
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (deploy_dir / "alerting" / "contact-points-slack-critical.yaml").exists()


# ---- Root policy (policies.yaml): deploy gate + receiver/routing logic ----


def test_root_policy_not_deployed_when_neither_smtp_nor_slack_set(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
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
    assert not (deploy_dir / "alerting" / "policies.yaml").exists()


def test_root_policy_receiver_is_smtp_email_when_smtp_set(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="grafana_smtp_host: smtp.example.com\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    doc = yaml.safe_load((deploy_dir / "alerting" / "policies.yaml").read_text())
    assert doc["policies"][0]["receiver"] == "grafana-smtp-email"
    assert "routes" not in doc["policies"][0]


def test_root_policy_receiver_is_grafana_default_when_only_slack_set(ansible_available, tmp_path):
    """Not routing to an undefined receiver when SMTP isn't configured —
    root stays on Grafana's own built-in default contact point."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="grafana_slack_token: xoxb-test\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={
            "_mon_grafana": True,
            "drayve_deploy_dir": str(deploy_dir),
            "grafana_alert_slack_channel": "#00_wooki",
        },
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    doc = yaml.safe_load((deploy_dir / "alerting" / "policies.yaml").read_text())
    assert doc["policies"][0]["receiver"] == "grafana-default-email"


def test_root_policy_includes_slack_critical_route_with_continue_true_when_slack_set(
    ansible_available, tmp_path
):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(
        tmp_path,
        "myhost",
        secrets_body="grafana_smtp_host: smtp.example.com\ngrafana_slack_token: xoxb-test\n",
    )
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={
            "_mon_grafana": True,
            "drayve_deploy_dir": str(deploy_dir),
            "grafana_alert_slack_channel": "#00_wooki",
        },
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    doc = yaml.safe_load((deploy_dir / "alerting" / "policies.yaml").read_text())
    root_policy = doc["policies"][0]
    assert root_policy["receiver"] == "grafana-smtp-email"
    routes = root_policy["routes"]
    assert routes[0]["receiver"] == "slack-critical"
    assert routes[0]["continue"] is True
    assert routes[0]["matchers"] == ["severity = critical"]


def test_root_policy_has_no_slack_route_when_only_smtp_set(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="grafana_smtp_host: smtp.example.com\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    doc = yaml.safe_load((deploy_dir / "alerting" / "policies.yaml").read_text())
    assert "routes" not in doc["policies"][0]


# ---- AFKI-W-239 guard + refined removal for policies.yaml ----


def test_monitoring_only_tag_fires_guard_instead_of_deleting_root_policy(ansible_available, tmp_path):
    """oops-0096-class repro: --tags monitoring (no secrets) must not delete
    an existing, correctly deployed SMTP/Slack root policy."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    (deploy_dir / "alerting" / "policies.yaml").write_text("apiVersion: 1\n")
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "Secrets nicht geladen" in combined, combined
    assert (deploy_dir / "alerting" / "policies.yaml").exists()


def test_secrets_plus_monitoring_with_smtp_and_slack_unset_deletes_root_policy_as_designed(
    ansible_available, tmp_path
):
    """Legitimate case: secrets loaded, host never configured SMTP or Slack
    (and no webhook token either) — deletion is correct and must not be
    blocked by the guard."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    (deploy_dir / "alerting" / "policies.yaml").write_text("apiVersion: 1\n")
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


def test_root_policy_survives_when_only_slack_disabled_but_smtp_still_configured(
    ansible_available, tmp_path
):
    """Refined removal condition: the webhook-path removal task must not
    delete policies.yaml just because Slack got disabled — SMTP still owns
    the file. Simulates a host that never had a webhook token, previously
    configured SMTP+Slack, and now dropped Slack again."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="grafana_smtp_host: smtp.example.com\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    (deploy_dir / "alerting" / "policies.yaml").write_text("apiVersion: 1\npolicies: []\n")
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    doc = yaml.safe_load((deploy_dir / "alerting" / "policies.yaml").read_text())
    assert doc["policies"][0]["receiver"] == "grafana-smtp-email"
    assert "routes" not in doc["policies"][0]


# ============================================================
# Anchor tests against the real task file (not just the inlined copy above)
# ============================================================


def test_real_tasks_file_has_slack_assert_and_deploy_and_guard():
    text = TASKS_FILE.read_text()
    assert "Assert Grafana alert Slack channel is configured when Slack token is set" in text
    assert "Deploy Grafana alert contact point (Slack critical)" in text
    assert "Remove Grafana alert contact point (Slack critical) when Slack not configured" in text
    assert "grafana_alert_slack_contact_point" in text
    assert "_mon_alert_slack_contact_point_precheck" in text
    assert "grafana_slack_token | default('') | length == 0" in text


def test_real_tasks_file_has_root_policy_task_and_smtp_slack_gating():
    text = TASKS_FILE.read_text()
    assert "Deploy Grafana alert policy (root: SMTP email + Slack critical)" in text
    assert "grafana-smtp-email" in text
    assert "grafana-default-email" in text
    assert "grafana_alert_root_policy" in text


def test_real_tasks_file_keeps_webhook_and_smtp_paths_untouched():
    """Regression guard: the additive Slack feature + root-policy fix must
    not have removed or renamed the existing webhook/SMTP tasks."""
    text = TASKS_FILE.read_text()
    assert "Deploy Grafana alert contact point (kigulls-api-webhook)" in text
    assert "Deploy Grafana alert policy (route to kigulls-api-webhook)" in text
    assert "Remove Grafana alert contact point/policy when token not configured" in text
    assert "Deploy Grafana alert contact point (email via SMTP)" in text
    assert "Remove Grafana alert contact point (email) when SMTP not configured" in text
