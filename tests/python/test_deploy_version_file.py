"""deploy.yml must leave /opt/drayve/VERSION behind and the footer must show the deploy version.

Runs ansible/tasks/version_file.yml through a wrapper playbook against localhost
with drayve_root pointing into a tmp dir (no host, no ssh), plus structural
checks on deploy.yml and the landing footer.

Run: ``pytest tests/python/test_deploy_version_file.py``
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "ansible/tasks/version_file.yml"
DEPLOY = ROOT / "ansible/playbooks/deploy.yml"
LANDING = ROOT / "ansible/templates/landing.html.j2"
ISO = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"


def _git(cwd: Path, *args: str) -> str:
    local_vars = set(subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True).stdout.split())
    git_env = {
        **{k: v for k, v in os.environ.items() if k not in local_vars},
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=git_env)
    return r.stdout.strip()


def _repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "f").write_text("x")
    _git(path, "add", "f")
    _git(path, "commit", "-q", "-m", "init")
    return _git(path, "rev-parse", "--short", "HEAD")


@pytest.fixture()
def layout(tmp_path):
    if shutil.which("ansible-playbook") is None or shutil.which("git") is None:
        pytest.skip("ansible-playbook/git not on PATH")
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: Test version file
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                - ansible.builtin.import_tasks: {TASKS}
            """
        )
    )
    drayve_root = tmp_path / "host-opt-drayve"
    drayve_root.mkdir()
    return wrapper, drayve_root


def _run(layout, extra: dict | None = None, site: Path | str = "") -> subprocess.CompletedProcess:
    wrapper, drayve_root = layout
    vars_ = {
        "drayve_root": str(drayve_root),
        "ops_deploy_root": str(ROOT),
        "ops_secrets_root": str(site),
        "deploy_ref": "v9.9.9",
        "backup": {"enabled": False},
        **(extra or {}),
    }
    cmd = ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", yaml.safe_dump(vars_, default_flow_style=True)]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "ANSIBLE_ROLES_PATH": str(ROOT / "ansible/roles")},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _read(layout) -> dict[str, str]:
    lines = (layout[1] / "VERSION").read_text().splitlines()
    assert [ln.split("=", 1)[0] for ln in lines] == ["drayve", "site", "kedge", "deployed", "from"], lines
    return dict(ln.split("=", 1) for ln in lines)


def _drayve_commit() -> str:
    return _git(ROOT, "rev-parse", "--short", "HEAD")


def test_normal_case(layout, tmp_path):
    site = tmp_path / "mysite"
    commit = _repo(site)
    _run(layout, site=site)
    v = _read(layout)
    assert v["drayve"] == f"v9.9.9 ({_drayve_commit()})"
    assert v["site"] == f"mysite {commit}"
    assert v["kedge"] == "none"
    assert re.fullmatch(ISO, v["deployed"])
    assert v["from"] == subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    assert stat.S_IMODE((layout[1] / "VERSION").stat().st_mode) == 0o644


def test_dirty_site_repo(layout, tmp_path):
    site = tmp_path / "mysite"
    commit = _repo(site)
    (site / "f").write_text("changed")
    _run(layout, site=site)
    assert _read(layout)["site"] == f"mysite {commit} dirty"


def test_site_untracked_file_is_not_dirty(layout, tmp_path):
    site = tmp_path / "mysite"
    commit = _repo(site)
    (site / "new").write_text("untracked")
    _run(layout, site=site)
    assert _read(layout)["site"] == f"mysite {commit}"


def test_no_ops_secrets_root(layout):
    _run(layout, site="")
    assert _read(layout)["site"] == "unknown"


def test_ops_secrets_root_not_a_git_repo(layout, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    _run(layout, site=plain)
    assert _read(layout)["site"] == "unknown"


def test_no_deploy_ref(layout):
    _run(layout, extra={"deploy_ref": ""})
    assert _read(layout)["drayve"] == f"unknown ({_drayve_commit()})"


def test_drayve_dir_not_a_git_root_reports_no_commit(layout, tmp_path):
    copy = tmp_path / "vendored"
    copy.mkdir()
    _run(layout, extra={"ops_deploy_root": str(copy)})
    assert _read(layout)["drayve"] == "v9.9.9"


def test_backup_without_kedge_target(layout):
    _run(layout, extra={"backup": {"enabled": True, "target": "local"}})
    assert _read(layout)["kedge"] == "none"


def test_backup_kedge_not_installed(layout, tmp_path):
    _run(
        layout,
        extra={"backup": {"enabled": True, "target": "kedge"}, "backup_kedge_install_dir": str(tmp_path / "missing")},
    )
    assert _read(layout)["kedge"] == "not-installed"


def test_backup_kedge_version(layout, tmp_path):
    kedge = tmp_path / "kedge"
    _repo(kedge)
    _git(kedge, "tag", "v1.2.3")
    _run(layout, extra={"backup": {"enabled": True, "target": "kedge"}, "backup_kedge_install_dir": str(kedge)})
    assert _read(layout)["kedge"] == "v1.2.3"


def test_identical_second_run_changes_nothing(layout, tmp_path):
    site = tmp_path / "mysite"
    _repo(site)
    _run(layout, site=site)
    first = (layout[1] / "VERSION").read_text()
    second = _run(layout, site=site)
    assert re.search(r"changed=0", second.stdout), second.stdout
    assert (layout[1] / "VERSION").read_text() == first


def test_changed_identity_gets_new_stamp(layout, tmp_path):
    site = tmp_path / "mysite"
    _repo(site)
    _run(layout, site=site)
    r = _run(layout, extra={"deploy_ref": "v9.9.10"}, site=site)
    assert re.search(r"changed=1", r.stdout), r.stdout
    assert _read(layout)["drayve"].startswith("v9.9.10 (")


def _flat(tasks):
    for t in tasks:
        yield t
        yield from _flat(t.get("block", []))


def test_deploy_runs_version_file_as_last_post_task():
    play = [p for p in yaml.safe_load(DEPLOY.read_text()) if p.get("name") == "Deploy"][0]
    last = play["post_tasks"][-1]
    assert "version_file.yml" in str(last)
    assert "ignore_errors" not in last
    assert "failed_when" not in last
    assert not any(t.get("ignore_errors") for t in _flat(yaml.safe_load(TASKS.read_text())))


def test_footer_follows_deploy_ref():
    footer = re.search(r"<footer>(.*?)</footer>", LANDING.read_text()).group(1)
    assert "0.1.0" not in footer
    jenv = Environment()
    jenv.filters["regex_replace"] = lambda v, p, r="": re.sub(p, r, v)
    tpl = jenv.from_string(footer)
    assert tpl.render(deploy_ref="v0.9.0") == "drayve 0.9.0"
    assert tpl.render(deploy_ref="v0.9.0", drayve_version="0.1.0") == "drayve 0.9.0"
    assert tpl.render(drayve_version="0.1.0") == "drayve 0.1.0"
    assert tpl.render() == "drayve dev"


def test_git_helpers_ignore_hook_environment(tmp_path, monkeypatch):
    """git exports GIT_DIR & co. into hooks; throwaway repos must not commit into that repo."""
    real = tmp_path / "real"
    before = _repo(real)
    monkeypatch.setenv("GIT_DIR", str(real / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(real / ".git" / "index"))
    monkeypatch.setenv("GIT_WORK_TREE", str(real))
    _repo(tmp_path / "scratch")
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_INDEX_FILE")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert _git(real, "rev-parse", "--short", "HEAD") == before
    assert _git(real, "rev-list", "--count", "HEAD") == "1"
