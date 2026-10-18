# Contributing — Drayve

Thanks for your interest in Drayve! Contributions are welcome.

## How to Contribute

This repository is a read-only mirror; there is no public issue tracker and no pull-request workflow.
Pull requests opened on the mirror are not merged here — the mirror is overwritten from the source of truth. Please send patches by e-mail instead.

1. **Report a bug or idea** — send an e-mail to **opensource@waldtmann.de**. Security issues go to **security@waldtmann.de** instead, see [SECURITY.md](SECURITY.md); do not post them publicly.
2. **Send a patch** — attach a `git format-patch` series against `main` to an e-mail to **opensource@waldtmann.de**. Describe what and why.

## Git Conventions

### Branches

`feature/short-description` or `fix/short-description`

### Commit Messages

Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`

### Git Hooks (optional)

`make hooks` enables the maintainer's hooks: a `commit-msg` hook that requires a ticket reference (or the literal `[no-ticket]`) in the commit message, and a `pre-push` hook that runs `make test`. You do not need them to contribute; `make setup` does not install them.

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
