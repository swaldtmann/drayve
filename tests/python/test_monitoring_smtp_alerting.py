"""Tests for KIG-W-054 — optional Grafana alert email contact point via SMTP.

Background: the only Grafana alert delivery channel drayve could configure
was the ``kigulls-api-webhook`` contact point, gated on
``grafana_alert_api_token``. KIgulls-Schwarm is being retired (KIG-W-054),
so drayve gains a second, independent delivery channel: a ``type: email``
contact point wired to Grafana's built-in SMTP support, gated on the new
``grafana_smtp_host`` secret var — same on/off idiom as the webhook path
(``roles/monitoring/tasks/main.yml`` line ~278), so a host that never sets
``grafana_smtp_host`` sees zero behavior change. Additive only: the webhook
contact point, ``policies.yaml`` and its routing are untouched.

Three things are covered here, each mirroring an existing test's approach
for the analogous webhook feature:

1. ``docker-compose.yml.j2`` only emits ``GF_SMTP_*`` env vars for the
   grafana container when ``grafana_smtp_host`` is set (isolated-snippet +
   anchor-test approach, same as ``test_grafana_auth_middleware.py``).
2. ``ansible.builtin.assert`` fails fast when ``grafana_smtp_host`` is set
   but ``grafana_alert_email_to`` is empty, instead of silently deploying a
   contact point that mails nobody.
3. The AFKI-W-239 cross-tag delete-guard (that already protects
   ``contact-points.yaml``/``policies.yaml``) also protects the new
   ``contact-points-email.yaml`` — a ``--tags monitoring`` run without
   ``secrets`` must not delete an already-deployed email contact point (same
   repro shape as ``test_monitoring_deploy_secrets_scope_guard.py``).

Run: ``pytest tests/python/test_monitoring_smtp_alerting.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from jinja2 import Environment, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"
ENV_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "env.j2"
TASKS_FILE = REPO_ROOT / "ansible" / "roles" / "monitoring" / "tasks" / "main.yml"


# ============================================================
# 1. docker-compose.yml.j2 — GF_SMTP_* env vars follow grafana_smtp_host
# ============================================================

# Isolated copy of the grafana environment snippet from docker-compose.yml.j2
# — same isolation approach as test_grafana_auth_middleware.py (the full
# compose template needs a long context dict to render end-to-end).
_GRAFANA_ENV_SNIPPET = (
    "{% if grafana_alert_api_token | default('') | length > 0 %}\n"
    "      - KIGULLS_API_TOKEN=${KIGULLS_API_TOKEN:-}\n"
    "{% endif %}\n"
    "{% if grafana_smtp_host | default('') | length > 0 %}\n"
    "      - GF_SMTP_ENABLED=true\n"
    "      - GF_SMTP_HOST=${GRAFANA_SMTP_HOST:-}:${GRAFANA_SMTP_PORT:-587}\n"
    "      - GF_SMTP_USER=${GRAFANA_SMTP_USER:-}\n"
    "      - GF_SMTP_PASSWORD=${GRAFANA_SMTP_PASSWORD:-}\n"
    "      - GF_SMTP_FROM_ADDRESS=${GRAFANA_SMTP_FROM_ADDRESS:-}\n"
    "{% endif %}\n"
)


def _render_env_snippet(*, grafana_smtp_host: str = "", grafana_alert_api_token: str = "") -> str:
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    template = env.from_string(_GRAFANA_ENV_SNIPPET)
    return template.render(
        grafana_smtp_host=grafana_smtp_host,
        grafana_alert_api_token=grafana_alert_api_token,
    )


def test_no_smtp_host_emits_no_gf_smtp_vars():
    out = _render_env_snippet()
    assert "GF_SMTP_ENABLED" not in out
    assert "GF_SMTP_HOST" not in out
    assert "GF_SMTP_USER" not in out
    assert "GF_SMTP_PASSWORD" not in out
    assert "GF_SMTP_FROM_ADDRESS" not in out


def test_smtp_host_set_emits_all_gf_smtp_vars():
    out = _render_env_snippet(grafana_smtp_host="smtp.example.com")
    assert "GF_SMTP_ENABLED=true" in out
    assert "GF_SMTP_HOST=${GRAFANA_SMTP_HOST:-}:${GRAFANA_SMTP_PORT:-587}" in out
    assert "GF_SMTP_USER=${GRAFANA_SMTP_USER:-}" in out
    assert "GF_SMTP_PASSWORD=${GRAFANA_SMTP_PASSWORD:-}" in out
    assert "GF_SMTP_FROM_ADDRESS=${GRAFANA_SMTP_FROM_ADDRESS:-}" in out


def test_smtp_gate_is_independent_of_webhook_token_gate():
    """Both channels are additive/independent — setting one must not imply
    or exclude the other."""
    out = _render_env_snippet(grafana_alert_api_token="tok123")
    assert "KIGULLS_API_TOKEN" in out
    assert "GF_SMTP_ENABLED" not in out

    out = _render_env_snippet(grafana_smtp_host="smtp.example.com", grafana_alert_api_token="tok123")
    assert "KIGULLS_API_TOKEN" in out
    assert "GF_SMTP_ENABLED=true" in out


def test_compose_template_actually_gates_smtp_env_vars():
    """Anchor test: the production template was changed, not just this snippet."""
    text = COMPOSE_TEMPLATE.read_text()
    assert "{% if grafana_smtp_host | default('') | length > 0 %}" in text
    assert "- GF_SMTP_ENABLED=true" in text
    assert "- GF_SMTP_HOST=${GRAFANA_SMTP_HOST:-}:${GRAFANA_SMTP_PORT:-587}" in text
    assert "- GF_SMTP_FROM_ADDRESS=${GRAFANA_SMTP_FROM_ADDRESS:-}" in text


def test_env_template_emits_smtp_vars_unconditionally_with_empty_default():
    """env.j2 (unlike docker-compose.yml.j2) always writes the GRAFANA_SMTP_*
    lines to .env, empty by default — same idiom as KIGULLS_API_TOKEN."""
    text = ENV_TEMPLATE.read_text()
    assert "GRAFANA_SMTP_HOST={{ grafana_smtp_host | default('') }}" in text
    assert "GRAFANA_SMTP_PORT={{ grafana_smtp_port | default('') }}" in text
    assert "GRAFANA_SMTP_USER={{ grafana_smtp_user | default('') }}" in text
    assert "GRAFANA_SMTP_PASSWORD={{ grafana_smtp_password | default('') }}" in text
    assert "GRAFANA_SMTP_FROM_ADDRESS={{ grafana_smtp_from_address | default('') }}" in text


# ============================================================
# 2 + 3 — ansible-playbook wrapper tests (assert + AFKI-W-239 delete guard)
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
                grafana_smtp_host: "{{ host_secrets.grafana_smtp_host | default('') }}"
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
# KIG-W-054 (assert + deploy + AFKI-W-239 delete guard + removal), same
# approach as test_monitoring_deploy_secrets_scope_guard.py.
SMTP_EMAIL_CONTACT_POINT_TASKS = """\
            # --- roles/monitoring/tasks/main.yml (KIG-W-054) ---
            - name: Assert Grafana alert email recipient is configured when SMTP is enabled
              ansible.builtin.assert:
                that:
                  - grafana_alert_email_to | default('') | length > 0
                fail_msg: "grafana_smtp_host is set but grafana_alert_email_to is empty."
              when: _mon_grafana and (grafana_smtp_host | default('') | length > 0)
              tags: [monitoring, alerting]

            - name: Deploy Grafana alert contact point (email via SMTP)
              ansible.builtin.copy:
                dest: "{{ drayve_deploy_dir }}/alerting/contact-points-email.yaml"
                content: |
                  apiVersion: 1
                  contactPoints:
                    - orgId: 1
                      name: grafana-smtp-email
                      receivers:
                        - uid: grafana-smtp-email-1
                          type: email
                          settings:
                            addresses: {{ grafana_alert_email_to }}
              when: _mon_grafana and (grafana_smtp_host | default('') | length > 0)
              register: grafana_alert_email_contact_point
              tags: [monitoring, alerting]

            - name: Check for host secrets file (cross-tag guard, AFKI-W-239)
              ansible.builtin.stat:
                path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
              register: _mon_secrets_file_check
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Check for existing Grafana alert contact-point file (email via SMTP) (cross-tag guard, AFKI-W-239)
              ansible.builtin.stat:
                path: "{{ drayve_deploy_dir }}/alerting/contact-points-email.yaml"
              register: _mon_alert_email_contact_point_precheck
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Guard — fail instead of deleting Grafana alert config when secrets weren't loaded (AFKI-W-239)
              ansible.builtin.assert:
                that:
                  - >-
                    not (
                      _mon_secrets_file_check.stat.exists and
                      drayve_secrets_facts_loaded is not defined and
                      (grafana_smtp_host | default('') | length == 0) and
                      (_mon_alert_email_contact_point_precheck.stat.exists | default(false))
                    )
                fail_msg: |
                  Secrets nicht geladen -- Monitoring-Deploy ohne `secrets`-Tag gefahren?
              when: _mon_grafana
              tags: [monitoring, alerting]

            - name: Remove Grafana alert contact point (email) when SMTP not configured
              ansible.builtin.file:
                path: "{{ drayve_deploy_dir }}/alerting/contact-points-email.yaml"
                state: absent
              when: _mon_grafana and (grafana_smtp_host | default('') | length == 0)
              tags: [monitoring, alerting]
"""


def _make_wrapper(tmp_path: Path) -> Path:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        "---\n"
        "- name: Test KIG-W-054 SMTP alert contact point\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n"
        + textwrap.indent(SECRETS_SENTINEL_TASKS, "    ")
        + textwrap.indent(SMTP_EMAIL_CONTACT_POINT_TASKS, "    ")
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


# ---- assert: grafana_alert_email_to required when SMTP gate is active ----


def test_smtp_enabled_without_recipient_fails_fast(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="grafana_smtp_host: smtp.example.com\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={
            "_mon_grafana": True,
            "drayve_deploy_dir": str(deploy_dir),
            "grafana_alert_email_to": "",
        },
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "grafana_alert_email_to is empty" in combined, combined
    assert not (deploy_dir / "alerting" / "contact-points-email.yaml").exists()


def test_smtp_enabled_with_recipient_deploys_contact_point(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="grafana_smtp_host: smtp.example.com\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={
            "_mon_grafana": True,
            "drayve_deploy_dir": str(deploy_dir),
            "grafana_alert_email_to": "ops@example.com",
        },
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    content = (deploy_dir / "alerting" / "contact-points-email.yaml").read_text()
    assert "ops@example.com" in content
    assert "type: email" in content


# ---- 3. no grafana_smtp_host set -> no email contact-point file ----


def test_smtp_disabled_by_default_no_contact_point_file(ansible_available, tmp_path):
    """kein grafana_smtp_host gesetzt -> keine email-Contact-Point-Datei."""
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
    assert not (deploy_dir / "alerting" / "contact-points-email.yaml").exists()


# ---- AFKI-W-239 delete guard extended to the email contact point ----


def test_monitoring_only_tag_fires_guard_instead_of_deleting_email_contact_point(
    ansible_available, tmp_path
):
    """oops-0096-class repro for the new SMTP path: --tags monitoring (no
    secrets) must not delete an existing, correctly deployed email contact
    point."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    (deploy_dir / "alerting" / "contact-points-email.yaml").write_text("apiVersion: 1\n")
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
    assert (deploy_dir / "alerting" / "contact-points-email.yaml").exists(), (
        "contact-points-email.yaml must survive when the guard fires"
    )


def test_secrets_plus_monitoring_tags_with_smtp_unset_deletes_as_designed(
    ansible_available, tmp_path
):
    """Legitimate case: secrets loaded, host never configured
    grafana_smtp_host — deletion is the correct, intended behavior and must
    not be blocked by the guard."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    deploy_dir = tmp_path / "deploy_dir"
    (deploy_dir / "alerting").mkdir(parents=True)
    (deploy_dir / "alerting" / "contact-points-email.yaml").write_text("apiVersion: 1\n")
    result = _run(
        wrapper,
        root,
        "myhost",
        extra_vars={"_mon_grafana": True, "drayve_deploy_dir": str(deploy_dir)},
        tags="secrets,monitoring",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (deploy_dir / "alerting" / "contact-points-email.yaml").exists()


def test_monitoring_tag_without_any_secrets_file_is_not_blocked(ansible_available, tmp_path):
    """Nicht-Ziel: a host with no secrets.yml at all and no email contact
    point ever deployed must not be blocked by the guard (nothing to
    protect)."""
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


# ============================================================
# Anchor tests against the real task file (not just the inlined copy above)
# ============================================================


def test_real_tasks_file_has_smtp_assert_and_deploy_and_guard():
    text = TASKS_FILE.read_text()
    assert "Assert Grafana alert email recipient is configured when SMTP is enabled" in text
    assert "Deploy Grafana alert contact point (email via SMTP)" in text
    assert "Remove Grafana alert contact point (email) when SMTP not configured" in text
    assert "grafana_alert_email_contact_point" in text
    # AFKI-W-239 guard extended, not replaced.
    assert "_mon_alert_email_contact_point_precheck" in text
    assert "grafana_smtp_host | default('') | length == 0" in text


def test_real_tasks_file_keeps_webhook_path_untouched():
    """Regression guard: the additive SMTP feature must not have removed or
    reworked the existing webhook contact-point/policy tasks."""
    text = TASKS_FILE.read_text()
    assert "Deploy Grafana alert contact point (kigulls-api-webhook)" in text
    assert "Deploy Grafana alert policy (route to kigulls-api-webhook)" in text
    assert "Remove Grafana alert contact point/policy when token not configured" in text
