"""Tests for the Authelia OIDC-clients Jinja template (W-144).

Renders ``config/authelia/configuration.yml.j2`` with various values for
``authelia_oidc_clients`` / ``authelia_oidc_authorization_policies`` /
``authelia_oidc_claims_policies`` and verifies the generated YAML.

Covers:
  - Backward compatibility: legacy Grafana-only default still renders.
  - Multi-client setup: Forgejo + Grafana coexist.
  - Authorization policies block renders with group-based rules.
  - Claims policies block renders with id_token claim list.
  - Per-client overrides (consent_mode, claims_policy, scopes) reach output.
  - Hash map maps to client_secret per client.

Run: ``pytest tests/python/test_authelia_template.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTHELIA_TEMPLATE = REPO_ROOT / "config" / "authelia" / "configuration.yml.j2"


GRAFANA_LEGACY_DEFAULT = [
    {
        "id": "grafana",
        "name": "Grafana",
        "authorization_policy": "one_factor",
        "redirect_uris": [
            "https://grafana.example.com/login/generic_oauth",
        ],
        "scopes": ["openid", "profile", "email", "groups"],
        "consent_mode": "implicit",
    }
]


@pytest.fixture
def render(ansible_jinja_env):
    env = ansible_jinja_env(AUTHELIA_TEMPLATE.parent)
    template = env.get_template(AUTHELIA_TEMPLATE.name)
    base_ctx = {
        "drayve_domain": "example.com",
        "auth_lldap": False,
        "lldap_port": 3890,
        "lldap_base_dn": "DC=drayve,DC=local",
        "authelia_ldap_password": "x",
        "authelia_session_secret": "s",
        "authelia_storage_encryption_key": "k",
        "authelia_jwt_secret": "j",
        "authelia_oidc_hmac_secret": "h",
        "authelia_jwks_pem": "-----BEGIN RSA PRIVATE KEY-----\nstub\n-----END RSA PRIVATE KEY-----\n",
        # W-144 vars
        "authelia_oidc_clients": GRAFANA_LEGACY_DEFAULT,
        "authelia_oidc_clients_default": GRAFANA_LEGACY_DEFAULT,
        "authelia_oidc_authorization_policies": {},
        "authelia_oidc_claims_policies": {},
        "authelia_oidc_client_secret_hashes": {
            "grafana": "$pbkdf2-sha512$310000$abc$def",
        },
    }

    def _render(**overrides: object) -> str:
        ctx = {**base_ctx, **overrides}
        return template.render(**ctx)

    return _render


def _parse(rendered: str) -> dict:
    return yaml.safe_load(rendered)


# ---- 1) Back-compat: legacy single-client default ----

def test_legacy_grafana_default_renders(render):
    cfg = _parse(render())
    clients = cfg["identity_providers"]["oidc"]["clients"]
    assert len(clients) == 1
    assert clients[0]["client_id"] == "grafana"
    assert clients[0]["client_name"] == "Grafana"
    assert clients[0]["authorization_policy"] == "one_factor"
    assert clients[0]["scopes"] == ["openid", "profile", "email", "groups"]
    assert clients[0]["client_secret"] == "$pbkdf2-sha512$310000$abc$def"


def test_legacy_default_has_no_policy_blocks(render):
    cfg = _parse(render())
    oidc = cfg["identity_providers"]["oidc"]
    assert "authorization_policies" not in oidc
    assert "claims_policies" not in oidc


# ---- 2) Multi-client: Forgejo + Grafana ----

@pytest.fixture
def two_client_ctx() -> dict:
    clients = [
        GRAFANA_LEGACY_DEFAULT[0],
        {
            "id": "forgejo",
            "name": "Forgejo",
            "authorization_policy": "forgejo_access",
            "consent_mode": "implicit",
            "redirect_uris": [
                "https://git.example.com/user/oauth2/Authelia/callback",
            ],
            "scopes": ["openid", "profile", "email", "groups"],
        },
    ]
    return {
        "authelia_oidc_clients": clients,
        "authelia_oidc_client_secret_hashes": {
            "grafana": "$pbkdf2-sha512$310000$g$h",
            "forgejo": "$pbkdf2-sha512$310000$f$j",
        },
        "authelia_oidc_authorization_policies": {
            "forgejo_access": {
                "default_policy": "deny",
                "rules": [
                    {"policy": "one_factor", "subject": ["group:forgejo_users"]},
                ],
            },
        },
    }


def test_two_clients_render(render, two_client_ctx):
    cfg = _parse(render(**two_client_ctx))
    clients = cfg["identity_providers"]["oidc"]["clients"]
    ids = [c["client_id"] for c in clients]
    assert ids == ["grafana", "forgejo"]


def test_forgejo_client_secret_per_client(render, two_client_ctx):
    cfg = _parse(render(**two_client_ctx))
    fj = next(c for c in cfg["identity_providers"]["oidc"]["clients"] if c["client_id"] == "forgejo")
    assert fj["client_secret"] == "$pbkdf2-sha512$310000$f$j"


def test_forgejo_consent_mode_implicit(render, two_client_ctx):
    cfg = _parse(render(**two_client_ctx))
    fj = next(c for c in cfg["identity_providers"]["oidc"]["clients"] if c["client_id"] == "forgejo")
    assert fj["consent_mode"] == "implicit"


def test_forgejo_redirect_uri_present(render, two_client_ctx):
    cfg = _parse(render(**two_client_ctx))
    fj = next(c for c in cfg["identity_providers"]["oidc"]["clients"] if c["client_id"] == "forgejo")
    assert fj["redirect_uris"] == [
        "https://git.example.com/user/oauth2/Authelia/callback",
    ]


# ---- 3) Authorization policies block ----

def test_authorization_policies_block(render, two_client_ctx):
    cfg = _parse(render(**two_client_ctx))
    pol = cfg["identity_providers"]["oidc"]["authorization_policies"]
    assert "forgejo_access" in pol
    assert pol["forgejo_access"]["default_policy"] == "deny"
    rules = pol["forgejo_access"]["rules"]
    assert rules[0]["policy"] == "one_factor"
    assert rules[0]["subject"] == ["group:forgejo_users"]


def test_authorization_policies_with_networks(render):
    ctx = {
        "authelia_oidc_authorization_policies": {
            "lan_only": {
                "default_policy": "deny",
                "rules": [
                    {"policy": "bypass", "networks": ["10.0.0.0/8"]},
                ],
            },
        },
    }
    cfg = _parse(render(**ctx))
    pol = cfg["identity_providers"]["oidc"]["authorization_policies"]
    assert pol["lan_only"]["rules"][0]["networks"] == ["10.0.0.0/8"]


# ---- 4) Claims policies ----

def test_claims_policies_id_token(render):
    ctx = {
        "authelia_oidc_claims_policies": {
            "default": {
                "id_token": ["groups", "email", "preferred_username"],
            },
        },
    }
    cfg = _parse(render(**ctx))
    cp = cfg["identity_providers"]["oidc"]["claims_policies"]
    assert cp["default"]["id_token"] == ["groups", "email", "preferred_username"]


def test_claims_policy_attached_to_client(render):
    clients = [
        {
            "id": "forgejo",
            "name": "Forgejo",
            "authorization_policy": "one_factor",
            "claims_policy": "default",
            "redirect_uris": ["https://git.example.com/user/oauth2/Authelia/callback"],
            "scopes": ["profile", "email", "groups"],
        },
    ]
    ctx = {
        "authelia_oidc_clients": clients,
        "authelia_oidc_client_secret_hashes": {"forgejo": "$pbkdf2-sha512$x"},
        "authelia_oidc_claims_policies": {
            "default": {"id_token": ["groups"]},
        },
    }
    cfg = _parse(render(**ctx))
    fj = cfg["identity_providers"]["oidc"]["clients"][0]
    assert fj["claims_policy"] == "default"


# ---- 5) Scopes / per-client overrides ----

def test_scopes_override_per_client(render):
    clients = [
        {
            "id": "minimal",
            "name": "Minimal",
            "authorization_policy": "one_factor",
            "redirect_uris": ["https://m.example.com/cb"],
            "scopes": ["openid"],
        },
    ]
    cfg = _parse(render(
        authelia_oidc_clients=clients,
        authelia_oidc_client_secret_hashes={"minimal": "$pbkdf2$x"},
    ))
    assert cfg["identity_providers"]["oidc"]["clients"][0]["scopes"] == ["openid"]


def test_scopes_default_when_omitted(render):
    clients = [
        {
            "id": "lazy",
            "name": "Lazy",
            "authorization_policy": "one_factor",
            "redirect_uris": ["https://l.example.com/cb"],
        },
    ]
    cfg = _parse(render(
        authelia_oidc_clients=clients,
        authelia_oidc_client_secret_hashes={"lazy": "$pbkdf2$x"},
    ))
    assert cfg["identity_providers"]["oidc"]["clients"][0]["scopes"] == [
        "openid", "profile", "email", "groups",
    ]


# ---- 6) Empty clients edge case ----

def test_empty_client_list_renders_valid_yaml(render):
    cfg = _parse(render(authelia_oidc_clients=[]))
    assert cfg["identity_providers"]["oidc"]["clients"] is None or cfg["identity_providers"]["oidc"]["clients"] == []


# ---- 7) Optional client fields: grant/response types, token auth method ----

def test_optional_client_advanced_fields(render):
    clients = [
        {
            "id": "spa",
            "name": "SPA",
            "authorization_policy": "one_factor",
            "redirect_uris": ["https://spa.example.com/cb"],
            "scopes": ["openid", "profile"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    ]
    cfg = _parse(render(
        authelia_oidc_clients=clients,
        authelia_oidc_client_secret_hashes={"spa": ""},
    ))
    c = cfg["identity_providers"]["oidc"]["clients"][0]
    assert c["grant_types"] == ["authorization_code", "refresh_token"]
    assert c["response_types"] == ["code"]
    assert c["token_endpoint_auth_method"] == "none"
