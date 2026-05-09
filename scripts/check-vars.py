#!/usr/bin/env python3
"""
Pre-Inventur: greppt Jinja-Var-Refs in ansible/ + molecule/ und checkt
ob sie irgendwo definiert sind (defaults, group_vars, set_fact, vars,
register, role-args, loop_var).

Lehre aus der Phase-1-Cascade S328: Variable referenziert, aber nirgends
definiert — schlug erst auf Hetzner auf. Dieser Check faengt die Klasse
lokal.

References mit `| default(...)` oder `is defined`/`is not defined` werden
als guarded gewertet und nicht gemeldet.

Usage:
    python3 scripts/check-vars.py [--whitelist FILE]

Exit:
    0 — alle Refs definiert, guarded, oder whitelisted
    1 — undefined Refs gefunden (Liste auf stderr)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml fehlt. pip install pyyaml", file=sys.stderr)
    sys.exit(2)

# Ansible-magic + Jinja-builtins.
MAGIC = {
    "ansible_facts", "ansible_loop", "ansible_play_batch",
    "ansible_play_hosts", "ansible_play_hosts_all", "ansible_play_name",
    "ansible_run_tags", "ansible_search_path", "ansible_skip_tags",
    "ansible_version", "groups", "group_names", "hostvars",
    "inventory_dir", "inventory_file", "inventory_hostname",
    "inventory_hostname_short", "play_hosts", "playbook_dir",
    "role_name", "role_names", "role_path", "ansible_become_user",
    "ansible_user", "ansible_host", "ansible_port", "ansible_ssh_user",
    "ansible_python_interpreter", "ansible_distribution",
    "ansible_distribution_release", "ansible_distribution_version",
    "ansible_os_family", "ansible_architecture", "ansible_hostname",
    "ansible_fqdn", "ansible_default_ipv4", "ansible_all_ipv4_addresses",
    "ansible_env", "ansible_user_id", "ansible_user_uid",
    "ansible_user_gid", "ansible_user_dir", "ansible_user_shell",
    "ansible_processor_count", "ansible_processor_vcpus",
    "ansible_memtotal_mb", "ansible_date_time", "ansible_kernel",
    "ansible_pkg_mgr", "ansible_service_mgr", "ansible_virtualization_type",
    "item", "ansible_loop_var", "loop", "vars", "environment",
    "lookup", "query", "q", "now", "true", "false", "none", "True",
    "False", "None", "omit", "undef", "ansible_check_mode",
    "ansible_diff_mode", "ansible_verbosity", "ansible_forks",
    "ansible_inventory_sources", "ansible_module_name",
    "ansible_python", "molecule_yml", "molecule_scenario",
    "molecule_ephemeral_directory", "molecule_instance_config",
    "ansible_managed", "range", "end",
}

VAR_REF_GUARD = re.compile(
    # Var-Ref + optional ggf direkter `| default(...)` oder `is (not )?defined`.
    r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)"
    r"(?P<guard>\s*\|\s*default\b|\s+is\s+(?:not\s+)?defined\b)?"
)

# Jinja-Schleifen: {% for X in ... %} oder {% for X, Y in ... %}
JINJA_FOR = re.compile(
    r"\{%\s*for\s+([a-zA-Z_][a-zA-Z0-9_]*)"
    r"(?:\s*,\s*([a-zA-Z_][a-zA-Z0-9_]*))?\s+in\b"
)
# Jinja {% set X = ... %}
JINJA_SET = re.compile(r"\{%\s*set\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=")


def load_yaml_safe(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None


def walk_definitions(node: Any, defs: set[str]) -> None:
    """Rekursiv durch yaml-AST: set_fact, register, vars, loop_var."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("set_fact", "ansible.builtin.set_fact"):
                if isinstance(v, dict):
                    for kk in v:
                        if isinstance(kk, str) and kk != "cacheable":
                            defs.add(kk)
            elif k == "register" and isinstance(v, str):
                defs.add(v)
            elif k == "vars" and isinstance(v, dict):
                defs.update(kk for kk in v if isinstance(kk, str))
            elif k == "loop_var" and isinstance(v, str):
                defs.add(v)
            elif k == "vars_from" and isinstance(v, str):
                pass  # role-include
            elif k == "name" and isinstance(v, str):
                pass
            walk_definitions(v, defs)
    elif isinstance(node, list):
        for item in node:
            walk_definitions(item, defs)


def collect_definitions(root: Path) -> set[str]:
    defs: set[str] = set()

    # Roles defaults + vars (top-level keys)
    for sub in ("defaults", "vars"):
        for d in (root / "ansible" / "roles").glob(f"*/{sub}/main.yml"):
            data = load_yaml_safe(d)
            if isinstance(data, dict):
                defs.update(k for k in data if isinstance(k, str))

    # Group_vars
    for d in (root / "ansible" / "inventory" / "group_vars").glob("**/*.yml"):
        data = load_yaml_safe(d)
        if isinstance(data, dict):
            defs.update(k for k in data if isinstance(k, str))

    # Alle yml in ansible/ + molecule/ rekursiv durchgehen
    for base in ("ansible", "molecule"):
        for f in (root / base).glob("**/*.yml"):
            if "/.venv/" in str(f):
                continue
            data = load_yaml_safe(f)
            walk_definitions(data, defs)

    # Jinja {% for X in ... %} und {% set X = ... %} aus allen yml-Files
    for base in ("ansible", "molecule"):
        for f in (root / base).glob("**/*.yml"):
            if "/.venv/" in str(f):
                continue
            try:
                txt = f.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for m in JINJA_FOR.finditer(txt):
                defs.add(m.group(1))
                if m.group(2):
                    defs.add(m.group(2))
            defs.update(JINJA_SET.findall(txt))

    # Molecule provisioner.inventory.group_vars.all (inline)
    for f in (root / "molecule").glob("*/molecule.yml"):
        data = load_yaml_safe(f)
        if isinstance(data, dict):
            prov = data.get("provisioner", {})
            inv = prov.get("inventory", {}) if isinstance(prov, dict) else {}
            gv = inv.get("group_vars", {}) if isinstance(inv, dict) else {}
            for grp in gv.values() if isinstance(gv, dict) else []:
                if isinstance(grp, dict):
                    defs.update(k for k in grp if isinstance(k, str))
            host_vars = inv.get("host_vars", {}) if isinstance(inv, dict) else {}
            for h in host_vars.values() if isinstance(host_vars, dict) else []:
                if isinstance(h, dict):
                    defs.update(k for k in h if isinstance(k, str))

    return defs


def collect_refs(root: Path) -> dict[str, list[tuple[str, int]]]:
    refs: dict[str, list[tuple[str, int]]] = {}
    for base in ("ansible", "molecule"):
        for f in (root / base).glob("**/*.yml"):
            if "/.venv/" in str(f):
                continue
            try:
                txt = f.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for ln, line in enumerate(txt.splitlines(), 1):
                for m in VAR_REF_GUARD.finditer(line):
                    if m.group("guard"):
                        continue  # default/is defined → guarded
                    name = m.group(1)
                    refs.setdefault(name, []).append(
                        (str(f.relative_to(root)), ln)
                    )
    return refs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Repo-Root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        help="Datei mit Whitelist-Vars, eine pro Zeile (# = Kommentar)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Nur Exit-Code, kein Output",
    )
    args = parser.parse_args()
    root = Path(args.root)

    extra: set[str] = set()
    wl_path = args.whitelist or root / ".ci" / "check-vars-whitelist.txt"
    if wl_path.exists():
        for line in wl_path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                extra.add(line)

    defs = collect_definitions(root)
    refs = collect_refs(root)

    allowed = MAGIC | defs | extra
    undef = {k: v for k, v in refs.items() if k not in allowed}

    if not undef:
        if not args.quiet:
            print(f"OK — {len(refs)} unique refs, {len(defs)} definitions, "
                  f"all references resolved or guarded.")
        return 0

    if args.quiet:
        return 1

    print(f"FAIL — {len(undef)} undefined references:", file=sys.stderr)
    for name in sorted(undef):
        loc = undef[name][0]
        more = f" (+{len(undef[name]) - 1} more)" if len(undef[name]) > 1 else ""
        print(f"  {name:40s} {loc[0]}:{loc[1]}{more}", file=sys.stderr)
    print(
        f"\nIf legitimate (CLI -e var, secrets.yml lookup, dynamic include),\n"
        f"add to {wl_path.relative_to(root)} (one per line, # = comment).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
