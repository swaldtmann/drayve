"""Validation of auth.access_control, auth.users and auth.remote.

One implementation for two callers:

  - scripts/validate-stack.py (``make validate``) imports ``check_auth`` by path.
  - ansible/tasks/auth_preflight.yml uses the filters ``drayve_auth_errors`` and
    ``drayve_auth_warnings`` (Ansible loads this directory automatically because
    it sits next to the playbooks).

Everything here is fail-closed: a value that the Authelia template would turn
into something other than what the operator wrote (a string iterated
character by character, a typo'd group that makes a rule never match, a portal
URL used as forward-auth endpoint) aborts instead.

Messages name the key path. They never echo template syntax from the input and
never see password hashes (those live in secrets.yml).
"""

from __future__ import annotations

from urllib.parse import urlsplit

POLICIES = ("deny", "one_factor", "two_factor", "bypass")
USER_KEYS = ("name", "displayname", "email", "groups")
FORWARD_AUTH_PATH = "/api/authz/forward-auth"
TEMPLATE_MARKERS = ("{{", "{%")


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _has_template(value: str) -> bool:
    return any(marker in value for marker in TEMPLATE_MARKERS)


def _check_template_syntax(value, where, errors) -> None:
    """Reject Jinja markers; never echo the offending value."""
    strings = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    if any(isinstance(s, str) and _has_template(s) for s in strings):
        errors.append(
            f"{where} contains template syntax ('{{{{' or '{{%'), which is not allowed "
            "in this key"
        )


def _check_policy(value, where, errors) -> None:
    if not isinstance(value, str) or value not in POLICIES:
        errors.append(
            f"{where} must be exactly one of {', '.join(POLICIES)} (lower case), got {value!r}"
        )


def _check_str_list(rule, key, where, errors) -> None:
    if key in rule and not _is_str_list(rule[key]):
        errors.append(
            f"{where}.{key} must be a list of strings (a plain string is an error, "
            f"write [\"...\"]), got {type(rule[key]).__name__}"
        )


def _check_rules(ac, auth, errors, warnings) -> None:
    rules = ac.get("rules")
    if rules is None:
        return
    if not isinstance(rules, list):
        errors.append("auth.access_control.rules must be a list")
        return

    if rules and "default_policy" not in ac:
        errors.append(
            "auth.access_control.default_policy is required as soon as rules are set: "
            "a request that matches no rule falls back to the default, and a silent "
            "one_factor default downgrades every rule that fails to match (typo in a "
            "group, wildcard that does not cover the bare domain). Set it explicitly, "
            "deny is the recommended value."
        )

    for i, rule in enumerate(rules):
        where = f"auth.access_control.rules[{i}]"
        if not isinstance(rule, dict):
            errors.append(f"{where} must be a mapping")
            continue

        domains = None
        if "domain" not in rule:
            errors.append(f"{where}.domain is required")
        else:
            d = rule["domain"]
            if isinstance(d, str):
                domains = [d]
            elif _is_str_list(d) and d:
                domains = d
            else:
                errors.append(f"{where}.domain must be a string or a non-empty list of strings")
            _check_template_syntax(d, f"{where}.domain", errors)

        if "policy" not in rule:
            errors.append(f"{where}.policy is required")
        else:
            _check_policy(rule["policy"], f"{where}.policy", errors)

        for key in ("groups", "resources", "networks"):
            _check_str_list(rule, key, where, errors)
            if key in rule:
                _check_template_syntax(rule[key], f"{where}.{key}", errors)

        _rule_warnings(rule, where, domains, warnings)


def _rule_warnings(rule, where, domains, warnings) -> None:
    bypass = rule.get("policy") == "bypass"
    groups = rule.get("groups") if _is_str_list(rule.get("groups")) else []
    resources = rule.get("resources") if _is_str_list(rule.get("resources")) else []
    names = {d.lower() for d in domains or []}

    wildcards = [d for d in names if d.startswith("*.")]
    if bypass and not groups and not resources and wildcards:
        warnings.append(
            f"{where}: bypass on a wildcard domain ({', '.join(sorted(wildcards))}) without "
            "groups or resources opens every host below it without login"
        )
    for w in sorted(wildcards):
        if w[2:] not in names:
            warnings.append(
                f"{where}.domain: '{w}' does not cover '{w[2:]}' itself; add '{w[2:]}' to "
                "the same rule if the bare domain is meant to be covered"
            )
    if bypass:
        for res in resources:
            if not res.startswith("^"):
                warnings.append(
                    f"{where}.resources: bypass regex is not anchored with '^' and also "
                    "matches that text later in the path or query (e.g. '^/public/' "
                    "instead of '/public')"
                )
                break


def _check_users(auth, errors) -> list | None:
    users = auth.get("users")
    if users is None:
        return None
    if not isinstance(users, list):
        errors.append("auth.users must be a list")
        return None
    seen: dict[str, str] = {}
    for i, user in enumerate(users):
        where = f"auth.users[{i}]"
        if not isinstance(user, dict):
            errors.append(f"{where} must be a mapping")
            continue
        for key in user:
            if key not in USER_KEYS:
                errors.append(
                    f"{where}.{key} is not supported (allowed: {', '.join(USER_KEYS)}); "
                    "to lock a user out, remove the entry from auth.users "
                    "(there is no 'disabled' switch)"
                )
        name = user.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{where}.name must be a non-empty string")
        else:
            _check_template_syntax(name, f"{where}.name", errors)
            folded = name.lower()
            if folded in seen:
                errors.append(
                    f"{where}.name '{name}' duplicates '{seen[folded]}' (user names must "
                    "be unique ignoring case)"
                )
            else:
                seen[folded] = name
        if "groups" in user:
            if not _is_str_list(user["groups"]):
                errors.append(f"{where}.groups must be a list of strings")
            else:
                _check_template_syntax(user["groups"], f"{where}.groups", errors)
    return users


def _check_group_existence(ac, users, errors) -> None:
    rules = ac.get("rules")
    if not isinstance(rules, list):
        return
    known = set()
    for u in users or []:
        if isinstance(u, dict) and _is_str_list(u.get("groups")):
            known.update(u["groups"])
    folded = {}
    for g in known:
        folded.setdefault(g.lower(), g)
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict) or not _is_str_list(rule.get("groups")):
            continue
        for g in rule["groups"]:
            if _has_template(g) or g in known:
                continue
            msg = (
                f"auth.access_control.rules[{i}].groups: group '{g}' is not a group of any "
                "entry in auth.users (file backend, case-sensitive), so this rule can "
                "never match"
            )
            if g.lower() in folded:
                msg += f"; did you mean '{folded[g.lower()]}'? (group names differ in case)"
            errors.append(msg)


def _check_remote(auth, errors, warnings) -> None:
    remote = auth.get("remote")
    remote = remote if isinstance(remote, dict) else {}
    fwd = remote.get("forward_auth_url")
    portal = remote.get("portal_url")

    fwd_host = None
    if fwd and isinstance(fwd, str) and fwd.startswith("https://"):
        parts = urlsplit(fwd)
        fwd_host = parts.hostname
        if parts.path != FORWARD_AUTH_PATH:
            errors.append(
                "auth.remote.forward_auth_url must point at the Authelia forward-auth "
                f"endpoint with the path {FORWARD_AUTH_PATH} (got path {parts.path or '/'!r}); "
                "the portal root or /api/health answers 200 to everyone and would let "
                "all requests through"
            )

    if portal:
        if not isinstance(portal, str) or not portal.startswith("https://"):
            errors.append(f"auth.remote.portal_url must start with https://: {portal!r}")
        elif fwd_host:
            p_host = urlsplit(portal).hostname or ""
            labels = p_host.split(".")
            parent = ".".join(labels[1:]) if len(labels) > 2 else p_host
            if not (
                fwd_host == p_host
                or fwd_host.endswith("." + p_host)
                or fwd_host.endswith("." + parent)
            ):
                warnings.append(
                    f"auth.remote.forward_auth_url host '{fwd_host}' is not below the host "
                    f"or domain of auth.remote.portal_url ('{p_host}'); check that this "
                    "really is the Authelia of the portal"
                )


def check_auth(auth) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for the ``auth`` section of a stack."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(auth, dict):
        return errors, warnings
    provider = auth.get("provider", "basic")

    if provider == "authelia_remote":
        _check_remote(auth, errors, warnings)

    if provider == "authelia":
        ac = auth.get("access_control")
        users = _check_users(auth, errors)
        if ac is not None:
            if not isinstance(ac, dict):
                errors.append("auth.access_control must be a mapping")
            else:
                if "default_policy" in ac:
                    _check_policy(ac["default_policy"], "auth.access_control.default_policy", errors)
                _check_rules(ac, auth, errors, warnings)
                if not _truthy(auth.get("lldap", False)):
                    _check_group_existence(ac, users, errors)
    return errors, warnings


# ---- Ansible filter plugin ---------------------------------------------------------


def drayve_auth_errors(auth):
    return check_auth(auth)[0]


def drayve_auth_warnings(auth):
    return check_auth(auth)[1]


class FilterModule:
    def filters(self):
        return {
            "drayve_auth_errors": drayve_auth_errors,
            "drayve_auth_warnings": drayve_auth_warnings,
        }
