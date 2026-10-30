# Self-hosted CI

drayve's CI runs on a single self-hosted machine instead of a hosted CI service. This page describes
how it is built, what it runs, and where its limits are, so that you can build something similar.
The infrastructure scripts themselves are not part of this repository; this is a description of the
design, not an installer.

The workflows live in `.gitea/workflows/` and target a Forgejo/Gitea-style Actions runner
(`forgejo-runner`). The ideas carry over to any runner that can take one job and exit.

## 1. Architecture

```
<ci-host>                       physical machine, Incus (outer)
 |  restricted Incus project, own bridge, own firewall tables
 |
 +-- runner VM                  one VM, its own Incus inside (inner), Docker, forgejo-runner
      |
      +-- test VM (Molecule scenario 1)    Ubuntu 24.04, 2 vCPU / 4 GiB, thrown away
      +-- test VM (Molecule scenario 2)
      +-- ...
```

Three layers:

1. **Host.** Runs the outer Incus, a dedicated bridge, and a firewall that only lets guests reach the
   public internet. It also runs the cycle service (section 2) that starts, resets and stops the
   runner VM. The host never executes job code.
2. **Runner VM.** A VM with its own Incus installed (nested virtualization) plus Docker. The Actions
   runner runs here, in host mode (label `self-hosted`, jobs run as root inside the VM). Molecule
   needs the `incus` CLI and a bridge; a `container:` key in a workflow is not honoured in host mode,
   which is why the runner itself lives in a disposable VM and not in a container.
3. **Test VMs.** The Molecule scenarios (see [testing.md](testing.md)) create one VM per scenario in
   the runner VM's inner Incus. They are real VMs, not containers, because the roles under test
   install Docker, change kernel settings and restart services.

Why nested instead of one flat level: the runner VM is the unit that gets reset (section 2). Everything
a job creates, including every test VM, dies with it. A failed or aborted job therefore cannot leave
test VMs behind, and the workflows need no cleanup step for them.

Sizing used (defaults, adjust to your machine): runner VM 10 vCPU / 20 GiB RAM, 60 GiB root disk plus a
150 GiB data disk (the inner storage pool sits directly on that block device). The outer Incus project
is capped slightly above that, so the host keeps cores and memory for other work. Nested VMs start
slowly: a 2 vCPU test VM took about 76 to 87 s to boot, independent of the inner storage backend; with
4 or more vCPUs it dropped to 37 to 49 s in our samples.

## 2. One job per runner, then reset

The runner takes **exactly one job**. Afterwards the whole VM is rolled back to a clean snapshot. The
point is that no runner identity and no secret survives from one job to the next.

One cycle, run by a service on the host in a loop:

1. Stop the runner VM hard if it is running.
2. Delete a leftover runner registration from a previous cycle, if one is recorded.
3. Restore the snapshot `clean` of **both** the VM and its data disk. A VM snapshot does not include
   the separate data disk; without restoring both, leftovers from earlier test VMs would stay in the
   inner storage pool and the inner Incus database and its pool would no longer match.
4. Register a new **ephemeral** runner through the forge API (`ephemeral: true`), and remember its id.
5. Start the VM and hand the new runner identity (UUID and token) to it **over stdin** through the VM
   agent. In the VM it is written only to a tmpfs and starts a systemd unit running
   `forgejo-runner one-job --wait`; the unit powers the VM off when the job ends.
6. Wait until the VM is off. If it is still up after the job time limit (3 hours here), stop it hard
   and deregister the runner from the host side.
7. Go to 1.

Properties this gives you:

- **No persistence.** The snapshot is taken from a state in which there is no runner identity, no
  token and no job environment. A script checks that before every snapshot and refuses otherwise.
  Refreshing the snapshot always starts from a restore of the old one, never from whatever the last
  job left on disk.
- **The identity is useless after the job.** The registration is ephemeral, so the forge removes it
  once the job finished; the host additionally deletes it. The token sits readable under a tmpfs path
  during the job (jobs run as root in the VM), and is worthless afterwards.
- **The identity never touches the host's configuration.** It is passed on stdin rather than through
  instance config keys, because those would show up in the Incus database, in `incus config show`
  and in instance snapshots.
- **Crash safety.** Every cycle begins by assuming nothing about the previous one, so a host reboot or
  a killed script at any point is repaired by the next cycle. All Incus calls before the hand-over
  run under timeouts; an unknown VM state is answered with a hard stop.
- **Cost.** A reset plus boot adds roughly 140 s per job. Because of this, the release workflow packs
  several scenarios into one job (section 4) instead of one job per scenario.

The host also copies each job's log out of the VM while it runs (read-only, the host pulls the
journal of the job unit; the VM has no access to the host), because the VM and its journal are gone
after the reset and the forge offers no log API.

## 3. Pull cache and pre-loaded images

Test VMs pull their Docker images on every run, and a fresh VM per run means no layer cache. Two things
make that bearable:

- **Docker Hub pull-through cache** inside the runner VM: the official `registry` image in proxy mode
  against Docker Hub, listening only on the inner bridge address (not on the VM's outer interface).
  Test VMs reach it as a Docker registry mirror; the workflows pass its address to Molecule through
  the environment variable `DRAYVE_MOLECULE_REGISTRY_MIRROR`. It is filled once, right before the
  snapshot is taken, so the cache is part of `clean` and every job starts with it warm. Cache entries
  do not expire (`ttl: 0s`, otherwise a frozen cache would be empty after a week). Misses are passed
  through and are lost at the next reset. The mirror only covers Docker Hub; other registries (for
  example `ghcr.io`) are pulled directly. Outside CI, with the variable unset, no mirror is used.
  Measured on one scenario: about 1055 s without the cache, about 329 s with it (one run each).
- **Pre-loaded images** in the snapshot: the Ubuntu 24.04 cloud image is stored in the inner Incus
  under the alias `u24c` (`DRAYVE_MOLECULE_INCUS_IMAGE=u24c`), and images in which a job itself runs
  are pulled into the VM's Docker image store before the snapshot. Freshness equals snapshot age;
  moving tags are only renewed when the snapshot is refreshed.

## 4. What runs when

| Workflow | Trigger | What it does |
|---|---|---|
| `test.yml` | push to `main`, pull request against `main` | One job: ansible-lint, playbook syntax check, ShellCheck, Python syntax of the test files, validation of `examples/*.yaml`, variable inventory check, pytest unit tests, bats unit tests |
| `release-gate-matrix.yml` | push of a tag `v*` | All 11 Molecule scenarios as full `molecule test` sequences (create, converge, idempotence, verify, destroy) on nested test VMs, in two jobs (5 and 6 scenarios) that run one after the other |
| `release-gate-single.yml` | manual (`workflow_dispatch`) | One chosen Molecule scenario, same setup |

Notes:

- `test.yml` does not need VMs, but runs on the same runner, so it also pays the reset overhead.
- In the matrix workflow a red scenario does not stop the others in its job; the step fails at the end
  with the list of failed scenarios. `max-parallel` is 1, because there is only one runner.
- The slowest scenario (`authelia-file`, about an hour, it redeploys several times) sits in the group
  without the Authentik stacks, to keep both jobs well below the job time limit.
- A red run of `test.yml` on a push to `main` sends one chat message to the maintainer (the notifier
  is `scripts/notify-ci-failure.sh`, configured through a repository secret and variable). Pull
  requests and tags stay silent. A CI UI alone is a pull surface; a red `main` went unnoticed for a
  month before this existed.
- Checkout is a plain shallow `git clone` with the workflow token; the workflows do not depend on
  `actions/checkout`.

## 5. Limits and trust model

- **Tests only.** The runner VM holds no deploy credentials, no cloud tokens and no SSH keys to
  production systems. The workflows create no cloud resources, publish nothing and deploy nothing.
  The only secret a workflow reads is the chat token of the failure notifier.
- **Run only code you trust.** Jobs run as root in the runner VM, and a pull request workflow executes
  the code of that pull request. The reset protects the next job, not the current one. The design
  assumes a private forge with trusted contributors; do not point it at a public repository that
  accepts pull requests from strangers.
- **Network.** Guests may reach the public internet only. The host-side firewall drops traffic to
  private, link-local, carrier-grade-NAT, multicast and documentation ranges, drops anything with a
  source address other than the guest's own, caps the number of simultaneous connections per source
  address, and disables IPv6. The bridge has no DHCP; addresses are static through cloud-init, and
  the NIC is pinned to its address (`security.ipv4_filtering`). DNS is answered by the host, with
  public upstreams only.
- **Not covered.** vsock between a guest and its host cannot be filtered with netfilter. Isolation
  between Docker containers and nested test VMs inside the runner VM was not tested; Docker's own
  rules probably allow container-to-VM traffic. The effect of the per-NIC IP filter was not measured.
  The firewall protects the host and the network around it, not jobs from each other within one cycle.
- **Capacity.** One job at a time. Several nested VMs in parallel inside the runner VM and memory
  pressure there were not measured. Role-level scenarios that still need a cloud driver are in no
  workflow.
- **Failure modes.** If the forge or the CI firewall is unreachable at the start of a cycle, the VM
  stays off and the service backs off (30 s up to 15 minutes). A runner registered with the label
  `self-hosted` takes any waiting run of any workflow in repositories it is registered for, so
  disable old workflows before switching the service on.

## 6. Rebuilding it

You need: a Linux host with KVM and nested virtualization, Incus with a storage pool you can snapshot
(ZFS here), and a forge with Actions and an API token that may create runners. In outline:

1. Create a restricted Incus project with a dedicated bridge and load the firewall rules before the
   first guest can start. Cap the project's CPU and memory below the machine's.
2. Build the runner VM with a separate data block volume, install Incus (inner), Docker and the
   runner binary (verify its checksum), keep the job unit disabled.
3. Pre-load the Ubuntu image, start the pull-through registry on the inner bridge, warm the cache,
   pull the job images.
4. Verify there is no runner state in the VM, then snapshot VM and data volume as `clean`.
5. Run the cycle service from section 2. Put the forge token on the host only, readable by root.
6. Run the unit-test workflow (`test.yml`) first, then one Molecule scenario by hand with
   `workflow_dispatch`, then the tag-triggered matrix.
