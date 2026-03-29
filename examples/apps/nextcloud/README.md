# Nextcloud on Drayve

Host your own Nextcloud on a Drayve-managed server.

## What you get

- Nextcloud 30 with MariaDB 11 and Redis 7
- TLS via Let's Encrypt (automatic)
- CalDAV/CardDAV redirects
- Protected by Drayve's CrowdSec + auth layer

## Setup

1. **Provision your server with Drayve** (if not done yet):

   ```bash
   make provision NAME=myserver DOMAIN=example.com
   ```

2. **Set DNS:** Create an A-Record for `cloud.example.com` → your server IP.

3. **Copy to server:**

   ```bash
   scp -r examples/apps/nextcloud/ root@myserver:/opt/drayve/apps/nextcloud/
   ```

4. **Configure on server:**

   ```bash
   ssh root@myserver
   cd /opt/drayve/apps/nextcloud
   cp .env.example .env
   nano .env   # fill in domain + passwords
   ```

5. **Start:**

   ```bash
   docker compose up -d
   ```

6. **Open** `https://cloud.example.com` — done.

## Backup

Nextcloud data lives in Docker volumes:

- `nextcloud_data` — files, config
- `nextcloud_db` — MariaDB database
- `nextcloud_redis` — session cache (optional)

If Drayve backup is enabled, add these volumes to your backup scope.
