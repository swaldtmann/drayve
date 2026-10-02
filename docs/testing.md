# Testing with Molecule

The scenarios under `molecule/` (`auth-none`, `authelia`, `authentik`,
`authentik-ldap`, `backup`, `backup-kedge`, `basic`, `integration`, `light`,
`sops`) converge the roles on a real VM, check idempotence, verify with
Testinfra and tear the VM down again.

Where the VM comes from is chosen by one environment variable,
`DRAYVE_MOLECULE_DRIVER`. The driver part of the configuration lives in one
place, `.config/molecule/config.yml` (Molecule loads it automatically from the
repository root); the scenario files only hold what is specific to the
scenario.

| `DRAYVE_MOLECULE_DRIVER` | Test host | Needs |
|--------------------------|-----------|-------|
| unset / `default` | local Incus VM (Ubuntu 24.04, 2 vCPU / 4 GiB / 20 GiB) | `incus` CLI + access to an Incus daemon |
| `molecule_hetznercloud` | Hetzner Cloud server (`cx23`, `nbg1`) | `HCLOUD_TOKEN`, costs money |

## Incus (default)

Requirements:

- the `incus` CLI on the machine that runs Molecule, with permission to talk to
  the daemon (member of `incus-admin`, or root); `incus list` must work
- the image `images:ubuntu/24.04/cloud` (pulled on first use, or cached)
- a bridge with DHCP and outbound NAT (the roles install Docker and pull images)
- the machine running Molecule must be able to reach the VM's bridge address
  on port 22 — Molecule connects over plain SSH as `root`, like on Hetzner

```bash
molecule test -s basic     # any scenario name from the list above
make test-integration      # shortcuts exist for some scenarios (make help)
```

How it works: `.config/molecule/default/create.yml` starts one VM per platform
(`incus launch --vm`), passes a per-run SSH key through cloud-init (root login),
waits for the Incus agent, cloud-init and the VM's IPv4 address, and records
the address in Molecule's instance config. `destroy.yml` deletes the VMs of
the instance config **and** every VM labelled with the scenario's namespace
(`user.drayve_molecule_ns`), so an aborted run is cleaned up by the next
`molecule destroy` or `molecule test`.

Instance names are `dm-<run id>-test-<scenario>`; the Molecule inventory host
stays `test-<scenario>` as on Hetzner.

Tunables (all optional):

| Variable | Default | Meaning |
|----------|---------|---------|
| `DRAYVE_MOLECULE_INCUS_IMAGE` | `images:ubuntu/24.04/cloud` | image to launch |
| `DRAYVE_MOLECULE_INCUS_CPUS` | `2` | `limits.cpu` |
| `DRAYVE_MOLECULE_INCUS_MEMORY` | `4GiB` | `limits.memory` |
| `DRAYVE_MOLECULE_INCUS_DISK` | `20GiB` | root disk size |
| `DRAYVE_MOLECULE_REGISTRY_MIRROR` | empty (no mirror) | Docker Hub pull-through cache for the test VMs (`docker_registry_mirrors`, space-separated for several). The CI workflows set `http://10.99.0.1:5000`, the cache on the runner VM's inner bridge. Applies to every scenario via `.config/molecule/config.yml`. `scripts/list-ci-images.py` lists the images to warm it with |
| `RESOURCE_NAMESPACE` | hash of the scenario directory | label used by `destroy` to find leftovers (same variable the Hetzner driver reads) |

A platform entry may override `cpus`, `memory` and `disk` (`basic` and `light`
use 4 / `8GiB`, matching their `cx33` Hetzner type).

Debugging a failed run: do **not** destroy first (`molecule test --destroy=never`
keeps the VM after a failure). Then the VM is still running:

```bash
incus list user.drayve_molecule_ns=<namespace>   # find it (dm-*)
incus exec dm-<id>-test-<scenario> -- bash       # shell without SSH
ssh -i ~/.ansible/tmp/molecule.*.<scenario>/ssh_key root@<address>
```

Leftovers without Molecule state (e.g. a wiped workspace):
list them with `incus list dm- -c n -f csv` and remove with
`incus delete --force <name>`.

## Hetzner Cloud

```bash
cp .env.test.example .env.test       # add HCLOUD_TOKEN
export DRAYVE_MOLECULE_DRIVER=molecule_hetznercloud
molecule test -s basic
```

With this value Molecule uses the `molecule-hetznercloud` plugin's own
create/destroy playbooks (the paths in the base config do not exist for it, so
Molecule falls back to the driver's). Each scenario creates a server named
`<namespace>-test-<scenario>` and deletes it at the end.

Switching the driver for a scenario that already has Molecule state needs
`molecule reset -s <scenario>` first; Molecule refuses to change drivers
silently (state lives in `~/.ansible/tmp/molecule.*.<scenario>`).

## Role-level scenarios

`ansible/roles/common` and `ansible/roles/docker` have their own Molecule
scenario (`make test-role NAME=...`). They are not covered by the shared base
config and still use the Hetzner driver (`HCLOUD_TOKEN` required).

## Static checks (no VM)

```bash
make lint
make test-unit      # ansible syntax-check + pytest + bats
```
