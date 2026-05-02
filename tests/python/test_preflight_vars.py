"""Tests for ansible/tasks/preflight_vars.yml (AFKI-W-115).

Runs ansible-playbook against a tiny wrapper that imports preflight_vars.yml
and checks that:
- all required vars present -> success
- any required var missing/empty -> failure with sprechende Meldung

Requires ansible-playbook on PATH (true in the .venv).
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "ansible" / "tasks" / "preflight_vars.yml"

REQUIRED_VARS = ("drayve_domain", "drayve_root", "ops_secrets_root", "drayve_user")
DEFAULTS = {
    "drayve_domain": "demo.example.com",
    "drayve_root": "/opt/drayve",
    "ops_secrets_root": "/site",
    "drayve_user": "drayve",
}


@pytest.fixture(scope="module")
def wrapper_playbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("preflight") / "wrapper.yml"
    target.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: Test preflight
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                - ansible.builtin.import_tasks: {PREFLIGHT}
            """
        )
    )
    return target


def _run(wrapper: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    cmd = ["ansible-playbook", "-i", "localhost,", str(wrapper)]
    for key, value in overrides.items():
        cmd.extend(["-e", f"{key}={value}"])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def test_all_vars_present_succeeds(ansible_available, wrapper_playbook):
    result = _run(wrapper_playbook, **DEFAULTS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "required variables present" in result.stdout


@pytest.mark.parametrize("missing", REQUIRED_VARS)
def test_missing_var_fails(ansible_available, wrapper_playbook, missing):
    overrides = dict(DEFAULTS)
    overrides[missing] = ""
    result = _run(wrapper_playbook, **overrides)
    assert result.returncode != 0, "expected failure when {} is empty".format(missing)
    assert "missing or empty" in result.stdout
    assert missing in result.stdout


def test_undefined_var_fails(ansible_available, wrapper_playbook):
    overrides = {k: v for k, v in DEFAULTS.items() if k != "drayve_domain"}
    result = _run(wrapper_playbook, **overrides)
    assert result.returncode != 0
    assert "<UNDEFINED>" in result.stdout
