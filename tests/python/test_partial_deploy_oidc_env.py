"""Tests for the partial-deploy OIDC `.env` gap (tag-scoped runs).

Bug: ``roles/deploy`` renders ``templates/env.j2`` ("Template .env", tag
``[deploy]``). The ``AUTHELIA_OIDC_<ID>_SECRET`` lines are driven by
``authelia_oidc_clients`` (set by ``roles/auth``, tag ``[auth]``) and
``authelia_oidc_client_secrets`` (set by ``roles/authelia``, reached only through
``include_role`` under tag ``[auth]``). The run drayve itself recommends,
``--tags secrets,deploy``, therefore rendered a ``.env`` without those lines:
Grafana got ``GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET`` empty and OIDC login broke
until the next full deploy.

Second finding: ``--tags authelia`` alone executes no task of the authelia role
(the role hangs under tag ``auth``), yet the AFKI-W-240 guard recommended
``--tags secrets,authelia``.

These tests build a mini playbook from the *real* tasks of ``roles/deploy``
(selected by name / by reference to ``authelia_oidc``) plus the real ``env.j2``,
so they exercise the actual wiring instead of an inline copy. Only the
``roles/secrets`` subset is inlined (same approach as
``test_deploy_secrets_tag_guard.py``).

Run: ``pytest tests/python/test_partial_deploy_oidc_env.py``
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_J2 = REPO_ROOT / "ansible" / "templates" / "env.j2"
DEPLOY_TASKS = REPO_ROOT / "ansible" / "roles" / "deploy" / "tasks" / "main.yml"
AUTH_TASKS = REPO_ROOT / "ansible" / "roles" / "auth" / "tasks" / "main.yml"
AUTHELIA_TASKS = REPO_ROOT / "ansible" / "roles" / "authelia" / "tasks" / "main.yml"

GRAFANA_CLIENT_SECRET = "grafana-client-secret-0123456789"
HMAC_SECRET = "hmac-secret-0123456789abcdef"

SECRETS_WITH_GRAFANA = textwrap.dedent(
    f"""\
    authelia_oidc_hmac_secret: "{HMAC_SECRET}"
    authelia_oidc_grafana_secret: "{GRAFANA_CLIENT_SECRET}"
    """
)
SECRETS_WITHOUT_GRAFANA = textwrap.dedent(
    f"""\
    authelia_oidc_hmac_secret: "{HMAC_SECRET}"
    """
)

# Subset of roles/secrets/tasks/main.yml (tag [secrets]).
SECRETS_STUB = """\
- name: Check for host secrets file
  ansible.builtin.stat:
    path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
  register: secrets_file
  tags: [secrets]

- name: Load host secrets (plaintext/quickstart)
  ansible.builtin.include_vars:
    file: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
    name: host_secrets
  when: secrets_file.stat.exists
  tags: [secrets]

- name: Set secret variables
  ansible.builtin.set_fact:
    authelia_oidc_hmac_secret: "{{ host_secrets.authelia_oidc_hmac_secret | default('') }}"
    authelia_oidc_grafana_secret: "{{ host_secrets.authelia_oidc_grafana_secret | default('') }}"
  when: secrets_file.stat.exists
  no_log: true
  tags: [secrets]
"""

CLIENTS_DEFAULT = [{"id": "grafana", "name": "Grafana"}]


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def _task_value(task: dict, module: str):
    return task.get(f"ansible.builtin.{module}", task.get(module))


def _real_deploy_tasks() -> list[dict]:
    """Real tasks of roles/deploy that matter for the OIDC `.env` lines.

    Selected: the auth_provider resolution, every task that references
    ``authelia_oidc`` (resolution/guard wiring), and "Template .env" (src/dest
    rewired to the test sandbox). Order of the real role is preserved.
    """
    tasks = yaml.safe_load(DEPLOY_TASKS.read_text())
    selected: list[dict] = []
    for task in tasks:
        name = task.get("name", "")
        if name == "Template .env":
            task = json.loads(json.dumps(task))
            tmpl = task["ansible.builtin.template"]
            tmpl["src"] = str(ENV_J2)
            tmpl["dest"] = "{{ env_dest }}"
            for key in ("owner", "group", "mode"):
                tmpl.pop(key, None)
            selected.append(task)
        elif name == "Resolve auth_provider from stack.yaml" or "authelia_oidc" in json.dumps(task):
            task = json.loads(json.dumps(task))
            for module in ("import_tasks", "include_tasks"):
                for prefix in ("ansible.builtin.", ""):
                    key = prefix + module
                    if key in task:
                        task[key] = str((DEPLOY_TASKS.parent / task[key]).resolve())
            selected.append(task)
    return selected


def _make_wrapper(tmp_path: Path) -> Path:
    play = [
        {
            "name": "Test partial-deploy OIDC .env",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "tasks": yaml.safe_load(SECRETS_STUB) + _real_deploy_tasks(),
        }
    ]
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(yaml.safe_dump(play, sort_keys=False))
    return wrapper


def _layout(tmp_path: Path, host: str, secrets_body: str) -> Path:
    root = tmp_path / "site"
    deploy = root / "deploy" / host
    deploy.mkdir(parents=True)
    (deploy / "secrets.yml").write_text(secrets_body)
    return root


def _run(
    tmp_path: Path,
    secrets_body: str,
    tags: str | None,
    provider: str = "authelia",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    host = "myhost"
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, host, secrets_body)
    env_dest = tmp_path / ".env"
    extra = {
        "inventory_hostname": host,
        "ops_secrets_root": str(root),
        "env_dest": str(env_dest),
        "drayve_domain": "example.test",
        "auth": {"provider": provider},
        "authelia_oidc_clients_default": CLIENTS_DEFAULT,
    }
    cmd = [
        "ansible-playbook",
        "-i",
        f"{host},",
        str(wrapper),
        "-e",
        json.dumps(extra),
    ]
    if tags:
        cmd.extend(["--tags", tags])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result, env_dest


def _env_lines(env_dest: Path) -> dict[str, str]:
    out = {}
    for line in env_dest.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


# --------------------------------------------------------------------------
# (a) restricted tags must still render the OIDC client secret lines
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tags", ["secrets,deploy", None])
def test_env_has_oidc_grafana_secret_with_value(ansible_available, tmp_path, tags):
    result, env_dest = _run(tmp_path, SECRETS_WITH_GRAFANA, tags)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    env = _env_lines(env_dest)
    assert "AUTHELIA_OIDC_GRAFANA_SECRET" in env, (
        "AUTHELIA_OIDC_GRAFANA_SECRET line missing from rendered .env"
    )
    assert env["AUTHELIA_OIDC_GRAFANA_SECRET"] == GRAFANA_CLIENT_SECRET, (
        "AUTHELIA_OIDC_GRAFANA_SECRET empty or wrong in rendered .env"
    )


def test_non_authelia_provider_renders_no_oidc_client_lines(ansible_available, tmp_path):
    result, env_dest = _run(tmp_path, SECRETS_WITH_GRAFANA, "secrets,deploy", provider="basic")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AUTHELIA_OIDC_GRAFANA_SECRET" not in _env_lines(env_dest)


# --------------------------------------------------------------------------
# (b) safety net: configured client without secret aborts before rendering
# --------------------------------------------------------------------------


def test_client_without_secret_aborts_with_client_id(ansible_available, tmp_path):
    result, env_dest = _run(tmp_path, SECRETS_WITHOUT_GRAFANA, "secrets,deploy")
    combined = result.stdout + result.stderr
    assert result.returncode != 0, "run must abort when a client has no secret"
    assert "No plain OIDC secret found for client 'grafana'" in combined, combined
    assert HMAC_SECRET not in combined, "secret value leaked into output"
    assert not env_dest.exists(), ".env must not be written when the safety net fires"


# --------------------------------------------------------------------------
# structural wiring: both entry points use the same shared resolution
# --------------------------------------------------------------------------


def _imports_oidc_resolve(tasks: list[dict]) -> list[dict]:
    return [
        t
        for t in tasks
        if "authelia_oidc" in str(_task_value(t, "import_tasks") or "")
    ]


def test_auth_role_uses_shared_resolution_under_auth_tag():
    imports = _imports_oidc_resolve(yaml.safe_load(AUTH_TASKS.read_text()))
    assert imports, "roles/auth must import the shared OIDC resolution tasks"
    assert "auth" in imports[0].get("tags", [])


def test_deploy_role_uses_shared_resolution_before_env_template():
    tasks = yaml.safe_load(DEPLOY_TASKS.read_text())
    names = [t.get("name") for t in tasks]
    idx = [i for i, t in enumerate(tasks) if t in _imports_oidc_resolve(tasks)]
    assert idx, "roles/deploy must import the shared OIDC resolution tasks"
    assert idx[0] < names.index("Template .env")
    assert "deploy" in tasks[idx[0]].get("tags", [])


def test_resolution_is_not_duplicated_in_authelia_role():
    names = [t.get("name") for t in yaml.safe_load(AUTHELIA_TASKS.read_text())]
    assert "Resolve per-client OIDC plain secrets" not in names


# --------------------------------------------------------------------------
# (c) AFKI-W-240 guard hint names a combination that runs the authelia role
# --------------------------------------------------------------------------


def _guard_fail_msg() -> str:
    for task in yaml.safe_load(AUTHELIA_TASKS.read_text()):
        if "AFKI-W-240" in task.get("name", "") and task["name"].startswith("Guard"):
            return task["ansible.builtin.assert"]["fail_msg"]
    raise AssertionError("AFKI-W-240 guard task not found")


def test_guard_fail_msg_does_not_recommend_ineffective_tags():
    msg = _guard_fail_msg()
    assert "--tags secrets,authelia" not in msg
    combos = [set(m.split(",")) for m in re.findall(r"--tags ([\w,]+)", msg)]
    assert combos, "fail_msg must name a --tags combination"
    for combo in combos:
        # `auth` is the tag of the block holding the include_role (its tasks
        # inherit it), `secrets` loads the facts the guard checks.
        assert {"secrets", "auth"} <= combo, combo


def test_recommended_combination_reaches_authelia_role_tasks(ansible_available, tmp_path):
    """Mirror of the real wiring (include_role in a block tagged auth, role tasks
    tagged authelia): the role tasks inherit the block tag, so `auth` reaches
    them while `authelia` alone never enters the block."""
    real_auth = yaml.safe_load(AUTH_TASKS.read_text())
    block = next(t for t in real_auth if t.get("name") == "Auth provider authelia — full SSO setup")
    assert block["tags"] == ["auth"]
    assert any(
        _task_value(t, "include_role") == {"name": "authelia"} for t in block["block"]
    )
    real_authelia = yaml.safe_load(AUTHELIA_TASKS.read_text())
    first = real_authelia[0]
    assert "authelia" in first["tags"] and "auth" not in first["tags"]

    role_dir = tmp_path / "roles" / "authelia" / "tasks"
    role_dir.mkdir(parents=True)
    (role_dir / "main.yml").write_text(
        yaml.safe_dump(
            [
                {
                    "name": "ROLE-TASK-RAN",
                    "ansible.builtin.debug": {"msg": "role-task-ran"},
                    "tags": ["authelia"],
                }
            ]
        )
    )
    play = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "tasks": [
                {
                    "name": "Auth provider authelia — full SSO setup",
                    "tags": ["auth"],
                    "block": [
                        {"name": "Include authelia role", "ansible.builtin.include_role": {"name": "authelia"}}
                    ],
                }
            ],
        }
    ]
    playbook = tmp_path / "pb.yml"
    playbook.write_text(yaml.safe_dump(play))

    def run(tags: str) -> str:
        r = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", str(playbook), "--tags", tags],
            capture_output=True,
            text=True,
            check=False,
            env={"ANSIBLE_ROLES_PATH": str(tmp_path / "roles"), "PATH": shutil.which("ansible-playbook").rsplit("/", 1)[0] + ":/usr/bin:/bin"},
        )
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    assert "role-task-ran" not in run("authelia")
    assert "role-task-ran" not in run("secrets,authelia")
    assert "role-task-ran" in run("secrets,auth")
