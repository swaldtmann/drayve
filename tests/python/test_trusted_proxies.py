"""``security.trusted_proxies`` and the underscore-header strip on ``auth@file``.

Origin: two-host measurement of ``auth.provider: authelia_remote`` (Authelia host A
did not trust the public IP of host B, deleted its ``X-Forwarded-*`` headers and
answered 400) and the finding that a client-sent ``Remote_User`` reached the
backend next to the real ``Remote-User``.

  1. Render: entries land in ``forwardedHeaders.trustedIPs`` next to
     ``172.16.0.0/12``; an empty list renders as before.
  2. Validation (fail-closed): ``make validate`` (scripts/validate-stack.py) and
     the deploy preflight (ansible/tasks/auth_preflight.yml) share one rule set in
     ansible/playbooks/filter_plugins/drayve_auth.py.
  3. The ``auth`` middleware is a chain: strip the four underscore headers, then
     forwardAuth. ``forwardedHeaders.insecure`` never shows up in the rendered file.

Run: ``pytest tests/python/test_trusted_proxies.py``
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_TEMPLATE = REPO_ROOT / "config" / "traefik" / "traefik.yml.j2"
TRAEFIK_TASKS = REPO_ROOT / "ansible" / "roles" / "traefik" / "tasks" / "main.yml"
PREFLIGHT_TASKS = REPO_ROOT / "ansible" / "tasks" / "auth_preflight.yml"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate-stack.py"
SCHEMA_PATH = REPO_ROOT / "config" / "stack.schema.yaml"

DOCKER_RANGE = "172.16.0.0/12"
UNDERSCORE_HEADERS = ["Remote_User", "Remote_Groups", "Remote_Email", "Remote_Name"]
HYPHEN_HEADERS = ["Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name"]


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_stack", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


# ---- 1. render ---------------------------------------------------------------------------


def _render_traefik(**ctx: object) -> str:
    env = Environment(loader=FileSystemLoader(TRAEFIK_TEMPLATE.parent), keep_trailing_newline=True)
    base = {"acme_email": "ops@example.com", "crowdsec_enabled": True}
    return env.get_template(TRAEFIK_TEMPLATE.name).render(**{**base, **ctx})


def _trusted_ips(**ctx: object) -> list[str]:
    cfg = yaml.safe_load(_render_traefik(**ctx))
    return cfg["entryPoints"]["websecure"]["forwardedHeaders"]["trustedIPs"]


def test_trusted_proxies_are_added_next_to_the_docker_range():
    ips = _trusted_ips(security={"trusted_proxies": ["203.0.113.10/32", "2001:db8::1"]})
    assert ips == [DOCKER_RANGE, "203.0.113.10/32", "2001:db8::1"]


@pytest.mark.parametrize(
    "ctx",
    [{}, {"security": {}}, {"security": {"crowdsec": True}}, {"security": {"trusted_proxies": []}}],
    ids=["no-security", "empty-security", "other-key", "empty-list"],
)
def test_without_trusted_proxies_the_output_is_unchanged(ctx):
    assert _trusted_ips(**ctx) == [DOCKER_RANGE]


@pytest.mark.parametrize(
    "ctx",
    [
        {},
        {"security": {"trusted_proxies": ["203.0.113.10/32"]}},
        {"security": {"trusted_proxies": ["0.0.0.0/0"], "insecure": True}},
    ],
)
def test_rendered_static_config_never_contains_insecure(ctx):
    assert "insecure" not in _render_traefik(**ctx)


# ---- 2. validation -----------------------------------------------------------------------


def _stack(security) -> dict:
    return {
        "drayve_version": "0.1.0",
        "stack": {"name": "x", "domain": "example.com", "acme_email": "ops@example.org"},
        "security": security,
    }


def _errors(security) -> list[str]:
    return validator.validate(_stack(security))[0]


@pytest.mark.parametrize(
    "value",
    [
        [],
        ["203.0.113.10"],
        ["203.0.113.10/32"],
        ["198.51.100.0/24", "203.0.113.10/32"],
        ["2001:db8::1"],
        ["2001:db8::/32"],
        ["10.0.0.0/8"],
    ],
)
def test_valid_trusted_proxies_are_accepted(value):
    assert _errors({"trusted_proxies": value}) == []


@pytest.mark.parametrize(
    "value",
    [
        ["0.0.0.0/0"],
        ["::/0"],
        ["0.0.0.0/0", "203.0.113.10/32"],
        ["0.0.0.0/00"],
        ["1.2.3.4/0"],
        ["not-an-ip"],
        [""],
        ["203.0.113.10/33"],
        ["203.0.113.10/255.255.255.255"],
        ["203.0.113.10 "],
        ["203.0.113.10/32\n"],
        ['203.0.113.10"'],
        ["203.0.113.5/24"],
        ["fe80::1%eth0"],
        ["{{ evil }}"],
        [203],
        [None],
        [["203.0.113.10"]],
        "203.0.113.10/32",
        "203.0.113.10/32,198.51.100.0/24",
        "0.0.0.0/0",
        None,
        {"203.0.113.10/32": True},
        True,
    ],
    ids=repr,
)
def test_bad_trusted_proxies_abort_validate(value):
    errors = _errors({"trusted_proxies": value})
    assert any("security.trusted_proxies" in e for e in errors), errors


def test_error_does_not_echo_template_syntax():
    errors = _errors({"trusted_proxies": ["{{ lookup('env', 'HOME') }}"]})
    assert errors and not any("lookup" in e for e in errors), errors


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def _preflight(tmp_path: Path, security):
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: wrapper
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                - ansible.builtin.import_tasks: {PREFLIGHT_TASKS}
            """
        )
    )
    vars_file = tmp_path / "vars.json"
    vars_file.write_text(json.dumps({"drayve_domain": "example.com", "security": security}))
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", f"@{vars_file}"],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "value",
    [["0.0.0.0/0"], ["::/0"], ["not-an-ip"], "203.0.113.10/32", ["1.2.3.4/0"]],
    ids=repr,
)
def test_preflight_rejects_bad_trusted_proxies(ansible_available, tmp_path, value):
    r = _preflight(tmp_path, {"trusted_proxies": value})
    assert r.returncode != 0, r.stdout
    assert "security.trusted_proxies" in r.stdout + r.stderr


def test_preflight_accepts_valid_trusted_proxies(ansible_available, tmp_path):
    r = _preflight(tmp_path, {"trusted_proxies": ["203.0.113.10/32", "2001:db8::1"]})
    assert r.returncode == 0, r.stdout + r.stderr


def test_preflight_passes_without_trusted_proxies(ansible_available, tmp_path):
    r = _preflight(tmp_path, {"crowdsec": True})
    assert r.returncode == 0, r.stdout + r.stderr


def test_schema_describes_trusted_proxies():
    schema = yaml.safe_load(SCHEMA_PATH.read_text())
    prop = schema["properties"]["security"]["properties"]["trusted_proxies"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"


# ---- 3. underscore headers stripped before forwardAuth ------------------------------------


def _auth_middlewares(ansible_jinja_env, auth_provider: str) -> dict:
    env = ansible_jinja_env(REPO_ROOT)
    tasks = yaml.safe_load(TRAEFIK_TASKS.read_text())
    ctx = {
        "auth_provider": auth_provider,
        "auth": {"remote": {"forward_auth_url": "https://auth.node01.example.org/api/authz/forward-auth"}},
    }
    rendered = []
    for task in tasks:
        copy = task.get("ansible.builtin.copy")
        if not copy or not Path(copy.get("dest", "")).name.startswith("auth-"):
            continue
        if env.compile_expression(task["when"])(**ctx):
            rendered.append(yaml.safe_load(env.from_string(copy["content"]).render(**ctx)))
    assert len(rendered) == 1, rendered
    return rendered[0]["http"]["middlewares"]


@pytest.mark.parametrize("provider", ["authelia", "authelia_remote"])
def test_auth_chain_strips_underscore_headers_before_forward_auth(ansible_jinja_env, provider):
    mws = _auth_middlewares(ansible_jinja_env, provider)
    chain = mws["auth"]["chain"]["middlewares"]
    kinds = [next(iter(mws[name])) for name in chain]
    assert kinds == ["headers", "forwardAuth"], (chain, kinds)
    strip = mws[chain[0]]["headers"]["customRequestHeaders"]
    assert strip == {h: "" for h in UNDERSCORE_HEADERS}


@pytest.mark.parametrize("provider", ["authelia", "authelia_remote"])
def test_strip_leaves_the_hyphen_headers_alone(ansible_jinja_env, provider):
    mws = _auth_middlewares(ansible_jinja_env, provider)
    chain = mws["auth"]["chain"]["middlewares"]
    strip = mws[chain[0]]["headers"]["customRequestHeaders"]
    assert not set(strip) & set(HYPHEN_HEADERS)
    fa = mws[chain[1]]["forwardAuth"]
    assert fa["authResponseHeaders"] == ["Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name"]


@pytest.mark.parametrize("provider", ["authelia", "authelia_remote"])
def test_no_rendered_middleware_contains_insecure(ansible_jinja_env, provider):
    mws = _auth_middlewares(ansible_jinja_env, provider)
    assert "insecure" not in json.dumps(mws).lower()


# ---- 4. entries that trust (almost) everyone ------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        "::ffff:0.0.0.0/96",
        "::ffff:0:0/96",
        "::ffff:10.0.0.0/104",
        "::ffff:203.0.113.10",
        "0.0.0.0/1",
        "128.0.0.0/1",
        "10.0.0.0/7",
        "::/1",
        "2000::/3",
    ],
)
def test_too_wide_or_mapped_entries_abort_validate(entry):
    errors = _errors({"trusted_proxies": [entry]})
    assert any("security.trusted_proxies[0]" in e for e in errors), errors


def test_mapped_entry_message_says_to_write_ipv4():
    errors = _errors({"trusted_proxies": ["::ffff:10.0.0.0/104"]})
    assert any("as IPv4" in e for e in errors), errors


@pytest.mark.parametrize(
    "entry",
    ["10.0.0.0/8", "0.0.0.0/8", "203.0.113.10/32", "2001:db8::/32", "2000::/16", "2001:db8::1"],
)
def test_narrow_enough_entries_stay_valid(entry):
    assert _errors({"trusted_proxies": [entry]}) == []


@pytest.mark.parametrize("entry", ["::ffff:0.0.0.0/96", "0.0.0.0/1", "::/1"])
def test_preflight_rejects_too_wide_entries(ansible_available, tmp_path, entry):
    r = _preflight(tmp_path, {"trusted_proxies": [entry]})
    assert r.returncode != 0, r.stdout
    assert "security.trusted_proxies" in r.stdout + r.stderr
