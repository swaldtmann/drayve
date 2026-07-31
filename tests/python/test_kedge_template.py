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
    "BACKUP_PRE_HOOK",
    "BACKUP_POST_HOOK",
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
        "backup_kedge_pre_hook": "",
        "backup_kedge_post_hook": "",
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def render(ansible_jinja_env):
    env = ansible_jinja_env(TEMPLATE_DIR, strict=True)
    template = env.get_template(".kedge.env.j2")

    def _render(**overrides: object) -> str:
        return template.render(**_base_ctx(**overrides))

    return _render


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

def test_defaults_render_required_keys_only(render):
    rendered = render()
    parsed = _parse_env(rendered)
    assert set(parsed.keys()) == REQUIRED_KEYS, (
        f"unexpected keys: {set(parsed.keys()) ^ REQUIRED_KEYS}"
    )


# ---- 2) Required values come through verbatim ----

def test_required_values_passthrough(render):
    rendered = render()
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
def test_stop_stack_renders_as_lowercase_bool(render, flag: bool, expected: str):
    rendered = render(backup_kedge_stop_stack=flag)
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_STOP_STACK"] == expected


# ---- 4) Optional excludes only render when non-empty ----

def test_excludes_empty_does_not_render(render):
    rendered = render(backup_kedge_exclude_mounts="")
    parsed = _parse_env(rendered)
    assert "BACKUP_EXCLUDE_MOUNTS" not in parsed


def test_excludes_set_renders_value(render):
    rendered = render(backup_kedge_exclude_mounts="/var/cache /tmp/large")
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_EXCLUDE_MOUNTS"] == '"/var/cache /tmp/large"'


def test_excludes_multiword_value_is_shell_sourceable(render):
    """Regression (EWH-W-132, P5.6): unquoted multi-word
    BACKUP_EXCLUDE_MOUNTS broke `source .kedge.env` itself (bash tried to
    run the second path as a command), aborting kedge backup under set -e.
    Actually source the rendered file in bash and assert the variable
    survives intact with all paths — the only check that would have
    caught this."""
    import subprocess

    rendered = render(
        backup_kedge_exclude_mounts="/ /sys /var/log /var/run /var/lib/docker"
    )
    result = subprocess.run(
        ["bash", "-c", f"set -e; source /dev/stdin <<'EOF'\n{rendered}\nEOF\necho \"$BACKUP_EXCLUDE_MOUNTS\""],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"sourcing failed: {result.stderr}"
    assert result.stdout.strip() == "/ /sys /var/log /var/run /var/lib/docker"


# ---- 4b) Optional pre-hook only renders when non-empty, stays sourceable ----

def test_pre_hook_empty_does_not_render(render):
    rendered = render(backup_kedge_pre_hook="")
    parsed = _parse_env(rendered)
    assert "BACKUP_PRE_HOOK" not in parsed


def test_pre_hook_set_renders_quoted_value(render):
    rendered = render(backup_kedge_pre_hook="echo hi")
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_PRE_HOOK"] == '"echo hi"'


def test_pre_hook_multiword_value_is_shell_sourceable(render):
    """Same class of bug as BACKUP_EXCLUDE_MOUNTS (P5.6): a hook command
    with spaces/operators must survive `source .kedge.env` intact."""
    import subprocess

    hook = "rm -rf /var/backups/ds389-dump && dsctl slapd-ewaldshof db2bak /var/backups/ds389-dump"
    rendered = render(backup_kedge_pre_hook=hook)
    result = subprocess.run(
        ["bash", "-c", f"set -e; source /dev/stdin <<'EOF'\n{rendered}\nEOF\necho \"$BACKUP_PRE_HOOK\""],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"sourcing failed: {result.stderr}"
    assert result.stdout.strip() == hook


# ---- 5) Optional healthcheck URL only renders when non-empty ----

def test_healthcheck_empty_does_not_render(render):
    rendered = render(backup_kedge_healthcheck_url="")
    parsed = _parse_env(rendered)
    assert "BACKUP_HEALTHCHECK_URL" not in parsed


def test_healthcheck_set_renders_value(render):
    url = "https://hc-ping.com/abcd-1234"
    rendered = render(backup_kedge_healthcheck_url=url)
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_HEALTHCHECK_URL"] == url


# ---- 6) Retention values pass numeric overrides ----

@pytest.mark.parametrize("d,w,m", [(14, 8, 12), (3, 2, 1), (30, 12, 24)])
def test_retention_overrides(render, d: int, w: int, m: int):
    rendered = render(
        backup_retain_daily=d,
        backup_retain_weekly=w,
        backup_retain_monthly=m,
    )
    parsed = _parse_env(rendered)
    assert parsed["BACKUP_KEEP_DAILY"] == str(d)
    assert parsed["BACKUP_KEEP_WEEKLY"] == str(w)
    assert parsed["BACKUP_KEEP_MONTHLY"] == str(m)


# ---- 7) Output stays shell-sourceable (no spaces in keys, simple values) ----

def test_output_is_shell_sourceable_shape(render):
    rendered = render(
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

def test_template_has_managed_banner(render):
    rendered = render(ansible_managed="Ansible managed: 2026-05-07")
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
        "backup_kedge_pre_hook",
        "backup_kedge_cron_wrapper",
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


def test_kedge_cron_template_sources_env_file():
    """The /etc/cron.d/ template must source the env file and invoke the
    symlinked kedge CLI for both backup and prune lines."""
    repo_root = Path(__file__).resolve().parents[2]
    tpl = (repo_root / "ansible/roles/backup/templates/kedge-cron.j2").read_text()
    assert "{{ backup_kedge_env_file }}" in tpl, "template must reference env file var"
    assert "/usr/local/bin/kedge backup" in tpl, "template must invoke kedge backup"
    assert "/usr/local/bin/kedge prune" in tpl, "template must invoke kedge prune"
    # cron.d format requires a user column
    assert " root " in tpl, "cron.d entries must specify the user column (root)"


# ---- 9b) Optional cron wrapper (fail-signal helper, e.g. alert-pub) ----

def _render_cron(ansible_jinja_env, **overrides: object) -> str:
    ctx: dict[str, object] = {
        "ansible_managed": "Ansible managed",
        "stack_name": "prod-genua",
        "backup_kedge_env_file": "/root/.kedge.env",
        "backup_kedge_log_file": "/var/log/kedge.log",
        "backup_schedule": "0 3 * * *",
        "backup_prune_schedule": "30 4 * * 0",
        "backup_kedge_cron_wrapper": "",
    }
    ctx.update(overrides)
    env = ansible_jinja_env(TEMPLATE_DIR, strict=True)
    # Mirror Ansible's real trim_blocks=True (this harness's Environment
    # factory doesn't set it — irrelevant for the .kedge.env.j2 tests since
    # they parse key/value lines and skip blanks, but load-bearing here
    # since we assert on exact rendered lines).
    env.trim_blocks = True
    template = env.get_template("kedge-cron.j2")
    return template.render(**ctx)


def test_cron_wrapper_empty_is_byte_identical_to_unwrapped(ansible_jinja_env):
    """Regression: adding the wrapper option must not change a single byte
    of the rendered output when the var is unset (default) — existing
    stacks without the var set must see zero diff on their next deploy."""
    rendered = _render_cron(ansible_jinja_env)
    assert "0 3 * * * root set -a; . /root/.kedge.env; set +a; /usr/local/bin/kedge backup >> /var/log/kedge.log 2>&1" in rendered
    assert "30 4 * * 0 root set -a; . /root/.kedge.env; set +a; /usr/local/bin/kedge prune >> /var/log/kedge.log 2>&1" in rendered
    assert "alert-pub" not in rendered
    assert "bash -c" not in rendered


def test_cron_wrapper_set_wraps_both_backup_and_prune(ansible_jinja_env):
    rendered = _render_cron(ansible_jinja_env, backup_kedge_cron_wrapper="/usr/local/sbin/alert-pub")
    assert (
        "0 3 * * * root /usr/local/sbin/alert-pub kedge-prod-genua-backup -- "
        "bash -c 'set -a; . /root/.kedge.env; set +a; /usr/local/bin/kedge backup' "
        ">> /var/log/kedge.log 2>&1"
    ) in rendered
    assert (
        "30 4 * * 0 root /usr/local/sbin/alert-pub kedge-prod-genua-prune -- "
        "bash -c 'set -a; . /root/.kedge.env; set +a; /usr/local/bin/kedge prune' "
        ">> /var/log/kedge.log 2>&1"
    ) in rendered


def test_cron_wrapper_job_names_differ_between_backup_and_prune(ansible_jinja_env):
    """alert-pub keys its alert topic off the job-name argument — backup and
    prune failures must not collide under the same name."""
    rendered = _render_cron(ansible_jinja_env, backup_kedge_cron_wrapper="/usr/local/sbin/alert-pub")
    assert "kedge-prod-genua-backup" in rendered
    assert "kedge-prod-genua-prune" in rendered


def test_kedge_cron_deployed_to_etc_crond():
    """Cron lives at /etc/cron.d/kedge-<stack_name> via template — NOT
    the Ansible cron module / root crontab."""
    tasks = _load_tasks()
    for t in tasks:
        if (t.get("name") or "") == "Deploy kedge cron file":
            tpl = t.get("ansible.builtin.template", {})
            assert tpl.get("src") == "kedge-cron.j2"
            assert "/etc/cron.d/kedge-" in tpl.get("dest", "")
            assert "{{ stack_name }}" in tpl.get("dest", "")
            assert tpl.get("mode") == "0644"
            whens = " ".join(_task_when(t))
            assert 'backup_target == "kedge"' in whens
            return
    pytest.fail("kedge cron-file template task not found")


def test_legacy_root_crontab_entries_removed_for_kedge():
    """Migration: when target=kedge, sweep old root-crontab entries
    (Ansible-cron-module era) so they don't double-fire alongside the
    new /etc/cron.d/ file."""
    tasks = _load_tasks()
    for t in tasks:
        if (t.get("name") or "").startswith("Remove legacy root-crontab"):
            cron = t.get("ansible.builtin.cron", {})
            assert cron.get("state") == "absent"
            assert cron.get("user") == "root"
            loop = t.get("loop") or []
            assert "drayve-backup" in loop
            assert "drayve-kedge-backup" in loop
            assert "drayve-kedge-prune" in loop
            whens = " ".join(_task_when(t))
            assert 'backup_target == "kedge"' in whens
            return
    pytest.fail("legacy root-crontab cleanup task not found")


def test_stack_name_fact_resolved():
    """A canonical stack_name fact must be set early; the cron-file
    template references it for /etc/cron.d/kedge-<stack_name>."""
    tasks = _load_tasks()
    for t in tasks:
        if (t.get("name") or "") == "Resolve backup settings from stack.yaml":
            facts = t.get("ansible.builtin.set_fact", {})
            assert "stack_name" in facts, "set_fact must define stack_name"
            expr = facts["stack_name"]
            assert "stack.name" in expr, "stack_name must prefer stack.name"
            assert "drayve_name" in expr, "stack_name must fall back to drayve_name"
            assert "inventory_hostname" in expr, "stack_name must finally fall back to inventory_hostname"
            return
    pytest.fail("Resolve task with stack_name fact not found")


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
