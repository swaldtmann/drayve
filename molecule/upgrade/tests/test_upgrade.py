"""Test: upgrade from a previous release to the working tree.

prepare.yml converged the baseline tag, converge.yml the working tree on top
(idempotence is checked by Molecule in between). These tests check the result:
services the new release introduced run, services it retired are gone, and
the host really started from the recorded baseline.
"""

import json
import re
import time


def test_baseline_release_was_recorded(host):
    """prepare.yml left a marker: `<tag> <commit>` of the converged baseline."""
    f = host.file("/etc/drayve-upgrade-baseline")
    assert f.exists
    assert re.match(r"^v\d+\.\d+\.\d+ [0-9a-f]{40}$", f.content_string.strip()), f.content_string


# === Retired: Promtail (replaced by Alloy in v0.10.0) ===

def test_no_promtail_container(host):
    """No promtail container, running or stopped."""
    cmd = host.run("docker ps -a --filter name=promtail --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def test_no_promtail_service_in_compose(host):
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert f.exists
    assert not f.contains("  promtail:")


# === Introduced: Alloy ===

def test_alloy_service_in_compose(host):
    f = host.file("/opt/drayve/deploy/stack/docker-compose.yml")
    assert f.contains("  alloy:")


def test_alloy_running(host):
    cmd = host.run("docker ps --filter name=alloy --format '{{.Status}}'")
    assert "Up" in cmd.stdout


# === The rest of the stack survived the upgrade ===

def test_stack_services_running(host):
    for name in ("traefik", "grafana", "prometheus", "loki", "node-exporter", "cadvisor"):
        cmd = host.run(f"docker ps --filter name={name} --format '{{{{.Status}}}}'")
        assert "Up" in cmd.stdout, f"{name}: {cmd.stdout!r}"


def test_no_container_restarting(host):
    cmd = host.run("docker ps --filter status=restarting --format '{{.Names}}'")
    assert cmd.stdout.strip() == ""


def _loki_label_values(host, label):
    """Values of a Loki label (the loki image has no shell/curl and no host port)."""
    ip = host.run(
        "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' loki"
    ).stdout.split()
    if not ip:
        return []
    cmd = host.run(f"curl -fsS http://{ip[0]}:3100/loki/api/v1/label/{label}/values")
    if cmd.rc != 0:
        return []
    try:
        return json.loads(cmd.stdout).get("data") or []
    except ValueError:
        return []


def test_alloy_ships_container_logs_to_loki(host):
    """Logs reach Loki with the label `container` after the upgrade."""
    deadline = time.time() + 90
    values = []
    while time.time() < deadline:
        values = _loki_label_values(host, "container")
        if "traefik" in values:
            break
        time.sleep(5)
    assert "traefik" in values, f"label container values: {values}"
