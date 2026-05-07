"""Tests for the kedge env template + backup role kedge target (W-132).

Renders ``ansible/roles/backup/templates/.kedge.env.j2`` with various
inputs and verifies the resulting env file is shell-sourceable, contains
exactly the expected keys, and never leaks empty optional fields.

Also verifies the structural contract of ``ansible/roles/backup/tasks/main.yml``:
the new kedge tasks are gated on ``backup_target == "kedge"``, the legacy
restic-wrapper tasks are gated on ``backup_target in ["local", "sftp"]``,
and the cron entries point at the env file + symlinked CLI.

Run: ``pytest tests/python/test_kedge_template.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_DIR = REPO_ROOT / "ansible" / "roles" / "backup"
TEMPLATE_DIR = ROLE_DIR / "templates"
TASKS_FILE = ROLE_DIR / "tasks" / "main.yml"
DEFAULTS_FILE = ROLE_DIR / "defaults" / "main.yml"

# Required + optional keys per .kedge.env.j2 contract.
REQUIRED_KEYS = {
    "STACK_DIR",
    "RESTIC_REPOSITORY",
    "RESTIC_PASSWORD",
    "BACKUP_STOP_STACK",
    "BACKUP_KEEP_DAILY",
    "BACKUP_KEEP_WEEKLY",
    "BACKUP_KEEP_MONTHLY",
}
OPTIONAL_KEYS = {
    "BACKUP_EXCLUDE_MOUNTS",
    "BACKUP_HEALTHCHECK_URL",
}


def _base_ctx(**overrides: object) -> dict[str, object]:
    ctx: dict[str, object] = {
        "ansible_managed": "Ansible managed",
        "backup_kedge_stack_dir": "/opt/drayve/genua",
        "backup_kedge_restic_repository":
            "sftp:u123456@u123456.your-storagebox.de:/genua",
        "backup_restic_password": "supersecret",
        "backup_kedge_stop_stack": True,
        "backup_kedge_exclude_mounts": "",
        "backup_retain_daily": 7,
        "backup_retain_weekly": 4,
        "backup_retain_monthly": 6,
        "backup_kedge_healthcheck_url": "",
    }
    ctx.update(overrides)
    return ctx


def _ansible_bool(value: object) -> bool:
    """Mimic Ansible's ``| bool`` filter for plain-Jinja2 rendering."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _render(**overrides: object) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    env.filters["bool"] = _ansible_bool
    template = env.get_template(".kedge.env.j2")
    return template.render(**_base_ctx(**overrides))


def _parse_env(rendered: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from a shell-sourceable env file (no quoting)."""
    out: dict[str, str] = {}
    for line in rendered.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        assert "=" in s, f"non-comment line without '=': {line!r}"
        k, _, v = s.partition("=")
        out[k] = v
    return out


# ---- 1) Defaults render the required key set, no optionals ----

def test_defaults_render_required_keys_only():
    rendered = _render()
    parsed = _parse_env(rendered)
    assert set(parsed.keys()) == REQUIRED_KEYS, (
        f"unexpected keys: {set(parsed.keys()) ^ REQUIRED_KEYS}"
    )


# ---- 2) Required values come through verbatim ----

def test_required_values_passthrough():
    rendered = _render()
    parsed = _parse_env(rendered)
    assert parsed["STACK_DIR"] == "/opt/drayve/genua"
    assert parsed["RESTIC_REPOSITORY"].startswith("sftp:")
    assert parsed["RESTIC_PASSWORD"] == "supersecret"
    assert parsed["BACKUP_STOP_STACK"] == "true"
    assert parsed["BACKUP_KEEP_DAILY"] == "7"
    assert parsed["BACKUP_KEEP_WEEKLY"] == "4"
    assert parsed["BACKUP_KEEP_MONTHLY"] == "6"


# ---- 3) Stop-stack flag renders as kedge-compatible bool ----

@pytest.mark.parametrize("flag,expected", [(True, "true"), (False, "false")])
def test_stop_stack_renders_as_lowercase_bool(flag: bool, expected: str):
    rendered = _render(backup_kedge_stop_stack=flag)
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_STOP_STACK"] == expected


# ---- 4) Optional excludes only render when non-empty ----

def test_excludes_empty_does_not_render():
    rendered = _render(backup_kedge_exclude_mounts="")
    parsed = _parse_env(rendered)
    assert "BACKUP_EXCLUDE_MOUNTS" not in parsed


def test_excludes_set_renders_value():
    rendered = _render(backup_kedge_exclude_mounts="/var/cache /tmp/large")
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_EXCLUDE_MOUNTS"] == "/var/cache /tmp/large"


# ---- 5) Optional healthcheck URL only renders when non-empty ----

def test_healthcheck_empty_does_not_render():
    rendered = _render(backup_kedge_healthcheck_url="")
    parsed = _parse_env(rendered)
    assert "BACKUP_HEALTHCHECK_URL" not in parsed


def test_healthcheck_set_renders_value():
    url = "https://hc-ping.com/abcd-1234"
    rendered = _render(backup_kedge_healthcheck_url=url)
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_HEALTHCHECK_URL"] == url


# ---- 6) Retention values pass numeric overrides ----

@pytest.mark.parametrize("d,w,m", [(14, 8, 12), (3, 2, 1), (30, 12, 24)])
def test_retention_overrides(d: int, w: int, m: int):
    rendered = _render(
        backup_retain_daily=d,
        backup_retain_weekly=w,
        backup_retain_monthly=m,
    )
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_KEEP_DAILY"] == str(d)
    assert parsed["BACKUP_KEEP_WEEKLY"] == str(w)
    assert parsed["BACKUP_KEEP_MONTHLY"] == str(m)


# ---- 7) Output stays shell-sourceable (no spaces in keys, simple values) ----

def test_output_is_shell_sourceable_shape():
    rendered = _render(
        backup_kedge_exclude_mounts="/var/cache",
        backup_kedge_healthcheck_url="https://hc-ping.com/x",
    )
    for line in rendered.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # KEY=value, KEY is uppercase-with-underscores
        key = s.split("=", 1)[0]
        assert key.isupper() or "_" in key
        assert " " not in key


# ---- 8) Template carries the ansible_managed banner ----

def test_template_has_managed_banner():
    rendered = _render(ansible_managed="Ansible managed: 2026-05-07")
    assert "Ansible managed: 2026-05-07" in rendered


# ---- 9) Defaults file declares the kedge contract ----

def test_defaults_declare_all_kedge_vars():
    text = DEFAULTS_FILE.read_text()
    for var in (
        "backup_kedge_repo",
        "backup_kedge_version",
        "backup_kedge_install_dir",
        "backup_kedge_env_file",
        "backup_kedge_log_file",
        "backup_kedge_stack_dir",
        "backup_kedge_restic_repository",
        "backup_kedge_stop_stack",
        "backup_kedge_exclude_mounts",
        "backup_kedge_healthcheck_url",
        "backup_prune_schedule",
    ):
        assert f"{var}:" in text, f"defaults missing {var}"


def test_defaults_pin_kedge_version():
    """v0.3.1 is the smoke-tested release on prod-genua (S327d)."""
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text())
    assert defaults["backup_kedge_version"].startswith("v0."), (
        "kedge_version must be a tag, never 'main'"
    )


# ---- 10) tasks/main.yml gates legacy + kedge correctly ----

def _load_tasks() -> list[dict]:
    return yaml.safe_load(TASKS_FILE.read_text())


def _task_when(task: dict) -> list[str]:
    """Return when-clauses as list of strings (single-string or list form)."""
    w = task.get("when")
    if w is None:
        return []
    if isinstance(w, str):
        return [w]
    return [str(x) for x in w]


def test_kedge_tasks_gated_on_target():
    tasks = _load_tasks()
    kedge_keywords = ("kedge",)
    found = 0
    for t in tasks:
        name = (t.get("name") or "").lower()
        if not any(k in name for k in kedge_keywords):
            continue
        if name.startswith("install kedge dependencies") \
                or name.startswith("clone kedge") \
                or name.startswith("symlink kedge") \
                or name.startswith("deploy kedge") \
                or name.startswith("remove legacy"):
            whens = " ".join(_task_when(t))
            assert 'backup_target == "kedge"' in whens, (
                f"task '{t['name']}' missing kedge gate: {whens!r}"
            )
            found += 1
    # 7 kedge-gated tasks total: deps, clone, symlink, env, legacy-cleanup,
    # backup-cron, prune-cron.
    assert found >= 6, f"expected >=6 kedge-gated tasks, found {found}"


def test_legacy_tasks_gated_on_local_or_sftp():
    tasks = _load_tasks()
    legacy_names = (
        "create local backup directory",
        "initialize restic repo (local)",
        "deploy backup script",
        "deploy backup cron job (legacy targets)",
        "install restic (legacy targets local/sftp)",
    )
    seen = 0
    for t in tasks:
        name = (t.get("name") or "").lower()
        for legacy in legacy_names:
            if name == legacy:
                whens = " ".join(_task_when(t))
                assert (
                    'backup_target in ["local", "sftp"]' in whens
                    or 'backup_target == "local"' in whens
                ), f"legacy task '{t['name']}' missing local/sftp gate: {whens!r}"
                seen += 1
    assert seen == len(legacy_names), (
        f"expected {len(legacy_names)} legacy tasks gated, found {seen}"
    )


def test_kedge_env_template_is_no_log():
    """Secret-bearing template task must not log values."""
    tasks = _load_tasks()
    for t in tasks:
        if (t.get("name") or "") == "Deploy kedge environment file":
            assert t.get("no_log") is True, "kedge env template task must be no_log"
            assert t.get("ansible.builtin.template", {}).get("mode") == "0600"
            return
    pytest.fail("'Deploy kedge environment file' task not found")


def test_kedge_cron_sources_env_file():
    """Cron jobs must source the env file before invoking kedge."""
    tasks = _load_tasks()
    cron_names = ("Deploy kedge backup cron (daily)",
                  "Deploy kedge prune cron (weekly)")
    found = 0
    for t in tasks:
        if (t.get("name") or "") in cron_names:
            job = t.get("ansible.builtin.cron", {}).get("job", "")
            assert "/root/.kedge.env" in job or "{{ backup_kedge_env_file }}" in job, (
                f"cron job does not source env file: {job!r}"
            )
            assert "/usr/local/bin/kedge" in job, (
                f"cron job does not invoke symlinked kedge CLI: {job!r}"
            )
            found += 1
    assert found == 2, f"expected 2 kedge cron tasks, found {found}"


def test_legacy_drayve_backup_cron_removed_for_kedge():
    """Migration path: when target switches to kedge, the old cron must go."""
    tasks = _load_tasks()
    for t in tasks:
        if (t.get("name") or "") == "Remove legacy drayve-backup cron entry (kedge target)":
            cron = t.get("ansible.builtin.cron", {})
            assert cron.get("name") == "drayve-backup"
            assert cron.get("state") == "absent"
            whens = " ".join(_task_when(t))
            assert 'backup_target == "kedge"' in whens
            return
    pytest.fail("legacy-cron-removal task not found")


def test_assert_blocks_missing_secrets():
    """Prerequisites assert must fail closed when host_secrets are empty."""
    tasks = _load_tasks()
    for t in tasks:
        if (t.get("name") or "") == "Validate kedge target prerequisites":
            asserts = t.get("ansible.builtin.assert", {})
            that = asserts.get("that") or []
            joined = " ".join(that)
            assert "backup_kedge_restic_repository" in joined
            assert "backup_restic_password" in joined
            return
    pytest.fail("kedge prerequisites assert task not found")
