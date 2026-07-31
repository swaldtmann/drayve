"""Tests for the common role."""


def test_packages_installed(host):
    """All common packages should be installed."""
    for pkg in ["curl", "jq", "rsync", "git", "ufw", "acl"]:
        assert host.package(pkg).is_installed


def test_swap_file_exists(host):
    """Swap file should exist with correct permissions."""
    f = host.file("/swapfile")
    assert f.exists
    assert f.mode == 0o600


def test_swap_enabled(host):
    """Swap should be active."""
    cmd = host.run("swapon --show --noheadings")
    assert cmd.stdout.strip() != ""


def test_swap_in_fstab(host):
    """Swap should be in fstab for persistence."""
    fstab = host.file("/etc/fstab")
    assert fstab.contains("/swapfile")


def test_ufw_enabled(host):
    """UFW should be active."""
    cmd = host.run("ufw status")
    assert "Status: active" in cmd.stdout


def test_ufw_ssh_allowed(host):
    """SSH (port 22) should be allowed."""
    cmd = host.run("ufw status")
    assert "22/tcp" in cmd.stdout


def test_ufw_http_allowed(host):
    """HTTP (port 80) should be allowed."""
    cmd = host.run("ufw status")
    assert "80/tcp" in cmd.stdout


def test_ufw_https_allowed(host):
    """HTTPS (port 443) should be allowed."""
    cmd = host.run("ufw status")
    assert "443/tcp" in cmd.stdout


def test_journald_forward_to_syslog_disabled_by_default(host):
    """EWH-W-139/KEDGE-W-012-adjacent: docker log-driver=journald otherwise
    duplicates every container log line into /var/log/syslog too."""
    journald_conf = host.file("/etc/systemd/journald.conf")
    assert journald_conf.contains("ForwardToSyslog=no")


def test_ssh_password_auth_disabled(host):
    """SSH password authentication should be disabled."""
    sshd = host.file("/etc/ssh/sshd_config")
    assert sshd.contains("PasswordAuthentication no")


def test_ssh_root_login_policy(host):
    """Root login should be restricted to key-only."""
    sshd = host.file("/etc/ssh/sshd_config")
    assert sshd.contains("PermitRootLogin prohibit-password")


def test_drayve_user_exists(host):
    """The drayve user should exist."""
    user = host.user("drayve")
    assert user.exists
    assert user.uid == 1000
    assert user.group == "drayve"


def test_drayve_group_exists(host):
    """The drayve group should exist."""
    group = host.group("drayve")
    assert group.exists
    assert group.gid == 1000


def test_directory_structure(host):
    """Base directories should exist with correct ownership."""
    for path in ["/opt/drayve", "/opt/drayve/deploy/stack", "/opt/drayve/config"]:
        d = host.file(path)
        assert d.exists
        assert d.is_directory
        assert d.user == "drayve"
        assert d.group == "drayve"
        assert d.mode == 0o755
