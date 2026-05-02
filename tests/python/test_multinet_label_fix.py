"""Tests for ``scripts/multinet-label-fix.py`` (W-112 part 1).

Covers every branch in ``fix_compose`` and the CLI ``main`` entrypoint:
  * single-network service (no change)
  * multi-network service without traefik labels (no change)
  * multi-network service with traefik.enable in dict-form labels
  * multi-network service with traefik.enable in list-form labels
  * multi-network service with bare ``traefik.enable`` flag
  * service that already pinned ``traefik.docker.network`` (no change)
  * primary-net override
  * idempotency (re-running on a fixed file)
  * non-dict service value (skipped, no crash)
  * empty / missing services key
  * CLI: dry-run vs --write
  * CLI: missing file is no-op
  * CLI: parse error returns exit code 2
  * CLI: --primary and DRAYVE_PRIMARY_NET env

Run: ``pytest tests/python/test_multinet_label_fix.py``
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "multinet-label-fix.py"


@pytest.fixture(scope="module")
def mod():
    """Load the script as a module — the dash in the filename forbids import."""
    spec = importlib.util.spec_from_file_location("multinet_label_fix", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- 1) fix_compose: pure-data behaviour ----------------------------------

def test_single_network_service_untouched(mod):
    data = {
        "services": {
            "app": {
                "networks": ["default"],
                "labels": ["traefik.enable=true"],
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == []
    assert data["services"]["app"]["labels"] == ["traefik.enable=true"]


def test_multinet_without_traefik_label_untouched(mod):
    data = {
        "services": {
            "worker": {
                "networks": ["default", "internal"],
                "labels": ["myapp.role=worker"],
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == []


def test_multinet_with_traefik_dict_labels_gets_label(mod):
    data = {
        "services": {
            "mealie": {
                "networks": {"default": None, "mealie_db": None},
                "labels": {
                    "traefik.enable": "true",
                    "traefik.http.routers.mealie.rule": "Host(`x`)",
                },
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == ["mealie"]
    assert (
        data["services"]["mealie"]["labels"]["traefik.docker.network"]
        == "drayve_default"
    )


def test_multinet_with_traefik_list_labels_gets_label(mod):
    data = {
        "services": {
            "mealie": {
                "networks": ["default", "mealie_db"],
                "labels": [
                    "traefik.enable=true",
                    "traefik.http.routers.mealie.rule=Host(`x`)",
                ],
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == ["mealie"]
    assert (
        "traefik.docker.network=drayve_default"
        in data["services"]["mealie"]["labels"]
    )


def test_bare_traefik_enable_flag_is_recognised(mod):
    data = {
        "services": {
            "svc": {
                "networks": ["default", "alt"],
                "labels": ["traefik.enable"],
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == ["svc"]


def test_explicit_traefik_network_preserved(mod):
    """User-pinned label wins — script never overrides explicit choice."""
    data = {
        "services": {
            "svc": {
                "networks": ["a", "b"],
                "labels": [
                    "traefik.enable=true",
                    "traefik.docker.network=user_choice",
                ],
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == []
    assert data["services"]["svc"]["labels"].count(
        "traefik.docker.network=user_choice"
    ) == 1


def test_explicit_traefik_network_dict_form_preserved(mod):
    data = {
        "services": {
            "svc": {
                "networks": ["a", "b"],
                "labels": {
                    "traefik.enable": "true",
                    "traefik.docker.network": "user_choice",
                },
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == []


def test_primary_net_override(mod):
    data = {
        "services": {
            "svc": {
                "networks": ["a", "b"],
                "labels": ["traefik.enable=true"],
            }
        }
    }
    _, changed = mod.fix_compose(data, primary_net="my_proxy")
    assert changed == ["svc"]
    assert "traefik.docker.network=my_proxy" in data["services"]["svc"]["labels"]


def test_idempotent_after_first_pass(mod):
    data = {
        "services": {
            "svc": {
                "networks": ["a", "b"],
                "labels": ["traefik.enable=true"],
            }
        }
    }
    mod.fix_compose(data)
    _, changed = mod.fix_compose(data)
    assert changed == []


def test_non_dict_service_skipped(mod):
    """Defensive — malformed compose with string value should not crash."""
    data = {"services": {"weird": "string-instead-of-dict"}}
    _, changed = mod.fix_compose(data)
    assert changed == []


def test_missing_services_key(mod):
    data = {"version": "3.8"}
    _, changed = mod.fix_compose(data)
    assert changed == []


def test_empty_services(mod):
    _, changed = mod.fix_compose({"services": {}})
    assert changed == []


def test_three_networks_also_triggers(mod):
    """Boundary check — fix applies for >=2 networks."""
    data = {
        "services": {
            "svc": {
                "networks": ["a", "b", "c"],
                "labels": ["traefik.enable=true"],
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == ["svc"]


def test_traefik_enable_false_skipped(mod):
    data = {
        "services": {
            "svc": {
                "networks": ["a", "b"],
                "labels": ["traefik.enable=false"],
            }
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == []


def test_no_labels_at_all_untouched(mod):
    data = {
        "services": {
            "svc": {"networks": ["a", "b"]},
        }
    }
    _, changed = mod.fix_compose(data)
    assert changed == []


def test_inject_label_typeerror_on_unknown_form(mod):
    with pytest.raises(TypeError):
        mod._inject_label(123, "primary")


# ---- 2) CLI ---------------------------------------------------------------

@pytest.fixture
def compose_file(tmp_path: Path) -> Path:
    p = tmp_path / "docker-compose.override.yml"
    p.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "mealie": {
                        "networks": ["default", "mealie_db"],
                        "labels": ["traefik.enable=true"],
                    }
                }
            },
            sort_keys=False,
        )
    )
    return p


def test_cli_dry_run_does_not_modify(mod, compose_file: Path, capsys):
    before = compose_file.read_text()
    rc = mod.main([str(compose_file)])
    assert rc == 0
    assert compose_file.read_text() == before
    err = capsys.readouterr().err
    assert "injecting" in err
    assert "mealie" in err


def test_cli_write_modifies_file(mod, compose_file: Path):
    rc = mod.main([str(compose_file), "--write"])
    assert rc == 0
    rewritten = yaml.safe_load(compose_file.read_text())
    labels = rewritten["services"]["mealie"]["labels"]
    assert "traefik.docker.network=drayve_default" in labels


def test_cli_write_idempotent(mod, compose_file: Path, capsys):
    mod.main([str(compose_file), "--write"])
    capsys.readouterr()
    rc = mod.main([str(compose_file), "--write"])
    assert rc == 0
    assert capsys.readouterr().err == ""


def test_cli_missing_file_is_noop(mod, tmp_path: Path):
    rc = mod.main([str(tmp_path / "absent.yml")])
    assert rc == 0


def test_cli_parse_error_returns_2(mod, tmp_path: Path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text("services: [this is, a, list, not, mapping]\n")
    rc = mod.main([str(bad)])
    assert rc == 2
    assert "parse error" in capsys.readouterr().err


def test_cli_primary_flag(mod, compose_file: Path):
    rc = mod.main([str(compose_file), "--primary", "alt_net", "--write"])
    assert rc == 0
    rewritten = yaml.safe_load(compose_file.read_text())
    assert (
        "traefik.docker.network=alt_net"
        in rewritten["services"]["mealie"]["labels"]
    )


def test_cli_env_default(mod, compose_file: Path, monkeypatch):
    monkeypatch.setenv("DRAYVE_PRIMARY_NET", "env_net")
    rc = mod.main([str(compose_file), "--write"])
    assert rc == 0
    rewritten = yaml.safe_load(compose_file.read_text())
    assert (
        "traefik.docker.network=env_net"
        in rewritten["services"]["mealie"]["labels"]
    )


def test_cli_no_changes_silent(mod, tmp_path: Path, capsys):
    p = tmp_path / "ok.yml"
    p.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "single": {
                        "networks": ["default"],
                        "labels": ["traefik.enable=true"],
                    }
                }
            }
        )
    )
    rc = mod.main([str(p)])
    assert rc == 0
    assert capsys.readouterr().err == ""


def test_cli_write_io_error(mod, compose_file: Path, monkeypatch):
    """Cover the OSError branch in main() during write."""

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(mod, "_dump_yaml", boom)
    rc = mod.main([str(compose_file), "--write"])
    assert rc == 2
