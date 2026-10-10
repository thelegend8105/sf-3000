# The lab's release upgrade: four procedures, one per visit

CANDIDATES, not yet vetted. Together they move a machine from Ubuntu 22.10
(end of life) to 26.04 LTS in place, one release at a time:

| visit | procedure                  | from         | to           |
|-------|----------------------------|--------------|--------------|
| 1     | `release-upgrade-to-23.04` | 22.10 kinetic | 23.04 lunar  |
| 2     | `release-upgrade-to-23.10` | 23.04 lunar  | 23.10 mantic |
| 3     | `release-upgrade-to-24.04` | 23.10 mantic | 24.04 noble  |
| 4     | `release-upgrade-to-26.04` | 24.04 noble  | 26.04 resolute |

Each is one upgrade, then a reboot, then the check. Visit 4 first installs
24.04's waiting updates and reboots, because its upgrade refuses otherwise
(below). Each runs with the person there, in a lab slot of 2 to 2.5 hours,
and the engine stops when the check passes. Ubuntu cannot skip a release, so 23.x is two visits. 24.04 is a
good place to pause: it is supported until 2029.

**Visits 1 to 3 healed on the 22.10 VM on 2026-10-10**, one after the
other, with no restore between them. From the apt check to the end of the
upgrader, visit 1 took 34 minutes (`evidence/ubuntu-22.10-2026-10-10-2.jsonl`),
visit 2 took 20 (`-3.jsonl`), and visit 3 took 27 (`-4.jsonl`). Visit 4's
upgrade has not healed yet. The same evening it was made to fail twice on
purpose, with the disk filled up (Runs E and F in `tests/procedure-proof/`,
`-5.jsonl`). The engine restored 24.04 both times.

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
- **Keeps room to restore.** While it runs, the engine sets aside 256 MB of
  the disk. If a step fails, it frees that first, so a disk that filled up
  during the upgrade still leaves room to record the failure and restore.
  Run F showed it: the disk filled halfway through the 26.04 install, and
  the restore still ran.
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
- **From 24.04 on**, Ubuntu's own lines are in
  `/etc/apt/sources.list.d/ubuntu.sources` instead (the upgrade to 24.04
  moves them there). To turn one block off, add the line `Enabled: no` to it.

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
the upgrader by hand would have been asked. Visit 2 went the same way for
23.10.

The 24.04 upgrader replaces old-releases addresses with the main archive, and
visit 3 showed it. It tries the country mirror for the machine's locale
first, then `archive.ubuntu.com`. On the VM (`en_GB`) all ten lines moved to
`gb.archive.ubuntu.com`, `-security` included. At its end it moved them from
`/etc/apt/sources.list` to `/etc/apt/sources.list.d/ubuntu.sources`, in the
deb822 format.

## `do-release-upgrade`, visits 3 and 4

Read in its source (23.10 and 24.04). Before it starts the upgrader, it quits
with exit 1, changing nothing, if:

- `/etc/update-manager/release-upgrades` says `Prompt=never`. (`Prompt=lts`
  is ignored on a release that is not an LTS.)
- any update is still waiting to be installed. A held package counts.
- `/var/run/reboot-required.pkgs` lists a kernel, `linux-base` or `libc6`.

So check each machine first. None of these change anything:
`do-release-upgrade -c` should say "New release ... available",
`apt-mark showhold` should print nothing, and
`cat /var/run/reboot-required.pkgs` should find no file.

On 23.10 (visit 3) the last two do not come up by themselves: it gets no
updates any more. On 24.04 (visit 4) they do. It still gets updates, and the
lab's visits are days apart, so a new kernel is usually waiting by then. So `release-upgrade-to-26.04` has two
steps. `updates` installs what waits and reboots; `to-26.04` then runs
`do-release-upgrade`, with nothing left waiting. With nothing waiting at the
start, step 1 is skipped.

Its own messages ("Checking for a new Ubuntu release", the signature check)
reach the step's log only when it fails. When it succeeds, it replaces itself
with the upgrader, and Python drops its unwritten output.

## The upgrader's own checks

Read in the 23.04 upgrader's source, and seen in the `main.log` of visits 1
to 3:

- **The EFI partition.** On a UEFI machine, the upgrader refuses to start
  unless `/boot/efi` is mounted read-write ("EFI System Partition (ESP) not
  usable"). The lab's machines are UEFI. The VM boots with BIOS, so it
  skipped this check ("Not an UEFI system").
- **Free space.** It works out what each folder needs before it downloads.
  In visit 1 it needed about 1.8 GB on `/`, with 12.2 GB free. In visit 2 it
  needed 1.2 GB, with 11.6 GB free. In visit 3 it needed 2.9 GB, with
  12.7 GB free. The 26.04 upgrader needed 2.7 GB, 1.4 GB of it for its
  download.
  If there is not enough, it stops before downloading, puts the old apt
  sources back and exits 1; the engine then restores the snapshot. Run E
  showed this, with 365 MB free: its `main.log` says "The upgrade needs a
  total of 2,734 M free space on disk '/'".
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

Visit 2: 20 minutes. Timeshift was already there, and 23.04 had no pending
updates. The upgrader took 16, of which about 6 were its download.

Visit 3: 27 minutes. The upgrader took 24, of which about 10 were its
download (1.3 GB).

Visit 4 has not healed yet. In Run F, the 26.04 upgrader's download (1.4 GB)
took 12 minutes.

The 23.04, 23.10 and 24.04 upgraders install in three passes: a dry run,
then libc6 alone, then everything else. If the libc6 pass fails, the upgrader
stops with exit 1, and the engine restores the snapshot. The 26.04 upgrader
has no libc6 pass. All this was read in their source; visits 1 to 3 showed
the three passes, and Run F the 26.04 upgrader's two.

## No terminal

Every upgrade runs under the engine's service, with no terminal:

- The upgrader's `DistUpgradeViewNonInteractive`:
  - answers yes to every question
  - keeps the existing version of any changed config file (seen in visit 3,
    for `/etc/fwupd/fwupd.conf`)
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
