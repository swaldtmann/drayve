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


def test_drayve_user_in_docker_group(host):
    """The drayve user should be in the docker group."""
    user = host.user("drayve")
    assert "docker" in user.groups
