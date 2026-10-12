"""Fixtures for the ``authelia-file`` scenario: VM access, sign-in client, TOTP, redeploys.

The VM address comes from the Molecule inventory (``ansible_host``); set
``AUTHELIA_FILE_VM_IP`` to override it (e.g. when debugging a kept VM).
"""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import tempfile
import time
from collections import namedtuple
from pathlib import Path

import authelia_probe as ap
import pytest

SCENARIO_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCENARIO_DIR.parents[1]
STACK_DIR = "/opt/drayve/deploy/stack"
AUTHELIA_DIR = f"{STACK_DIR}/authelia"
AUTHELIA_CONFIG_ARGS = "--config /config/configuration.yml"

Container = namedtuple("Container", "id started_at restart_count running")
Deployed = namedtuple("Deployed", "stdout changed recap")

# One generator per TOTP secret for the whole pytest run: Authelia rejects a code
# it already accepted, also across test modules.
_TOTP_GENERATORS: dict[str, ap.TotpGenerator] = {}
# TOTP registrations made by ensure_totp, per user (the secret never changes afterwards).
_TOTP_CONFIGS: dict[str, dict] = {}


class Vm:
    def __init__(self, host):
        self.host = host
        try:
            variables = host.ansible.get_variables()
        except Exception:  # no ansible backend (e.g. run by hand with --hosts)
            variables = {}
        self.ip = os.environ.get("AUTHELIA_FILE_VM_IP") or variables.get("ansible_host") or host.backend.get_hostname()
        try:
            ipaddress.ip_address(self.ip)
        except ValueError:  # an inventory name, not an address: ask the VM
            self.ip = self.sh("hostname -I").split()[0]
        self.domain = variables.get("drayve_domain", "test.drayve.local")
        self.grafana_secret_initial = variables.get("authelia_oidc_grafana_secret", "")
        # The VM's clock decides which one-time code is valid.
        remote = int(self.sh("date +%s").strip())
        self.clock_offset = remote - time.time()

    def run(self, command: str):
        return self.host.run(command)

    def sh(self, command: str) -> str:
        result = self.host.run(command)
        assert result.rc == 0, f"`{command}` failed (rc={result.rc}): {result.stdout[-300:]} {result.stderr[-300:]}"
        return result.stdout

    def file_text(self, path: str) -> str:
        return self.host.file(path).content_string

    def container(self, name: str) -> Container:
        out = self.sh(f"docker inspect {name} --format '{{{{.Id}}}} {{{{.State.StartedAt}}}} "
                      f"{{{{.RestartCount}}}} {{{{.State.Running}}}}'").split()
        return Container(out[0], out[1], int(out[2]), out[3] == "true")

    def logs(self, name: str, tail: int = 25) -> str:
        r = self.run(f"docker logs --tail {tail} {name} 2>&1")
        return r.stdout

    def registered_totp(self, user: str) -> dict | None:
        """The user's TOTP registration as the CLI exports it from the database, or None."""
        r = self.run(f"docker exec authelia authelia storage user totp export uri {AUTHELIA_CONFIG_ARGS}")
        return ap.parse_otpauth_uri(r.stdout + r.stderr, user=user)

    def ensure_totp(self, user: str) -> dict:
        """Register TOTP for ``user`` through Authelia's CLI unless it exists, and return its settings.

        The API route needs an identity check by one-time code from the notifier
        file; the CLI writes the same database record without it. The first
        attempt hands over a fixed secret, so the result does not depend on how
        the CLI words its output; without that option the printed URI is used.
        """
        if user in _TOTP_CONFIGS:
            return _TOTP_CONFIGS[user]
        cfg = self.registered_totp(user)
        if cfg is None:
            generate = f"docker exec authelia authelia storage user totp generate {user} {AUTHELIA_CONFIG_ARGS}"
            r = self.run(f"{generate} --secret {ap.FIXTURE_TOTP_SECRET}")
            fixed = r.rc == 0
            if not fixed:
                r = self.run(generate)
            cfg = ap.parse_otpauth_uri(r.stdout + r.stderr, user=user) or self.registered_totp(user)
            if cfg is None and fixed:
                cfg = {"secret": ap.FIXTURE_TOTP_SECRET, "digits": 6, "period": 30, "algorithm": "SHA1"}
            assert cfg is not None, f"no TOTP registration for {user} (rc={r.rc}): {(r.stdout + r.stderr)[-300:]}"
        _TOTP_CONFIGS[user] = cfg
        return cfg

    def totp_generator(self, cfg: dict) -> ap.TotpGenerator:
        gen = _TOTP_GENERATORS.get(cfg["secret"])
        if gen is None:
            gen = ap.TotpGenerator(cfg["secret"], digits=cfg["digits"], period=cfg["period"],
                                   algorithm=cfg["algorithm"], clock=lambda: time.time() + self.clock_offset)
            _TOTP_GENERATORS[cfg["secret"]] = gen
        return gen


@pytest.fixture(scope="module")
def vm(host):
    return Vm(host)


@pytest.fixture(scope="module")
def portal(vm):
    return ap.Portal(vm.ip, vm.domain)


@pytest.fixture(scope="module")
def stack_ready(portal):
    """Traefik, CrowdSec bouncer and Authelia answer: a protected page redirects to the portal."""
    session = portal.new_session()
    portal.wait_until(lambda: portal.outcome(session, portal.url()).kind == "portal_redirect",
                      "protected page redirecting to the Authelia portal", timeout=300)


@pytest.fixture(scope="module")
def sign_in(portal, vm):
    """Factory: a fresh session signed in as a fixture user (``second_factor``: also TOTP)."""

    def _sign_in(user: str, second_factor: bool = False):
        session = portal.new_session()
        code = vm.totp_generator(vm.ensure_totp(user)).next_code() if second_factor else None
        portal.login(session, user, ap.FIXTURE_PASSWORDS[user], totp=code)
        return session

    return _sign_in


@pytest.fixture(scope="module")
def deploy(vm):
    """Run the scenario's converge.yml again, from the test (state after each deploy is checked).

    Extra variables accumulate over the module: a later deploy keeps what an earlier
    one set (an added rule, a new user, a rotated secret), so it changes only
    what the test is about.
    """
    persisted: dict = {}
    inventory = os.path.dirname(os.environ["MOLECULE_INVENTORY_FILE"])
    env = {
        **os.environ,
        "ANSIBLE_ROLES_PATH": str(REPO_ROOT / "ansible" / "roles"),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_STDOUT_CALLBACK": "default",  # parse_changed_tasks reads this format
        "ANSIBLE_HOST_KEY_CHECKING": "False",
    }

    def _deploy(extra_vars: dict | None = None, tags: str | None = None) -> Deployed:
        persisted.update(extra_vars or {})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         dir=os.environ.get("MOLECULE_EPHEMERAL_DIRECTORY")) as handle:
            json.dump(persisted, handle)
        os.chmod(handle.name, 0o600)
        cmd = ap.ansible_command(inventory, str(SCENARIO_DIR / "converge.yml"), handle.name, tags)
        proc = subprocess.run(cmd, cwd=SCENARIO_DIR, env=env, capture_output=True, text=True, timeout=2400)
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-60:])
            pytest.fail(f"redeploy failed (rc={proc.returncode}, tags={tags}):\n{tail}", pytrace=False)
        return Deployed(proc.stdout, ap.parse_changed_tasks(proc.stdout), ap.parse_recap(proc.stdout))

    return _deploy
