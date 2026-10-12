"""Unit tests for the pure logic of the ``authelia-file`` Molecule scenario.

``molecule/authelia-file/tests/authelia_probe.py`` holds the login probes the
scenario runs against a real VM (first/second factor, redirect evaluation,
OIDC token exchange, Ansible output parsing). The VM part cannot run in CI of
this repo, so everything that is logic and not I/O is tested here:

  - TOTP against the RFC 6238 test vectors, and the "never send the same code
    twice" rule (Authelia rejects a re-used one-time code)
  - "the Authelia portal page answers 200 too" must not count as a successful
    access to a protected page
  - parsing of Ansible output (changed tasks, recap) and of ``otpauth://`` URIs
  - the HTTP flows with a fake session (no network)

Run: ``pytest tests/python/test_authelia_file_probe.py``
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "molecule" / "authelia-file" / "tests" / "authelia_probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("authelia_probe", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load()

PORTAL = "auth.test.drayve.local"


# --- TOTP -------------------------------------------------------------------

# RFC 6238 appendix B, SHA-1, 8 digits, secret "12345678901234567890".
RFC_SECRET_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize("at,expected", RFC_VECTORS)
def test_totp_matches_rfc6238_vectors_with_eight_digits(at, expected):
    assert probe.totp_code(RFC_SECRET_B32, at, digits=8) == expected


@pytest.mark.parametrize("at,expected", RFC_VECTORS)
def test_totp_six_digits_are_the_tail_of_the_eight_digit_value(at, expected):
    assert probe.totp_code(RFC_SECRET_B32, at) == expected[-6:]


def test_totp_accepts_unpadded_lowercase_base32():
    # Authelia prints the secret without '=' padding.
    unpadded = RFC_SECRET_B32.rstrip("=").lower()
    assert probe.totp_code(unpadded, 59, digits=8) == "94287082"


def test_totp_rfc_sha256_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZA===="  # 32 bytes
    assert probe.totp_code(secret, 59, digits=8, algorithm="SHA256") == "46119246"


class FakeClock:
    def __init__(self, now: float):
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_generator_never_returns_the_same_code_twice_in_one_period():
    clock = FakeClock(1111111110.0)  # inside a period, 20 s before the next one
    gen = probe.TotpGenerator(RFC_SECRET_B32, clock=clock, sleep=clock.sleep)
    first = gen.next_code()
    second = gen.next_code()
    assert first != second
    assert clock.slept, "second call has to wait for the next period"
    assert clock.now >= 1111111110.0 + 20


def test_generator_does_not_wait_when_the_period_changed():
    clock = FakeClock(1111111110.0)
    gen = probe.TotpGenerator(RFC_SECRET_B32, clock=clock, sleep=clock.sleep)
    gen.next_code()
    clock.now += 31
    gen.next_code()
    assert clock.slept == []


def test_generator_code_is_the_code_of_the_current_period():
    clock = FakeClock(59.0)
    gen = probe.TotpGenerator(RFC_SECRET_B32, digits=8, clock=clock, sleep=clock.sleep)
    assert gen.next_code() == "94287082"


# --- otpauth:// URIs --------------------------------------------------------

URI_ALICE = (
    "otpauth://totp/test.drayve.local:alice?algorithm=SHA1&digits=6"
    "&issuer=test.drayve.local&period=30&secret=GEZDGNBVGY3TQOJQ"
)
URI_BOB = URI_ALICE.replace(":alice", ":bob").replace("GEZDGNBVGY3TQOJQ", "MFRGGZDFMZTWQ2LK")


def test_parse_uri_from_generate_output_line():
    out = f"Successfully generated TOTP configuration for user 'alice' with URI '{URI_ALICE}'\n"
    cfg = probe.parse_otpauth_uri(out)
    assert cfg["secret"] == "GEZDGNBVGY3TQOJQ"
    assert (cfg["digits"], cfg["period"], cfg["algorithm"]) == (6, 30, "SHA1")


def test_parse_uri_selects_the_requested_user_from_an_export():
    out = f"{URI_BOB}\n{URI_ALICE}\n"
    assert probe.parse_otpauth_uri(out, user="alice")["secret"] == "GEZDGNBVGY3TQOJQ"
    assert probe.parse_otpauth_uri(out, user="bob")["secret"] == "MFRGGZDFMZTWQ2LK"


def test_parse_uri_returns_none_without_a_uri():
    assert probe.parse_otpauth_uri("no totp configurations\n") is None
    assert probe.parse_otpauth_uri(URI_BOB, user="alice") is None


def test_parse_uri_defaults_when_parameters_are_missing():
    cfg = probe.parse_otpauth_uri("otpauth://totp/x:alice?secret=GEZDGNBVGY3TQOJQ")
    assert (cfg["digits"], cfg["period"], cfg["algorithm"]) == (6, 30, "SHA1")


# --- response classification ------------------------------------------------

def test_redirect_to_portal_is_a_portal_redirect():
    out = probe.classify_response(
        "https://test.drayve.local/", 302,
        "https://auth.test.drayve.local/?rd=https%3A%2F%2Ftest.drayve.local%2F&rm=GET", PORTAL)
    assert out.kind == "portal_redirect"
    assert out.status == 302


def test_portal_page_answering_200_is_not_a_success():
    # The portal HTML is a 200 as well: asking it directly must never read as "access granted".
    out = probe.classify_response("https://auth.test.drayve.local/", 200, "", PORTAL)
    assert out.kind == "portal_page"
    assert out.kind != "ok"


def test_200_on_another_host_is_ok():
    assert probe.classify_response("https://test.drayve.local/", 200, "", PORTAL).kind == "ok"


@pytest.mark.parametrize("status", [401, 403])
def test_401_and_403_are_denied(status):
    assert probe.classify_response("https://test.drayve.local/", status, "", PORTAL).kind == "denied"


def test_redirect_to_somewhere_else_is_not_a_portal_redirect():
    out = probe.classify_response("https://grafana.test.drayve.local/", 307,
                                  "/login/generic_oauth?redirectTo=%2F", PORTAL)
    assert out.kind == "redirect_other"


def test_relative_redirect_on_the_portal_host_counts_as_portal_redirect():
    # A relative Location keeps the host of the request that returned it.
    out = probe.classify_response("https://auth.test.drayve.local/x", 302, "/y", PORTAL)
    assert out.kind == "portal_page"  # asking the portal itself is never a protected-page result
    out = probe.classify_response("https://test.drayve.local/x", 302, "/y", PORTAL)
    assert out.kind == "redirect_other"


def test_portal_redirect_target_returns_the_original_url():
    loc = "https://auth.test.drayve.local/?rd=https%3A%2F%2Ftest.drayve.local%2Fpath%3Fa%3D1&rm=GET"
    assert probe.portal_redirect_target(loc, PORTAL) == "https://test.drayve.local/path?a=1"


def test_portal_redirect_target_is_none_for_other_hosts_or_without_rd():
    assert probe.portal_redirect_target("https://evil.example/?rd=https%3A%2F%2Fx", PORTAL) is None
    assert probe.portal_redirect_target("https://auth.test.drayve.local/", PORTAL) is None


# --- OIDC -------------------------------------------------------------------

def test_extract_oidc_code_from_callback_location():
    loc = "https://grafana.test.drayve.local/login/generic_oauth?code=authelia_ac_x1&iss=https%3A%2F%2Fauth&state=s"
    assert probe.extract_oidc_code(loc) == "authelia_ac_x1"


def test_extract_oidc_code_none_without_code():
    assert probe.extract_oidc_code("https://auth.test.drayve.local/?rd=x") is None
    assert probe.extract_oidc_code("") is None


def test_secret_mismatch_detected_from_authelia_error_body():
    body = json.dumps({
        "error": "invalid_client",
        "error_description": "Client authentication failed (e.g., unknown client, no client authentication "
                             "included, or unsupported authentication method). The provided client secret "
                             "did not match the registered client secret.",
    })
    assert probe.oidc_secret_mismatch(401, body) is True


def test_other_token_errors_are_not_a_secret_mismatch():
    body = json.dumps({"error": "invalid_request",
                       "error_description": "Error occurred determining the effective issuer"})
    assert probe.oidc_secret_mismatch(400, body) is False
    assert probe.oidc_secret_mismatch(200, "{}") is False


# --- Ansible output ---------------------------------------------------------

ANSIBLE_OUT = """\
PLAY [Authelia file backend] ***************************************************

TASK [Gathering Facts] *********************************************************
ok: [test-authelia-file]

TASK [authelia : Deploy Authelia users database (file backend)] ****************
changed: [test-authelia-file]

TASK [authelia : Store OIDC client secret hash (per client)] *******************
changed: [test-authelia-file] => (item=grafana)
skipping: [test-authelia-file] => (item=forgejo)

TASK [authelia : Deploy Authelia configuration] ********************************
ok: [test-authelia-file]

TASK [authelia : Deploy Authelia configuration] ********************************
changed: [test-authelia-file]

RUNNING HANDLER [traefik : Restart traefik] ************************************
changed: [test-authelia-file]

PLAY RECAP *********************************************************************
test-authelia-file         : ok=137  changed=4    unreachable=0    failed=0    skipped=60   rescued=0    ignored=0
"""


def test_parse_changed_tasks_lists_each_changed_task_once_in_order():
    assert probe.parse_changed_tasks(ANSIBLE_OUT) == [
        "authelia : Deploy Authelia users database (file backend)",
        "authelia : Store OIDC client secret hash (per client)",
        "authelia : Deploy Authelia configuration",
        "traefik : Restart traefik",
    ]


def test_parse_changed_tasks_empty_for_an_unchanged_run():
    out = ANSIBLE_OUT.replace("changed: [", "ok: [")
    assert probe.parse_changed_tasks(out) == []


def test_parsers_ignore_ansi_colour_codes():
    # Molecule may export ANSIBLE_FORCE_COLOR; the parsers must not depend on it.
    coloured = (
        "\x1b[0;33mchanged: [test-authelia-file]\x1b[0m\n"
        "TASK [authelia : Deploy Authelia configuration] ****\n"
        "\x1b[0;33mchanged: [test-authelia-file]\x1b[0m\n"
        "\x1b[0;33mtest-authelia-file\x1b[0m         : \x1b[0;32mok=3\x1b[0m    \x1b[0;33mchanged=1\x1b[0m    "
        "unreachable=0    failed=0    skipped=0    rescued=0    ignored=0\n"
    )
    assert probe.parse_changed_tasks(coloured) == ["authelia : Deploy Authelia configuration"]
    assert probe.parse_recap(coloured)["test-authelia-file"]["changed"] == 1


def test_fixture_password_hashes_in_the_scenario_match_the_fixture_passwords():
    import base64
    import yaml
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    # Molecule turns `$$` into `$` when it reads molecule.yml.
    text = (REPO_ROOT / "molecule" / "authelia-file" / "molecule.yml").read_text().replace("$$", "$")
    cfg = yaml.safe_load(text)
    hashes = cfg["provisioner"]["inventory"]["group_vars"]["all"]["host_secrets"]["authelia_user_password_hashes"]
    assert set(hashes) == set(probe.FIXTURE_PASSWORDS)

    def unb64(s):
        return base64.b64decode(s + "=" * (-len(s) % 4))

    for user, password in probe.FIXTURE_PASSWORDS.items():
        _, algo, version, params, salt, digest = hashes[user].split("$")
        assert (algo, version) == ("argon2id", "v=19")
        p = dict(kv.split("=") for kv in params.split(","))
        kdf = Argon2id(salt=unb64(salt), length=len(unb64(digest)), iterations=int(p["t"]),
                       lanes=int(p["p"]), memory_cost=int(p["m"]))
        kdf.verify(password.encode(), unb64(digest))  # raises InvalidKey on mismatch


def test_parse_recap():
    recap = probe.parse_recap(ANSIBLE_OUT)
    assert recap == {"test-authelia-file": {"ok": 137, "changed": 4, "unreachable": 0, "failed": 0}}


def test_ansible_command_passes_extra_vars_as_file_and_tags():
    cmd = probe.ansible_command("/inv/inventory", "/sc/converge.yml",
                                extra_vars_file="/tmp/vars.json", tags="secrets,deploy")
    assert cmd[0] == "ansible-playbook"
    assert cmd[cmd.index("-i") + 1] == "/inv/inventory"
    assert cmd[cmd.index("-e") + 1] == "@/tmp/vars.json"
    assert cmd[cmd.index("--tags") + 1] == "secrets,deploy"
    assert cmd[-1] == "/sc/converge.yml"


def test_ansible_command_without_options_has_neither_tags_nor_extra_vars():
    cmd = probe.ansible_command("/inv", "/sc/converge.yml")
    assert "--tags" not in cmd and "-e" not in cmd


# --- .env helpers (never expose the value) ----------------------------------

ENV_TEXT = "A=1\nAUTHELIA_OIDC_GRAFANA_SECRET=abcdef0123\nAUTHELIA_OIDC_FORGEJO_SECRET=\n"


def test_env_var_length_counts_only():
    assert probe.env_var_length(ENV_TEXT, "AUTHELIA_OIDC_GRAFANA_SECRET") == 10
    assert probe.env_var_length(ENV_TEXT, "AUTHELIA_OIDC_FORGEJO_SECRET") == 0
    assert probe.env_var_length(ENV_TEXT, "MISSING") == 0


def test_env_var_sha256_lets_the_caller_compare_without_the_value():
    import hashlib
    assert probe.env_var_sha256(ENV_TEXT, "AUTHELIA_OIDC_GRAFANA_SECRET") == hashlib.sha256(b"abcdef0123").hexdigest()
    assert probe.env_var_sha256(ENV_TEXT, "MISSING") is None


# --- HTTP flows with a fake session -----------------------------------------

def resp(status=200, headers=None, text="", payload=None):
    return SimpleNamespace(
        status_code=status, headers=headers or {}, text=text,
        json=lambda: payload if payload is not None else {},
    )


class FakeSession:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def _next(self, method, url, kw):
        self.calls.append((method, url, kw))
        return self.queue.pop(0)

    def get(self, url, **kw):
        return self._next("GET", url, kw)

    def post(self, url, **kw):
        return self._next("POST", url, kw)


@pytest.fixture
def portal():
    return probe.Portal("203.0.113.10", "test.drayve.local", pin_dns=False)


def test_outcome_never_follows_redirects(portal):
    s = FakeSession(resp(302, {"Location": "https://auth.test.drayve.local/?rd=x"}))
    out = portal.outcome(s, "https://test.drayve.local/")
    assert out.kind == "portal_redirect"
    assert s.calls[0][2]["allow_redirects"] is False


def test_outcome_for_the_portal_url_itself_is_not_ok(portal):
    s = FakeSession(resp(200))
    assert portal.outcome(s, "https://auth.test.drayve.local/").kind == "portal_page"


def test_first_factor_posts_json_to_the_api(portal):
    s = FakeSession(resp(200, payload={"status": "OK"}))
    portal.login(s, "alice", "pw-x")
    method, url, kw = s.calls[0]
    assert (method, url) == ("POST", "https://auth.test.drayve.local/api/firstfactor")
    assert kw["json"]["username"] == "alice" and kw["json"]["password"] == "pw-x"


def test_wrong_password_is_reported_as_probe_failure(portal):
    s = FakeSession(resp(401, payload={"status": "KO"}, text='{"status":"KO"}'))
    with pytest.raises(probe.ProbeFailure):
        portal.login(s, "alice", "wrong")


def test_login_with_second_factor_sends_the_code(portal):
    s = FakeSession(resp(200, payload={"status": "OK"}), resp(200, payload={"status": "OK"}))
    portal.login(s, "alice", "pw-x", totp="123456")
    method, url, kw = s.calls[1]
    assert (method, url) == ("POST", "https://auth.test.drayve.local/api/secondfactor/totp")
    assert kw["json"]["token"] == "123456"


def test_rejected_second_factor_is_reported(portal):
    s = FakeSession(resp(200, payload={"status": "OK"}), resp(401, text="no"))
    with pytest.raises(probe.ProbeFailure):
        portal.login(s, "alice", "pw-x", totp="000000")


def test_try_login_returns_the_status_instead_of_raising(portal):
    s = FakeSession(resp(401, payload={"status": "KO"}))
    assert portal.try_first_factor(s, "alice", "wrong").status_code == 401


def test_oidc_token_exchange_authorizes_then_posts_the_code(portal):
    cb = "https://grafana.test.drayve.local/login/generic_oauth?code=authelia_ac_9&state=probe"
    s = FakeSession(resp(302, {"Location": cb}), resp(200, payload={"id_token": "x"}))
    r = portal.oidc_token(s, "grafana", "secret-x", "https://grafana.test.drayve.local/login/generic_oauth")
    assert r.status_code == 200
    (m1, u1, k1), (m2, u2, k2) = s.calls
    assert m1 == "GET" and u1.startswith("https://auth.test.drayve.local/api/oidc/authorization?")
    assert "client_id=grafana" in u1 and k1["allow_redirects"] is False
    assert (m2, u2) == ("POST", "https://auth.test.drayve.local/api/oidc/token")
    assert k2["auth"] == ("grafana", "secret-x")
    assert k2["data"]["code"] == "authelia_ac_9" and k2["data"]["grant_type"] == "authorization_code"


def test_oidc_token_exchange_fails_loudly_without_a_code(portal):
    s = FakeSession(resp(302, {"Location": "https://auth.test.drayve.local/?rd=x"}))
    with pytest.raises(probe.ProbeFailure):
        portal.oidc_token(s, "grafana", "secret-x", "https://grafana.test.drayve.local/login/generic_oauth")
