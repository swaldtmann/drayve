"""Login probes for the ``authelia-file`` Molecule scenario (no browser).

Runs on the Molecule controller against the test VM: sign in through the
Authelia API (first factor, TOTP), walk the OIDC code flow, evaluate redirects.
Hostnames below the scenario domain are pinned to the VM's address, so no DNS
entry is needed on the controller; certificates are not verified (the VM has no
publicly valid certificate).

The functions without I/O (TOTP, response classification, Ansible output
parsing, .env helpers) are unit-tested in ``tests/python/test_authelia_file_probe.py``
of the repository, so they can be checked without a VM. ``requests`` is imported
lazily for that reason.

Authelia answers every protected page it does not let through with a redirect to
its portal, and the portal page itself is a plain 200. Therefore nothing here
follows redirects automatically: a 200 only counts when the request went to a
protected host and was answered by it (see ``classify_response``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import socket
import struct
import time
from collections import namedtuple
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse


# Test fixtures, not credentials: passwords of the throw-away users in the scenario's
# file backend. The argon2id hashes in molecule.yml belong to exactly these values
# (checked by tests/python/test_authelia_file_probe.py).
FIXTURE_PASSWORDS = {
    "alice": "alice-test-fixture-pw",
    "bob": "bob-test-fixture-pw",
    "carol": "carol-test-fixture-pw",
}

# Fixed TOTP secret (base32, 32 bytes) for alice, handed to Authelia's CLI so the
# tests know it even if the CLI output is not parseable. A test value.
FIXTURE_TOTP_SECRET = "MF2XI2DFNRUWCLLGNFWGKLLGNF4HI5LSMUWXI33UOAWWWZLZEEQQ"


class ProbeFailure(AssertionError):
    """A probe step did not behave as required (an assertion, so xfail(raises=AssertionError) sees it)."""


class ProbeTimeout(RuntimeError):
    """Something did not become ready in time (not an assertion: never hidden by an expected failure)."""


# ---------------------------------------------------------------- TOTP (RFC 6238)

def totp_code(secret_b32: str, at: float, digits: int = 6, period: int = 30, algorithm: str = "SHA1") -> str:
    clean = secret_b32.strip().replace(" ", "").replace("=", "").upper()
    key = base64.b32decode(clean + "=" * (-len(clean) % 8))
    message = struct.pack(">Q", int(at // period))
    digest = hmac.new(key, message, getattr(hashlib, algorithm.lower())).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def parse_otpauth_uri(text: str, user: str | None = None) -> dict | None:
    """First ``otpauth://totp/...`` URI in ``text`` (of ``user`` if given), or None."""
    for raw in re.findall(r"otpauth://totp/[^\s'\"<>]+", text):
        parsed = urlparse(raw)
        label = unquote(parsed.path.lstrip("/"))
        account = label.split(":")[-1]
        if user is not None and account != user:
            continue
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if "secret" not in query:
            continue
        return {
            "secret": query["secret"],
            "digits": int(query.get("digits", 6)),
            "period": int(query.get("period", 30)),
            "algorithm": query.get("algorithm", "SHA1").upper(),
            "label": label,
        }
    return None


class TotpGenerator:
    """One-time codes that are never sent twice.

    Authelia rejects a code it has already accepted (re-use protection), so two
    sign-ins within the same period need different codes: ``next_code`` waits
    for the next period when it would repeat the last one.
    """

    def __init__(self, secret: str, *, digits: int = 6, period: int = 30, algorithm: str = "SHA1",
                 clock=time.time, sleep=time.sleep):
        self.secret, self.digits, self.period, self.algorithm = secret, digits, period, algorithm
        self._clock, self._sleep = clock, sleep
        self._last: str | None = None

    def _code(self) -> str:
        return totp_code(self.secret, self._clock(), self.digits, self.period, self.algorithm)

    def next_code(self) -> str:
        code = self._code()
        if code == self._last:
            now = self._clock()
            self._sleep(self.period - (now % self.period) + 0.5)
            code = self._code()
        self._last = code
        return code


# ---------------------------------------------------------- response evaluation

Outcome = namedtuple("Outcome", "kind status location")


def classify_response(request_url: str, status: int, location: str, portal_host: str) -> Outcome:
    """What a response to ``request_url`` means for "did the protected page let me in?".

    kinds: ``ok`` (200 from a protected host), ``portal_redirect`` (sent to the
    Authelia portal), ``portal_page`` (the request itself went to the portal:
    its 200 is never an access), ``denied`` (401/403), ``redirect_other``, ``other``.
    """
    location = location or ""
    if urlparse(request_url).hostname == portal_host:
        return Outcome("portal_page", status, location)
    if 300 <= status < 400:
        target_host = urlparse(urljoin(request_url, location)).hostname
        return Outcome("portal_redirect" if target_host == portal_host else "redirect_other", status, location)
    if status == 200:
        return Outcome("ok", status, location)
    if status in (401, 403):
        return Outcome("denied", status, location)
    return Outcome("other", status, location)


def portal_redirect_target(location: str, portal_host: str) -> str | None:
    """The page the portal will return to (``rd=``), if ``location`` points at the portal."""
    parsed = urlparse(location or "")
    if parsed.hostname != portal_host:
        return None
    values = parse_qs(parsed.query).get("rd")
    return values[0] if values else None


def extract_oidc_code(location: str) -> str | None:
    values = parse_qs(urlparse(location or "").query).get("code")
    return values[0] if values else None


def oidc_secret_mismatch(status: int, body: str) -> bool:
    """Authelia's rejection of a wrong client secret ("... client secret did not match ...")."""
    return status != 200 and "secret did not match" in (body or "").lower()


# ------------------------------------------------------------- Ansible output

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_TASK_HEADER = re.compile(r"^(?:TASK|RUNNING HANDLER) \[(.+)\] \*+\s*$")
_RECAP = re.compile(r"^(\S+)\s*:\s*ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)")


def parse_changed_tasks(stdout: str) -> list[str]:
    """Names (``role : task``) of tasks that reported ``changed``, once each, in order."""
    changed: list[str] = []
    current = None
    for line in _ANSI.sub("", stdout).splitlines():
        header = _TASK_HEADER.match(line)
        if header:
            current = header.group(1)
        elif line.startswith("changed: [") and current and current not in changed:
            changed.append(current)
    return changed


def parse_recap(stdout: str) -> dict[str, dict[str, int]]:
    recap = {}
    for line in _ANSI.sub("", stdout).splitlines():
        m = _RECAP.match(line)
        if m:
            recap[m.group(1)] = {"ok": int(m.group(2)), "changed": int(m.group(3)),
                                 "unreachable": int(m.group(4)), "failed": int(m.group(5))}
    return recap


def ansible_command(inventory: str, playbook: str, extra_vars_file: str | None = None,
                    tags: str | None = None) -> list[str]:
    cmd = ["ansible-playbook", "-i", inventory]
    if tags:
        cmd += ["--tags", tags]
    if extra_vars_file:
        cmd += ["-e", f"@{extra_vars_file}"]
    return cmd + [playbook]


# -------------------------------------------------------------- .env helpers

def _env_value(env_text: str, name: str) -> str | None:
    prefix = name + "="
    for line in env_text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def env_var_length(env_text: str, name: str) -> int:
    """Length of a variable's value (0 if missing or empty). Never returns the value."""
    return len(_env_value(env_text, name) or "")


def env_var_sha256(env_text: str, name: str) -> str | None:
    """sha256 of a variable's value, to compare against a known secret without printing it."""
    value = _env_value(env_text, name)
    return None if value is None else hashlib.sha256(value.encode()).hexdigest()


# --------------------------------------------------------------- HTTP (VM)

_PIN: dict[str, str] = {}
_ORIG_GETADDRINFO = socket.getaddrinfo


def _pinned_getaddrinfo(host, *args, **kwargs):
    if isinstance(host, str):
        for domain, ip in _PIN.items():
            if host == domain or host.endswith("." + domain):
                host = ip
                break
    return _ORIG_GETADDRINFO(host, *args, **kwargs)


def _install_dns_pin(ip: str, domain: str) -> None:
    _PIN[domain] = ip
    socket.getaddrinfo = _pinned_getaddrinfo


def _json(response) -> dict:
    try:
        data = response.json()
    except Exception:  # not JSON (HTML error page, empty body)
        return {}
    return data if isinstance(data, dict) else {}


class Portal:
    """Client for one test VM: Authelia portal, protected hosts, OIDC, Grafana."""

    def __init__(self, ip: str, domain: str, pin_dns: bool = True, timeout: int = 30):
        self.ip, self.domain, self.timeout = ip, domain, timeout
        self.portal_host = f"auth.{domain}"
        if pin_dns:
            _install_dns_pin(ip, domain)

    # -- sessions and plain requests
    def new_session(self):
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        session = requests.Session()
        session.verify = False
        session.trust_env = False  # no proxy / CA bundle from the CI environment
        return session

    def url(self, host: str | None = None, path: str = "/") -> str:
        return f"https://{host + '.' if host else ''}{self.domain}{path}"

    def outcome(self, session, url: str) -> Outcome:
        r = session.get(url, allow_redirects=False, timeout=self.timeout)
        return classify_response(url, r.status_code, r.headers.get("Location", ""), self.portal_host)

    # -- sign-in
    def try_first_factor(self, session, user: str, password: str, target: str | None = None):
        body = {"username": user, "password": password, "keepMeLoggedIn": False}
        if target:
            body["targetURL"] = target
        return session.post(f"https://{self.portal_host}/api/firstfactor", json=body, timeout=self.timeout)

    def try_second_factor(self, session, token: str, target: str | None = None):
        body = {"token": token}
        if target:
            body["targetURL"] = target
        return session.post(f"https://{self.portal_host}/api/secondfactor/totp", json=body, timeout=self.timeout)

    def login(self, session, user: str, password: str, totp: str | None = None, target: str | None = None) -> None:
        r = self.try_first_factor(session, user, password, target)
        if r.status_code != 200 or _json(r).get("status") != "OK":
            raise ProbeFailure(f"first factor for {user!r} failed: HTTP {r.status_code} {r.text[:200]}")
        if totp is not None:
            r = self.try_second_factor(session, totp, target)
            if r.status_code != 200 or _json(r).get("status") != "OK":
                raise ProbeFailure(f"second factor for {user!r} failed: HTTP {r.status_code} {r.text[:200]}")

    # -- OIDC
    def oidc_token(self, session, client_id: str, secret: str, redirect_uri: str,
                   scope: str = "openid profile email groups"):
        """Code flow as client ``client_id``: authorize (session must be signed in), then exchange the code."""
        query = urlencode({"client_id": client_id, "response_type": "code", "scope": scope,
                           "redirect_uri": redirect_uri, "state": "probe-state-0123456789"})
        r = session.get(f"https://{self.portal_host}/api/oidc/authorization?{query}",
                        allow_redirects=False, timeout=self.timeout)
        location = r.headers.get("Location", "")
        code = extract_oidc_code(location)
        if not code:
            raise ProbeFailure(f"no authorization code for client {client_id!r}: HTTP {r.status_code} "
                               f"Location={location[:200]}")
        return session.post(f"https://{self.portal_host}/api/oidc/token",
                            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                            auth=(client_id, secret), timeout=self.timeout)

    def grafana_oidc_login(self, session) -> dict:
        """Grafana sign-in over OIDC, up to ``/api/user``. Session must be signed in at the portal.

        Returns ``{"login", "email", "role"}``; raises ProbeFailure naming the step that broke.
        """
        base = self.url("grafana", "")
        authorize = f"https://{self.portal_host}/api/oidc/authorization"
        url, trail, authz = base + "/", [], None
        for _ in range(15):
            r = session.get(url, allow_redirects=False, timeout=self.timeout)
            location = r.headers.get("Location", "")
            trail.append(f"{r.status_code} {url[:90]} -> {location[:120]}")
            if not (300 <= r.status_code < 400 and location):
                raise ProbeFailure("Grafana never redirected to Authelia's authorization endpoint: " + " | ".join(trail))
            nxt = urljoin(url, location)
            if nxt.startswith(authorize):
                authz = nxt
                break
            if urlparse(nxt).hostname == self.portal_host:
                raise ProbeFailure("bounced to the Authelia portal (not signed in for Grafana): " + " | ".join(trail))
            url = nxt
        if not authz:
            raise ProbeFailure("too many redirects before Authelia: " + " | ".join(trail))
        r = session.get(authz, allow_redirects=False, timeout=self.timeout)
        callback = urljoin(authz, r.headers.get("Location", ""))
        if not callback.startswith(base + "/login/generic_oauth") or not extract_oidc_code(callback):
            raise ProbeFailure(f"authorization did not return a code to Grafana: HTTP {r.status_code} "
                               f"Location={callback[:200]}")
        # Grafana exchanges the code with Authelia's token endpoint inside this request.
        r = session.get(callback, allow_redirects=False, timeout=60)
        if r.status_code not in (302, 303):
            raise ProbeFailure(f"Grafana callback failed: HTTP {r.status_code} {r.text[:200]}")
        u = session.get(base + "/api/user", allow_redirects=False, timeout=self.timeout)
        if u.status_code != 200 or "json" not in u.headers.get("Content-Type", ""):
            raise ProbeFailure(f"/api/user after the callback: HTTP {u.status_code} "
                               f"(Grafana did not accept the sign-in) callback-> {r.headers.get('Location', '')[:120]}")
        info = _json(u)
        orgs = session.get(base + "/api/user/orgs", timeout=self.timeout).json()
        return {"login": info.get("login"), "email": info.get("email"), "role": orgs[0]["role"] if orgs else None}

    # -- waiting
    def wait_until(self, predicate, what: str, timeout: float = 240, interval: float = 5):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                result = predicate()
                if result:
                    return result
                last = "not yet"
            except Exception as exc:  # connection refused / reset while Traefik or the app starts
                last = f"{type(exc).__name__}: {str(exc)[:120]}"
            time.sleep(interval)
        raise ProbeTimeout(f"{what} not ready after {timeout:.0f} s (last: {last})")
