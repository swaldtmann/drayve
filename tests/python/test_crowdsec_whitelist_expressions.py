"""Tests for CrowdSec whitelist expressions.

Renders ``ansible/roles/traefik/templates/crowdsec-whitelist.yaml.j2`` and the
crowdsec service of ``ansible/templates/docker-compose.yml.j2``, and checks the
task wiring and the stack schema. The functional tests at the bottom run the
real "Deploy CrowdSec IP whitelist" task through ansible-playbook against a
disposable CrowdSec container (skipped without docker/ansible).

Run: ``pytest tests/python/test_crowdsec_whitelist_expressions.py``
"""

from __future__ import annotations

import getpass
import grp
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_ROLE = REPO_ROOT / "ansible" / "roles" / "traefik"
COMPOSE_DIR = REPO_ROOT / "ansible" / "templates"
MOUNT = "./crowdsec/whitelist.yaml:/etc/crowdsec/parsers/s02-enrich/whitelist.yaml:ro"

# Output of the template before the expression key existed (IP list only).
GOLDEN_IP_ONLY = """\
name: drayve/whitelist
description: "Whitelisted IPs from stack.yaml"
whitelist:
  reason: "stack.yaml crowdsec_whitelist"
  ip:
    - "127.0.0.1"
    - "203.0.113.1"
"""

EXPR = (
    "evt.Meta.traefik_router_name startsWith 'nextcloud@' "
    "&& evt.Meta.http_verb == 'GET' "
    "&& evt.Meta.http_status in ['200','404'] "
    "&& evt.Meta.http_path matches "
    "'^/ocs/v2\\\\.php/apps/user_status/api/v1/statuses/[A-Za-z0-9._@-]+$'"
)
SCHEMA = REPO_ROOT / "config" / "stack.schema.yaml"
WHITELIST_TASK = "Deploy CrowdSec IP whitelist"


@pytest.fixture
def render_whitelist(ansible_jinja_env):
    env = ansible_jinja_env(TRAEFIK_ROLE / "templates", strict=True)
    env.trim_blocks = True  # Ansible's template/lookup default
    template = env.get_template("crowdsec-whitelist.yaml.j2")

    def _render(**security: object) -> str:
        return template.render(security=security)

    return _render


@pytest.fixture
def render_compose(ansible_jinja_env):
    env = ansible_jinja_env(COMPOSE_DIR)
    template = env.get_template("docker-compose.yml.j2")

    def _render(**security: object) -> dict:
        ctx = {
            "crowdsec_enabled": True,
            "auth_provider": "basic",
            "security": security,
            "monitoring_services": {},
            "drayve_domain": "example.test",
        }
        return yaml.safe_load(template.render(**ctx))

    return _render


def test_ips_only_output_unchanged(render_whitelist):
    ips = ["127.0.0.1", "203.0.113.1"]
    assert render_whitelist(crowdsec_whitelist=ips) == GOLDEN_IP_ONLY
    assert (
        render_whitelist(crowdsec_whitelist=ips, crowdsec_whitelist_expressions=[])
        == GOLDEN_IP_ONLY
    )


def test_expression_rendered_as_valid_yaml(render_whitelist):
    out = render_whitelist(
        crowdsec_whitelist=["127.0.0.1"], crowdsec_whitelist_expressions=[EXPR]
    )
    doc = yaml.safe_load(out)
    assert doc["whitelist"]["ip"] == ["127.0.0.1"]
    assert doc["whitelist"]["expression"] == [EXPR]


def test_expression_without_ips_has_no_empty_ip_key(render_whitelist):
    doc = yaml.safe_load(
        render_whitelist(crowdsec_whitelist=[], crowdsec_whitelist_expressions=[EXPR])
    )
    assert "ip" not in doc["whitelist"]
    assert doc["whitelist"]["expression"] == [EXPR]


def test_expression_with_quotes_and_backslash_roundtrips(render_whitelist):
    tricky = 'evt.Meta.http_path matches "^/a\\\\.b/" && evt.Meta.x == \'y\''
    doc = yaml.safe_load(
        render_whitelist(crowdsec_whitelist=[], crowdsec_whitelist_expressions=[tricky])
    )
    assert doc["whitelist"]["expression"] == [tricky]


def _crowdsec(compose: dict) -> dict:
    return compose["services"]["crowdsec"]


def test_compose_renders_no_parsers_env_even_if_key_is_given(render_compose):
    env = _crowdsec(
        render_compose(crowdsec_parsers=["crowdsecurity/nextcloud-whitelist"])
    )["environment"]
    assert not any(e.startswith("PARSERS=") for e in env)


def test_parsers_feature_is_gone_from_schema_docs_and_example():
    schema = yaml.safe_load(SCHEMA.read_text())
    assert "crowdsec_parsers" not in schema["properties"]["security"]["properties"]
    for f in (
        REPO_ROOT / "docs" / "configuration.md",
        REPO_ROOT / "examples" / "stack-full.yaml",
        SCHEMA,
        COMPOSE_DIR / "docker-compose.yml.j2",
    ):
        text = f.read_text()
        assert "crowdsec_parsers" not in text, f
        assert "nextcloud-whitelist" not in text, f


def test_compose_mounts_whitelist_for_expressions_only(render_compose):
    vols = _crowdsec(
        render_compose(crowdsec_whitelist_expressions=[EXPR])
    )["volumes"]
    assert MOUNT in vols


def test_compose_no_whitelist_mount_by_default(render_compose):
    assert MOUNT not in _crowdsec(render_compose())["volumes"]


def _tasks() -> list[dict]:
    return yaml.safe_load((TRAEFIK_ROLE / "tasks" / "main.yml").read_text())


def test_deploy_task_runs_for_expressions(tmp_path):
    task = next(t for t in _tasks() if t.get("name") == "Deploy CrowdSec IP whitelist")
    assert "crowdsec_whitelist_expressions" in task["when"]


def test_remove_task_requires_both_lists_empty():
    task = next(
        t for t in _tasks() if t.get("name") == "Remove CrowdSec whitelist when empty"
    )
    assert "crowdsec_whitelist_expressions" in task["when"]


def _expr_schema() -> dict:
    schema = yaml.safe_load(SCHEMA.read_text())
    return schema["properties"]["security"]["properties"][
        "crowdsec_whitelist_expressions"
    ]


def _schema_accepts(value: object) -> bool:
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft7Validator(_expr_schema())
    return not list(validator.iter_errors(value))


def test_schema_declares_expressions_as_string_array():
    node = _expr_schema()
    assert node["type"] == "array"
    assert node["items"]["type"] == "string"


def test_schema_accepts_the_example_expression():
    assert _schema_accepts([EXPR])
    assert _schema_accepts([])


@pytest.mark.parametrize(
    "bad",
    ["", "a\nb", "a\r\nb", "a\n", "\nb"],
    ids=["empty", "newline", "crlf", "trailing-newline", "leading-newline"],
)
def test_schema_rejects_empty_and_multiline_expression(bad):
    assert not _schema_accepts([bad])


def test_example_stack_shows_the_narrowed_expression():
    text = (REPO_ROOT / "examples" / "stack-full.yaml").read_text()
    assert "http_status in" in text
    assert "matches" in text


def test_docs_show_the_narrowed_expression():
    text = (REPO_ROOT / "docs" / "configuration.md").read_text()
    assert "evt.Meta.http_status in ['200','404']" in text
    assert "[A-Za-z0-9._@-]+$" in text
    assert "fall outside the exception" in text


# --- config test before the whitelist file replaces the live one -------------


def _whitelist_task() -> dict:
    return next(t for t in _tasks() if t.get("name") == WHITELIST_TASK)


def test_whitelist_task_validates_with_crowdsec_config_test():
    copy = _whitelist_task()["ansible.builtin.copy"]
    validate = copy["validate"]
    assert validate.count("%s") == 1
    assert "crowdsec -t" in validate
    assert "docker run" in validate and "--rm" in validate
    assert "crowdsec_image" in validate


def test_whitelist_task_does_not_touch_deploy_role():
    # The check must not live next to "Docker compose up" (parallel work there).
    deploy = (REPO_ROOT / "ansible/roles/deploy/tasks/main.yml").read_text()
    assert "crowdsec -t" not in deploy


needs_docker = pytest.mark.skipif(
    shutil.which("docker") is None
    or shutil.which("ansible-playbook") is None
    or subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker daemon and ansible-playbook required",
)


def _run_task(tmp_path: Path, expressions: list[str]) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the REAL whitelist task via ansible-playbook (connection local)."""
    deploy_dir = tmp_path / "deploy"
    (deploy_dir / "crowdsec").mkdir(parents=True)
    (tmp_path / "templates").mkdir()
    shutil.copy(
        TRAEFIK_ROLE / "templates" / "crowdsec-whitelist.yaml.j2",
        tmp_path / "templates",
    )
    play = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "tasks": [_whitelist_task()],
        }
    ]
    (tmp_path / "play.yml").write_text(yaml.safe_dump(play))
    extra = {
        "drayve_deploy_dir": str(deploy_dir),
        "drayve_user": getpass.getuser(),
        "drayve_group": grp.getgrgid(os.getgid()).gr_name,
        "crowdsec_enabled": True,
        "security": {"crowdsec_whitelist_expressions": expressions},
    }
    (tmp_path / "vars.json").write_text(json.dumps(extra))
    res = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(tmp_path / "play.yml"),
         "-e", f"@{tmp_path / 'vars.json'}"],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )
    return res, deploy_dir / "crowdsec" / "whitelist.yaml"


def _crowdsec_containers() -> str:
    out = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}} {{.Image}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return "\n".join(l for l in out.splitlines() if "crowdsec" in l)


@needs_docker
def test_functional_valid_expression_is_written(tmp_path):
    before = _crowdsec_containers()
    res, target = _run_task(tmp_path, [EXPR])
    assert res.returncode == 0, res.stdout + res.stderr
    assert target.exists()
    assert yaml.safe_load(target.read_text())["whitelist"]["expression"] == [EXPR]
    assert _crowdsec_containers() == before


@needs_docker
def test_functional_broken_expression_fails_readably_and_writes_nothing(tmp_path):
    before = _crowdsec_containers()
    res, target = _run_task(tmp_path, ["evt.Meta.http_verb =="])
    assert res.returncode != 0
    assert "unable to compile whitelist expression" in res.stdout + res.stderr
    assert not target.exists()
    assert _crowdsec_containers() == before


def test_docs_example_parses_to_the_tested_expression():
    # The YAML block in the docs, loaded as YAML, must equal the expression the
    # functional tests compile and replay (quoting survives YAML -> to_json).
    text = (REPO_ROOT / "docs" / "configuration.md").read_text()
    start = text.index("```yaml\nsecurity:\n  crowdsec_whitelist_expressions:")
    block = text[start + len("```yaml\n"):].split("```", 1)[0]
    parsed = yaml.safe_load(block)["security"]["crowdsec_whitelist_expressions"]
    assert parsed == [EXPR]
