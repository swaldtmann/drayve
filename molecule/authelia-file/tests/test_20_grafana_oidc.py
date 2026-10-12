"""Grafana signs in through Authelia (OIDC) and maps the group to a role.

alice (group lldap_admin, which Grafana's role mapping tests) must become Admin,
bob Viewer. The flow is the real one: Grafana redirects to Authelia, Authelia
returns a code, Grafana exchanges it at Authelia's token endpoint, /api/user.
"""

import authelia_probe as ap
import pytest

# >>> SWITCH - the one place to arm the Grafana checks <<<
# Set to True once the Grafana back channel fix (token/userinfo over HTTPS through
# Traefik, issue: Authelia 4.39 rejects the plain-HTTP call with "invalid
# X-Forwarded-Proto header value 'http'") and the claims_policy for the Grafana
# client (groups in the ID token, else alice stays Viewer) are merged into this
# branch. Until then both tests are EXPECTED to fail; strict=True turns an
# unexpected pass into a failure, which is the reminder to flip this flag.
# Also set the test-only TLS variable of that fix in molecule.yml if it needs one
# (planned name: grafana_oauth_tls_skip_verify), because the VM has no valid certificate.
GRAFANA_BACKCHANNEL_FIXED = True

grafana_expected_red = pytest.mark.xfail(
    not GRAFANA_BACKCHANNEL_FIXED,
    strict=True,
    raises=AssertionError,  # a timeout or crash is a real error, not the known one
    reason="remove after the grafana backchannel fix is merged (M2: Authelia 4.39 rejects the "
           "http://authelia:9091 token call; M2b: no claims_policy, groups missing from the ID token)",
)

pytestmark = pytest.mark.usefixtures("stack_ready")


@grafana_expected_red
def test_grafana_oidc_sign_in_makes_alice_an_admin(portal, sign_in):
    session = sign_in("alice", second_factor=True)  # admins need two factors on every domain
    user = portal.grafana_oidc_login(session)
    assert user["email"] == "alice@test.drayve.local", user
    assert user["role"] == "Admin", f"alice (group lldap_admin) got role {user['role']!r}"


@grafana_expected_red
def test_grafana_oidc_sign_in_makes_bob_a_viewer(portal, sign_in):
    session = sign_in("bob")  # one factor is enough on grafana.<domain> for viewers
    user = portal.grafana_oidc_login(session)
    assert user["email"] == "bob@test.drayve.local", user
    assert user["role"] == "Viewer", f"bob got role {user['role']!r}"
