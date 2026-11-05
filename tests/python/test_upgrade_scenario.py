"""The ``upgrade`` Molecule scenario and its baseline-tag resolver.

The scenario converges a previous release (a git tag) first and the working
tree on top of it. These tests read repository files and run the resolver
script; they do not need a VM.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO_ROOT / "molecule" / "upgrade"
WORKFLOWS = REPO_ROOT / ".gitea" / "workflows"
RESOLVER = REPO_ROOT / "scripts" / "upgrade-from-tag.sh"

TAGS = "\n".join(
    ["v0.9.3", "v0.10.2", "v0.8.13", "v0.10.1", "v0.9.10", "v0.10.0-rc1", "latest", "v0.8.2"]
)


def _resolve(tags: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(RESOLVER), *args],
        input=tags,
        capture_output=True,
        text=True,
        check=False,
    )


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_scenario_files_exist():
    for name in ("molecule.yml", "prepare.yml", "baseline-converge.yml", "converge.yml",
                 "tests/test_upgrade.py"):
        assert (SCENARIO_DIR / name).is_file(), name


def test_scenario_runs_in_matrix_group_g2():
    include = _workflow("release-gate-matrix.yml")["jobs"]["molecule"]["strategy"]["matrix"]["include"]
    g2 = next(e for e in include if e["name"] == "g2")
    assert "upgrade" in g2["group"].split()


def test_single_workflow_passes_an_optional_baseline_tag():
    wf = _workflow("release-gate-single.yml")
    trigger = wf.get("on", wf.get(True))
    inp = trigger["workflow_dispatch"]["inputs"]["upgrade_from"]
    assert inp["required"] is False


@pytest.mark.parametrize("name", ["release-gate-matrix.yml", "release-gate-single.yml"])
def test_workflows_fetch_the_baseline_tag(name):
    text = (WORKFLOWS / name).read_text()
    assert "scripts/upgrade-from-tag.sh" in text
    assert "DRAYVE_UPGRADE_FROM" in text


def test_docs_describe_the_scenario():
    text = (REPO_ROOT / "docs" / "testing.md").read_text()
    assert "`upgrade`" in text
    assert "DRAYVE_UPGRADE_FROM" in text


# === Resolver ===

def test_resolver_picks_highest_release_tag_without_current():
    r = _resolve(TAGS)
    assert (r.returncode, r.stdout.strip()) == (0, "v0.10.2")


def test_resolver_picks_predecessor_of_a_release_tag():
    r = _resolve(TAGS, "v0.10.2")
    assert (r.returncode, r.stdout.strip()) == (0, "v0.10.1")


def test_resolver_orders_numerically_not_lexically():
    r = _resolve(TAGS, "v0.10.0")
    assert (r.returncode, r.stdout.strip()) == (0, "v0.9.10")


def test_resolver_predecessor_of_a_tag_not_in_the_list():
    r = _resolve(TAGS, "v0.10.3")
    assert (r.returncode, r.stdout.strip()) == (0, "v0.10.2")


def test_resolver_ignores_a_branch_name_as_current():
    r = _resolve(TAGS, "feat/something")
    assert (r.returncode, r.stdout.strip()) == (0, "v0.10.2")


def test_resolver_fails_without_predecessor():
    r = _resolve("v0.8.2\n", "v0.8.2")
    assert r.returncode == 1
    assert "no release tag" in r.stderr


def test_resolver_fails_on_empty_list():
    r = _resolve("")
    assert r.returncode == 1
