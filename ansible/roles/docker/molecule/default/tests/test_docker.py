"""Tests for the docker role."""


def test_docker_installed(host):
    """Docker should be installed."""
    cmd = host.run("docker --version")
    assert cmd.rc == 0
    assert "Docker version" in cmd.stdout


def test_docker_compose_installed(host):
    """Docker Compose plugin should be installed."""
    cmd = host.run("docker compose version")
    assert cmd.rc == 0


def test_docker_service_running(host):
    """Docker service should be running and enabled."""
    svc = host.service("docker")
    assert svc.is_running
    assert svc.is_enabled


def test_docker_daemon_json(host):
    """Docker daemon should use journald logging."""
    f = host.file("/etc/docker/daemon.json")
    assert f.exists
    assert f.contains('"log-driver": "journald"')


def test_docker_daemon_extra_merged(host):
    """docker_daemon_extra (molecule.yml group_vars) must be merged into
    daemon.json alongside the base keys."""
    f = host.file("/etc/docker/daemon.json")
    assert f.contains('"debug": true')
    assert f.contains('"log-driver": "journald"')


def test_drayve_user_in_docker_group(host):
    """The drayve user should be in the docker group."""
    user = host.user("drayve")
    assert "docker" in user.groups


def test_docker_user_chain_default_drop(host):
    """DOCKER-USER baseline: default DROP is the last rule, 80/443 are open."""
    rules = host.run("iptables -S DOCKER-USER").stdout.strip().splitlines()
    assert rules[-1] == "-A DOCKER-USER -j DROP"
    joined = "\n".join(rules)
    assert "--ctorigdstport 80" in joined
    assert "--ctorigdstport 443" in joined
    assert host.service("drayve-docker-user-fw").is_enabled
