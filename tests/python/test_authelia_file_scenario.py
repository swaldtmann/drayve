"""The ``authelia-file`` Molecule scenario is wired in everywhere a scenario has to be.

A scenario that exists but is missing from the manual workflow, the tag-push
matrix, the Makefile or the docs is silently never run (or never findable).
These tests read the repository files only; they do not need a VM.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = "authelia-file"
SCENARIO_DIR = REPO_ROOT / "molecule" / SCENARIO
WORKFLOWS = REPO_ROOT / ".gitea" / "workflows"


def _scenarios() -> list[str]:
    return sorted(p.parent.name for p in (REPO_ROOT / "molecule").glob("*/molecule.yml"))


def _matrix_groups() -> list[list[str]]:
    wf = yaml.safe_load((WORKFLOWS / "release-gate-matrix.yml").read_text())
    include = wf["jobs"]["molecule"]["strategy"]["matrix"]["include"]
    return [entry["group"].split() for entry in include]


def _single_options() -> list[str]:
    wf = yaml.safe_load((WORKFLOWS / "release-gate-single.yml").read_text())
    # PyYAML reads the bare key `on` as boolean True.
    trigger = wf.get("on", wf.get(True))
    return trigger["workflow_dispatch"]["inputs"]["scenario"]["options"]


def test_scenario_files_exist():
    for name in ("molecule.yml", "converge.yml", "prepare.yml", "tests/conftest.py",
                 "tests/authelia_probe.py"):
        assert (SCENARIO_DIR / name).is_file(), name


@pytest.mark.parametrize("scenario", _scenarios())
def test_every_scenario_can_be_picked_in_the_manual_workflow(scenario):
    assert scenario in _single_options()


@pytest.mark.parametrize("scenario", _scenarios())
def test_every_scenario_runs_in_exactly_one_matrix_group(scenario):
    groups = [g for g in _matrix_groups() if scenario in g]
    assert len(groups) == 1, f"{scenario} is in {len(groups)} groups"


def test_workflows_list_no_scenario_that_does_not_exist():
    existing = set(_scenarios())
    assert set(_single_options()) <= existing
    assert {s for g in _matrix_groups() for s in g} <= existing


def test_matrix_comments_state_the_real_number_of_scenarios():
    n = len(_scenarios())
    text = (WORKFLOWS / "release-gate-matrix.yml").read_text()
    assert f"All {n} scenarios run" in text
    sizes = " + ".join(str(len(g)) for g in _matrix_groups())
    assert f"two jobs ({sizes} scenarios)" in text
    single = (WORKFLOWS / "release-gate-single.yml").read_text()
    assert f"(all {n} scenarios)" in single


def test_makefile_has_a_target_for_the_scenario():
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert f"test-{SCENARIO}:" in makefile
    assert f"$(MOLECULE) test -s {SCENARIO}" in makefile


def test_docs_list_the_scenario_and_say_what_it_checks():
    docs = (REPO_ROOT / "docs" / "testing.md").read_text()
    assert f"`{SCENARIO}`" in docs.split("\n\n")[1], "scenario missing from the opening list"
    assert f"## The `{SCENARIO}` scenario" in docs


def test_scenario_is_the_file_backend_with_the_requested_access_rules():
    cfg = yaml.safe_load((SCENARIO_DIR / "molecule.yml").read_text())
    v = cfg["provisioner"]["inventory"]["group_vars"]["all"]
    assert v["auth_lldap"] is False and v["auth"]["lldap"] is False
    assert v["auth"]["access_control"]["default_policy"] == "deny"
    rules = v["af_base_rules"]
    admin, viewer = rules
    assert admin["policy"] == "two_factor" and admin["groups"] == ["admins"]
    assert set(admin["domain"]) == {"test.drayve.local", "*.test.drayve.local"}
    assert viewer["policy"] == "one_factor" and viewer["groups"] == ["viewers"]
    assert isinstance(viewer["domain"], str), "viewers may reach exactly one domain"
    groups = {u["name"]: u["groups"] for u in v["af_users"]}
    assert groups["alice"] == ["admins", "lldap_admin"] and groups["bob"] == ["viewers"]


def test_dollar_signs_in_molecule_yml_are_escaped_for_molecules_interpolation():
    # Molecule substitutes $NAME / ${NAME} in molecule.yml ("Invalid placeholder in string"
    # at `molecule syntax`); a literal $ has to be written $$ (argon2id hashes!).
    import re

    text = (SCENARIO_DIR / "molecule.yml").read_text()
    bare = re.findall(r"(?<!\$)\$(?!\$|\{)\w*", text)
    assert bare == [], f"unescaped $ in molecule.yml: {bare[:3]}"


def test_the_grafana_checks_have_exactly_one_switch():
    text = (SCENARIO_DIR / "tests" / "test_20_grafana_oidc.py").read_text()
    assert text.count("GRAFANA_BACKCHANNEL_FIXED = ") == 1


def test_scenario_does_not_use_the_real_hetzner_or_a_mirror_of_its_own():
    text = (SCENARIO_DIR / "molecule.yml").read_text()
    assert "platforms" not in text and "server_type" not in text  # shared base config provides the host
