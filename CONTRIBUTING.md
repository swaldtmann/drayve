# Contributing — Drayve

Thanks for your interest in Drayve! Contributions are welcome.

## How to Contribute

1. **Open an issue** — Found a bug or have a feature idea? Open an issue on Codeberg first.
2. **Fork + branch** — Fork the repo, create a feature branch (`feature/short-description`).
3. **Make changes** — Write code, add tests.
4. **Pull request** — Open a PR against `main`. Describe what and why.

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
make test-integration
```

## Checklist: New deploy/ directories

When adding a new `deploy/<name>/` directory, check for files that were committed before `.gitignore` existed. Use `git ls-files deploy/<name>/` to verify — tracked files bypass `.gitignore` silently. Remove with `git rm --cached` if needed.

## What We Don't Accept

- Changes that violate privacy principles
- Dependencies on proprietary cloud services
- Code without tests (for new features)

## License

By contributing, you agree that your contributions will be licensed under the Apache-2.0 License (see [LICENSE](LICENSE)).
