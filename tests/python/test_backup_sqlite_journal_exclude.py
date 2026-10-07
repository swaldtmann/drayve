"""Legacy backup script must exclude transient SQLite rollback journals.

CrowdSec keeps a SQLite DB in a Docker volume. Its ``*.db-journal`` file
appears and vanishes while the live volume backup runs; restic then fails
to read it and exits 3 ("snapshot incomplete"), which ``set -e`` turns into
a failed backup (Molecule scenario ``backup``, ``test_backup_script_runs``).

Only the rollback journal is excluded: ``-wal``/``-shm`` belong to the data
set, and the ``--tag config`` backup of the deploy dir stays untouched.

Run: ``pytest tests/python/test_backup_sqlite_journal_exclude.py``
"""

from __future__ import annotations

from pathlib import Path

TASKS = Path(__file__).resolve().parents[2] / "ansible/roles/backup/tasks/main.yml"


def _restic_backup_lines() -> list[str]:
    return [
        line.strip()
        for line in TASKS.read_text().splitlines()
        if "restic backup" in line
    ]


def test_volume_backup_excludes_sqlite_journal():
    lines = [ln for ln in _restic_backup_lines() if '--tag "$vol"' in ln]
    assert len(lines) == 1
    assert "--exclude '*.db-journal'" in lines[0]


def test_wal_and_shm_not_excluded():
    text = TASKS.read_text()
    assert "-wal" not in text
    assert "-shm" not in text


def test_config_backup_has_no_exclude():
    lines = [ln for ln in _restic_backup_lines() if "--tag config" in ln]
    assert len(lines) == 1
    assert "--exclude" not in lines[0]
