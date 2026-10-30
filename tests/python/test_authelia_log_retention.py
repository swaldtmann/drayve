"""Authelia authentication_logs retention: role wiring + cron.d template.

The daily job (``roles/authelia/files/drayve-authelia-retention.sh``, behaviour
covered by ``tests/bats/authelia-retention.bats``) deletes rows older than
``authelia_auth_log_retention_days`` from the Authelia SQLite DB. Authelia
4.39 has no retention option of its own (storage docs, checked 2026-10-30).

Run: ``pytest tests/python/test_authelia_log_retention.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "authelia"
CRON_FILE = "/etc/cron.d/drayve-authelia-retention"
SCRIPT_DEST = "/usr/local/sbin/drayve-authelia-retention"


def _defaults() -> dict:
    return yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())


def _tasks() -> list[dict]:
    return yaml.safe_load((ROLE / "tasks" / "main.yml").read_text())


def _task(name_part: str) -> dict:
    for t in _tasks():
        if name_part in t.get("name", ""):
            return t
    pytest.fail(f"no task with {name_part!r} in roles/authelia/tasks/main.yml")


def test_defaults_retention_14_days():
    d = _defaults()
    assert d["authelia_auth_log_retention_days"] == 14
    assert d["authelia_auth_log_retention_schedule"]
    assert d["authelia_auth_log_retention_log_file"] == "/var/log/drayve-authelia-retention.log"


def test_sqlite3_package_installed():
    t = _task("sqlite3")
    assert t["ansible.builtin.apt"]["name"] == "sqlite3"
    assert t["ansible.builtin.apt"]["state"] == "present"


def test_script_deployed_root_executable():
    t = _task("retention script")
    c = t["ansible.builtin.copy"]
    assert c["src"] == "drayve-authelia-retention.sh"
    assert c["dest"] == SCRIPT_DEST
    assert (c["owner"], c["group"], str(c["mode"])) == ("root", "root", "0755")


def test_cron_file_in_cron_d_not_crontab_module():
    t = _task("retention cron")
    tpl = t["ansible.builtin.template"]
    assert tpl["src"] == "authelia-retention-cron.j2"
    assert tpl["dest"] == CRON_FILE
    assert str(tpl["mode"]) == "0644" and tpl["owner"] == "root"
    assert not any("ansible.builtin.cron" in x for x in _tasks()), "use /etc/cron.d, not the cron module"


def test_retention_tasks_tagged_authelia():
    for part in ("sqlite3", "retention script", "retention cron"):
        assert "authelia" in _task(part)["tags"]


@pytest.fixture
def render(ansible_jinja_env):
    env = ansible_jinja_env(ROLE / "templates", strict=True)
    tpl = env.get_template("authelia-retention-cron.j2")

    def _render(**overrides: object) -> str:
        ctx = {
            "ansible_managed": "Ansible managed",
            "authelia_data_dir": "/opt/drayve/deploy/stack/authelia/data",
            "authelia_auth_log_retention_days": 14,
            "authelia_auth_log_retention_schedule": "17 3 * * *",
            "authelia_auth_log_retention_log_file": "/var/log/drayve-authelia-retention.log",
        }
        ctx.update(overrides)
        return tpl.render(**ctx)

    return _render


def _job_line(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#") and "=" not in ln.split()[0]]
    assert len(lines) == 1, lines
    return lines[0]


def test_cron_line_schedule_user_command(render):
    line = _job_line(render())
    assert line.startswith("17 3 * * * root ")
    assert SCRIPT_DEST in line
    assert "RETENTION_DAYS=14" in line
    assert "AUTHELIA_DB=/opt/drayve/deploy/stack/authelia/data/db.sqlite3" in line
    assert line.endswith(">> /var/log/drayve-authelia-retention.log 2>&1")


def test_cron_file_has_mailto_and_path(render):
    out = render()
    assert "MAILTO=\"\"\n" in out
    assert "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n" in out


def test_cron_days_follow_variable(render):
    assert "RETENTION_DAYS=30" in _job_line(render(authelia_auth_log_retention_days=30))
