#!/usr/bin/env python3
"""Inject ``traefik.docker.network`` labels for multi-network services.

Background (W-112): when a service is attached to more than one Docker
network and a Traefik docker-provider routes traffic to it, Traefik picks
one of the backend IPs at random. If the picked IP belongs to an
internal-only network (e.g. an isolated DB net), the request hangs at the
TCP layer — the well-known "gateway timeout but the service is healthy"
symptom. Production case: rezepte.ewaldshof.de, ~5.5h downtime.

The fix is to set ``traefik.docker.network=<primary>`` on the service so
Traefik knows which network to route through. This script does that
automatically for any user-supplied compose override that drayve deploys.

Behaviour:

* Services with fewer than two networks → untouched.
* Services without ``traefik.enable=true`` → untouched (non-traefik).
* Services that already define ``traefik.docker.network=...`` → untouched.
* Otherwise: inject ``traefik.docker.network=<primary>`` and emit a
  one-line note to stderr so deploys log the change.

The primary network defaults to ``drayve_default`` — the implicit network
of drayve's main ``docker-compose.yml`` (compose project name ``drayve``).
Override via ``--primary <name>`` or env ``DRAYVE_PRIMARY_NET``.

Idempotent: re-running on a fixed file is a no-op.

Usage::

    multinet-label-fix.py [--primary drayve_default] [--write] FILE

Exit codes:
    0  success (file unchanged or rewritten)
    2  parse / IO error
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml


DEFAULT_PRIMARY_NET = "drayve_default"


def _is_traefik_enabled(labels: Any) -> bool:
    """Detect whether a service has the ``traefik.enable=true`` label.

    Compose accepts labels both as a list (``- key=value``) and as a dict
    (``key: value``). Handle both.
    """
    if labels is None:
        return False
    if isinstance(labels, dict):
        val = labels.get("traefik.enable")
        return str(val).lower() == "true"
    if isinstance(labels, list):
        for item in labels:
            if not isinstance(item, str):
                continue
            if item.strip().lower().startswith("traefik.enable="):
                _, _, value = item.partition("=")
                return value.strip().lower() == "true"
            if item.strip().lower() == "traefik.enable":
                # bare flag form — uncommon but valid
                return True
    return False


def _has_explicit_traefik_network(labels: Any) -> bool:
    """Return True iff the user already pinned the traefik network."""
    if labels is None:
        return False
    if isinstance(labels, dict):
        return any(str(k).startswith("traefik.docker.network") for k in labels)
    if isinstance(labels, list):
        for item in labels:
            if isinstance(item, str) and item.strip().startswith(
                "traefik.docker.network"
            ):
                return True
    return False


def _network_count(networks: Any) -> int:
    """Count distinct networks the service joins. None / empty → 0."""
    if networks is None:
        return 0
    if isinstance(networks, (list, dict)):
        return len(networks)
    return 0


def _inject_label(labels: Any, primary_net: str) -> Any:
    """Return a new labels structure with the primary-net label appended.

    Preserves the original structural form (list vs dict).
    """
    label = f"traefik.docker.network={primary_net}"
    if labels is None:
        return [label]
    if isinstance(labels, list):
        return [*labels, label]
    if isinstance(labels, dict):
        new = dict(labels)
        new["traefik.docker.network"] = primary_net
        return new
    raise TypeError(f"Unsupported labels type: {type(labels)!r}")


def fix_compose(
    data: dict, primary_net: str = DEFAULT_PRIMARY_NET
) -> tuple[dict, list[str]]:
    """Mutate compose data in place and return ``(data, changed_services)``.

    The original ``data`` object is modified directly; the return is a
    convenience for callers that prefer expression form.
    """
    services = data.get("services") or {}
    if not isinstance(services, dict):
        raise ValueError(
            f"compose 'services' is {type(services).__name__}, expected mapping"
        )
    changed: list[str] = []
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        if _network_count(svc.get("networks")) < 2:
            continue
        if not _is_traefik_enabled(svc.get("labels")):
            continue
        if _has_explicit_traefik_network(svc.get("labels")):
            continue
        svc["labels"] = _inject_label(svc.get("labels"), primary_net)
        changed.append(name)
    return data, changed


def _load_yaml(path: Path) -> dict:
    with path.open() as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML is not a mapping")
    return data


def _dump_yaml(data: dict, path: Path) -> None:
    with path.open("w") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "file",
        type=Path,
        help="Path to docker-compose.override.yml",
    )
    parser.add_argument(
        "--primary",
        default=os.environ.get("DRAYVE_PRIMARY_NET", DEFAULT_PRIMARY_NET),
        help=f"Primary Traefik network (default: {DEFAULT_PRIMARY_NET})",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write changes back to the file (default: dry-run)",
    )
    args = parser.parse_args(argv)

    if not args.file.exists():
        # No override file → nothing to do. Not an error.
        return 0

    try:
        data = _load_yaml(args.file)
    except (yaml.YAMLError, ValueError) as exc:
        print(f"multinet-label-fix: parse error: {exc}", file=sys.stderr)
        return 2

    try:
        _, changed = fix_compose(data, primary_net=args.primary)
    except (TypeError, ValueError) as exc:
        print(f"multinet-label-fix: parse error: {exc}", file=sys.stderr)
        return 2

    if not changed:
        return 0

    msg = (
        f"multinet-label-fix: injecting traefik.docker.network={args.primary} "
        f"on {len(changed)} service(s): {', '.join(changed)}"
    )
    print(msg, file=sys.stderr)

    if args.write:
        try:
            _dump_yaml(data, args.file)
        except OSError as exc:
            print(f"multinet-label-fix: write error: {exc}", file=sys.stderr)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
