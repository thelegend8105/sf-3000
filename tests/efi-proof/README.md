# efi-proof — visit 1 and its restore on an EFI dual-boot desktop

**Both runs passed on 2026-10-16**, at engine commit `2415fcd`. Run 1
healed in 48 minutes. Run 2 failed on purpose and was restored. Neither
changed anything of Windows'. The records are in
`evidence/ubuntu-22.10-2026-10-16.jsonl` and `-2.jsonl`. "What happened",
at the end, has the details.

The lab's machines are not like any VM the engine had run on before. Those
VMs were BIOS servers. The lab's machines are EFI desktops
(GNOME), and they dual-boot Windows. Their EFI system partition (ESP), which
`/boot/efi` mounts, is on the Windows disk. It holds Windows' boot files
beside Ubuntu's. Windows must not be touched. These runs prove three things
before visit 1 runs on a lab machine (computer 03, 2026-10-21):

- visit 1 (22.10 to 23.04) heals on an EFI desktop, and how long it takes;
- when visit 1 fails partway, the engine's restore brings the machine back
  to 22.10, and it boots;
- neither the upgrade nor the restore changes anything of Windows': its files
  on the ESP, its partitions, its boot entry, or the boot order.

| Run | what it proves |
|-----|----------------|
| 1 | Visit 1 heals on an EFI desktop. Windows' parts are unchanged after it. |
| 2 | Visit 1 fails partway through the install. The restore crosses back from a half-upgraded 23.04 to 22.10 on EFI. Windows' parts are unchanged after it. |

## The VM

`KineticDesktop`, built 2026-10-16, SSH on 127.0.0.1:2228. It copies lab
computer 03 where that matters (03's reports of 2026-10-07 and 2026-10-14):

| | 03 | the VM |
|-|----|--------|
| firmware | EFI, Secure Boot off | the same |
| Ubuntu | 22.10 desktop (GNOME), locale en_IN | the same, installed offline from `ubuntu-22.10-desktop-amd64.iso` |
| `/` | ext4 on a hard disk (sda), no separate /home or /boot | ext4 on SATA disk sda (60 GB), the same |
| Windows disk | NVMe: ESP 100M, MSR 16M, NTFS, recovery NTFS 700M | NVMe (2 GB): ESP 94M, MSR 16M, NTFS 1.2G, recovery NTFS 736M |
| ESP | 96M, 26M free: EFI/Boot, EFI/HP, EFI/Microsoft, EFI/ubuntu | 93M, 26M free: the same folders, and a filler file |
| boot order | ubuntu, then Windows Boot Manager | the same |
| GRUB menu | Ubuntu, Advanced options, Windows Boot Manager (on /dev/nvme0n1p1), UEFI Firmware Settings | the same |
| apt | old-releases, set by hand | the same (`sed` below) |
| 22.10 updates | installed (by the engine's step on 2026-10-14) | installed by hand before the runs |

What differs: 1 vCPU and 8 GB (03 has 20 threads and 15 GiB), VMSVGA
graphics (03 has NVIDIA 535), and Windows itself. The VM's Windows parts are
stand-ins of random bytes, made by `make-windows-standins.sh`. A stand-in
proves the files stay as they were. It cannot prove Windows still starts.

Graphics, on 22.10's kernel 5.19 with EFI and 1 vCPU: only VMSVGA works.
VBoxSVGA froze the login screen; VBoxVGA left it blank.

## The tools

- `make-windows-standins.sh` makes the stand-in Windows parts, once.
- `check-windows.sh` prints everything Windows owns, and writes nothing.
  Run it before and after, then `diff -I '^#' before.txt after.txt`. It works
  on a lab machine too: a partition over 4 GiB gets only its first 64 MiB
  hashed. Two sections may differ: EFI/ubuntu, which is Ubuntu's, and
  EFI/BOOT when it holds Ubuntu's shim (the section says which). Every other
  line must match.
- `../procedure-proof/fill-disk.py mid-install to-23.04` fills the disk once
  the upgrader is installing, for Run 2.

## Steps

```sh
# 0. Setup, once (done 2026-10-16). In the VM's own terminal, typed:
#      sudo sed -i 's/in.archive/old-releases/; s/security.ubuntu/old-releases.ubuntu/' /etc/apt/sources.list
#      sudo apt-get update                  # only Hit:/Get:, no Err:
#      sudo apt-get install -y openssh-server
#    Then over ssh -p 2228 rht@127.0.0.1:
#      sudo apt-get install -y git
#      git clone https://github.com/thelegend8105/sf-3000.git ~/sf-3000
#      sudo bash ~/sf-3000/tests/efi-proof/make-windows-standins.sh
#    It ends with the boot order "ubuntu, Windows Boot Manager, ...", the ESP
#    26M free, and "GRUB's menu lists Windows Boot Manager".
#    The 22.10 updates by hand, as 03 has them, then a reboot:
#      sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade
#      sudo reboot
#    The state to compare against:
#      sudo bash ~/sf-3000/tests/efi-proof/check-windows.sh > ~/windows-before.txt
#    Power off, then on Windows:
#      E:\VirtualBox\VBoxManage.exe snapshot KineticDesktop take efi-ready

# --- Run 1: visit 1 heals ---------------------------------------------------
cd ~/sf-3000
sudo python3 engine/runner.py --procedures candidates/procedures --run release-upgrade-to-23.04 --take-snapshot
# First "The engine needs jsonschema ...": answer y (an engine/install record).
# Then the box: "install   : timeshift", one step to-23.04, 1 reboot. Answer y.
# (On 2026-10-16 the first pass answered n at the box, as the lab kit does,
# to read the box first. That logs a `declined` record in the clone's logs/.)
# Over ssh the answers can be piped: printf 'y\ny\n' | sudo python3 ...
# journalctl -fu sf3000-procedure: the snapshot (a full copy), to-23.04
# running, the reboot. After it:
sudo python3 engine/runner.py --status          # phase done
grep VERSION= /etc/os-release                   # 23.04 (Lunar Lobster)
sudo bash tests/efi-proof/check-windows.sh > ~/windows-after-run-1.txt
diff -I '^#' ~/windows-before.txt ~/windows-after-run-1.txt
# Only EFI/ubuntu (and EFI/BOOT, if it holds Ubuntu's shim) may differ, and
# GRUB's menu may gain an entry of Ubuntu's. And on Windows, a screenshot
# shows GNOME's login screen:
#   E:\VirtualBox\VBoxManage.exe controlvm KineticDesktop screenshotpng <file>

# --- Run 2: visit 1 fails partway; the restore -------------------------------
# Copy Run 1's logs off first (below). Power off, restore efi-ready, start.
# Second session first; it waits:
sudo python3 tests/procedure-proof/fill-disk.py mid-install to-23.04
# First session: the same --run as Run 1, the same two y.
# The upgrader downloads, then installs. A minute into the install the second
# session prints "filled ... MB", and dpkg starts to fail. Then: exit 1,
# "freed the engine's 256 MB reserve", "restoring Timeshift snapshot ...",
# and a reboot. After it:
grep VERSION= /etc/os-release                   # 22.10 again
sudo bash tests/efi-proof/check-windows.sh > ~/windows-after-run-2.txt
diff -I '^#' ~/windows-before.txt ~/windows-after-run-2.txt
# The same rule as Run 1. (On 2026-10-16 nothing differed at all: the
# restore put 22.10's own GRUB back in EFI/ubuntu.)

# --- Copy the logs off (on Windows, from the repo) ---------------------------
#   scp -r -P 2228 rht@127.0.0.1:/var/log/sf3000 logs/kinetic-desktop-run-<n>
#   and /var/log/dist-upgrade (copied to ~ with sudo cp -r and chown first),
#   and the windows-*.txt files.
```

The records to expect:

- Run 1: `engine/install installed` (python3-jsonschema),
  `release-upgrade-to-23.04/install installed` (timeshift), and
  `to-23.04 healed`, verify_after 2304.
- Run 2: the same two install records, then `to-23.04 rolled_back fix_failed`
  with rollback_result `ok: back to 22.10 packages:...`.

## What happened (2026-10-16)

Claude ran both runs over ssh, at the user's request. The engine was the
clone at `2415fcd`. The tools here were not committed yet: they were copied
to the VM by scp. They are committed as they ran, except for three changes
made after use. `make-windows-standins.sh` now does the two setup steps
below that were done by hand. And `check-windows.sh` now reads a GRUB menu
title in double quotes too: after Run 1 it printed the new memtest entry's
whole line. Run 2's check used the fixed version.

**Setup.** Two things went differently from the steps above, and the script
now does both:

- GRUB's menu did not list Windows after the script's `update-grub`.
  Ubuntu's GRUB treats an unset `GRUB_DISABLE_OS_PROBER` as "auto": os-prober
  runs only when grub.cfg does not exist yet, or already holds an entry it
  found (`/etc/grub.d/30_os-prober`). The VM's grub.cfg was made before the
  stand-ins, so os-prober was skipped. Moving grub.cfg aside for one run
  fixed it, by hand. 03 found Windows at its install, so it keeps looking.
- `mkntfs` gave both NTFS partitions the same serial number: it seeds it
  from the clock, and both were made in one second. `ntfslabel
  --new-serial` gave the recovery partition its own, by hand.

The 22.10 updates by hand (287 packages, 13 minutes) did two things worth
knowing. The SBAT level went from `sbat,1,2021030218` to
`sbat,1,2022052400 grub,2`, as 03's did on 2026-10-14. And shim 15.7
rewrote EFI/BOOT, as well as EFI/ubuntu. EFI/BOOT held Ubuntu's shim, the
firmware's fallback copy, so it is Ubuntu's to update. So `check-windows.sh`
now gives EFI/BOOT its own section. 03's EFI/Boot is most likely the same:
1.9 MB, the size of shim, mm and fb together, and dated after
EFI/Microsoft. Every stand-in kept its hash through the updates.

**Run 1** (records in `evidence/ubuntu-22.10-2026-10-16.jsonl`):

| IST | what |
|-----|------|
| 17:10:56 | `engine/install installed`: python3-jsonschema, after a y. The first time a VM showed it: the server VMs have it already. |
| 17:11:45 | the y at the box; Timeshift installed |
| 17:11:46 | the snapshot, of `22.10 packages:af890f897a40621d`: 5 min 14 s, a full copy of 13 GB |
| 17:17:34 | the upgrader starts. main.log: "run in 'desktop' mode, (because of pkg 'ubuntu-desktop')" |
| 17:20 to 17:32 | its download |
| 17:32 to 17:58 | its install, in passes; the main one took 17 minutes |
| 17:58:51 | the step exits 0; the engine reboots |
| 17:59:48 | the check after reads 2304: `to-23.04 healed` |

48 minutes from the y to the end. The machine came up on 23.04 with
kernel 6.2.0-39 and Python 3.11, at GNOME's login screen (screenshot in the
copies). `ubuntu-desktop` stayed installed. The kernel's metapackage on this
desktop is `linux-generic-hwe-22.04`: the upgrader moved it to 6.2.0-39 and
added `linux-generic`. apt now points at lunar on old-releases, which visit
2 needs. The upgrader logs "No valid mirror found", then sees the upgrade is
from old-releases and rewrites the sources there itself.

`check-windows.sh` after Run 1 differed from before in two lines only:
`EFI/ubuntu/grubx64.efi` (lunar's GRUB) and a new memtest entry in GRUB's
menu. Boot entries and their order, the partition table, every stand-in on
the ESP, EFI/BOOT, and the raw bytes of the MSR and both NTFS partitions
were the same. os-prober ran during the upgrade, so its read-only mounts of
the NTFS partitions changed none of their bytes.

**Run 2** (records in `evidence/ubuntu-22.10-2026-10-16-2.jsonl`). The VM
went back to `efi-ready` first. `fill-disk.py mid-install to-23.04` ran as a
transient service (`systemd-run`), so it could not end with the ssh session.

| IST | what |
|-----|------|
| 18:03:41 | `engine/install installed` again |
| 18:04:07 | the snapshot, of the same `22.10 packages:af890f897a40621d` |
| 18:10:37 | the upgrader starts; the filler sees its stamp |
| 18:27:00 | dpkg unpacks; the filler waits 60 s, then fills `/` |
| about 18:28 | dpkg fails: no space for the locales package's script, then for dpkg's own status. The upgrader: "Could not install the upgrades ... A recovery will run now (dpkg --configure -a)". Exit 1. |
| 18:28:07 | the engine restores. Timeshift syncs, then GRUB: "Installing for x86_64-efi platform. Installation finished. No error reported." `update-grub` finds Windows Boot Manager. A reboot. |
| 18:29:43 | "restored: the machine is back as it was before step 1 (22.10 packages:af890f897a40621d)" |

The record: `to-23.04 rolled_back fix_failed`, rollback_result `ok: back to
22.10 packages:af890f897a40621d`. No leftovers were found to stop
(`leftovers_stopped` null). The step's output ends with the upgrader's own
log, printed because it failed: the first time that print ran on a VM
(DESIGN.md §16, 2026-10-14). It held no lock message, so the engine
restored, which was right. dpkg's audit was clean, the fill file was gone,
and GNOME's login screen came up on 22.10.

`check-windows.sh` after Run 2 matched before **in every line**. The
restore reinstalled 22.10's own GRUB, so even EFI/ubuntu went back byte for
byte.

**What the runs did not show.** The VM has no NVIDIA card, so it cannot
show whether 03's NVIDIA driver (prebuilt `linux-modules-nvidia-535`) comes
through the upgrade. Its login screen could be missing after a `healed`
visit 1, because the engine's check reads only the release and dpkg's
state. The VM's Windows is a stand-in: these runs prove Windows' files and
boot entry are unchanged, not that Windows still starts. Both snapshots were
full copies; 03 has an older Timeshift snapshot, so its first one may link
to it.

The journal of the boot a restore undoes is rolled back with it: the
step's own log in `/var/log/sf3000/` is the record of that boot.

The copies, checked with md5: `logs/kinetic-desktop-setup/`,
`logs/kinetic-desktop-run-1/` and `logs/kinetic-desktop-run-2/`.
