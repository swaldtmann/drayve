#!/usr/bin/env python3
"""List the container images the Molecule scenarios can pull (one ``name:tag`` per line).

Used to warm the Docker Hub pull-through cache of the CI runner VM: the cache
only helps for images that are in it, so the list has to be complete.

Sources (a superset of what a single scenario pulls; extra images only cost
cache space):

* ``*_image`` variables with a string value anywhere in
  ``ansible/inventory/group_vars/``, ``ansible/roles/*/defaults/`` and
  ``molecule/*/molecule.yml`` (scenario overrides)
* ``image:`` lines in ``ansible/templates/*.j2`` and ``ansible/roles/*/templates/*.j2``:
  a literal ref, ``{{ var | default('ref') }}`` (the default is listed, plus
  every value of ``var``), or ``{{ var }}`` (every value of ``var``)

Output: Docker Hub images by default; ``--other`` lists images from other
registries (the mirror only serves Docker Hub), ``--all`` both. Images without a
tag get ``:latest``; a leading ``docker.io/`` is dropped.

Usage: scripts/list-ci-images.py [--repo-root DIR] [--other | --all]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(:[A-Za-z0-9._-]+)?$")
_IMAGE_LINE = re.compile(r"^\s*image:\s*(.+?)\s*$")
_TEMPLATE_VAR = re.compile(r"^\{\{\s*(\w+)\s*(?:\|\s*default\(\s*(['\"])(.+?)\2\s*\)\s*)?\}\}$")


def normalize(ref: str) -> str:
    ref = ref.strip()
    if ref.startswith("docker.io/"):
        ref = ref[len("docker.io/"):]
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        ref += ":latest"
    return ref


def is_docker_hub(ref: str) -> bool:
    """Docker Hub unless the first path component names a registry host."""
    ref = ref.removeprefix("docker.io/")
    if "/" not in ref:
        return True
    first = ref.split("/", 1)[0]
    return not ("." in first or ":" in first or first == "localhost")


def _walk_image_vars(node: object, found: dict[str, set[str]]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.endswith("_image") and isinstance(value, str):
                found.setdefault(key, set()).add(value)
            else:
                _walk_image_vars(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_image_vars(item, found)


def _image_vars(root: Path) -> dict[str, set[str]]:
    files: list[Path] = []
    files += sorted((root / "ansible" / "inventory" / "group_vars").rglob("*.yml"))
    files += sorted(root.glob("ansible/roles/*/defaults/*.yml"))
    files += sorted(root.glob("molecule/*/molecule.yml"))
    found: dict[str, set[str]] = {}
    for f in files:
        _walk_image_vars(yaml.safe_load(f.read_text()), found)
    return found


def _template_files(root: Path) -> list[Path]:
    return sorted(root.glob("ansible/templates/*.j2")) + sorted(root.glob("ansible/roles/*/templates/*.j2"))


def collect_images(root: Path) -> set[str]:
    variables = _image_vars(root)
    raw: set[str] = {v for values in variables.values() for v in values}
    for tpl in _template_files(root):
        for line in tpl.read_text().splitlines():
            m = _IMAGE_LINE.match(line)
            if not m:
                continue
            expr = m.group(1)
            t = _TEMPLATE_VAR.match(expr)
            if t:
                if t.group(3):
                    raw.add(t.group(3))
                raw.update(variables.get(t.group(1), set()))
            elif "{{" not in expr:
                raw.add(expr)
    return {normalize(r) for r in raw if _REF.match(r)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--other", action="store_true", help="list images from registries other than Docker Hub")
    group.add_argument("--all", action="store_true", help="list Docker Hub and other images")
    args = ap.parse_args(argv)

    images = collect_images(args.repo_root)
    if args.other:
        chosen = {i for i in images if not is_docker_hub(i)}
    elif args.all:
        chosen = images
    else:
        chosen = {i for i in images if is_docker_hub(i)}
    for ref in sorted(chosen):
        print(ref)
    return 0


if __name__ == "__main__":
    sys.exit(main())
