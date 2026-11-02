# Rebuild from a kedge backup

`make rebuild NAME=<site>` provisions a fresh box, replaces its stack with the latest (or a chosen) kedge snapshot and
runs a basic verify. Target is always a new box, never the live host: stage 2 stops the stack and kedge overwrites volumes
and the stack directory.

```bash
make rebuild NAME=demo REBUILD_ARGS="-e rebuild_restic_repository=sftp:u1234-sub1@u1234-sub1.your-storagebox.de:/demo \
  -e rebuild_restic_password=... -e rebuild_restic_sftp_key=/path/to/readonly_key"
```

Stages: (1) `provision.yml` unchanged; (2) restore: restore-only env `/root/.kedge-restore.env`, `docker compose down`,
`kedge restore <snapshot>`, stack up; (3) verify (`ansible/tasks/rebuild_verify.yml`).

| Variable | Default | |
|---|---|---|
| `rebuild_restic_repository` | `backup_kedge_restic_repository` | restic repo of the backup |
| `rebuild_restic_password` | `backup_restic_password` | with quickstart secrets that is a new password: pass the real one |
| `rebuild_restic_sftp_key` | none | private key file on the controller, read-only Storage Box account |
| `rebuild_restic_sftp_port` | `23` | |
| `rebuild_snapshot` | `latest` | |
| `rebuild_kedge_version` | `backup_kedge_version` | |
| `rebuild_verify_tasks` | none | path to a tasks file included last, e.g. a data comparison |

No backup cron is installed: run `make backup NAME=<site>` once the rebuilt box is accepted.

## Verify hook

`rebuild_verify_tasks` points to a tasks file on the controller. It runs on the rebuilt host, after the basic checks, with
the play variables in scope; a failing task fails the rebuild.

### Bundled hook: mockforge data comparison

`ansible/tasks/rebuild_verify_mockforge.yml` regenerates a [mockforge](https://github.com/swaldtmann/mockforge)
Nextcloud tree with the seed used for the backup and compares it (relative path + sha256, file count) against the
restored docker volume (`scripts/rebuild-tree-compare.sh`). For rebuild drills on throwaway boxes with synthetic data:

```bash
make rebuild NAME=probe REBUILD_ARGS="... \
  -e rebuild_verify_tasks=$PWD/ansible/tasks/rebuild_verify_mockforge.yml \
  -e rebuild_mockforge_src=/path/to/mockforge -e rebuild_mockforge_volume=drayve_nextcloud_data"
```

Optional: `rebuild_mockforge_files` (300) and `rebuild_mockforge_seed` (1) must match the backup. Result line:
`mockforge verify <volume>: ['files=300 mismatches=0']`.

## DOCKER-USER baseline

See the `docker` role defaults (`docker_firewall_enabled`, `docker_firewall_allowed_ports`). Check on a host:
`iptables -S DOCKER-USER` ends with `-A DOCKER-USER -j DROP`.
