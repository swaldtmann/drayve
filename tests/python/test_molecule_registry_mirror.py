"""Registry mirror for the Molecule scenarios is switched by an env variable.

``DRAYVE_MOLECULE_REGISTRY_MIRROR`` (unset/empty = no mirror) feeds
``docker_registry_mirrors`` for every scenario from ONE place, the shared
Molecule base config ``.config/molecule/config.yml``. The CI workflows set it to
the pull-through cache that runs inside the self-hosted runner VM. Before, each
``molecule/*/molecule.yml`` hard-coded a mirror host (``192.0.2.10``, a
documentation address that never answered), so local runs and CI behaved the
same, and the mirror could not be pointed at the real cache.

The Jinja expression is rendered here with a native-types environment (the same
literal_eval step Ansible applies to a ``"{{ ... }}"`` variable) and the
``lookup`` function replaced by an env reader. Then the result goes through the
real ``daemon.json.j2``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from jinja2.nativetypes import NativeEnvironment

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_CONFIG = REPO_ROOT / ".config" / "molecule" / "config.yml"
WORKFLOWS = REPO_ROOT / ".gitea" / "workflows"
TEMPLATE_DIR = REPO_ROOT / "ansible" / "roles" / "docker" / "templates"
ENV_VAR = "DRAYVE_MOLECULE_REGISTRY_MIRROR"
CI_MIRROR = "http://10.99.0.1:5000"


def _base_expression() -> str:
    cfg = yaml.safe_load(BASE_CONFIG.read_text())
    return cfg["provisioner"]["inventory"]["group_vars"]["all"]["docker_registry_mirrors"]


def _render_mirrors(monkeypatch: pytest.MonkeyPatch, value: str | None):
    if value is None:
        monkeypatch.delenv(ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(ENV_VAR, value)

    def lookup(kind: str, name: str) -> str:
        assert kind == "ansible.builtin.env"
        return os.environ.get(name, "")

    env = NativeEnvironment(undefined=StrictUndefined)
    env.globals["lookup"] = lookup
    return env.from_string(_base_expression()).render()


def test_scenarios_do_not_hardcode_a_mirror():
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in sorted((REPO_ROOT / "molecule").glob("*/molecule.yml"))
        if "registry_mirrors" in p.read_text()
    ]
    assert offenders == []


def test_base_config_reads_the_env_variable():
    assert ENV_VAR in _base_expression()


@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_means_no_mirror(monkeypatch, value):
    assert _render_mirrors(monkeypatch, value) == []


def test_set_means_that_mirror(monkeypatch):
    assert _render_mirrors(monkeypatch, CI_MIRROR) == [CI_MIRROR]


def _daemon_json(mirrors) -> str:
    e = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), undefined=StrictUndefined, trim_blocks=True)
    e.filters["to_json"] = json.dumps
    return e.get_template("daemon.json.j2").render(docker_registry_mirrors=mirrors, docker_daemon_extra={})


def test_rendered_daemon_json_without_mirror_has_no_registry_mirrors(monkeypatch):
    parsed = json.loads(_daemon_json(_render_mirrors(monkeypatch, "")))
    assert "registry-mirrors" not in parsed


def test_rendered_daemon_json_with_mirror_lists_it(monkeypatch):
    parsed = json.loads(_daemon_json(_render_mirrors(monkeypatch, CI_MIRROR)))
    assert parsed["registry-mirrors"] == [CI_MIRROR]


def _workflow_files():
    return sorted(WORKFLOWS.glob("*.yml"))


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflows_parse_as_yaml(path):
    assert isinstance(yaml.safe_load(path.read_text()), dict)


def test_every_molecule_job_sets_the_ci_mirror():
    seen = 0
    for path in _workflow_files():
        wf = yaml.safe_load(path.read_text())
        for name, job in wf["jobs"].items():
            runs = "\n".join(s.get("run", "") for s in job.get("steps", []))
            if "molecule test" not in runs:
                continue
            seen += 1
            assert job.get("env", {}).get(ENV_VAR) == CI_MIRROR, f"{path.name}:{name}"
    assert seen >= 2  # release-gate-matrix + release-gate-single
