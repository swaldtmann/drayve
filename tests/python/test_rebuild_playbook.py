"""Structure tests for ansible/playbooks/rebuild.yml.

rebuild.yml chains provision -> kedge restore -> basic verify. A live run needs
a box and a real (or mockforge-seeded) backup, so these tests pin the contract
statically: order of the stages, the safety properties of the restore stage,
the optional verify hook and the Makefile entry point.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "ansible" / "playbooks" / "rebuild.yml"
VERIFY_TASKS = REPO_ROOT / "ansible" / "tasks" / "rebuild_verify.yml"
RESTORE_SETUP = REPO_ROOT / "ansible" / "roles" / "backup" / "tasks" / "kedge_restore_setup.yml"
MAKEFILE = REPO_ROOT / "Makefile"


@pytest.fixture(scope="module")
def plays() -> list[dict]:
    return yaml.safe_load(PLAYBOOK.read_text())


def _imported(play: dict) -> str:
    return play.get("import_playbook") or play.get("ansible.builtin.import_playbook") or ""


def _flat_text(path: Path) -> str:
    return path.read_text()


def test_playbook_exists(plays):
    assert isinstance(plays, list) and plays


def test_stage_order_provision_restore_verify(plays):
    provision = next(i for i, p in enumerate(plays) if _imported(p).endswith("provision.yml"))
    restore = next(i for i, p in enumerate(plays) if "restore" in p.get("name", "").lower())
    verify = next(i for i, p in enumerate(plays) if "verify" in p.get("name", "").lower())
    assert provision < restore < verify


def test_restore_stage_stops_stack_before_kedge_restore(plays):
    restore = next(p for p in plays if "restore" in p.get("name", "").lower())
    text = yaml.safe_dump(restore["tasks"])
    down = text.index("compose down")
    run = text.index("/usr/local/bin/kedge restore")
    assert down < run


def test_restore_never_forces_live_and_has_no_cron():
    text = _flat_text(PLAYBOOK) + _flat_text(RESTORE_SETUP)
    assert "--force-live" not in text
    assert "/etc/cron.d" not in text, "a rebuilt box must not start backing up into the source repo"


def test_restore_requires_repository_and_password(plays):
    restore = next(p for p in plays if "restore" in p.get("name", "").lower())
    text = yaml.safe_dump(restore["tasks"])
    assert "rebuild_restic_repository" in text
    assert "rebuild_restic_password" in text
    assert "assert" in text


def test_restore_env_is_read_only_and_secret_protected():
    text = _flat_text(RESTORE_SETUP)
    assert "RESTIC_NO_LOCK=1" in text
    assert "no_log: true" in text
    assert '"0600"' in text


def test_basic_verify_checks_containers_https_and_firewall():
    text = _flat_text(VERIFY_TASKS)
    assert "restarting" in text
    assert "unhealthy" in text
    assert "DOCKER-USER" in text
    assert "uri:" in text or "ansible.builtin.uri" in text


def test_verify_hook_is_optional_and_included_last():
    text = _flat_text(VERIFY_TASKS)
    assert "rebuild_verify_tasks" in text
    assert "include_tasks" in text
    hook = text.index("rebuild_verify_tasks")
    assert hook > text.index("DOCKER-USER"), "hook must run after the basic checks"
    assert re.search(r"rebuild_verify_tasks\s*\|\s*default\(''\)\s*\|\s*length\s*>\s*0", text)


def test_makefile_has_rebuild_target():
    text = _flat_text(MAKEFILE)
    assert re.search(r"^rebuild:.*##", text, re.M)
    assert "playbooks/rebuild.yml" in text
    assert "test-syntax-rebuild" in text


@pytest.mark.skipif(shutil.which("ansible-playbook") is None, reason="ansible-playbook not on PATH")
def test_playbook_syntax_check():
    res = subprocess.run(
        [
            "ansible-playbook", "--syntax-check", "-i", "localhost,",
            "-e", "drayve_name=stub", "-e", "domain=stub.example.com", "-e", "provider_type=manual",
            str(PLAYBOOK),
        ],
        capture_output=True, text=True,
        env={**__import__("os").environ, "ANSIBLE_ROLES_PATH": str(REPO_ROOT / "ansible" / "roles")},
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_restore_setup_does_not_use_ansible_managed_in_task_content():
    # `ansible_managed` only exists while rendering templates; in a copy `content:`
    # it is undefined and the live run fails ("'ansible_managed' is undefined").
    assert "ansible_managed" not in _flat_text(RESTORE_SETUP)


def test_restore_summary_uses_slice_not_last_with_argument():
    # jinja's `last` filter takes no count: `last(15)` fails at runtime
    # ("do_last() takes 2 positional arguments but 3 were given").
    assert not re.search(r"\|\s*last\(\s*\d+", _flat_text(PLAYBOOK))
