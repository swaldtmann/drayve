"""The stored OIDC client-secret hash must follow the plain secret.

B03 (drayve-authelia RCA): the Argon2id hash was only generated when its file
did not exist, while the plain secret goes to the client on every run. After a
secret rotation both sides disagreed and the OIDC login failed.

Detection: a sha256 checksum of the plain secret is stored next to the hash
file (``.oidc_<id>_secret.sha256``, mode 0600, never the secret itself). The
hash is regenerated when the file is missing (upgrade case) or the checksum
differs. ``authelia crypto hash validate`` was rejected: it exits 0 on a
mismatch and only reports the result as text on stdout.

The ``when`` of the generation task is evaluated with Jinja2 against concrete
scenarios, so the tests check behaviour, not just wording.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import jinja2
import pytest
import yaml

TASKS_FILE = (
    Path(__file__).resolve().parents[2] / "ansible/roles/authelia/tasks/main.yml"
)
DOCS_FILE = Path(__file__).resolve().parents[2] / "docs/auth-oidc-clients.md"

GEN_NAME = "Generate Argon2id hash for OIDC client secret (per client)"
# The resolve task moved to ansible/tasks/authelia_oidc_resolve.yml (shared with
# roles/deploy); the secret/hash section of this role now starts at the assert.
START = "Fail when any OIDC client is missing a plain secret"
END = "Expose legacy authelia_oidc_grafana_secret_hash alias"


@pytest.fixture(scope="module")
def tasks() -> list[dict]:
    return yaml.safe_load(TASKS_FILE.read_text())


def _by_name(tasks: list[dict], name: str) -> dict:
    found = [t for t in tasks if t.get("name") == name]
    assert len(found) == 1, f"expected exactly one task named {name!r}"
    return found[0]


def _eval_when(task: dict, **ctx) -> bool:
    w = task["when"]
    expr = " and ".join(f"({x})" for x in (w if isinstance(w, list) else [w]))
    env = jinja2.Environment()
    env.filters["hash"] = lambda v, algo="sha1": hashlib.new(algo, str(v).encode()).hexdigest()
    return bool(env.compile_expression(expr)(**ctx))


def _ctx(secret: str, hash_exists: bool, stored_sum: str | None) -> dict:
    return {
        "item": {"item": {"id": "grafana"}, "stat": {"exists": hash_exists}},
        "authelia_oidc_client_secrets": {"grafana": secret},
        "_oidc_sum_stored": {} if stored_sum is None else {"grafana": stored_sum},
    }


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def test_generation_not_only_tied_to_missing_file(tasks):
    when = str(_by_name(tasks, GEN_NAME)["when"])
    assert "sha256" in when, (
        "hash generation depends only on the hash file existing; "
        f"a rotated secret never gets a new hash: {when!r}"
    )


def test_unchanged_secret_keeps_hash(tasks):
    t = _by_name(tasks, GEN_NAME)
    assert _eval_when(t, **_ctx("s3cret-A", True, _sha("s3cret-A"))) is False


def test_changed_secret_regenerates(tasks):
    t = _by_name(tasks, GEN_NAME)
    assert _eval_when(t, **_ctx("s3cret-B", True, _sha("s3cret-A"))) is True


def test_upgrade_hash_without_checksum_regenerates_once(tasks):
    t = _by_name(tasks, GEN_NAME)
    assert _eval_when(t, **_ctx("s3cret-A", True, None)) is True


def test_first_run_generates(tasks):
    t = _by_name(tasks, GEN_NAME)
    assert _eval_when(t, **_ctx("s3cret-A", False, None)) is True


@pytest.mark.parametrize("hash_exists", [True, False])
@pytest.mark.parametrize("stored", [None, "", _sha("old")])
def test_empty_secret_never_generates(tasks, hash_exists, stored):
    t = _by_name(tasks, GEN_NAME)
    assert _eval_when(t, **_ctx("", hash_exists, stored)) is False


def test_checksum_written_with_hash_mode_0600(tasks):
    writers = [
        t
        for t in tasks
        if ".oidc_" in str(t.get("ansible.builtin.copy", ""))
        and "sha256" in str(t.get("ansible.builtin.copy", ""))
    ]
    assert len(writers) == 1, "expected one task writing the secret checksum file"
    t = writers[0]
    assert t["ansible.builtin.copy"]["mode"] == "0600"
    # only written for clients whose hash was (re)generated in this run
    assert "skipped" in str(t["when"])
    # the checksum, never the secret itself
    assert "hash('sha256')" in t["ansible.builtin.copy"]["content"]


def test_all_secret_and_hash_tasks_use_no_log(tasks):
    names = [t.get("name") for t in tasks]
    section = tasks[names.index(START) : names.index(END) + 1]
    assert len(section) >= 8
    for t in section:
        if "ansible.builtin.stat" in t:  # path only, registers no content
            continue
        assert t.get("no_log") is True, f"task {t['name']!r} lacks no_log: true"


def test_docs_describe_rotation_and_argon2id():
    text = DOCS_FILE.read_text()
    assert "PBKDF2" not in text, "docs still claim PBKDF2 while argon2id is generated"
    assert "rotat" in text.lower() and "deploy" in text.lower()
