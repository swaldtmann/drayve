"""Authentik integration tests — zero-touch auth stack."""


# === Authentik containers ===

def test_authentik_server_running(host):
    """Authentik server container should be running."""
    cmd = host.run("docker ps --filter name=authentik-server --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_authentik_worker_running(host):
    """Authentik worker container should be running."""
    cmd = host.run("docker ps --filter name=authentik-worker --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_authentik_db_running(host):
    """Authentik PostgreSQL container should be running."""
    cmd = host.run("docker ps --filter name=authentik-db --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_authentik_db_healthy(host):
    """Authentik PostgreSQL should be healthy."""
    cmd = host.run("docker inspect authentik-db --format '{{.State.Health.Status}}'")
    assert cmd.stdout.strip() == "healthy"


def test_authentik_redis_running(host):
    """Authentik Redis container should be running."""
    cmd = host.run("docker ps --filter name=authentik-redis --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_authentik_redis_healthy(host):
    """Authentik Redis should be healthy."""
    cmd = host.run("docker inspect authentik-redis --format '{{.State.Health.Status}}'")
    assert cmd.stdout.strip() == "healthy"


def test_authentik_api_responds(host):
    """Authentik API should respond on port 9000."""
    # Authentik image has no curl; query via container IP from the host
    cmd = host.run(
        "curl -sf http://"
        "$(docker inspect -f '{{.NetworkSettings.Networks.drayve_default.IPAddress}}' "
        "authentik-server):9000/-/health/ready/"
    )
    assert cmd.rc == 0


# === Bootstrap ===

def test_bootstrap_script_exists(host):
    """Bootstrap script should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/authentik/bootstrap/bootstrap.py")
    assert f.exists
    assert f.mode == 0o755


# === Traefik forwardAuth ===

def test_traefik_running(host):
    """Traefik should be running."""
    cmd = host.run("docker ps --filter name=traefik --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_traefik_has_authentik_middleware(host):
    """Traefik dynamic config should have authentik forwardAuth."""
    f = host.file(
        "/opt/drayve/deploy/stack/traefik/config/dynamic/auth-authentik.yml"
    )
    assert f.exists
    assert f.contains("forwardAuth")
    assert f.contains("authentik-server:9000")
    assert f.contains("maxResponseBodySize")


def test_no_stale_auth_middleware(host):
    """No authelia or basic auth middleware should be present."""
    for name in ["auth-basic.yml", "auth-authelia.yml", "auth-none.yml"]:
        f = host.file(
            f"/opt/drayve/deploy/stack/traefik/config/dynamic/{name}"
        )
        assert not f.exists, f"Stale middleware file: {name}"


# === Infrastructure ===

def test_docker_running(host):
    """Docker should be running."""
    svc = host.service("docker")
    assert svc.is_running


def test_compose_stack(host):
    """docker-compose.yml should exist and reference authentik."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert f.exists
    assert f.contains("authentik-server")
    assert f.contains("authentik-worker")
    assert f.contains("authentik-db")


def test_env_file(host):
    """.env should exist with authentik secrets."""
    f = host.file("/opt/drayve/deploy/stack/.env")
    assert f.exists
    assert f.mode == 0o600
    assert f.contains("AUTHENTIK_SECRET_KEY")
    assert f.contains("AUTHENTIK_BOOTSTRAP_TOKEN")
    assert f.contains("AUTHENTIK_OIDC_GRAFANA_SECRET")


def test_landing_page(host):
    """Landing page should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/landing/index.html")
    assert f.exists


def test_grafana_running(host):
    """Grafana should be running (full profile)."""
    cmd = host.run("docker ps --filter name=grafana --format '{{.Status}}'")
    assert "Up" in cmd.stdout


# === Bootstrap result ===

def test_bootstrap_api_token_active(host):
    """Bootstrap should have created a working API token."""
    cmd = host.run(
        "docker exec authentik-server python -c \""
        "import os, django; "
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authentik.root.settings'); "
        "django.setup(); "
        "from authentik.core.models import Token; "
        "t = Token.objects.get(identifier='drayve-bootstrap'); "
        "print(t.key)\""
    )
    assert cmd.rc == 0
    assert len(cmd.stdout.strip()) >= 10


def test_bootstrap_grafana_oidc_provider(host):
    """Bootstrap should have created the Grafana OIDC provider."""
    ip = host.run(
        "docker inspect -f '{{.NetworkSettings.Networks.drayve_default.IPAddress}}' "
        "authentik-server"
    ).stdout.strip()
    token = host.run(
        "docker exec authentik-server python -c \""
        "import os, django; "
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authentik.root.settings'); "
        "django.setup(); "
        "from authentik.core.models import Token; "
        "print(Token.objects.get(identifier='drayve-bootstrap').key)\""
    ).stdout.strip()
    cmd = host.run(
        "curl -sf -H 'Authorization: Bearer %s' "
        "http://%s:9000/api/v3/providers/oauth2/?name=grafana" % (token, ip)
    )
    assert cmd.rc == 0
    assert '"client_id":"grafana"' in cmd.stdout.replace(" ", "")


def test_bootstrap_proxy_providers(host):
    """Bootstrap should have created proxy providers (whoami, landing, traefik)."""
    ip = host.run(
        "docker inspect -f '{{.NetworkSettings.Networks.drayve_default.IPAddress}}' "
        "authentik-server"
    ).stdout.strip()
    token = host.run(
        "docker exec authentik-server python -c \""
        "import os, django; "
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authentik.root.settings'); "
        "django.setup(); "
        "from authentik.core.models import Token; "
        "print(Token.objects.get(identifier='drayve-bootstrap').key)\""
    ).stdout.strip()
    cmd = host.run(
        "curl -sf -H 'Authorization: Bearer %s' "
        "http://%s:9000/api/v3/providers/proxy/?page_size=50" % (token, ip)
    )
    assert cmd.rc == 0
    for name in ["whoami", "landing", "traefik-dashboard"]:
        assert name in cmd.stdout, f"Proxy provider '{name}' not found"


def test_bootstrap_applications(host):
    """Bootstrap should have created applications.

    Uses Django ORM directly — the Authentik REST API returns large nested
    provider_obj per application, which gets truncated by the SSH/Testinfra
    transport layer when piped through curl | python3.
    """
    cmd = host.run(
        "docker exec authentik-server python -c \""
        "import os, django; "
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authentik.root.settings'); "
        "django.setup(); "
        "from authentik.core.models import Application; "
        "slugs = list(Application.objects.values_list('slug', flat=True)); "
        "print(' '.join(slugs))\""
    )
    assert cmd.rc == 0, f"Django ORM query failed: {cmd.stderr}"
    found = cmd.stdout.strip().split()
    for slug in ["whoami", "landing", "grafana", "traefik-dashboard"]:
        assert slug in found, f"Application '{slug}' not found (got: {found})"


def test_bootstrap_embedded_outpost(host):
    """Embedded outpost should have proxy providers assigned."""
    ip = host.run(
        "docker inspect -f '{{.NetworkSettings.Networks.drayve_default.IPAddress}}' "
        "authentik-server"
    ).stdout.strip()
    token = host.run(
        "docker exec authentik-server python -c \""
        "import os, django; "
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authentik.root.settings'); "
        "django.setup(); "
        "from authentik.core.models import Token; "
        "print(Token.objects.get(identifier='drayve-bootstrap').key)\""
    ).stdout.strip()
    cmd = host.run(
        "curl -sf -H 'Authorization: Bearer %s' "
        "http://%s:9000/api/v3/outposts/instances/?page_size=50" % (token, ip)
    )
    assert cmd.rc == 0
    assert "Embedded" in cmd.stdout
    assert '"providers"' in cmd.stdout


# === Grafana OIDC config ===

def test_grafana_oidc_configured(host):
    """Grafana should have OIDC auth configured via env."""
    f = host.file("/opt/drayve/deploy/stack/.env")
    assert f.contains("AUTHENTIK_OIDC_GRAFANA_SECRET")


# === Container memory limits ===

def test_container_memory_limits(host):
    """Key containers should have memory limits set."""
    for name in ["authentik-server", "authentik-worker", "authentik-db", "traefik"]:
        cmd = host.run(
            "docker inspect %s --format '{{.HostConfig.Memory}}'" % name
        )
        assert cmd.rc == 0
        mem = int(cmd.stdout.strip())
        assert mem > 0, f"Container '{name}' has no memory limit"


# === CrowdSec integration ===

def test_crowdsec_running(host):
    """CrowdSec should be running and healthy."""
    cmd = host.run("docker inspect crowdsec --format '{{.State.Health.Status}}'")
    assert cmd.stdout.strip() == "healthy"


def test_crowdsec_traefik_bouncer(host):
    """CrowdSec should have a bouncer registered."""
    cmd = host.run("docker exec crowdsec cscli bouncers list -o raw")
    assert cmd.rc == 0
    # At least one bouncer should be registered
    assert "traefik" in cmd.stdout.lower() or len(cmd.stdout.strip().split("\n")) > 1
