#!/usr/bin/env python3
"""Drayve Label Scanner.

Scans compose files for Docker labels used by the Drayve frame:
- traefik.* — routing, TLS, middleware
- drayve.landing.* — landing page entries
- drayve.monitoring.* — custom Grafana dashboards

Runs on the server at deploy time. Output: JSON to stdout.

Usage:
    python3 label-scanner.py /opt/drayve/deploy/stack
"""

import json
import os
import subprocess
import sys
import yaml


def scan_compose_file(path):
    """Parse a compose file and extract drayve/traefik labels per service."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        print(f"WARNING: Failed to parse {path}: {e}", file=sys.stderr)
        return []

    if not data or "services" not in data:
        return []

    results = []
    for svc_name, svc_config in data.get("services", {}).items():
        labels_raw = svc_config.get("labels", [])

        # Labels can be a list of "key=value" or a dict
        labels = {}
        if isinstance(labels_raw, list):
            for item in labels_raw:
                item = str(item).strip().strip('"').strip("'")
                if "=" in item:
                    k, v = item.split("=", 1)
                    labels[k.strip()] = v.strip()
        elif isinstance(labels_raw, dict):
            labels = {str(k): str(v) for k, v in labels_raw.items()}

        # Extract drayve.* and relevant traefik.* labels
        drayve_labels = {}
        traefik_labels = {}
        for k, v in labels.items():
            if k.startswith("drayve."):
                drayve_labels[k] = v
            elif k.startswith("traefik."):
                traefik_labels[k] = v

        # Skip services with no drayve labels and no traefik routing
        has_drayve = bool(drayve_labels)
        has_traefik_router = any("routers" in k and "rule" in k for k in traefik_labels)

        if has_drayve or has_traefik_router:
            entry = {
                "service": svc_name,
                "source": path,
                "labels": {**drayve_labels, **traefik_labels},
            }

            # Extract structured landing page info
            if drayve_labels.get("drayve.landing.name"):
                entry["landing"] = {
                    "name": drayve_labels.get("drayve.landing.name"),
                    "icon": drayve_labels.get("drayve.landing.icon", "&#x1F4E6;"),
                    "category": drayve_labels.get("drayve.landing.category", "Apps"),
                    "description": drayve_labels.get("drayve.landing.description", ""),
                }

            # Extract URL from traefik Host rule
            for k, v in traefik_labels.items():
                if "routers" in k and "rule" in k and "Host(" in v:
                    # Extract host from Host(`domain.tld`)
                    host = v.split("Host(`")[1].split("`)")[0] if "Host(`" in v else ""
                    if host:
                        entry["url"] = f"https://{host}"
                        if "landing" in entry:
                            entry["landing"]["url"] = entry["url"]
                    break

            # Extract middleware (for auth-sync)
            for k, v in traefik_labels.items():
                if "middlewares" in k:
                    entry["middlewares"] = v
                    break

            # Extract dashboard name
            dashboard = drayve_labels.get("drayve.monitoring.dashboard")
            if dashboard:
                entry["dashboard"] = dashboard

            results.append(entry)

    return results


def scan_directory(base_dir):
    """Scan frame/ and services/ for compose files."""
    all_entries = []
    scan_paths = []

    # Frame compose files
    frame_dir = os.path.join(base_dir, "frame")
    if os.path.isdir(frame_dir):
        for subdir in sorted(os.listdir(frame_dir)):
            for fname in ["compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"]:
                path = os.path.join(frame_dir, subdir, fname)
                if os.path.isfile(path):
                    scan_paths.append(path)
                    break

    # Service compose files
    services_dir = os.path.join(base_dir, "services")
    if os.path.isdir(services_dir):
        for subdir in sorted(os.listdir(services_dir)):
            for fname in ["compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"]:
                path = os.path.join(services_dir, subdir, fname)
                if os.path.isfile(path):
                    scan_paths.append(path)
                    break

    # Also scan the main docker-compose.yml (frame services defined by Ansible)
    main_compose = os.path.join(base_dir, "docker-compose.yml")
    if os.path.isfile(main_compose):
        scan_paths.append(main_compose)

    for path in scan_paths:
        entries = scan_compose_file(path)
        all_entries.extend(entries)

    return all_entries


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <deploy-dir>", file=sys.stderr)
        sys.exit(1)

    base_dir = sys.argv[1]
    if not os.path.isdir(base_dir):
        print(f"Error: {base_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    entries = scan_directory(base_dir)

    # Output grouped by category
    output = {
        "services": entries,
        "landing": [e["landing"] for e in entries if "landing" in e],
        "auth_sync": [
            {"service": e["service"], "url": e.get("url", ""), "middlewares": e.get("middlewares", "")}
            for e in entries
            if "authentik@file" in e.get("middlewares", "")
        ],
        "dashboards": [
            {"service": e["service"], "dashboard": e["dashboard"]}
            for e in entries
            if "dashboard" in e
        ],
    }

    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
