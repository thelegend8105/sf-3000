# The lab's release upgrade: four procedures, one per visit

CANDIDATES, not yet vetted. Together they move a machine from Ubuntu 22.10
(end of life) to 26.04 LTS in place, one release at a time:

| visit | procedure                  | from         | to           |
|-------|----------------------------|--------------|--------------|
| 1     | `release-upgrade-to-23.04` | 22.10 kinetic | 23.04 lunar  |
| 2     | `release-upgrade-to-23.10` | 23.04 lunar  | 23.10 mantic |
| 3     | `release-upgrade-to-24.04` | 23.10 mantic | 24.04 noble  |
| 4     | `release-upgrade-to-26.04` | 24.04 noble  | 26.04 resolute |

Each is one upgrade, then a reboot, then the check. It runs with the person
there, in a lab slot of 2 to 2.5 hours, and the engine stops when the check
passes. Ubuntu cannot skip a release, so 23.x is two visits. 24.04 is a
good place to pause: it is supported until 2029.

**Visit 1 healed on the 22.10 VM on 2026-10-10**, in 34 minutes from the apt
check to the end of the upgrader (`evidence/ubuntu-22.10-2026-10-10-2.jsonl`).
Visits 2 to 4 have not run yet.

```bash
sudo python3 engine/runner.py --procedures candidates/procedures \
     --run release-upgrade-to-23.04 --take-snapshot   # visit 1; asks first
python3 engine/runner.py --status                     # keep the machine on until it says done
```

Run the wrong one and nothing happens. Each one requires the release it
upgrades from, and the engine then names the one that applies.

## What the engine does around each one

- **Checks apt first.** `apt-get update` must reach every source. A dead
  one (a third-party repository with no 22.10, a mirror that does not exist)
  stops the run before the y/N, with nothing changed.
- **Installs Timeshift** with apt after the y, if it is missing.
- **Takes a Timeshift snapshot** (not `/home`, not `/boot/efi`). If the
  upgrade fails, or its check does not pass after the reboot, the engine
  restores the snapshot and reboots by itself, then checks the machine is
  back. `/boot/efi` is left out because on the lab's machines it is the
  Windows disk's EFI partition: a restore must not rewrite Windows' boot
  files.
- **Holds apt's lock while it snapshots or restores,** so the automatic
  updates cannot change packages in the middle. It also writes the snapshot
  to disk before the upgrade starts.
- **Keeps the upgrader's logs through a restore.** The upgrader writes why it
  failed only to `/var/log/dist-upgrade/main.log`. That folder is left out
  of the snapshot, so after a failed upgrade is restored, the log is still
  there to read.
- **Waits after a cut-off.** If the machine goes down mid-upgrade, the boot
  after does not restore by itself. Running the same command again offers the
  restore; `--cancel` leaves the machine as it is.

## If the apt check refuses

It names each source that failed. Turn each one off, then run the same
command again:

- **A file in `/etc/apt/sources.list.d/`** (such as `cloudflare-client.list`):
  move it out, `sudo mv /etc/apt/sources.list.d/cloudflare-client.list /root/`
- **A line in `/etc/apt/sources.list`** (such as one for
  `in.old-releases.ubuntu.com`): put a `#` at its start, with
  `sudo nano /etc/apt/sources.list`

The upgrade turns third-party sources off by itself anyway, so the machine
loses nothing it would have kept. A source listed twice is only a warning,
and the check lets it through.

## Why 23.04 and 23.10 fetch their upgrader by hand

Checked 2026-10-07 in the source (update-manager's `MetaRelease.py`, branch
ubuntu/kinetic). Plain `do-release-upgrade` skips every newer release that
changelogs.ubuntu.com/meta-release marks "Supported: 0". 23.04 and 23.10 are
both marked so. On 22.10 it would therefore offer 24.04 directly, but the
24.04 upgrader is built to upgrade from 23.10 (its DistUpgrade.cfg says
`From=mantic`), not 22.10. `-d` does not help either: meta-release-development
no longer lists 22.10.

The offer shows up on its own. On the 22.10 VM, once apt pointed at
old-releases, the login message said "New release '24.04.5 LTS' available.
Run 'do-release-upgrade' to upgrade to it." (2026-10-08). The lab's machines
will say the same. Do not accept it.

So these two steps do what Ubuntu's EOLUpgrades page describes. They download
the next release's upgrader from old-releases, from the same URLs that
meta-release lists. They check its signature against Ubuntu's archive keyring,
the same keyring `do-release-upgrade` itself checks with. Then they run it.

24.04 and 26.04 use ordinary `do-release-upgrade`. From 23.10 the next
supported release is 24.04, and from 24.04 it is 26.04 (LTS to LTS).

## Old-releases

The 23.04 and 23.10 upgraders look for their release on the main archive and
on a country mirror first. In visit 1 both answered 404 for 23.04, so the
upgrader logged `ERROR No valid mirror found` and asked whether to rewrite
`sources.list` anyway. The non-interactive frontend answers yes, so every
line moved to 23.04 on old-releases, `-security` included. A person running
the upgrader by hand would have been asked.

The 24.04 upgrader replaces old-releases addresses with the main archive. So
the upgrade to 24.04 should move apt back to the main archive by itself. This
was read in the source; it has not yet been seen on a machine.

## The upgrader's own checks

Read in the 23.04 upgrader's source, and seen in visit 1's `main.log`:

- **The EFI partition.** On a UEFI machine, the upgrader refuses to start
  unless `/boot/efi` is mounted read-write ("EFI System Partition (ESP) not
  usable"). The lab's machines are UEFI. The VM boots with BIOS, so it
  skipped this check ("Not an UEFI system").
- **Free space.** It works out what each folder needs before it downloads.
  In visit 1 it needed about 1.8 GB on `/`, with 12.2 GB free.
- **Who started it.** Running as root, it looks for `SUDO_UID` or
  `PKEXEC_UID`, to ask that user's desktop not to lock the screen. The
  engine's service has neither ("failed to determine user upgrading"). So on
  a desktop the screen may lock during the upgrade. The upgrade carries on.

## How long

Visit 1 on the VM (a server, 1 CPU): 34 minutes from the apt check to the end
of the upgrader. Timeshift's install took about 2 minutes, and the first
snapshot 2 minutes. `full-upgrade` installed 22.10's 137 pending updates in
9 minutes. The upgrader took 19, of which about 5 were its download. A
desktop has more packages, so expect longer.

## No terminal

Every upgrade runs under the engine's service, with no terminal:

- The upgrader's `DistUpgradeViewNonInteractive`:
  - answers yes to every question
  - keeps the existing version of any changed config file
  - does not reboot by itself (`RealReboot=no`); the engine reboots instead
- `apt-get` gets `--force-confdef`/`--force-confold`, for the same
  config-file choice. It also gets `DEBIAN_FRONTEND=noninteractive`, so
  debconf asks nothing.
- `NEEDRESTART_SUSPEND=1` stops needrestart from restarting services
  mid-upgrade, including the engine's own service.
- `RELEASE_UPGRADER_NO_SCREEN=1` stops the upgrader from re-running itself
  inside GNU screen, which needs a terminal.

## The check

Each upgrade's goal is "this release or newer, and dpkg reports no
half-installed packages" (`dpkg --audit` prints nothing). VERSION_ID 22.10
reads as 2210, so "23.04 or newer" is "greater than 2303".

If a machine is already at the goal, the engine says there is nothing to do.
Each procedure also requires the exact release it upgrades from, so it never
runs an upgrader built for another release.
