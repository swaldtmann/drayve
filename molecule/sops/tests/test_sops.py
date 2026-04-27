"""Test: secrets.mode=sops — SOPS-encrypted secrets decrypted and applied."""


# === Secrets applied ===

def test_env_file_exists(host):
    """.env should exist with secrets from SOPS-encrypted file."""
    f = host.file("/opt/drayve/deploy/stack/.env")
    assert f.exists
    assert f.mode == 0o600


def test_env_contains_grafana_password(host):
    """Grafana password from SOPS should be in .env."""
    cmd = host.run("grep -c GRAFANA_ADMIN_PASSWORD /opt/drayve/deploy/stack/.env")
    assert cmd.rc == 0
    assert int(cmd.stdout.strip()) >= 1


def test_htpasswd_exists(host):
    """htpasswd file should be created from SOPS-decrypted password."""
    f = host.file("/opt/drayve/deploy/stack/.htpasswd")
    assert f.exists
    assert f.mode == 0o600


# === Secrets not leaked ===

def test_no_plaintext_secrets_in_compose(host):
    """docker-compose.yml should not contain plaintext secret values."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert not f.contains("sops-test-password")
    assert not f.contains("sops-test-grafana")


def test_env_references_not_plaintext(host):
    """.env should contain the secrets but compose should reference via ${VAR}."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    # Compose should use env var references, not inline values
    assert f.contains("${") or not f.contains("sops-test")


# === Stack runs ===

def test_docker_running(host):
    """Docker should be running."""
    svc = host.service("docker")
    assert svc.is_running


def test_traefik_running(host):
    """Traefik should be running."""
    cmd = host.run("docker ps --filter name=traefik --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_promtail_running(host):
    """Promtail should be running (light monitoring includes promtail)."""
    cmd = host.run("docker ps --filter name=promtail --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_landing_page(host):
    """Landing page should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/landing/index.html")
    assert f.exists


def test_basic_auth_middleware(host):
    """Basic auth middleware should be configured."""
    f = host.file("/opt/drayve/deploy/stack/traefik/config/dynamic/auth-basic.yml")
    assert f.exists
    assert f.contains("basicAuth")
