"""State after the first deploy: redirect, first and second factor, group access rules.

Everything signs in through Authelia's API from the controller (no browser).
Test users come from the file backend (molecule.yml): alice (admins, two factors on
every domain), bob (viewers, one factor, only grafana.<domain>).
"""

import authelia_probe as ap
import pytest

pytestmark = pytest.mark.usefixtures("stack_ready")


# --- 11: Authelia starts cleanly ------------------------------------------------

def test_authelia_container_has_not_restarted_since_the_first_deploy(vm):
    """With the file backend nothing has to be created after `compose up` (the LLDAP bind user
    made Authelia crash-loop four times on a first deploy)."""
    c = vm.container("authelia")
    assert c.running, "authelia is not running:\n" + vm.logs("authelia")
    assert c.restart_count == 0, f"authelia restarted {c.restart_count}x:\n" + vm.logs("authelia")


# --- 1: protected page -> portal ----------------------------------------------------

def test_protected_page_without_sign_in_redirects_to_the_authelia_portal(portal):
    session = portal.new_session()
    page = portal.url()
    out = portal.outcome(session, page)
    assert out.kind == "portal_redirect", f"expected a redirect to {portal.portal_host}, got {out}"
    assert 300 <= out.status < 400
    assert ap.portal_redirect_target(out.location, portal.portal_host) == page, (
        f"the portal must be told where to return to (rd=...): {out.location}")


def test_the_portal_page_itself_is_a_200_and_does_not_count_as_access(portal):
    # Why the check above looks at the Location header: the portal answers 200 for anybody.
    session = portal.new_session()
    out = portal.outcome(session, portal.url("auth"))
    assert out.status == 200
    assert out.kind == "portal_page"


# --- 2: first factor -------------------------------------------------------------------

@pytest.mark.parametrize("user", ["alice", "bob"])
def test_first_factor_accepts_users_from_the_file(portal, user):
    session = portal.new_session()
    portal.login(session, user, ap.FIXTURE_PASSWORDS[user])


def test_first_factor_rejects_a_wrong_password_and_an_unknown_user(portal):
    # One attempt each: Authelia's regulation bans a user after 3 failures within 2 minutes.
    session = portal.new_session()
    wrong = portal.try_first_factor(session, "alice", "not-the-fixture-password")
    assert wrong.status_code == 401, f"wrong password: HTTP {wrong.status_code} {wrong.text[:200]}"
    unknown = portal.try_first_factor(session, "mallory", "whatever")
    assert unknown.status_code == 401, f"unknown user: HTTP {unknown.status_code} {unknown.text[:200]}"


# --- 3: second factor (TOTP) ---------------------------------------------------------

def test_totp_can_be_registered_for_alice_with_the_cli(vm):
    """Registration goes through Authelia's CLI (`storage user totp generate`): the API route
    needs an identity check with a one-time code from the notifier file first."""
    cfg = vm.ensure_totp("alice")
    assert len(cfg["secret"]) >= 16 and cfg["digits"] == 6 and cfg["period"] == 30


def test_alice_with_first_factor_only_does_not_reach_the_two_factor_page(portal, sign_in):
    session = sign_in("alice")
    out = portal.outcome(session, portal.url())
    assert out.kind == "portal_redirect", f"two_factor page must not open with one factor: {out}"


def test_alice_with_second_factor_reaches_the_two_factor_page(portal, sign_in):
    session = sign_in("alice", second_factor=True)
    out = portal.outcome(session, portal.url())
    assert (out.kind, out.status) == ("ok", 200), f"expected the landing page after TOTP, got {out}"


# --- 4: group rule "viewers see only one domain" ------------------------------------------

def test_viewer_bob_reaches_the_domain_his_group_may_see(portal, sign_in):
    session = sign_in("bob")
    url = portal.url("grafana", "/api/health")  # Grafana is up when this answers 200 through Traefik
    portal.wait_until(lambda: portal.outcome(session, url).kind == "ok", "Grafana behind Traefik", timeout=300)
    out = portal.outcome(session, url)
    assert (out.kind, out.status) == ("ok", 200), f"expected bob to reach grafana.{portal.domain}, got {out}"


def test_viewer_bob_gets_403_on_another_domain(portal, sign_in):
    session = sign_in("bob")
    out = portal.outcome(session, portal.url())
    assert (out.kind, out.status) == ("denied", 403), f"expected 403 for bob on {portal.domain}, got {out}"
