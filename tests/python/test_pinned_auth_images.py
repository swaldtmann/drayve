"""Guard: Authelia, lldap and Grafana images must stay pinned to a full version.

A floating tag (``latest``, ``stable``, a bare minor line such as ``4.39``)
let breaking changes in through the back door (Authelia 4.39.19 in 2026-05,
lldap ``:stable`` in 2026-07). Defaults, compose fallbacks and molecule
scenarios must carry an exact ``x.y.z`` tag, and the default and the compose
fallback must agree.

Run: ``pytest tests/python/test_pinned_auth_images.py``
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all" / "vars.yml"
AUTH_DEFAULTS = REPO_ROOT / "ansible" / "roles" / "auth" / "defaults" / "main.yml"
AUTHELIA_DEFAULTS = REPO_ROOT / "ansible" / "roles" / "authelia" / "defaults" / "main.yml"
COMPOSE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"
MOLECULE = sorted((REPO_ROOT / "molecule").glob("*/molecule.yml"))

# variable -> repository the value must point at
REPOS = {
    "authelia_image": "authelia/authelia",
    "lldap_image": "lldap/lldap",
    "grafana_image": "grafana/grafana",
}
FULL_VERSION = re.compile(r"^v?\d+\.\d+\.\d+$")


def _assignments(path: Path, var: str) -> list[str]:
    """Values of ``<var>: <value>`` lines (YAML, any indentation)."""
    pat = re.compile(rf"^\s*{var}:\s*(\S+)\s*$", re.M)
    return pat.findall(path.read_text())


def _compose_fallback(var: str) -> list[str]:
    pat = re.compile(rf"\{{\{{\s*{var}\s*\|\s*default\('([^']+)'\)\s*\}}\}}")
    return pat.findall(COMPOSE.read_text())


def _all_refs() -> list[tuple[str, str, str]]:
    """(label, variable, image reference) for every place the three images are set."""
    refs = []
    for path in (VARS, AUTH_DEFAULTS, AUTHELIA_DEFAULTS, *MOLECULE):
        for var in REPOS:
            for val in _assignments(path, var):
                refs.append((f"{path.relative_to(REPO_ROOT)}:{var}", var, val))
    for var in REPOS:
        for val in _compose_fallback(var):
            refs.append((f"{COMPOSE.relative_to(REPO_ROOT)}:{var}-fallback", var, val))
    return refs


REFS = _all_refs()


def test_every_image_variable_is_found_somewhere():
    seen = {var for _, var, _ in REFS}
    assert seen == set(REPOS)
    # authelia: 2 defaults + compose fallback; lldap: vars + compose fallback; grafana: vars
    labels = {label for label, _, _ in REFS}
    assert "ansible/roles/auth/defaults/main.yml:authelia_image" in labels
    assert "ansible/roles/authelia/defaults/main.yml:authelia_image" in labels
    assert "ansible/templates/docker-compose.yml.j2:authelia_image-fallback" in labels
    assert "ansible/templates/docker-compose.yml.j2:lldap_image-fallback" in labels
    assert "ansible/inventory/group_vars/all/vars.yml:lldap_image" in labels
    assert "ansible/inventory/group_vars/all/vars.yml:grafana_image" in labels


@pytest.mark.parametrize("label,var,ref", REFS, ids=[r[0] for r in REFS])
def test_image_is_pinned_to_full_version(label, var, ref):
    repo, _, tag = ref.partition(":")
    assert repo == REPOS[var], f"{label}: unexpected repository {ref}"
    assert tag, f"{label}: no tag in {ref}"
    assert tag not in ("latest", "stable"), f"{label}: floating tag {ref}"
    assert FULL_VERSION.match(tag), f"{label}: {ref} is not an exact x.y.z version"


def _single(values: list[str], what: str) -> str:
    assert len(values) == 1, f"{what}: expected exactly one, got {values}"
    return values[0]


def test_authelia_defaults_and_compose_fallback_agree():
    a = _single(_assignments(AUTH_DEFAULTS, "authelia_image"), "auth defaults")
    b = _single(_assignments(AUTHELIA_DEFAULTS, "authelia_image"), "authelia defaults")
    c = _single(_compose_fallback("authelia_image"), "compose fallback")
    assert a == b == c


def test_lldap_default_and_compose_fallback_agree():
    a = _single(_assignments(VARS, "lldap_image"), "vars.yml")
    c = _single(_compose_fallback("lldap_image"), "compose fallback")
    assert a == c


def test_molecule_scenarios_use_the_default_versions():
    expected = {
        "lldap_image": _single(_assignments(VARS, "lldap_image"), "vars.yml"),
        "grafana_image": _single(_assignments(VARS, "grafana_image"), "vars.yml"),
    }
    for path in MOLECULE:
        for var, want in expected.items():
            for val in _assignments(path, var):
                assert val == want, f"{path.relative_to(REPO_ROOT)}:{var} {val} != {want}"
