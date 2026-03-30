"""Test: no auth + monitoring light — only exporters, no Grafana."""


# === Auth: none ===

def test_no_authelia_container(host):
    """Authelia should NOT be running."""
    cmd = host.run("docker ps --filter name=authelia --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_lldap_container(host):
    """LLDAP should NOT be running."""
    cmd = host.run("docker ps --filter name=lldap --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_auth_middleware(host):
    """No auth middleware should be configured."""
    import os
    dynamic_dir = "/opt/drayve/deploy/stack/traefik/config/dynamic"
    cmd = host.run(f"ls {dynamic_dir}/auth-*.yml 2>/dev/null || echo 'none'")
    assert "none" in cmd.stdout


# === Monitoring: light ===

def test_no_grafana(host):
    """Grafana should NOT be running in light profile."""
    cmd = host.run("docker ps --filter name=grafana --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_prometheus(host):
    """Prometheus should NOT be running in light profile."""
    cmd = host.run("docker ps --filter name=prometheus --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_loki(host):
    """Loki should NOT be running in light profile."""
    cmd = host.run("docker ps --filter name=loki --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_cadvisor(host):
    """cAdvisor should NOT be running in light profile."""
    cmd = host.run("docker ps --filter name=cadvisor --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_node_exporter_running(host):
    """Node exporter should be running (included in light)."""
    cmd = host.run("docker ps --filter name=node-exporter --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_promtail_running(host):
    """Promtail should be running (included in light)."""
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


def test_no_grafana_in_compose(host):
    """Compose should not contain grafana service."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert not f.contains("  grafana:")


def test_landing_page_no_auth(host):
    """Landing page should NOT have auth middleware."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert not f.contains("middlewares=auth@file")


def test_traefik_running(host):
    """Traefik should be running."""
    cmd = host.run("docker ps --filter name=traefik --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_landing_page(host):
    """Landing page should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/landing/index.html")
    assert f.exists
