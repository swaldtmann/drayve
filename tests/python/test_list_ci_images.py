"""Tests for ``scripts/list-ci-images.py``.

The script lists every container image the Molecule scenarios can pull, one
``name:tag`` per line, so the Docker Hub pull-through cache in the CI runner VM
can be warmed with exactly those images. Sources: ``*_image`` variables (group
vars, role defaults, scenario overrides) and ``image:`` lines in the Jinja
compose templates (literal, ``{{ var | default('x') }}``, ``{{ var }}``).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "list-ci-images.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("list_ci_images", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lci = _load_module()


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    _write(tmp_path, "ansible/inventory/group_vars/all/vars.yml",
           "grafana_image: grafana/grafana:12.3.6\n"
           "cadvisor_image: ghcr.io/google/cadvisor:0.56.2\n"
           "not_an_img: nope\n")
    _write(tmp_path, "ansible/roles/traefik/defaults/main.yml", "traefik_image: traefik:v3.6\n")
    _write(tmp_path, "molecule/basic/molecule.yml",
           "platforms:\n  - name: test-basic\n    image: ubuntu-24.04\n"
           "provisioner:\n  inventory:\n    group_vars:\n      all:\n"
           "        loki_image: grafana/loki:3.3.2\n"
           "        grafana_image: grafana/grafana:12.3.6\n")
    _write(tmp_path, "ansible/templates/docker-compose.yml.j2",
           "services:\n"
           "  a:\n    image: {{ traefik_image | default('traefik:v3.5') }}\n"
           "  b:\n    image: nginx:alpine\n"
           "  c:\n    image: {{ grafana_image }}\n"
           "  d:\n    image: {{ unknown_image }}\n")
    return tmp_path


def test_collects_vars_scenario_overrides_template_defaults_and_literals(mini_repo):
    images = lci.collect_images(mini_repo)
    assert images == {
        "grafana/grafana:12.3.6",
        "ghcr.io/google/cadvisor:0.56.2",
        "traefik:v3.6",
        "traefik:v3.5",
        "grafana/loki:3.3.2",
        "nginx:alpine",
    }


def test_incus_platform_image_and_non_image_vars_are_not_listed(mini_repo):
    images = lci.collect_images(mini_repo)
    assert "ubuntu-24.04" not in images
    assert "nope" not in images


@pytest.mark.parametrize(
    "ref, hub",
    [
        ("traefik:v3.6", True),
        ("grafana/loki:3.3.2", True),
        ("docker.io/library/redis:7", True),
        ("ghcr.io/google/cadvisor:0.56.2", False),
        ("quay.io/x/y:1", False),
        ("localhost:5000/x:1", False),
    ],
)
def test_is_docker_hub(ref, hub):
    assert lci.is_docker_hub(ref) is hub


def test_image_without_tag_gets_latest():
    assert lci.normalize("redis") == "redis:latest"
    assert lci.normalize("docker.io/library/redis:7") == "library/redis:7"


def _run(*args: str, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--repo-root", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def test_cli_default_prints_only_hub_images_sorted(mini_repo):
    r = _run(root=mini_repo)
    assert r.returncode == 0
    assert r.stdout.splitlines() == [
        "grafana/grafana:12.3.6", "grafana/loki:3.3.2", "nginx:alpine", "traefik:v3.5", "traefik:v3.6",
    ]


def test_cli_other_prints_non_hub_images(mini_repo):
    r = _run("--other", root=mini_repo)
    assert r.stdout.splitlines() == ["ghcr.io/google/cadvisor:0.56.2"]


def test_cli_all_prints_both(mini_repo):
    r = _run("--all", root=mini_repo)
    assert "ghcr.io/google/cadvisor:0.56.2" in r.stdout.splitlines()
    assert "nginx:alpine" in r.stdout.splitlines()


def test_real_repo_contains_known_scenario_images():
    images = lci.collect_images(REPO_ROOT)
    for ref in ("traefik:v3.6", "grafana/loki:3.3.2", "postgres:16-alpine", "nginx:alpine",
                "authelia/authelia:4.39", "ghcr.io/goauthentik/server:2025.10.4"):
        assert ref in images
    assert all(":" in i for i in images)
