"""`.env` generation in roles/deploy must be idempotent when an env.override
exists.

Bug: "Template .env" wrote the file WITHOUT the override block, then
"Append env.override to .env" (blockinfile) added it again. With an
env.override both tasks reported `changed` on every run (file differs from
the template, block missing after step 1), so the deploy never converged.

The test loads the REAL tasks from roles/deploy/tasks/main.yml (selected by
name, not an inlined copy), runs them via ansible-playbook against localhost
and asserts the second run reports changed=0. It also pins the on-disk format
to what the old blockinfile produced (same markers), so hosts upgrading from
the old role see no diff.
"""

from __future__ import annotations

import getpass
import grp
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_YML = REPO_ROOT / "ansible" / "roles" / "deploy" / "tasks" / "main.yml"

ENV_TASKS = {
    "Check for env.override",
    "Try sops-decrypt env.override",
    "Resolve env.override content",
    "Template .env",
    "Append env.override to .env",
}
BEGIN = "# BEGIN USER APP SECRETS (from env.override)"
END = "# END USER APP SECRETS (from env.override)"
OVERRIDE = "MY_APP_VAR=abc123\nOTHER=x y\n"


def _real_env_tasks() -> list[dict]:
    tasks = yaml.safe_load(MAIN_YML.read_text())
    picked = [t for t in tasks if t.get("name") in ENV_TASKS]
    assert any(t["name"] == "Template .env" for t in picked)
    return picked


def _run(tmp_path: Path, secrets_root: Path, env_dest: Path) -> str:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "env tasks from the real role",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "tasks": _real_env_tasks(),
                }
            ]
        )
    )
    cmd = [
        "ansible-playbook",
        "-i",
        "localhost,",
        str(wrapper),
        "-e", "inventory_hostname=myhost",
        "-e", f"ops_secrets_root={secrets_root}",
        "-e", f"ops_deploy_root={REPO_ROOT}",
        "-e", f"drayve_deploy_dir={env_dest.parent}",
        "-e", f"drayve_user={getpass.getuser()}",
        "-e", f"drayve_group={grp.getgrgid(os.getgid()).gr_name}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    return out


def _changed(out: str) -> int:
    m = re.search(r"changed=(\d+)", out)
    assert m, out
    return int(m.group(1))


@pytest.fixture()
def layout(tmp_path):
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")
    root = tmp_path / "site"
    (root / "deploy" / "myhost").mkdir(parents=True)
    (root / "deploy" / "myhost" / "env.override").write_text(OVERRIDE)
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    return tmp_path, root, dest_dir / ".env"


def test_second_run_with_env_override_reports_changed_0(layout):
    tmp_path, root, env_dest = layout
    _run(tmp_path, root, env_dest)
    second = _run(tmp_path, root, env_dest)
    assert _changed(second) == 0, second


def test_env_contains_override_block_with_markers_once(layout):
    tmp_path, root, env_dest = layout
    _run(tmp_path, root, env_dest)
    _run(tmp_path, root, env_dest)
    text = env_dest.read_text()
    assert text.count(BEGIN) == 1 and text.count(END) == 1
    assert text.endswith(f"{BEGIN}\n{OVERRIDE}{END}\n"), text[-200:]


def test_changed_override_updates_block_in_place(layout):
    tmp_path, root, env_dest = layout
    _run(tmp_path, root, env_dest)
    (root / "deploy" / "myhost" / "env.override").write_text("NEW=1\n")
    out = _run(tmp_path, root, env_dest)
    assert _changed(out) == 1, out
    text = env_dest.read_text()
    assert "NEW=1" in text and "MY_APP_VAR" not in text
    assert _changed(_run(tmp_path, root, env_dest)) == 0


def test_without_env_override_no_block(layout):
    tmp_path, root, env_dest = layout
    (root / "deploy" / "myhost" / "env.override").unlink()
    _run(tmp_path, root, env_dest)
    assert BEGIN not in env_dest.read_text()
    assert _changed(_run(tmp_path, root, env_dest)) == 0


def test_upgrade_from_legacy_blockinfile_layout_is_a_noop(layout):
    """A host whose .env was built by the old template + blockinfile flow
    (same markers) must show changed=0 on the first run of the new role."""
    tmp_path, root, env_dest = layout
    # Build the legacy file: template without override, then blockinfile.
    (root / "deploy" / "myhost" / "env.override").rename(tmp_path / "ov.keep")
    _run(tmp_path, root, env_dest)
    (root / "deploy" / "myhost" / "env.override").write_text("")  # restore below
    (tmp_path / "ov.keep").rename(root / "deploy" / "myhost" / "env.override")
    subprocess.run(
        [
            "ansible", "localhost", "-i", "localhost,", "-c", "local",
            "-m", "ansible.builtin.blockinfile",
            "-a", f"path={env_dest} marker='# {{mark}} USER APP SECRETS (from env.override)' "
                  f"block='{OVERRIDE.rstrip()}'",
        ],
        capture_output=True, text=True, check=True,
    )
    legacy = env_dest.read_bytes()
    assert BEGIN.encode() in legacy
    out = _run(tmp_path, root, env_dest)
    assert _changed(out) == 0, out
    assert env_dest.read_bytes() == legacy
