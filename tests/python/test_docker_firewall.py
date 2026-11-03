"""Tests for the DOCKER-USER firewall baseline of the docker role.

Docker publishes container ports straight into the nat/FORWARD path, so UFW
rules never see that traffic (Oops-0094). The docker role therefore installs
a script that rebuilds the DOCKER-USER chain with a default DROP and RETURNs
only for the ports the operator already allows externally (ufw_allowed_ports).

These tests render ansible/roles/docker/templates/docker-user-fw.sh.j2 and
check the resulting rule order, plus the wiring in defaults and tasks.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_DIR = REPO_ROOT / "ansible" / "roles" / "docker"
TEMPLATE_DIR = ROLE_DIR / "templates"

PORTS_80_443 = [
    {"port": "80", "proto": "tcp"},
    {"port": "443", "proto": "tcp"},
]


def _render(ansible_jinja_env, ports=None) -> list[str]:
    env = ansible_jinja_env(TEMPLATE_DIR, strict=True)
    tpl = env.get_template("docker-user-fw.sh.j2")
    out = tpl.render(
        ansible_managed="Ansible managed",
        docker_firewall_allowed_ports=PORTS_80_443 if ports is None else ports,
    )
    return out.splitlines()


def _rules(lines: list[str]) -> list[str]:
    return [ln.strip() for ln in lines if "DOCKER-USER" in ln and "-A" in ln]


def test_template_exists():
    assert (TEMPLATE_DIR / "docker-user-fw.sh.j2").is_file()


def test_unit_template_exists():
    assert (TEMPLATE_DIR / "drayve-docker-user-fw.service.j2").is_file()


def test_last_rule_is_default_drop(ansible_jinja_env):
    rules = _rules(_render(ansible_jinja_env))
    assert rules, "no -A DOCKER-USER rules rendered"
    assert rules[-1].endswith("-j DROP")
    assert rules.count(rules[-1]) == 1


def test_established_and_bridge_traffic_returns_before_drop(ansible_jinja_env):
    rules = _rules(_render(ansible_jinja_env))
    drop = len(rules) - 1
    est = next(i for i, r in enumerate(rules) if "ESTABLISHED,RELATED" in r and r.endswith("-j RETURN"))
    bridge = next(i for i, r in enumerate(rules) if "-i br+" in r and r.endswith("-j RETURN"))
    docker0 = next(i for i, r in enumerate(rules) if "-i docker0" in r and r.endswith("-j RETURN"))
    assert est < drop and bridge < drop and docker0 < drop


def test_allowed_ports_return_before_drop(ansible_jinja_env):
    rules = _rules(_render(ansible_jinja_env))
    drop = len(rules) - 1
    for port in ("80", "443"):
        idx = [
            i for i, r in enumerate(rules)
            if f"--ctorigdstport {port} " in r + " " and "-p tcp" in r and r.endswith("-j RETURN")
        ]
        assert idx, f"no RETURN rule for tcp/{port}"
        assert idx[0] < drop


def test_ports_follow_the_list_and_protocol(ansible_jinja_env):
    rules = _rules(_render(ansible_jinja_env, [{"port": "8443", "proto": "udp"}]))
    joined = "\n".join(rules)
    assert "-p udp" in joined and "--ctorigdstport 8443" in joined
    assert "--ctorigdstport 80 " not in joined + " "
    assert "--ctorigdstport 443 " not in joined + " "


def test_empty_port_list_still_drops(ansible_jinja_env):
    rules = _rules(_render(ansible_jinja_env, []))
    assert rules[-1].endswith("-j DROP")
    assert not any("--ctorigdstport" in r for r in rules)


def test_rendered_script_is_valid_bash(ansible_jinja_env, tmp_path):
    bash = shutil.which("bash")
    assert bash
    script = tmp_path / "fw.sh"
    script.write_text("\n".join(_render(ansible_jinja_env)) + "\n")
    res = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_defaults_enable_baseline_and_follow_ufw_ports():
    defaults = yaml.safe_load((ROLE_DIR / "defaults" / "main.yml").read_text())
    assert defaults["docker_firewall_enabled"] is True
    assert "ufw_allowed_ports" in str(defaults["docker_firewall_allowed_ports"])


def test_tasks_are_gated_by_the_switch_and_start_the_unit():
    tasks = (ROLE_DIR / "tasks" / "main.yml").read_text()
    assert "docker-user-fw.sh.j2" in tasks
    assert "drayve-docker-user-fw.service.j2" in tasks
    assert "docker_firewall_enabled" in tasks
    assert "drayve-docker-user-fw" in tasks
