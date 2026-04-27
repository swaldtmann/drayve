# TODO

Beifang aus Werkstatt-Sessions. Nicht priorisiert — Roadmap bleibt führend.

## Bugs / Correctness

- [ ] PBKDF2-Hash wird bei Secret-Rotation nicht erneuert — Hash manuell löschen nötig, kein Drift-Check (S168#1)
- [ ] Authelia DB wird bei Config-Change gelöscht — killt aktive Sessions (S168#2)
- [ ] LLDAP `lldap_set_password` setzt Passwort bei jedem Deploy neu, auch ohne Änderung (S168#5)
- [ ] `curl -s` statt `curl -sf` in `authelia/tasks/post-deploy.yml` — HTTP-Fehler werden still geschluckt (S168#16)
- [ ] LLDAP Healthcheck prüft nur HTTP-Port (17170), nicht LDAP-Port (3890) (S168#17)
- [ ] AUTHENTIK_BOOTSTRAP_TOKEN erzeugt keinen API-Token in 2025.2.4 — Django-Shell Workaround nötig (S215#7)

## Test Coverage

- [ ] Molecule-Szenarien testen kein SOPS — AGE-Key/SOPS-Pfad ungetestet (S168#3)
- [ ] Molecule `basic` Szenario: kein curl-Test ob Basic Auth tatsächlich 401 liefert (S168#4)
- [ ] Pre-push Hook validiert nur statische Liste — sollte alle `examples/*.yaml` per Glob prüfen (S215#13)

## Release Preparation

- [ ] `drayve_version` in stack.yaml noch `0.1.0` — Tag ist `v0.2.0-rc1`, muss synchronisiert werden (S168#10)
- [ ] `deploy/authbox` per `git add -f` committed — echte Hostnames/Domains sichtbar bei Public-Release (S168#11)
- [ ] Backup-Role referenziert privates Repo — externer User bekommt Fehler bei `backup: enabled: true` (S168#13)

## QoL / Hardening

- [ ] Kein `make test-basic` / `make test-light` Target — nur per `molecule test -s` erreichbar (S168#7)
- [ ] CrowdSec Whitelist nur auf Parser-Ebene — Bouncer `excludedIPRanges` wäre robuster (S168#6, Issue #83)
- [ ] Kein Rollback-Mechanismus — alte compose.yml wird überschrieben (S168#18)

## Dokumentation

- [ ] Authentik Erststart dauert 3-5 Min auf cx23 — Health-Check braucht 60 Retries à 5s (S215#5)
- [ ] Port 9000 nicht auf Host exposed — Health-Checks müssen über docker inspect/exec (S215#6)
- [ ] `invalidation_flow` ist Pflichtfeld für Proxy/OAuth2-Provider in 2025.2.4 (S215#8)
