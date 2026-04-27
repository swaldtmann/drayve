#!/usr/bin/env python3
"""Validate a stack.yaml against the Drayve schema."""

import sys
import yaml
import re

SCHEMA_PATH = "config/stack.schema.yaml"

ENUMS = {
    "drayve_version": ["0.1.0"],
    "provider.type": ["hetzner", "manual"],
    "auth.provider": ["none", "basic", "authelia", "authentik"],
    "monitoring.profile": ["full", "light", "none"],
    "secrets.mode": ["quickstart", "sops"],
    "backup.target": ["storagebox", "s3", "local", "ssh"],
}

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
PLACEHOLDER_EMAILS = {"you@example.com", "admin@example.com", ""}


def get_nested(data, path):
    """Get a value from nested dict by dot-path."""
    keys = path.split(".")
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def validate(config):
    errors = []
    warnings = []

    # Required: drayve_version
    if "drayve_version" not in config:
        errors.append("Missing required field: drayve_version")
    elif str(config["drayve_version"]) not in ENUMS["drayve_version"]:
        errors.append(
            f"Invalid drayve_version: '{config['drayve_version']}' "
            f"(allowed: {ENUMS['drayve_version']})"
        )

    # Required: stack.name, stack.domain
    stack = config.get("stack")
    if not isinstance(stack, dict):
        errors.append("Missing required section: stack")
    else:
        if "name" not in stack:
            errors.append("Missing required field: stack.name")
        elif not NAME_PATTERN.match(str(stack["name"])):
            errors.append(
                f"Invalid stack.name: '{stack['name']}' "
                f"(must match ^[a-z][a-z0-9-]*$)"
            )
        if "domain" not in stack:
            errors.append("Missing required field: stack.domain")

    # Enum validations
    for path, allowed in ENUMS.items():
        if path == "drayve_version":
            continue
        val = get_nested(config, path)
        if val is not None and str(val) not in allowed:
            errors.append(f"Invalid {path}: '{val}' (allowed: {allowed})")

    # acme_email: warn if missing or placeholder
    acme_email = get_nested(config, "stack.acme_email") or ""
    if str(acme_email).strip() in PLACEHOLDER_EMAILS:
        warnings.append(
            "stack.acme_email is empty or a placeholder — "
            "Let's Encrypt will reject certificate requests without a valid email"
        )

    # lldap only with authelia
    auth = config.get("auth", {})
    if auth.get("lldap") and auth.get("provider", "basic") != "authelia":
        errors.append(
            "auth.lldap=true requires auth.provider=authelia"
        )

    # auth.ldap only with authentik
    if auth.get("ldap") and auth.get("provider", "basic") != "authentik":
        errors.append(
            "auth.ldap requires auth.provider=authentik"
        )

    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <stack.yaml>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    try:
        with open(path) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in {path}: {e}", file=sys.stderr)
        sys.exit(1)

    if config is None:
        print(f"Error: {path} is empty", file=sys.stderr)
        sys.exit(1)

    errors, warnings = validate(config)
    if errors:
        print(f"Validation failed for {path}:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    is_example = "example" in path.lower()
    for warn in warnings:
        if not is_example:
            print(f"  WARNING: {warn}")

    print(f"OK: {path} is valid (drayve_version={config['drayve_version']})")


if __name__ == "__main__":
    main()
