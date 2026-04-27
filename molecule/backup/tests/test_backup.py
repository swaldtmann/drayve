"""Test: backup role — restic, local target, cron job, dry-run."""


# === Restic installed ===

def test_restic_installed(host):
    """Restic should be installed."""
    cmd = host.run("restic version")
    assert cmd.rc == 0
    assert "restic" in cmd.stdout


# === Backup directory ===

def test_backup_dir_exists(host):
    """Local backup repo directory should exist."""
    f = host.file("/opt/drayve/backups")
    assert f.exists
    assert f.is_directory
    assert f.mode == 0o700


def test_restic_repo_initialized(host):
    """Restic repo should be initialized."""
    cmd = host.run(
        "RESTIC_PASSWORD=test-restic-password-32chars-long "
        "restic snapshots --repo /opt/drayve/backups"
    )
    assert cmd.rc == 0


# === Backup script ===

def test_backup_script_exists(host):
    """Backup script should be deployed."""
    f = host.file("/opt/drayve/deploy/stack/backup.sh")
    assert f.exists
    assert f.mode == 0o700


def test_backup_script_contains_restic(host):
    """Backup script should use restic."""
    f = host.file("/opt/drayve/deploy/stack/backup.sh")
    assert f.contains("restic backup")
    assert f.contains("restic forget")


def test_backup_script_stops_services(host):
    """Backup script should stop services for consistency."""
    f = host.file("/opt/drayve/deploy/stack/backup.sh")
    assert f.contains("docker compose stop")


# === Cron job ===

def test_backup_cron_exists(host):
    """Backup cron job should be registered."""
    cmd = host.run("crontab -l")
    assert cmd.rc == 0
    assert "drayve-backup" in cmd.stdout or "backup.sh" in cmd.stdout


def test_backup_cron_schedule(host):
    """Backup cron should run at 03:00."""
    cmd = host.run("crontab -l")
    assert "0 3" in cmd.stdout


# === Backup dry-run ===

def test_backup_script_runs(host):
    """Backup script should execute without error."""
    cmd = host.run("/opt/drayve/deploy/stack/backup.sh")
    assert cmd.rc == 0


def test_backup_created_snapshots(host):
    """After running backup, snapshots should exist."""
    cmd = host.run(
        "RESTIC_PASSWORD=test-restic-password-32chars-long "
        "restic snapshots --repo /opt/drayve/backups --json"
    )
    assert cmd.rc == 0
    assert cmd.stdout.strip() != "[]"


# === Infrastructure (sanity) ===

def test_docker_running(host):
    """Docker should be running."""
    svc = host.service("docker")
    assert svc.is_running


def test_traefik_running(host):
    """Traefik should be running after backup restore."""
    cmd = host.run("docker ps --filter name=traefik --format '{{.Status}}'")
    assert "Up" in cmd.stdout
