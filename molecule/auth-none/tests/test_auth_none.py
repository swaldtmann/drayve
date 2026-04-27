"""Test: auth.provider=none — no auth containers, no middleware."""


# === Auth: none ===

def test_no_authelia_container(host):
    """Authelia should NOT be running."""
    cmd = host.run("docker ps --filter name=authelia --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_lldap_container(host):
    """LLDAP should NOT be running."""
    cmd = host.run("docker ps --filter name=lldap --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_authentik_container(host):
    """Authentik should NOT be running."""
    cmd = host.run("docker ps --filter name=authentik --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_auth_none_middleware(host):
    """Traefik should have auth-none middleware (passthrough)."""
    f = host.file(
        "/opt/drayve/deploy/stack/traefik/config/dynamic/auth-none.yml"
    )
    assert f.exists


def test_no_stale_auth_middleware(host):
    """No basic/authelia/authentik auth middleware should be present."""
    for name in ["auth-basic.yml", "auth-authelia.yml", "auth-authentik.yml"]:
        f = host.file(
            f"/opt/drayve/deploy/stack/traefik/config/dynamic/{name}"
        )
        assert not f.exists, f"Stale middleware file: {name}"


# === Infrastructure ===

def test_docker_running(host):
    """Docker should be running."""
    svc = host.service("docker")
    assert svc.is_running


def test_traefik_running(host):
    """Traefik should be running."""
    cmd = host.run("docker ps --filter name=traefik --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_compose_file(host):
    """docker-compose.yml should exist."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert f.exists


def test_no_auth_in_compose(host):
    """Compose should not contain any auth service."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert not f.contains("  authelia:")
    assert not f.contains("  authentik-server:")
    assert not f.contains("  lldap:")


def test_landing_page(host):
    """Landing page should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/landing/index.html")
    assert f.exists


def test_grafana_running(host):
    """Grafana should be running (full monitoring)."""
    cmd = host.run("docker ps --filter name=grafana --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_crowdsec_healthy(host):
    """CrowdSec should be healthy."""
    cmd = host.run("docker inspect crowdsec --format '{{.State.Health.Status}}'")
    assert cmd.stdout.strip() == "healthy"
