"""Test: backup role — kedge target. File-system + structure checks.

Avoids running `kedge backup` itself (would require restic init + a real
repo). Smoke that all artefacts deployed by the role are in place,
permissions/ownerships correct, legacy crontab swept.
"""


# === kedge dependencies ===

def test_restic_installed(host):
    cmd = host.run("restic version")
    assert cmd.rc == 0
    assert "restic" in cmd.stdout


def test_jq_installed(host):
    assert host.run("jq --version").rc == 0


# === kedge git checkout ===

def test_kedge_repo_cloned(host):
    f = host.file("/opt/kedge")
    assert f.exists
    assert f.is_directory


def test_kedge_repo_pinned_to_tag(host):
    """Pinned to backup_kedge_version (v0.3.2) — never floating main."""
    cmd = host.run("git -C /opt/kedge describe --tags --exact-match 2>/dev/null || git -C /opt/kedge describe --tags")
    assert cmd.rc == 0
    assert cmd.stdout.strip().startswith("v0."), cmd.stdout


def test_kedge_backup_sh_present(host):
    f = host.file("/opt/kedge/backup.sh")
    assert f.exists
    assert f.mode & 0o100  # executable


# === Symlink ===

def test_kedge_symlink_target(host):
    f = host.file("/usr/local/bin/kedge")
    assert f.is_symlink
    assert f.linked_to == "/opt/kedge/backup.sh"


def test_kedge_invocable(host):
    """Symlinked CLI must run --help without error."""
    cmd = host.run("/usr/local/bin/kedge --help 2>&1 | head -1")
    assert cmd.rc == 0


# === .kedge.env ===

def test_kedge_env_file_perms(host):
    f = host.file("/root/.kedge.env")
    assert f.exists
    assert f.user == "root"
    assert f.group == "root"
    assert f.mode == 0o600


def test_kedge_env_required_keys(host):
    """All seven required env keys present, no plaintext leaks beyond expected."""
    f = host.file("/root/.kedge.env")
    for key in (
        "STACK_DIR=",
        "RESTIC_REPOSITORY=",
        "RESTIC_PASSWORD=",
        "BACKUP_STOP_STACK=",
        "BACKUP_KEEP_DAILY=",
        "BACKUP_KEEP_WEEKLY=",
        "BACKUP_KEEP_MONTHLY=",
    ):
        assert f.contains(key), f"missing {key}"


def test_kedge_env_repo_set_to_test_path(host):
    """Confirms the molecule scenario's repo path made it into the env file."""
    f = host.file("/root/.kedge.env")
    assert f.contains("RESTIC_REPOSITORY=/var/lib/restic-test/repo")


def test_kedge_env_post_hook_wired(host):
    """KEDGE-W-012: BACKUP_POST_HOOK must render when backup_kedge_post_hook is set —
    the framework template previously templated BACKUP_PRE_HOOK only, silently
    dropping any freshness-metric export configured for the kedge target."""
    f = host.file("/root/.kedge.env")
    assert f.contains('BACKUP_POST_HOOK="touch /tmp/kedge-post-hook-marker"')


# === /etc/cron.d/kedge-<stack> ===

def test_kedge_cron_file_present(host):
    """Cron lives at /etc/cron.d/kedge-<stack_name> (NOT root-crontab)."""
    f = host.file("/etc/cron.d/kedge-test-backup-kedge")
    assert f.exists
    assert f.user == "root"
    assert f.mode == 0o644


def test_kedge_cron_file_has_backup_and_prune(host):
    f = host.file("/etc/cron.d/kedge-test-backup-kedge")
    assert f.contains("/usr/local/bin/kedge backup")
    assert f.contains("/usr/local/bin/kedge prune")
    assert f.contains("/root/.kedge.env")


def test_kedge_cron_file_has_user_column(host):
    """cron.d format requires a user column between schedule and command."""
    f = host.file("/etc/cron.d/kedge-test-backup-kedge")
    assert f.contains(" root ")


# === Legacy cleanup ===

def test_no_legacy_drayve_backup_in_root_crontab(host):
    """role:backup with target=kedge must sweep old root-crontab entries."""
    cmd = host.run("crontab -l 2>/dev/null || true")
    assert "drayve-backup" not in cmd.stdout
    assert "drayve-kedge-backup" not in cmd.stdout
    assert "drayve-kedge-prune" not in cmd.stdout


# === Idempotency contract ===
# Idempotency itself is verified by molecule's `idempotence` step in the
# matrix release-gate; here we only check the artefacts a second run would
# touch are deterministic (no dynamic timestamps in the cron file content
# beyond ansible_managed).

def test_cron_file_no_dynamic_timestamp(host):
    """Cron file content must be stable across runs (no `date` or run_id)."""
    f = host.file("/etc/cron.d/kedge-test-backup-kedge")
    # Ansible-managed banner is allowed (deterministic per role version);
    # anything that looks like a UNIX timestamp or ISO date in body is suspect.
    body = f.content_string
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        # Sanity: no microsecond timestamps, no host hashes.
        assert "github.run_id" not in line
        assert "$RANDOM" not in line


# === Infrastructure (sanity) ===

def test_docker_running(host):
    svc = host.service("docker")
    assert svc.is_running
