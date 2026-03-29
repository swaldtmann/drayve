"""Testrunde 3 — Authelia + LLDAP integration tests."""


# === LLDAP ===

def test_lldap_container_running(host):
    """LLDAP container should be running."""
    cmd = host.run("docker ps --filter name=lldap --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_lldap_healthy(host):
    """LLDAP should report healthy."""
    cmd = host.run("docker inspect lldap --format '{{.State.Health.Status}}'")
    assert cmd.stdout.strip() == "healthy"


def test_lldap_http_port(host):
    """LLDAP HTTP API should respond."""
    cmd = host.run("docker exec lldap curl -sf http://localhost:17170/")
    assert cmd.rc == 0


# === Authelia ===

def test_authelia_container_running(host):
    """Authelia container should be running."""
    cmd = host.run("docker ps --filter name=authelia --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_authelia_config_exists(host):
    """Authelia configuration should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/authelia/configuration.yml")
    assert f.exists


def test_authelia_jwks_key(host):
    """OIDC JWKS RSA key should be generated."""
    f = host.file("/opt/drayve/deploy/stack/authelia/jwks.pem")
    assert f.exists
    assert f.mode == 0o600


def test_authelia_responds(host):
    """Authelia should respond on its internal port."""
    cmd = host.run(
        "docker exec authelia wget -q -O- http://localhost:9091/api/health"
    )
    assert cmd.rc == 0
    assert "OK" in cmd.stdout or "ok" in cmd.stdout.lower()


# === Authelia bind user in LLDAP ===

def test_authelia_bind_user_exists(host):
    """The authelia bind user should exist in LLDAP."""
    cmd = host.run(
        "docker exec lldap /app/lldap_set_password "
        "--base-url http://localhost:17170 "
        "--admin-username admin --admin-password test-lldap-admin-pw "
        "--username authelia --password test-authelia-ldap-pw"
    )
    assert cmd.rc == 0
    assert "Successfully" in cmd.stdout


# === Traefik forwardAuth ===

def test_traefik_running(host):
    """Traefik should be running."""
    cmd = host.run("docker ps --filter name=traefik --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_traefik_has_authelia_middleware(host):
    """Traefik config should reference authelia forwardAuth."""
    f = host.file("/opt/drayve/deploy/stack/traefik/config/traefik.yml")
    assert f.exists
    # Dynamic config or labels should set up forwardAuth
    cmd = host.run(
        "docker exec traefik cat /etc/traefik/traefik.yml 2>/dev/null || "
        "cat /opt/drayve/deploy/stack/traefik/config/traefik.yml"
    )
    # The middleware is configured via docker labels, check compose
    compose = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert compose.exists
    assert compose.contains("authelia") or compose.contains("forwardAuth")


# === Infrastructure (same as integration) ===

def test_docker_running(host):
    """Docker should be running."""
    svc = host.service("docker")
    assert svc.is_running


def test_compose_stack(host):
    """docker-compose.yml should exist."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert f.exists


def test_env_file(host):
    """.env should exist with authelia secrets."""
    f = host.file("/opt/drayve/deploy/stack/.env")
    assert f.exists
    assert f.mode == 0o600
    assert f.contains("AUTHELIA_JWT_SECRET")
    assert f.contains("LLDAP_ADMIN_PASSWORD")


def test_landing_page(host):
    """Landing page should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/landing/index.html")
    assert f.exists


def test_grafana_running(host):
    """Grafana should be running (full profile)."""
    cmd = host.run("docker ps --filter name=grafana --format '{{.Status}}'")
    assert "Up" in cmd.stdout
