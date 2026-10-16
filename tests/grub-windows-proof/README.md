# grub-windows-proof — VM runs for `windows-missing-from-grub`

**Ran on 2026-10-16 on `KineticDesktop`** (Ubuntu 22.10 desktop, EFI, with
stand-in Windows parts from `tests/efi-proof/`). The entry healed twice and
rolled back once, as planned. It read healthy in every case where it should.
The records are in `evidence/ubuntu-22.10-2026-10-16-3.jsonl`, `-4` and `-5`.
The playbook that ran is commit `aff2c9c` on branch
`fix/windows-missing-from-grub`. Only its `source:` line changed after.

## The problem

A dual-boot machine starts Ubuntu with no menu, so Windows seems gone. It is
not gone. The firmware still has its boot entry, and its files are on the
EFI partition. Only GRUB's menu stopped listing it.

How it happens, read on the VM:

- `/usr/sbin/grub-mkconfig` sets `GRUB_DISABLE_OS_PROBER="auto"` before it
  reads `/etc/default/grub`. Ubuntu's file does not set it, so "auto" stays.
- With "auto", `/etc/grub.d/30_os-prober` looks for other systems only when
  grub.cfg does not exist yet, or already holds an entry os-prober found.
- So one rebuild that misses Windows hides it for good. Each later rebuild
  sees no os-prober entry and skips the search.
- Without another system listed, GRUB also hides its menu. With one listed,
  `30_os-prober` sets `timeout_style=menu`.

GRUB 2.06 first shipped with the search off. Ubuntu added "auto" later
(LP: #1955109). A machine that rebuilt its menu once in between kept the
problem.

## The trap, made by hand

The environment cannot set the trap: `grub-mkconfig` sets the default without
looking at it. So the trap is one rebuild with the search off in the config,
then the config put back byte for byte:

```sh
sudo cp -p /etc/default/grub /root/grub.orig
echo 'GRUB_DISABLE_OS_PROBER=true' | sudo tee -a /etc/default/grub
sudo update-grub                     # "os-prober will not be executed"
sudo cp -p /root/grub.orig /etc/default/grub && sudo rm /root/grub.orig
md5sum /etc/default/grub             # e7e2c4ab..., as before
sudo update-grub                     # still "will not be executed": the trap holds
```

## The VM's snapshots

| snapshot | state |
|----------|-------|
| `grub-base` | `efi-ready`, plus python3-jsonschema (installed by the engine after a y) and the playbook copied into `~/sf-3000/candidates/`. GRUB lists Windows. Healthy. |
| `grub-trap` | `grub-base` with the trap set. The entry reads PROBLEM 1. `~/grub-windows-before.txt` is `check-windows.sh` before the trap. The demo's starting point. |

## Steps

```sh
# 0. From grub-trap (Windows), then ssh -p 2228 rht@127.0.0.1, cd ~/sf-3000

# --- Run 1 and Run 3: the fix heals --------------------------------------
python3 engine/runner.py --playbooks candidates          # as rht
#   ✗ [PROBLEM] windows-missing-from-grub measured=1, nothing ERROR
sudo bash ~/efi-tools/check-windows.sh > ~/grub-windows-trap.txt
sudo python3 engine/runner.py --playbooks candidates --fix windows-missing-from-grub
#   Answer y. "fix exited 0 — verifying", "verified healthy (measured=0)",
#   outcome: healed.
sudo bash ~/efi-tools/check-windows.sh > ~/grub-windows-after-fix.txt
diff -I '^#' ~/grub-windows-before.txt ~/grub-windows-after-fix.txt
#   no output: everything as before the trap
diff -I '^#' ~/grub-windows-trap.txt ~/grub-windows-after-fix.txt
#   only "> Windows Boot Manager (on /dev/nvme0n1p1)" in GRUB's menu section
sudo reboot     # a warm reboot shows the menu with Windows for 10 s

# --- Run 2: the fix cannot work, so the engine undoes it ---------------------
# From grub-trap again. Leave a stale firmware entry, as when Windows' files
# are gone but its entry stays:
sudo mv /boot/efi/EFI/Microsoft/Boot/bootmgfw.efi /root/bootmgfw.efi.moved
sudo python3 engine/runner.py --playbooks candidates --fix windows-missing-from-grub
#   Answer y. "fix ran but the machine is still unhealthy (measured=1)",
#   "rolling back", "rollback succeeded", outcome: rolled_back.
md5sum /etc/default/grub                 # e7e2c4ab..., as before
ls /etc/default/grub.sf3000.bak          # gone
sudo mv /root/bootmgfw.efi.moved /boot/efi/EFI/Microsoft/Boot/bootmgfw.efi

# --- The healthy cases (a sweep writes no record) -----------------------------
# A. grub-base as it is (GRUB lists Windows)                  -> HEALTHY 0
# B. GRUB_DISABLE_OS_PROBER=true in /etc/default/grub, or "true" in a
#    /etc/default/grub.d/*.cfg drop-in, then update-grub         -> HEALTHY 0
# C. no Windows entry in the firmware (efibootmgr -b 0001 -B)    -> HEALTHY 0
# D. grub.cfg at 0600, as Ubuntu leaves it when a GRUB password is set:
#    ERROR as rht (it cannot tell, so it does not guess), PROBLEM as root

# --- Copy the logs off before any restore -------------------------------------
# ~/sf-3000/logs/runs.jsonl holds the --fix records. Copy it verbatim to
# evidence/ (naming in evidence/README.md).
```

## What happened (2026-10-16, UTC; add 05:30 for IST)

| run | time | record | before → after | wall time, y to outcome |
|-----|------|--------|----------------|-------------------------|
| 1 | 13:59:07 | `healed` | 1 → 0 | 5.4 s |
| 2 | 14:03:01 | `rolled_back`, rollback_result `ok` | 1 → 1 | |
| 3 | 14:06:12 | `healed` | 1 → 0 | 4.9 s |

- Runs 1 and 3: `check-windows.sh` after the fix matched the record taken
  before the trap, every section. The only difference from the trap state
  was GRUB's menu, which listed Windows again. The fix added three lines to
  `/etc/default/grub`: a blank, a comment, and `GRUB_DISABLE_OS_PROBER=false`.
  A rebuild after it said "os-prober will be executed" and found Windows.
- Run 2: os-prober found nothing, so the verify read 1 again. The undo put
  `/etc/default/grub` back byte for byte (md5 `e7e2c4ab...`) and rebuilt the
  menu. The backup was gone, and a rebuild after it skipped the search
  again: the machine was exactly as before the fix.
- The healthy cases A to D read as listed in Steps.

**What the screen shows.** Seen with `VBoxManage controlvm KineticDesktop
screenshotpng`, about two frames a second, after `sudo reboot`:

- With Windows listed (`grub-base`, and after each fix): GRUB's menu, with
  "Ubuntu", "Advanced options for Ubuntu", "Windows Boot Manager (on
  /dev/nvme0n1p1)" and "UEFI Firmware Settings", counting down from 10 s.
- In the trap: no menu. The firmware's logo, then a black screen for about
  10 s (GRUB waiting with its menu hidden), then Ubuntu's boot logo.
- A cold start right after a snapshot restore showed no menu even with
  Windows listed. The firmware's logo was followed within a second by the
  kernel. The likely cause is the host's slow virtual clock (VirtualBox on
  Hyper-V) jumping forward past GRUB's countdown. Not checked further. A
  warm reboot showed the menu every time. For a demo, use a warm reboot.

The screenshots and every copied file are in `logs/grub-windows-proof-run-1`,
`-run-2`, `-run-3` and `-cases` (not in git).

**Not proven here.**

- A real Windows starting from the restored menu entry. The VM's Windows is
  random bytes.
- BIOS machines. The detect reads 0 there by its first line; no BIOS VM ran it.
- Other releases. Only 22.10's GRUB (2.06-2ubuntu14.1) ran it.
- A stale firmware entry reads PROBLEM. Without root the detect cannot see
  the ESP, so it cannot tell a live Windows entry from a dead one. The fix
  then fails its verify and is undone (Run 2). Safe, but a false alarm.

## For the merge

Draft text for the files this branch does not touch.

**evidence/README.md**

> ### `ubuntu-22.10-2026-10-16-3.jsonl`
>
> Run 1 of `tests/grub-windows-proof/`: `windows-missing-from-grub` on
> `KineticDesktop`, from VBox snapshot `grub-trap`, playbook from commit
> `aff2c9c`. One `--fix` record: `healed`, 1 → 0, at 19:29 IST.
> `check-windows.sh` after the fix matched the record from before the trap.
>
> ### `ubuntu-22.10-2026-10-16-4.jsonl`
>
> Run 2: the same, with Windows' boot file moved off the ESP first, so
> os-prober could find nothing. One record: `rolled_back`, verify_after 1,
> rollback_result `ok`, at 19:33 IST. `/etc/default/grub` came back byte for
> byte.
>
> ### `ubuntu-22.10-2026-10-16-5.jsonl`
>
> Run 3: Run 1 again from `grub-trap`. `healed`, 1 → 0, at 19:36 IST, 4.9 s
> from the y.
>
> Each file holds one record because each run began from a snapshot restore,
> which deletes the clone's run log.

**DESIGN.md §16 entry**

> 2026-10-16 — `windows-missing-from-grub` reads the firmware and GRUB's
> menu, never the ESP. A sweep runs without root, and Ubuntu mounts the ESP
> fmask=0077, so a person cannot list it. Users can read the firmware's boot
> entries and `/boot/grub/grub.cfg`, and those answer the question. It never
> runs os-prober, because os-prober mounts partitions and a detect must not
> change the machine. The price: a stale Windows entry reads PROBLEM, and the
> fix is then undone by its verify. A deliberate `GRUB_DISABLE_OS_PROBER=true`
> reads healthy: it is a choice, not a fault. The fix writes Ubuntu's own
> setting in `/etc/default/grub`, where Ubuntu documents it, rather than a
> drop-in. It takes a fresh backup each time (`cp -p`, not `cp -n`), so the
> undo puts back exactly what that fix changed: `cp -n` would keep an older
> backup and undo an earlier fix too.

**DESIGN.md §12/§13 status lines**

> `windows-missing-from-grub` (candidate): healed twice and rolled back once
> on the EFI 22.10 desktop VM, 2026-10-16 (`tests/grub-windows-proof/`).
> Healthy in all four cases that must not fire. Not yet run with a real
> Windows, on BIOS, or on another release.

**README.md status line**

> A new candidate, `windows-missing-from-grub`, puts Windows back in GRUB's
> start-up menu on an EFI dual-boot machine. It healed on the EFI 22.10 VM in
> about 5 s, and the engine undid it when it could not work.

**docs/sf3000-tracker.xlsx**

Ubuntu sheet, a new row:

| column | value |
|--------|-------|
| ID | windows-missing-from-grub |
| Problem | Windows missing from the start-up menu (dual-boot, EFI) |
| Category | Boot |
| Detect (read-only) | efibootmgr -v has bootmgfw.efi; /boot/grub/grub.cfg has not; GRUB_DISABLE_OS_PROBER not true |
| "Healthy" = | 0 (GRUB lists Windows, or no Windows, or the search is off on purpose) |
| Fix (sketch) | GRUB_DISABLE_OS_PROBER=false in /etc/default/grub (backup first), update-grub |
| Risk | moderate |
| Repo file | candidates/windows-missing-from-grub.yaml |
| Status | Testing |
| Verified detect cmd | yes (22.10 EFI desktop VM, as rht and as root) |
| Verified fix cmd(s) | yes (healed twice) |
| Reversible? / Undo | yes: the backup back, update-grub (rolled_back once) |
| Tested on (OS + ver) | Ubuntu 22.10 desktop, EFI (KineticDesktop) |
| Notes / gotchas | "auto" hides Windows for good after one rebuild without it. A stale Windows entry reads PROBLEM; the verify then undoes the fix. grub.cfg at 0600 (GRUB password): ERROR as a user. |
| Last VM run | 2026-10-16 |
| Next step | a real dual-boot machine; another release |

Run log sheet, three rows:

| Date (IST) | Release | Commit | Playbook | Outcome | Detail | Evidence |
|------------|---------|--------|----------|---------|--------|----------|
| 2026-10-16 19:29 | 22.10 | aff2c9c | windows-missing-from-grub | healed | Run 1 (tests/grub-windows-proof): 1 → 0, 5.4 s; Windows' parts unchanged | evidence/ubuntu-22.10-2026-10-16-3.jsonl |
| 2026-10-16 19:33 | 22.10 | aff2c9c | windows-missing-from-grub | rolled_back | Run 2: Windows' boot file moved away; verify 1; /etc/default/grub back byte for byte | evidence/ubuntu-22.10-2026-10-16-4.jsonl |
| 2026-10-16 19:36 | 22.10 | aff2c9c | windows-missing-from-grub | healed | Run 3: Run 1 again, 4.9 s | evidence/ubuntu-22.10-2026-10-16-5.jsonl |
