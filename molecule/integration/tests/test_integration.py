"""Integration tests — verify the full Drayve stack after deploy."""

import json


# === Infrastructure ===

def test_docker_running(host):
    """Docker should be running."""
    svc = host.service("docker")
    assert svc.is_running


def test_compose_stack_directory(host):
    """The deploy directory should have docker-compose.yml."""
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert f.exists


def test_env_file_exists(host):
    """.env should exist with restricted permissions."""
    f = host.file("/opt/drayve/deploy/stack/.env")
    assert f.exists
    assert f.mode == 0o600


# === Containers ===

def test_traefik_running(host):
    """Traefik container should be running."""
    cmd = host.run("docker ps --filter name=traefik --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_crowdsec_running(host):
    """CrowdSec container should be running."""
    cmd = host.run("docker ps --filter name=crowdsec --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_grafana_running(host):
    """Grafana container should be running (full profile)."""
    cmd = host.run("docker ps --filter name=grafana --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_prometheus_running(host):
    """Prometheus container should be running (full profile)."""
    cmd = host.run("docker ps --filter name=prometheus --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_loki_running(host):
    """Loki container should be running (full profile)."""
    cmd = host.run("docker ps --filter name=loki --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_promtail_running(host):
    """Promtail container should be running."""
    cmd = host.run("docker ps --filter name=promtail --format '{{.Status}}'")
    assert "Up" in cmd.stdout


def test_node_exporter_running(host):
    """Node exporter container should be running."""
    cmd = host.run("docker ps --filter name=node-exporter --format '{{.Status}}'")
    assert "Up" in cmd.stdout


# === Ports ===

def test_port_80_listening(host):
    """Port 80 should be listening (Traefik HTTP)."""
    assert host.socket("tcp://0.0.0.0:80").is_listening


def test_port_443_listening(host):
    """Port 443 should be listening (Traefik HTTPS)."""
    assert host.socket("tcp://0.0.0.0:443").is_listening


# === Config files ===

def test_traefik_config(host):
    """Traefik config should exist."""
    f = host.file("/opt/drayve/deploy/stack/traefik/config/traefik.yml")
    assert f.exists


def test_acme_json_permissions(host):
    """acme.json should have 600 permissions."""
    f = host.file("/opt/drayve/deploy/stack/traefik/certs/acme.json")
    assert f.exists
    assert f.mode == 0o600


def test_prometheus_config(host):
    """Prometheus config should exist."""
    f = host.file("/opt/drayve/deploy/stack/monitoring/prometheus/prometheus.yml")
    assert f.exists


def test_promtail_config(host):
    """Promtail config should exist."""
    f = host.file("/opt/drayve/deploy/stack/monitoring/promtail/promtail-config.yml")
    assert f.exists


def test_landing_page(host):
    """Landing page should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/landing/index.html")
    assert f.exists
    assert f.contains("test.drayve.local")


# === Firewall ===

def test_ufw_active(host):
    """UFW should be active."""
    cmd = host.run("ufw status")
    assert "Status: active" in cmd.stdout


# === Grafana dashboards ===

def test_grafana_dashboards_provisioned(host):
    """Grafana dashboard JSONs should be provisioned."""
    d = host.file("/opt/drayve/deploy/stack/monitoring/grafana/provisioning/dashboards/json")
    assert d.exists
    assert d.is_directory
    cmd = host.run("ls /opt/drayve/deploy/stack/monitoring/grafana/provisioning/dashboards/json/*.json")
    assert cmd.rc == 0
