"""What a redeploy does to a running Authelia (file backend).

The tests run in file order and build on each other: each one deploys again with
the scenario's converge.yml (``deploy`` fixture; extra variables accumulate) and
checks the result. Run the module as a whole, on a VM that only the first
converge and ``test_10_*`` have touched.
"""

import hashlib

import authelia_probe as ap
import pytest

pytestmark = pytest.mark.usefixtures("stack_ready")

STACK_DIR = "/opt/drayve/deploy/stack"
AUTHELIA_DIR = f"{STACK_DIR}/authelia"
EXTRA_RULE = {"domain": "extra.test.drayve.local", "policy": "one_factor", "groups": ["viewers"]}
CAROL = {"name": "carol", "displayname": "Carol Newcomer", "email": "carol@test.drayve.local", "groups": ["viewers"]}
ROTATED_GRAFANA_SECRET = "rotated-grafana-oidc-secret-32chars"
GRAFANA_REDIRECT = "https://grafana.test.drayve.local/login/generic_oauth"
HASH_FILE = f"{AUTHELIA_DIR}/data/.oidc_grafana_hash"
SUM_FILE = f"{AUTHELIA_DIR}/data/.oidc_grafana_secret.sha256"


# --- 5: second deploy without a change ----------------------------------------------------

def test_second_deploy_without_changes_leaves_authelia_alone(vm, deploy):
    before = vm.container("authelia")
    result = deploy()
    in_authelia = [t for t in result.changed if t.startswith("authelia : ")]
    assert in_authelia == [], "the authelia role reported changes on an unchanged redeploy: " + "; ".join(in_authelia)
    after = vm.container("authelia")
    assert after.id == before.id, "the authelia container was recreated by a redeploy without changes"
    assert after.started_at == before.started_at


# --- 6: harmless config change ---------------------------------------------------------------

def test_config_change_keeps_the_totp_registration_and_second_factor_sign_in(portal, vm, deploy, sign_in):
    vm.ensure_totp("alice")  # registered before the deploy: this registration must survive

    deploy({"af_extra_rules": [EXTRA_RULE]})

    in_container = vm.run("docker exec authelia cat /config/configuration.yml").stdout
    assert "extra.test.drayve.local" in in_container, (
        "the running authelia container does not see the new rule (config not applied)")

    portal.wait_until(lambda: portal.outcome(portal.new_session(), portal.url()).kind == "portal_redirect",
                      "Authelia after the config change", timeout=300)
    # The code is computed from the secret registered BEFORE the deploy: it only works if
    # the database (and with it the registration) survived the config change.
    session = sign_in("alice", second_factor=True)
    out = portal.outcome(session, portal.url())
    assert (out.kind, out.status) == ("ok", 200), f"second factor sign-in after the config change: {out}"


# --- 7: new user ---------------------------------------------------------------------------------

def test_a_user_added_in_the_stack_can_sign_in_after_the_deploy(portal, vm, deploy):
    """Open question: the users file is a single-file bind mount that the deploy replaces.
    Does the running container see the new version without a restart?"""
    before = vm.container("authelia")

    deploy({"af_extra_users": [CAROL]})

    after = vm.container("authelia")
    on_host = "carol" in vm.file_text(f"{AUTHELIA_DIR}/users_database.yml")
    in_container = "carol" in vm.run("docker exec authelia cat /config/users_database.yml").stdout
    diagnosis = (f"users file on the host has carol: {on_host}; inside the container: {in_container}; "
                 f"container recreated by the deploy: {after.id != before.id}")
    assert on_host, "the deploy did not write carol into the users file. " + diagnosis

    portal.wait_until(lambda: portal.outcome(portal.new_session(), portal.url()).kind == "portal_redirect",
                      "Authelia after adding a user", timeout=300)
    r = portal.try_first_factor(portal.new_session(), "carol", ap.FIXTURE_PASSWORDS["carol"])
    assert r.status_code == 200, f"carol cannot sign in (HTTP {r.status_code}). {diagnosis}"


# --- 8: rotated OIDC client secret --------------------------------------------------------------

def test_rotated_oidc_secret_changes_the_stored_hash(vm, deploy):
    hash_before = vm.file_text(HASH_FILE)
    deploy({"authelia_oidc_grafana_secret": ROTATED_GRAFANA_SECRET})
    changed = vm.file_text(HASH_FILE) != hash_before
    assert changed, "the stored OIDC hash for grafana did not change after the secret changed"
    expected_sum = hashlib.sha256(ROTATED_GRAFANA_SECRET.encode()).hexdigest()
    assert vm.file_text(SUM_FILE).strip() == expected_sum, "checksum file does not follow the new secret"


def test_authelia_accepts_the_rotated_secret_at_the_token_endpoint(portal, sign_in):
    portal.wait_until(lambda: portal.outcome(portal.new_session(), portal.url()).kind == "portal_redirect",
                      "Authelia after the secret rotation", timeout=300)
    session = sign_in("bob")
    r = portal.oidc_token(session, "grafana", ROTATED_GRAFANA_SECRET, GRAFANA_REDIRECT)
    assert not ap.oidc_secret_mismatch(r.status_code, r.text), (
        f"'client secret did not match' with the NEW secret: HTTP {r.status_code} {r.text[:300]}")
    assert r.status_code == 200 and "id_token" in r.json(), f"token exchange: HTTP {r.status_code} {r.text[:300]}"


def test_authelia_rejects_the_previous_secret_after_rotation(portal, sign_in, vm):
    session = sign_in("bob")
    r = portal.oidc_token(session, "grafana", vm.grafana_secret_initial, GRAFANA_REDIRECT)
    assert ap.oidc_secret_mismatch(r.status_code, r.text), (
        f"the OLD secret still works or fails differently: HTTP {r.status_code} {r.text[:300]}")


# --- 9: partial deploy ----------------------------------------------------------------------------

def test_partial_deploy_keeps_the_grafana_oidc_secret_in_env(vm, deploy):
    deploy(tags="secrets,deploy")
    env_text = vm.file_text(f"{STACK_DIR}/.env")
    length = ap.env_var_length(env_text, "AUTHELIA_OIDC_GRAFANA_SECRET")
    assert length > 0, "AUTHELIA_OIDC_GRAFANA_SECRET is empty or missing in .env after --tags secrets,deploy"
    current = ap.env_var_sha256(env_text, "AUTHELIA_OIDC_GRAFANA_SECRET")
    assert current == hashlib.sha256(ROTATED_GRAFANA_SECRET.encode()).hexdigest(), (
        f".env carries a secret of length {length} that is not the current one")
