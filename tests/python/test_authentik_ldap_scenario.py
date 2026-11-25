"""The authentik-ldap molecule scenario must use a user filter its LLDAP backend understands.

Release gate authentik-ldap failed with ``Admin group 'lldap_admin' not found in Authentik after
300s``. Cause: the 389-DS default ``user_filter`` names ``nsOrgPerson``; LLDAP's schema has no such
class, so authentik's ``ldap_sync`` task dies before the group sync with
``invalid class in objectClass attribute: nsOrgPerson`` and no group ever arrives. The 389-DS
default stays; the scenario overrides it for LLDAP.

Run: ``pytest tests/python/test_authentik_ldap_scenario.py``
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = REPO_ROOT / "molecule" / "authentik-ldap" / "molecule.yml"
LLDAP_OBJECT_CLASSES_UNKNOWN = ("nsOrgPerson", "nsPerson")


def _ldap_vars():
    cfg = yaml.safe_load(SCENARIO.read_text())
    return cfg["provisioner"]["inventory"]["group_vars"]["all"]["auth"]["ldap"]


def test_scenario_user_filter_avoids_389ds_only_object_classes():
    user_filter = _ldap_vars().get("user_filter", "")
    assert user_filter, "scenario must override auth.ldap.user_filter (389-DS default breaks LLDAP)"
    for cls in LLDAP_OBJECT_CLASSES_UNKNOWN:
        assert cls not in user_filter, f"LLDAP has no objectClass {cls}"
    assert "inetOrgPerson" in user_filter


def test_389ds_default_is_unchanged():
    schema = yaml.safe_load((REPO_ROOT / "config" / "stack.schema.yaml").read_text())
    text = yaml.dump(schema)
    assert "nsOrgPerson" in text
