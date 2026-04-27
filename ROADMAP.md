# Roadmap

Drayve uses named "voyages" as roadmap milestones.

## Maiden Voyage — Extract & Prove (current)

**Goal:** Standalone framework with first independent deploy on a production server.

**Status:** Phase 2 (Service Integration) — Phases 0–1 complete.

| Phase | What | Status |
|-------|------|--------|
| 0a — Foundation | Repo, `stack.yaml` schema, generic Makefile | Done |
| 0b — Roles | Extract common, docker, traefik, auth, monitoring roles | Done |
| 0c — Proof | Secrets management, landing page, smoke test on fresh VPS | Done |
| 1 — Dashboards | Generic Grafana dashboards (host, containers, Traefik, CrowdSec, backup) | Done |
| 2 — Service Integration | Docker Labels as service contract, deploy-time scanning, auth-sync | In progress |
| 3 — Hardening | E2E tests (Molecule + Testinfra), documentation | Next |

### Design Decisions

- **Ansible for the frame, not services** — Ansible manages infrastructure (Traefik, monitoring, auth, backup) idempotently. Services bring their own compose files and connect via Docker labels
- **Docker Labels as contract** — Services declare routing (`traefik.*`), landing page (`drayve.landing.*`), and monitoring (`drayve.monitoring.*`) via labels. No central registry
- **Deploy-time scanning** — Landing page, auth-sync, and dashboard provisioning are rendered from compose files at `make deploy`, not from running containers
- **Auth as provider interface** — `auth.provider: none | basic | authelia | authentik`
- **Monitoring profiles** — `monitoring.profile: full | light | none` with per-service overrides
- **Secrets** — Quick-start (random generation) + SOPS/Age for versioned secrets
- **No PaaS** — Drayve provides the stage, not the play. No image builds, no CI/CD, no buildpacks
- **Single host** — Docker Compose, deliberately not Kubernetes

## Second Wind — Consumers & Maturity (next)

**Goal:** Eliminate infrastructure duplication across multiple servers. Alerting and backup maturity.

| Phase | What |
|-------|------|
| 1 — First Consumer | Migrate an existing production stack to Drayve |
| 2 — Alerting | Generic alert rules, configurable contact points (Slack, MQTT, mail) |
| 3 — Backup Maturity | Automated restore tests, backup monitoring dashboard, configurable retention |
| 4 — More Consumers | Roll out to remaining servers |

## Not Planned

| Topic | Reason |
|-------|--------|
| Kubernetes support | Deliberately single-host, Docker Compose |
| Cloud provider lock-in | Hetzner first, provider abstraction later |
