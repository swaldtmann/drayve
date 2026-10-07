# Contributing — Drayve

Thanks for your interest in Drayve! Contributions are welcome.

## How to Contribute

This repository is a read-only mirror; there is no public issue tracker and no pull-request workflow.

1. **Report a bug or idea** — send an e-mail to **security@waldtmann.de** (the same address as in [SECURITY.md](SECURITY.md)). Security issues: follow [SECURITY.md](SECURITY.md) and do not post them publicly.
2. **Send a patch** — attach a `git format-patch` series against `main` to that e-mail. Describe what and why.

## Git Conventions

### Branches

`feature/short-description` or `fix/short-description`

### Commit Messages

Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`

## QA

### Lint + Validate

```bash
make lint                  # Lint playbooks + roles
make test                  # Lint + validate all examples
make validate NAME=myhost  # Validate stack.yaml for a host
```

### Role Tests (Molecule + Hetzner)

```bash
cp .env.test.example .env.test
# Edit .env.test — add your HCLOUD_TOKEN

make test-role NAME=common
```

### Scenario Tests (Molecule + Incus)

```bash
make test-integration   # local Incus VM; see docs/testing.md
```

## Checklist: New deploy/ directories

When adding a new `deploy/<name>/` directory, check for files that were committed before `.gitignore` existed. Use `git ls-files deploy/<name>/` to verify — tracked files bypass `.gitignore` silently. Remove with `git rm --cached` if needed.

## What We Don't Accept

- Changes that violate privacy principles
- Dependencies on proprietary cloud services
- Code without tests (for new features)

## License

By contributing, you agree that your contributions will be licensed under the Apache-2.0 License (see [LICENSE](LICENSE)).
