"""Tests for the mockforge data-comparison hook of `make rebuild`.

scripts/rebuild-tree-compare.sh compares two file trees by relative path and
sha256 and prints one summary line; ansible/tasks/rebuild_verify_mockforge.yml
is the hook (`rebuild_verify_tasks`) that regenerates the mockforge tree with
the backup's seed and runs the comparison against the restored volume.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "rebuild-tree-compare.sh"
HOOK = REPO_ROOT / "ansible" / "tasks" / "rebuild_verify_mockforge.yml"


def _compare(expected: Path, actual: Path, count: int | None = None) -> subprocess.CompletedProcess:
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    cmd = ["bash", str(SCRIPT), str(expected), str(actual)]
    if count is not None:
        cmd.append(str(count))
    return subprocess.run(cmd, capture_output=True, text=True)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


FILES = {"a.txt": "alpha", "sub/b.txt": "beta", "sub/deep/c.txt": "gamma", "with space.txt": "delta"}


def test_identical_trees_pass(tmp_path):
    exp = _tree(tmp_path / "exp", FILES)
    act = _tree(tmp_path / "act", FILES)
    res = _compare(exp, act, count=4)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "files=4 mismatches=0" in res.stdout


def test_changed_content_fails(tmp_path):
    exp = _tree(tmp_path / "exp", FILES)
    act = _tree(tmp_path / "act", {**FILES, "sub/b.txt": "BETA"})
    res = _compare(exp, act)
    assert res.returncode != 0
    assert "mismatches=1" in res.stdout
    assert "sub/b.txt" in res.stdout


def test_missing_and_extra_files_fail(tmp_path):
    exp = _tree(tmp_path / "exp", FILES)
    act_files = {k: v for k, v in FILES.items() if k != "a.txt"}
    act_files["extra.txt"] = "x"
    act = _tree(tmp_path / "act", act_files)
    res = _compare(exp, act)
    assert res.returncode != 0
    assert "mismatches=2" in res.stdout
    assert "a.txt" in res.stdout and "extra.txt" in res.stdout


def test_wrong_expected_count_fails(tmp_path):
    exp = _tree(tmp_path / "exp", FILES)
    act = _tree(tmp_path / "act", FILES)
    res = _compare(exp, act, count=300)
    assert res.returncode != 0
    assert "300" in res.stdout + res.stderr


def test_empty_trees_fail(tmp_path):
    (tmp_path / "exp").mkdir()
    (tmp_path / "act").mkdir()
    res = _compare(tmp_path / "exp", tmp_path / "act")
    assert res.returncode != 0, "two empty trees must not count as a successful comparison"


def test_missing_directory_fails(tmp_path):
    exp = _tree(tmp_path / "exp", FILES)
    res = _compare(exp, tmp_path / "does-not-exist")
    assert res.returncode != 0


@pytest.fixture(scope="module")
def hook_tasks() -> list[dict]:
    assert HOOK.exists(), f"{HOOK} missing"
    return yaml.safe_load(HOOK.read_text())


def test_hook_is_task_list_with_required_inputs(hook_tasks):
    assert isinstance(hook_tasks, list) and hook_tasks
    text = HOOK.read_text()
    for var in ("rebuild_mockforge_src", "rebuild_mockforge_volume", "rebuild_mockforge_files", "rebuild_mockforge_seed"):
        assert var in text, var


def test_hook_runs_comparison_script_and_regenerates_with_seed():
    text = HOOK.read_text()
    assert "rebuild-tree-compare.sh" in text
    assert "mockforge" in text and "nextcloud" in text
    assert "--seed" in text and "--files" in text
    assert "docker volume inspect" in text
