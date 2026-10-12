"""Guard: every container image the framework deploys must stay pinned to an exact version.

A floating tag (``latest``, ``stable``, ``alpine``, a bare major or minor line
such as ``4.39`` or ``v3.6``) lets breaking changes in through the back door
(Authelia 4.39.19 in 2026-05, lldap ``:stable`` in 2026-07). Defaults, compose
fallbacks and molecule scenarios must carry an exact version tag, the default
and the compose fallback must agree, and every ``image:`` line in the compose
template must go through a variable registered here.

Run: ``pytest tests/python/test_pinned_images.py``
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all" / "vars.yml"
ROLE_DEFAULTS = sorted((REPO_ROOT / "ansible" / "roles").glob("*/defaults/main.yml"))
COMPOSE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"
MOLECULE = sorted((REPO_ROOT / "molecule").glob("*/molecule.yml"))

# variable -> repository the value must point at
REPOS = {
    "traefik_image": "traefik",
    "crowdsec_image": "crowdsecurity/crowdsec",
    "landing_image": "nginx",
    "authelia_image": "authelia/authelia",
    "lldap_image": "lldap/lldap",
    "authentik_image": "ghcr.io/goauthentik/server",
    "authentik_pg_image": "postgres",
    "authentik_redis_image": "redis",
    "node_exporter_image": "prom/node-exporter",
    "cadvisor_image": "ghcr.io/google/cadvisor",
    "loki_image": "grafana/loki",
    "promtail_image": "grafana/promtail",
    "grafana_image": "grafana/grafana",
    "prometheus_image": "prom/prometheus",
}
# Exact version: x.y.z (leading v allowed), optionally with an -alpine suffix.
# Postgres versions are x.y (16.15 is exact; "16" is a major line).
FULL_VERSION = re.compile(r"^v?\d+\.\d+\.\d+(-alpine(\d+\.\d+)?)?$")
POSTGRES_VERSION = re.compile(r"^\d+\.\d+(-alpine(\d+\.\d+)?)?$")
# Authentik Postgres/Redis stay in their major line; a Postgres major change
# needs a data migration.
MAJOR_LINE = {"authentik_pg_image": "16.", "authentik_redis_image": "7."}


def _assignments(path: Path, var: str) -> list[str]:
    """Values of ``<var>: <value>`` lines (YAML, any indentation)."""
    pat = re.compile(rf"^\s*{var}:\s*(\S+)\s*$", re.M)
    return pat.findall(path.read_text())


def _compose_fallback(var: str) -> list[str]:
    pat = re.compile(rf"\{{\{{\s*{var}\s*\|\s*default\('([^']+)'\)\s*\}}\}}")
    return pat.findall(COMPOSE.read_text())


def _default_sources() -> list[Path]:
    return [VARS, *ROLE_DEFAULTS]


def _defaults(var: str) -> list[str]:
    return [v for p in _default_sources() for v in _assignments(p, var)]


def _all_refs() -> list[tuple[str, str, str]]:
    """(label, variable, image reference) for every place an image is set."""
    refs = []
    for path in (*_default_sources(), *MOLECULE):
        for var in REPOS:
            for val in _assignments(path, var):
                refs.append((f"{path.relative_to(REPO_ROOT)}:{var}", var, val))
    for var in REPOS:
        for val in _compose_fallback(var):
            refs.append((f"{COMPOSE.relative_to(REPO_ROOT)}:{var}-fallback", var, val))
    return refs


REFS = _all_refs()


def test_every_image_variable_has_a_default():
    for var in REPOS:
        assert _defaults(var), f"{var}: no default in vars.yml or a role defaults file"


def test_compose_images_all_go_through_registered_variables():
    """No literal ``image: foo:tag`` and no unregistered variable in the compose template."""
    lines = re.findall(r"^\s*image:\s*(.+?)\s*$", COMPOSE.read_text(), re.M)
    assert lines
    for line in lines:
        m = re.match(r"^\{\{\s*(\w+)\s*(?:\|\s*default\('[^']+'\)\s*)?\}\}$", line)
        assert m, f"compose template: literal image reference {line!r} (use a *_image variable)"
        assert m.group(1) in REPOS, f"compose template: {m.group(1)} is not registered in REPOS"


def test_no_unregistered_image_variable():
    """A new ``*_image`` variable must be added to REPOS, otherwise it escapes the pin check."""
    pat = re.compile(r"^\s*(\w+_image):\s*\S", re.M)
    for path in (*_default_sources(), *MOLECULE):
        for var in pat.findall(path.read_text()):
            assert var in REPOS, f"{path.relative_to(REPO_ROOT)}: {var} is not registered in REPOS"


@pytest.mark.parametrize("label,var,ref", REFS, ids=[r[0] for r in REFS])
def test_image_is_pinned_to_exact_version(label, var, ref):
    repo, _, tag = ref.partition(":")
    assert repo == REPOS[var], f"{label}: unexpected repository {ref}"
    assert tag, f"{label}: no tag in {ref}"
    assert tag not in ("latest", "stable", "alpine"), f"{label}: floating tag {ref}"
    rx = POSTGRES_VERSION if var == "authentik_pg_image" else FULL_VERSION
    assert rx.match(tag), f"{label}: {ref} is not an exact version"
    if var in MAJOR_LINE:
        assert tag.startswith(MAJOR_LINE[var]), f"{label}: {ref} left major line {MAJOR_LINE[var]}x"


def _single(values: list[str], what: str) -> str:
    assert len(values) == 1, f"{what}: expected exactly one, got {values}"
    return values[0]


@pytest.mark.parametrize("var", sorted(REPOS))
def test_defaults_and_compose_fallback_agree(var):
    defaults = _defaults(var)
    assert defaults
    assert len(set(defaults)) == 1, f"{var}: defaults disagree: {defaults}"
    fallbacks = _compose_fallback(var)
    if fallbacks:
        assert set(fallbacks) == {defaults[0]}, f"{var}: compose fallback {fallbacks} != default {defaults[0]}"


def test_molecule_scenarios_use_the_default_versions():
    for path in MOLECULE:
        for var in REPOS:
            want = _single(sorted(set(_defaults(var))), f"{var} default")
            for val in _assignments(path, var):
                assert val == want, f"{path.relative_to(REPO_ROOT)}:{var} {val} != {want}"
