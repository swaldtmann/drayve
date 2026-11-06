"""Test: a LAPI outage must not turn into a site-wide 403 (fail-open bouncer).

The bouncer plugin's default ``updateMaxFailure: 0`` answers every request
with 403 once one stream update against the LAPI fails. The role renders
``-1`` (never block on update failures); decisions already pulled into the
bouncer cache keep applying.

Sequence: ban a test IP, wait until the bouncer stream has pulled it, stop
crowdsec for ~2 minutes, probe every 10 s. Ordinary clients must never see
403; the banned IP keeps getting 403. crowdsec is started again at the end.
"""

import time

import pytest

BANNED_IP = "203.0.113.77"  # TEST-NET-3, never a real client
DOMAIN = "test.drayve.local"
MIDDLEWARE = "/opt/drayve/deploy/stack/traefik/config/dynamic/crowdsec.yml"
PROBES = 12
INTERVAL = 10


def _status(host, forwarded_for=None):
    header = f"-H 'X-Forwarded-For: {forwarded_for}' " if forwarded_for else ""
    cmd = host.run(
        f"curl -sk -o /dev/null -w '%{{http_code}}' --max-time 8 {header}"
        f"--resolve {DOMAIN}:443:127.0.0.1 https://{DOMAIN}/"
    )
    return cmd.stdout.strip()


def test_middleware_renders_update_max_failure_minus_one(host):
    f = host.file(MIDDLEWARE)
    assert f.exists
    assert f.contains("updateMaxFailure: -1")


@pytest.fixture
def banned_ip_in_bouncer_cache(host):
    """Ban BANNED_IP and wait until the bouncer stream delivers it (403)."""
    host.run_expect([0], f"docker exec crowdsec cscli decisions add --ip {BANNED_IP} --duration 1h --reason molecule")
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if _status(host, BANNED_IP) == "403":
                break
            time.sleep(3)
        else:
            pytest.fail("banned test IP never got 403 before the outage: "
                        "X-Forwarded-For not honoured or bouncer stream not pulling")
        yield
    finally:
        host.run(f"docker exec crowdsec cscli decisions delete --ip {BANNED_IP}")


def test_lapi_outage_is_fail_open_and_cached_ban_holds(host, banned_ip_in_bouncer_cache):
    assert _status(host) not in ("403", "000"), "baseline request already blocked"
    ok = []
    banned = []
    started = host.run_expect([0], "date '+%Y-%m-%d %H:%M:%S'").stdout.strip()
    host.run_expect([0], "docker stop crowdsec")
    try:
        for i in range(PROBES):
            time.sleep(INTERVAL)
            ok.append(_status(host))
            banned.append(_status(host, BANNED_IP))
    finally:
        host.run("docker start crowdsec")
    # The watchdog restarts Traefik after 60 s of failing probes; it must stay
    # quiet in the outage window (earlier restarts belong to converge).
    wd = host.run(f"journalctl -t drayve-bouncer-watchdog --since '{started}' --no-pager").stdout
    print(f"watchdog journal since {started}: {wd.strip() or '(empty)'}")
    print(f"ordinary clients during outage: {ok}")
    print(f"banned test IP during outage:   {banned}")
    assert not [s for s in ok if s in ("403", "000")], f"ordinary requests blocked: {ok}"
    assert all(s == "403" for s in banned), f"cached ban stopped applying: {banned}"
    assert "restart" not in wd.lower(), f"watchdog fired during the outage: {wd}"
