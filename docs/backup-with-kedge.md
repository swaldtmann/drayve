# Backup with kedge

Since v0.5.0, drayve's `backup` role can delegate to
[kedge](https://codeberg.org/StephanWaldtmann/kedge) — a generic
encrypted backup tool for Docker Compose stacks. kedge auto-discovers
volumes, bind mounts, and database services, runs DB pre-hooks, and
ships snapshots into a restic repository.

This is the recommended target for new deployments. The original
in-role restic wrapper (`backup.target: local` / `sftp`) is deprecated
but kept for backwards compatibility.

## Why a separate tool

- Auto-discovery instead of hand-maintained volume lists.
- DB-aware pre-hooks (Postgres, MySQL/MariaDB, Valkey/Redis, MongoDB).
- Independently versioned and tested (round-trip Hetzner Cloud test
  in kedge's own CI).
- Same restic repository format — switching the target does not
  re-encrypt the repo.

## Enabling kedge target

In `stack.yaml`:

```yaml
backup:
  enabled: true
  target: kedge
  schedule: "0 3 * * *"           # daily backup
  prune_schedule: "30 4 * * 0"    # weekly prune
```

In `host_secrets` (sops-encrypted, see
[`docs/secrets.md`](secrets.md)):

```yaml
backup_restic_password: <strong-random-string>
backup_kedge_restic_repository: sftp:u123456@u123456.your-storagebox.de:/myhost
```

Then deploy:

```bash
cd ansible
ansible-playbook playbooks/backup.yml -l <host> -e @../stack.yaml
```

## What the role does

1. Installs `restic`, `jq`, `rsync`, `git`.
2. Clones kedge to `/opt/kedge`, pinned to `backup_kedge_version`
   (default `v0.3.1`). Updates on subsequent runs.
3. Symlinks `/opt/kedge/backup.sh` → `/usr/local/bin/kedge`.
4. Renders `/root/.kedge.env` from `host_secrets` (mode 0600,
   `no_log: true`).
5. Installs two cron jobs as root:
   - `drayve-kedge-backup` — daily, runs `kedge backup`
   - `drayve-kedge-prune` — weekly, runs `kedge prune`
6. Removes the legacy `drayve-backup` cron entry to prevent
   double-runs during migration.

## Tunables

All `backup_kedge_*` defaults live in
`ansible/roles/backup/defaults/main.yml`. Override per host or per
stack:

| Variable                          | Default                                              | Notes                                       |
|-----------------------------------|------------------------------------------------------|---------------------------------------------|
| `backup_kedge_repo`               | `https://codeberg.org/StephanWaldtmann/kedge.git`    | Mirror URL if you self-host kedge           |
| `backup_kedge_version`            | `v0.3.4`                                             | Pin to a tag, never `main`                  |
| `backup_kedge_install_dir`        | `/opt/kedge`                                         |                                             |
| `backup_kedge_env_file`           | `/root/.kedge.env`                                   | mode 0600, root-owned                       |
| `backup_kedge_log_file`           | `/var/log/kedge.log`                                 |                                             |
| `backup_kedge_stack_dir`          | `{{ drayve_deploy_dir }}`                            | Where docker-compose.yml lives              |
| `backup_kedge_restic_repository`  | *(empty — required via `host_secrets`)*              | restic repo URI                             |
| `backup_kedge_stop_stack`         | `true`                                               | `false` = hot backup (see kedge README)     |
| `backup_kedge_exclude_mounts`     | `""`                                                 | Space-separated paths to skip               |
| `backup_kedge_healthcheck_url`    | `""`                                                 | Healthchecks.io / Uptime Kuma URL           |
| `backup_kedge_pre_hook`           | `""`                                                 | Command run before kedge's own backup steps |
| `backup_kedge_cron_wrapper`       | `""`                                                 | Cron wrapper, contract `<wrapper> <job-name> -- <cmd...>`. Wraps both the backup and prune cron line — e.g. a fail-signal helper. See "Fail-signal wrapper" below. |

## Fail-signal wrapper

kedge itself only runs `BACKUP_FAIL_HOOK`/`BACKUP_POST_HOOK` for the `backup`
subcommand's own `cleanup()` trap — `kedge prune` has no hook or healthcheck
integration at all. If you need uniform fail-alerting for both cron jobs (not
just backup), set `backup_kedge_cron_wrapper` instead of relying on kedge's
native hooks:

```yaml
backup:
  ...
  kedge_cron_wrapper: /usr/local/bin/my-fail-signal-wrapper
```

The wrapper must accept the contract `<wrapper> <job-name> -- <cmd...>`
(run the command, forward its stdout/stderr and exit code unchanged, fire
your alert on non-zero exit or on whatever failure signal you detect). Both
generated cron lines — `kedge-<stack_name>-backup` and
`kedge-<stack_name>-prune` — get wrapped identically. Empty (default): cron
lines are unwrapped, exactly as before this option existed.

## Migration from `local` or `sftp`

The restic repository format is identical, so an existing repo keeps
working:

1. Set `backup.target: kedge` in `stack.yaml`.
2. Add `backup_kedge_restic_repository` to `host_secrets`. Use the
   same URI you used before:
   - was `target: sftp` → `sftp:<sftp_host>:<sftp_path>`
   - was `target: local` → the local path (e.g. `/opt/drayve/backups`)
3. Run `ansible-playbook playbooks/backup.yml -l <host>`.
4. The role removes the old `drayve-backup` cron and installs the
   two kedge crons. The next scheduled run will use kedge against
   the same restic repository.
5. Verify: `restic -r <repo> snapshots` should keep listing your old
   snapshots after the first kedge run.

The legacy `backup.sh` deployed by the old role at
`{{ drayve_deploy_dir }}/backup.sh` is left in place for manual
rollback. Remove it once you are confident in the migration.

## Verifying

After the first run, check on the host:

```bash
sudo crontab -l                    # nothing (cron is in /etc/cron.d/)
ls /etc/cron.d/                    # drayve-kedge-backup, drayve-kedge-prune
ls -l /usr/local/bin/kedge         # → /opt/kedge/backup.sh
sudo cat /root/.kedge.env          # should contain STACK_DIR, RESTIC_REPOSITORY, ...
sudo set -a; . /root/.kedge.env; set +a; kedge list   # last snapshots
```

A manual run as root:

```bash
sudo bash -c 'set -a; . /root/.kedge.env; set +a; kedge backup'
```

should produce a fresh snapshot in the restic repository.

## See also

- [kedge README](https://codeberg.org/StephanWaldtmann/kedge) —
  full CLI reference, hot-backup safety classification, restore
  procedures.
- [`docs/secrets.md`](secrets.md) — how `host_secrets` is encrypted
  and decrypted.
