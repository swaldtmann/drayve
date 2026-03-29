# drayve

Generic Ops framework for Docker stacks. Provision, deploy, monitor, secure, backup — from a single `stack.yaml`.

> Work in Progress — Phase 0a (Fundament)

## Quick Start

```bash
# Python dependencies (for validate, lint)
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml ansible ansible-lint

cp examples/stack-minimal.yaml stack.yaml
# Edit stack.yaml to match your server
make validate
make provision NAME=myserver DOMAIN=example.com
```

## stack.yaml

All configuration lives in one file. See `config/stack.schema.yaml` for the full schema.

```yaml
drayve_version: "0.1.0"
stack:
  name: myserver
  domain: example.com
auth:
  provider: basic          # none | basic | authelia
monitoring:
  profile: light           # full | light | none
secrets:
  mode: quickstart         # quickstart | sops
```

## Make Targets

```
make help                  Show all targets
make validate              Validate stack.yaml
make provision             Provision new server (NAME= DOMAIN=)
make deploy-dev            Deploy to dev
make deploy-prod           Deploy to prod (REF=)
make burn                  Tear down server (NAME=)
make status                Server status (NAME=)
make secrets-init          Scaffold AGE key + SOPS config
make secrets-template      Scaffold SOPS secrets (NAME=)
make backup                Deploy backup config
```

## Examples

- `examples/stack-minimal.yaml` — Basic auth, light monitoring, quickstart secrets
- `examples/stack-full.yaml` — Authelia + LLDAP, full monitoring, SOPS secrets

## License

Apache 2.0
