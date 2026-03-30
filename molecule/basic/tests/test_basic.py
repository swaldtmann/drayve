"""Test: basic auth + full monitoring — no Authelia/LLDAP."""


# === Auth: basic ===

def test_no_authelia_container(host):
    """Authelia should NOT be running."""
    cmd = host.run("docker ps --filter name=authelia --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_lldap_container(host):
    """LLDAP should NOT be running."""
    cmd = host.run("docker ps --filter name=lldap --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_basic_auth_middleware(host):
    """Traefik should have basic auth middleware configured."""
    f = host.file("/opt/drayve/deploy/stack/traefik/config/dynamic/auth-basic.yml")
    assert f.exists
    assert f.contains("basicAuth")


# === Monitoring: full ===

def test_grafana_running(host):
    """Grafana should be running."""
    cmd = host.run("docker ps --filter name=grafana --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_prometheus_running(host):
    """Prometheus should be running."""
    cmd = host.run("docker ps --filter name=prometheus --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_loki_running(host):
    """Loki should be running."""
    cmd = host.run("docker ps --filter name=loki --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_node_exporter_running(host):
    """Node exporter should be running."""
    cmd = host.run("docker ps --filter name=node-exporter --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_cadvisor_running(host):
    """cAdvisor should be running."""
    cmd = host.run("docker ps --filter name=cadvisor --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_promtail_running(host):
    """Promtail should be running."""
    cmd = host.run("docker ps --filter name=promtail --format '{{.Status}}'")
    assert "Up" in cmd.stdout


# === Infrastructure ===

def test_docker_running(host):
    """Docker should be running."""
    svc = host.service("docker")
    assert svc.is_running


def test_compose_file(host):
    """docker-compose.yml should exist."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert f.exists


def test_no_authelia_in_compose(host):
    """Compose should not contain authelia service."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert not f.contains("  authelia:")


def test_landing_page(host):
    """Landing page should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/landing/index.html")
    assert f.exists


def test_traefik_running(host):
    """Traefik should be running."""
    cmd = host.run("docker ps --filter name=traefik --format '{{.Status}}'")
    assert "Up" in cmd.stdout
