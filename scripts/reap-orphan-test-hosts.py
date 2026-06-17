#!/usr/bin/env python3
"""Reap orphaned molecule test hosts in the hcloud project (AFKI-W-122 Folge).

The release-gate molecule runs create hosts named ``test-<scenario>``. A clean
``molecule test`` destroys them at the end of the sequence, and the workflow's
``Safety-destroy`` step catches most interrupts. This reaper is the last line of
defence: it deletes any hcloud server whose name matches a *known molecule
scenario host* AND is older than the age threshold — so an in-flight test is
never killed.

The allowlist is derived from ``molecule/*/molecule.yml`` so it stays in sync
when scenarios are added or renamed. A server is only ever reaped if its name is
in that allowlist; arbitrary ``test-*`` hosts are left untouched.

Env:
  HCLOUD_TOKEN    (required) — project token for the molecule project
  REAP_AGE_HOURS  (default 4) — minimum host age before reaping
  DRY_RUN         (default "0") — "1" lists candidates without deleting

Exit 0 on success/no-op (cron-safe), 1 only on a hard error (missing token /
hcloud lib). Logs to stdout.

Anlass: 2 verwaiste ``test-basic`` liefen 38 Tage (greenfields-Context, S346).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MOLECULE_ROOT = REPO_ROOT / "molecule"
DEFAULT_AGE_HOURS = 4


def load_allowlist(molecule_root: Path) -> set[str]:
    """Collect molecule platform host names from molecule/*/molecule.yml."""
    names: set[str] = set()
    for mol in sorted(molecule_root.glob("*/molecule.yml")):
        try:
            data = yaml.safe_load(mol.read_text()) or {}
        except yaml.YAMLError:
            continue
        for platform in data.get("platforms") or []:
            name = (platform or {}).get("name")
            if name:
                names.add(str(name))
    return names


def select_orphans(servers, allowlist: set[str], threshold: timedelta, now: datetime):
    """Return servers whose name is in the allowlist and that are older than threshold.

    Each server is any object exposing ``.name`` (str) and ``.created`` (tz-aware
    datetime). The age guard prevents reaping a host that a running test still owns.
    """
    orphans = []
    for srv in servers:
        if srv.name not in allowlist:
            continue
        created = srv.created
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now - created >= threshold:
            orphans.append(srv)
    return orphans


def main() -> int:
    token = os.environ.get("HCLOUD_TOKEN", "").strip()
    if not token:
        print("FATAL: HCLOUD_TOKEN not set", file=sys.stderr)
        return 1

    try:
        from hcloud import Client
    except ImportError:
        print("FATAL: hcloud python lib not available (pip install hcloud)", file=sys.stderr)
        return 1

    age_hours = float(os.environ.get("REAP_AGE_HOURS", DEFAULT_AGE_HOURS))
    dry_run = os.environ.get("DRY_RUN", "0").strip() in {"1", "true", "yes"}
    threshold = timedelta(hours=age_hours)

    allowlist = load_allowlist(MOLECULE_ROOT)
    if not allowlist:
        print(f"WARN: no molecule platform names found under {MOLECULE_ROOT} — nothing to reap")
        return 0
    print(f"allowlist ({len(allowlist)}): {', '.join(sorted(allowlist))}")

    client = Client(token=token)
    servers = client.servers.get_all()
    now = datetime.now(timezone.utc)
    orphans = select_orphans(servers, allowlist, threshold, now)

    if not orphans:
        print(f"no orphans (age >= {age_hours}h) among {len(servers)} servers — clean")
        return 0

    for srv in orphans:
        age_h = (now - srv.created).total_seconds() / 3600
        if dry_run:
            print(f"DRY_RUN would delete: {srv.name} (age {age_h:.1f}h, id {srv.id})")
        else:
            print(f"deleting orphan: {srv.name} (age {age_h:.1f}h, id {srv.id})")
            srv.delete()
    return 0


if __name__ == "__main__":
    sys.exit(main())
