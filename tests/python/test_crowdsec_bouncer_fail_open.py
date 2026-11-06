"""Tests for the CrowdSec bouncer middleware's LAPI-outage behaviour.

The plugin (crowdsec-bouncer-traefik-plugin v1.8.1, bouncer.go) with the
default ``updateMaxFailure: 0`` answers every request with 403 as soon as one
stream update against the LAPI fails. ``-1`` means "never block on update
failures": new decisions are not fetched during the outage, cached decisions
keep applying. The role renders the key from ``crowdsec_update_max_failure``
(default -1).

Run: ``pytest tests/python/test_crowdsec_bouncer_fail_open.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_ROLE = REPO_ROOT / "ansible" / "roles" / "traefik"
TASK_NAME = "Deploy CrowdSec bouncer middleware (plugin)"


def _defaults() -> dict:
    return yaml.safe_load((TRAEFIK_ROLE / "defaults" / "main.yml").read_text())


@pytest.fixture
def render_bouncer(ansible_jinja_env):
    env = ansible_jinja_env(REPO_ROOT)
    tasks = yaml.safe_load((TRAEFIK_ROLE / "tasks" / "main.yml").read_text())
    task = next(t for t in tasks if t.get("name") == TASK_NAME)
    content = task["ansible.builtin.copy"]["content"]

    def _render(**overrides: object) -> dict:
        ctx = {
            "crowdsec_bouncer_key": "k",
            "crowdsec_update_max_failure": _defaults().get("crowdsec_update_max_failure"),
            **overrides,
        }
        doc = yaml.safe_load(env.from_string(content).render(**ctx))
        return doc["http"]["middlewares"]["crowdsec"]["plugin"]["bouncer"]

    return _render


def test_default_is_minus_one():
    assert _defaults()["crowdsec_update_max_failure"] == -1


def test_middleware_renders_minus_one_as_integer_by_default(render_bouncer):
    value = render_bouncer()["updateMaxFailure"]
    assert value == -1 and isinstance(value, int), value


@pytest.mark.parametrize("override", [0, 3])
def test_host_can_opt_back_into_fail_closed(render_bouncer, override):
    value = render_bouncer(crowdsec_update_max_failure=override)["updateMaxFailure"]
    assert value == override and isinstance(value, int), value
