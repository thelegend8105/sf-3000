# evidence — run logs copied off the test VMs

Each file here is a copy of a VM's run log, made before the VM was reverted:
`logs/runs.jsonl` in the clone for fixes, `/var/log/sf3000/runs.jsonl` for
procedures. A revert deletes it, and `logs/` is gitignored, so this folder is
the only lasting record of what the engine actually did on a machine.

**Rules**

- Copy the file as it is. Never edit a record, even to fix a typo.
- One file per copy, named `<distro>-<release>-<date of the last run>.jsonl`.
  If that name is taken, add `-2`, `-3` and so on.
- The tracker's Run log sheet points at these files, one row per record.

Copy it off the VM like this (on Windows, from the repo):

    scp -P 2222 rht@127.0.0.1:sf-3000/logs/runs.jsonl evidence/ubuntu-26.04-<date>.jsonl
    scp -P 2223 rht@127.0.0.1:/var/log/sf3000/runs.jsonl evidence/ubuntu-22.10-<date>.jsonl   # procedures

Each VM has its own SSH port: 2222 for 26.04, 2223 for 22.10. The tracker's
Ubuntu versions sheet lists them all.

**Timestamps are UTC.** The project's dates are IST (UTC+05:30), the same as
its commits. So a run logged at `2026-09-07T19:14Z` is dated 2026-09-08 in
the tracker and the docs.

**Older records have fewer fields.** The record format grew as the engine did.
`blocked_reason` appears from commit 841625b on, and `settle_seconds` from
46e6a5b on. A missing field means the engine that wrote the record predates
it. It does not mean the value was null.

## The files

### `ubuntu-26.04-2026-09-10.jsonl`

14 runs on `Ubuntu Server Test` (Ubuntu 26.04 LTS, resolute), from
2026-09-07 23:35 to 2026-09-10 22:36 IST. Copied off on 2026-10-03.

It does **not** hold the runs before 2026-09-07 23:35 IST. Something reset the
log that evening, after 21:46 IST. It was either a snapshot revert or a fresh
clone; nobody remembers which, and nothing recorded it. The only snapshot,
`clean-baseline`, was taken at 23:21 IST that evening, and restoring it brings
no run log back (checked 2026-10-04). So those records are gone for good. They
survive only as transcriptions in two commit messages:

- `541cb09` quotes five records read from the VM's log at the time:
  net-tools-missing `healed` (09-05 14:20, 09-05 16:18, 09-07 21:30) and
  rollback-proof `rolled_back` (09-05 16:17, 09-07 21:29).
- `a42e25c` describes boot-partition-full `healed`, 100% → 76% (09-07).

### `ubuntu-26.04-2026-10-04.jsonl`

Session 1: the fixtures in `tests/branch-proof/` and `tests/rollback-proof/`.
5 runs on `Ubuntu Server Test`, 2026-10-04 00:23 to 00:30 IST, commit
`69658de`, from a fresh clone after restoring `clean-baseline`.

- Line 1: `declined` (rollback-failed-proof, answered N).
- Line 2: `rollback_failed` (rollback-failed-proof, answered y).
- Line 3: `rolled_back` with `verify_error` set (verify-error-proof).
- Lines 4–5: `rolled_back` twice (rollback-proof). These give `rolled_back`
  a record in a file; its two earlier runs survive only in commit 541cb09.

The snapshot-gate test ran first and has no line, by design: the engine
refuses before it logs anything. It refused with exit 4, and its marker file
was never created. That result comes from the terminal output, confirmed by
the person who ran it.

### `ubuntu-26.04-2026-10-06.jsonl`

Session 2: the services in `tests/systemd-proof/`, for `failed-systemd-units`.
2 runs on `Ubuntu Server Test`, 2026-10-06 23:34 and 23:36 IST, commit
`10cb1fd`, from a fresh clone after restoring `clean-baseline` at 23:19 IST.
Copied off at 23:39 IST.

- Line 1: `healed` (proof A, `sf3000-recovers`). Found 1, restarted it,
  waited 30 s, found 0. This is the first real `settle_seconds` wait.
- Line 2: `verify_failed` (proof B, `sf3000-crashes-late`). Found 1,
  restarted it, waited 30 s, still found 1. `reverse: none` logged "nothing
  to undo".

Two results have no line, by design. Both come from the terminal, shown in a
screenshot and confirmed by the person who ran it:

- **The privilege refusal.** Just before 23:50, after the two runs, the fix
  was run without sudo while `sf3000-crashes-late` was failed. It refused
  with exit 3 before running the check, and asked nothing.
- **The VM's journal** for the three test services, transcribed below. The
  VM prints UTC, so add 05:30 for IST. The oneshot started once (23:31:45
  IST) and never again, so neither fix restarted it. The journal also times
  proof B: the fix restarted the service at 23:36:51 and it crashed at
  23:37:01, before the check that came 30 seconds after the restart.

```
2026-10-06T18:01:45+00:00 rhtvm systemd[1]: Starting sf3000-oneshot.service - SF3000 proof C - a oneshot that fails...
2026-10-06T18:01:45+00:00 rhtvm systemd[1]: sf3000-oneshot.service: Main process exited, code=exited, status=1/FAILURE
2026-10-06T18:01:45+00:00 rhtvm systemd[1]: sf3000-oneshot.service: Failed with result 'exit-code'.
2026-10-06T18:01:45+00:00 rhtvm systemd[1]: Failed to start sf3000-oneshot.service - SF3000 proof C - a oneshot that fails.
2026-10-06T18:02:59+00:00 rhtvm systemd[1]: Started sf3000-recovers.service - SF3000 proof A - fails until /run/sf3000-ok exists.
2026-10-06T18:02:59+00:00 rhtvm systemd[1]: sf3000-recovers.service: Main process exited, code=exited, status=1/FAILURE
2026-10-06T18:02:59+00:00 rhtvm systemd[1]: sf3000-recovers.service: Failed with result 'exit-code'.
2026-10-06T18:04:34+00:00 rhtvm systemd[1]: Started sf3000-recovers.service - SF3000 proof A - fails until /run/sf3000-ok exists.
2026-10-06T18:06:02+00:00 rhtvm systemd[1]: Started sf3000-crashes-late.service - SF3000 proof B - runs for 10s, then crashes.
2026-10-06T18:06:14+00:00 rhtvm systemd[1]: sf3000-crashes-late.service: Main process exited, code=exited, status=1/FAILURE
2026-10-06T18:06:14+00:00 rhtvm systemd[1]: sf3000-crashes-late.service: Failed with result 'exit-code'.
2026-10-06T18:06:51+00:00 rhtvm systemd[1]: Started sf3000-crashes-late.service - SF3000 proof B - runs for 10s, then crashes.
2026-10-06T18:07:01+00:00 rhtvm systemd[1]: sf3000-crashes-late.service: Main process exited, code=exited, status=1/FAILURE
2026-10-06T18:07:01+00:00 rhtvm systemd[1]: sf3000-crashes-late.service: Failed with result 'exit-code'.
```

The journal shows whole seconds. The first crash shows 12 seconds after its
start, not 10; the second shows 10. Each crash came well before the next
check.

### `ubuntu-22.10-2026-10-07.jsonl`

Session 3: `eos-release-dead-repos`, following `tests/eos-proof/`. 2 runs on
`KineticServer` (Ubuntu 22.10, kinetic), 2026-10-07 11:10 and 11:12 IST,
commit `4410bee`. The VM was fresh from its `clean-baseline` snapshot, and
the repo was cloned onto it that morning. Copied off at 11:15 IST. These are
the first records from a release other than 26.04, and the first run of this
detect anywhere.

- Line 1: `healed`. The detect found 20 old-archive addresses, the fix
  rewrote them, and the check found 0.
- Line 2: `healed` again, 2 minutes later, after the undo was run by hand.
  It found the same 20, then 0.

Both records' `fix_command` matches the playbook's fix text exactly.

**Why 20, not 10.** The VM's sources.list has 10 active `deb` lines. Each
has a commented-out `# deb-src` twin with the same address, and the detect
counts those too. After the second fix, `grep -c` on the backup copy
(`sources.list.sf3000.bak`, the original file) counted 10 `deb` lines and
10 `# deb-src` lines. The detect was changed later that day to skip
comments, so these records come from the older detect.

The other results write no record. They come from the terminal, confirmed by
the person who ran it:

- `df -h / /home` showed `/` and `/home` on separate partitions.
- `apt-get update` failed before the fix ("does not have a Release file"),
  and was clean after each fix.
- The sweep before the first fix left `/etc/apt/sources.list`'s sha256
  unchanged. The detect changed nothing.
- The playbook's `reverse` text, run by hand between the two fixes, brought
  back the original file: its sha256 matched the first one.

The VM was not reverted afterwards. It was powered off and saved as the
snapshot `repos-fixed`, the starting point for the hand-run upgrade demo.

### `ubuntu-22.10-2026-10-07-2.jsonl`

The 22.10 re-run, with the detect that skips commented-out lines (commit
`0be2d76`), following `tests/eos-proof/`. 2 runs on `KineticServer`,
2026-10-07 12:13 and 12:16 IST, on a fresh clone after restoring
`clean-baseline` at 12:09 IST (the time the host made the new disk image).
Copied off at 12:17 IST. The name ends in `-2` because the morning's file
already has this date.

- Line 1: `healed`. The detect found 10, the active lines only, and then 0.
- Line 2: `healed` again, after the undo was run by hand. 10, then 0.

Both records' `fix_command` matches the playbook's fix text exactly. The old
detect would have counted 20 on this file, so 10 also shows that the VM ran
the new one.

**Step 4b writes no record**, because a sweep only prints. Between the two
runs, the active lines were rewritten by hand and the comments were left on
the old address. The detect must then read HEALTHY, and it did. Transcribed
from a screenshot by the person who ran it (12:23 IST):

```
rht@kineticvm:~/sf-3000$ grep -c '^# deb-src http://archive' /etc/apt/sources.list   # 10: the comments still say archive
10
rht@kineticvm:~/sf-3000$ python3 engine/runner.py --playbooks candidates
Loaded 3 valid playbook(s).

Running checks on linux/ubuntu:
------------------------------------------------------------
✓ [HEALTHY] display-gpu-driver     measured=0
✓ [HEALTHY] eos-release-dead-repos measured=0
✓ [HEALTHY] wifi-down              measured=0
------------------------------------------------------------
0 problem(s) found. (No fixes were executed.)
```

The remaining checks in the steps were run as written: the hashes, and
`apt-get update` after each fix. The person who ran them reported no
mismatch.

### `ubuntu-22.10-2026-10-09.jsonl`

The procedure runs in `tests/procedure-proof/`, Runs A to D, on
`KineticServer` (Ubuntu 22.10, kinetic), from 2026-10-08 19:56 IST to
2026-10-09 22:18 IST. The VM started from `repos-fixed`, restored at 18:57 IST
on 2026-10-08. These are the first procedure runs on a real machine. The
records are the service's own, from `/var/log/sf3000/runs.jsonl`, not the
clone's `logs/`. Copied off at 22:18 IST, after Run D. The copy is identical,
byte for byte, to one made after Run C at 19:46 IST, so Run D wrote nothing.

- Lines 1–3: Run A, no snapshot, commit `1851179`. `one` healed after a
  reboot (`verify_after` 1). `two` healed. `three` was stopped at its 1-minute
  limit (`fix_failed`, `fix_exit_code` null), and the procedure stopped. In the
  terminal: `one two` were there before and after a further `sleep 300`, and
  the service had removed itself.
- Line 4: Run B, commit `1851179`. `--take-snapshot` installed Timeshift
  22.06.5-1 with apt after the y: 196 new packages, 86.8 MB fetched in 42 s.
- Line 5: Run B's snapshot failed (`snapshot_failed`). Timeshift refused
  `--tags O` with "Unknown value specified for option --tags (O)", so no step
  ran, and the service removed itself. Fixed in `2b09c29` (DESIGN §16).
- Lines 6–8: Run B again, after `git pull` to `1ff0250`. Snapshot
  `2026-10-09_13-07-11` took 130 s, for 8.2 GB. `one` healed after a reboot,
  `two` healed, and `three` was stopped at its limit. The engine then restored
  the snapshot by itself: `rolled_back`, "ok: back to 22.10
  packages:ec3d46dddee73727". It is the first automatic restore on a real
  machine.
- Line 9: Run C, commit `1ff0250`. Snapshot `2026-10-09_14-06-41` took 23 s,
  linked to the one before. The VM was powered off during step `long`
  (`VBoxManage controlvm poweroff`), and the boot after restored nothing. The
  next `--run` offered the restore and was answered n; the one after was
  answered y, at 19:41 IST (`rollback_requested`). `rolled_back`, with
  `failure: interrupted` and the same fingerprint.

No package changed during the runs. Every fingerprint reads
`packages:ec3d46dddee73727`, and GRUB found only the release kernel,
5.19.0-21, after both restores. So the automatic updates installed nothing.

**Timeshift's own logs** (`snapshot.log` and `restore.log` in
`/var/log/sf3000/`) were copied off as well, but are not kept here. Both
restores:

- answered Timeshift's two questions, "Press ENTER to continue" and
  "Re-install GRUB2 bootloader? (y/n)", with their defaults. The engine gives
  Timeshift no input, and its source said this would happen (DESIGN §16).
- deleted the markers the steps had made: `one` and `two` in Run B, `started`
  in Run C. These are the lines that prove the files came back:

  ```
  *deleting   var/lib/sf3000-proof/two
  *deleting   var/lib/sf3000-proof/one
  *deleting   var/lib/sf3000-proof/
  ```

- left alone the engine's state, its logs, its service files and everything
  inside `/home/rht`. Run C's restore reset the timestamp of the folder
  `/home/rht` itself.
- reinstalled GRUB ("Installation finished. No error reported."), then
  rebooted with `reboot -f`.

Run C's `restore.log` ends in 11 zero bytes. The forced reboot cut off the
file's last write.

**Two results write no record.** They come from the terminal, shown in
screenshots and confirmed by the person who ran them:

- **The boot after Run C's power cut** held the restore back and removed the
  service. The journal (UTC):

  ```
  2026-10-09T14:09:00+0000 kineticvm systemd[1]: Starting SF 3000: carry on procedure cut-off-proof after a reboot...
  2026-10-09T14:09:03+0000 kineticvm systemd[1]: sf3000-procedure.service: Current command vanished from the unit file, execution of the command list won't be resumed.
  2026-10-09T14:09:03+0000 kineticvm python3[658]: ✗ step long was cut off: the machine went down while it was running. Nothing was undone; the engine waits for a person.
  2026-10-09T14:09:03+0000 kineticvm python3[658]:   → to restore Timeshift snapshot 2026-10-09_14-06-41: run the same --run command again with sudo (it asks first)
  2026-10-09T14:09:03+0000 kineticvm python3[658]:   → to leave the machine as it is: sudo python3 engine/runner.py --cancel
  2026-10-09T14:09:03+0000 kineticvm systemd[1]: sf3000-procedure.service: Deactivated successfully.
  2026-10-09T14:09:03+0000 kineticvm systemd[1]: Started sf3000-procedure.service.
  ```

- **Run D**, at 22:03 IST: its apt check's start in `apt-check.log`, copied
  off on 2026-10-10. Two dead sources were added: a host under
  `.invalid`, and a suite old-releases does not have. The apt check refused
  with exit 9 before the box, and `--status` still showed Run C:

  ```
  → checking that apt can update from every source (apt-get update; output in /var/log/sf3000/apt-check.log)
  ✗ apt cannot update from every source, so nothing was started:
      E: The repository 'http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite Release' does not have a Release file.
    → fix or remove those sources (/etc/apt/sources.list and /etc/apt/sources.list.d/), then run this again. Nothing was installed or upgraded.
    → apt's full output: /var/log/sf3000/apt-check.log
  exit 9
  ```

  It named only the missing suite. apt 2.5.3 prints no "Failed to fetch" line
  for any source once one source fails hard (`apt-pkg/update.cc`). So the dead
  host appeared only on an `Err:` line, and the engine's report left those
  out.

### `ubuntu-22.10-2026-10-10.jsonl`

Runs B and D again, with the fixes of commit `74340e2`: dpkg's lock held
around the snapshot and the restore, the snapshot written to disk, and every
dead apt source named. On `KineticServer`, 2026-10-10 15:13 to 15:21 IST. The
VM carried on from where Run D left it on 2026-10-09. It was not restored in
between (its disk image was last created on 2026-10-08 at 18:57 IST). The
clone was pulled to `74340e2` first. Copied off at 15:22 IST, after Run D.

The file is the whole log again: 12 records. Lines 1–9 are the 2026-10-09
file, byte for byte. Lines 10–12 are new:

- Line 10: `healed` (`one`). Snapshot `2026-10-10_09-45-29`, 27 s, linked to
  Run C's. Then `one`, a reboot, and its check after the boot
  (`verify_after` 1).
- Line 11: `healed` (`two`).
- Line 12: `rolled_back` (`three`, `failure: fix_failed`). It was stopped at
  its 1-minute limit, and the engine restored the snapshot by itself. "ok:
  back to 22.10 packages:ec3d46dddee73727": the same fingerprint as every run
  since 2026-10-09.

**Run B: the lock.** Before the run, a second SSH session held dpkg's
frontend lock with a python3 holder (`fcntl.lockf`, as apt takes it) that
waited for Enter. `sudo apt-get check` was refused, so the holder worked. The
apt check still passed: `apt-get update` takes a different lock. These lines
come from the terminals, shown in a screenshot (15:16 IST) and confirmed by
the person who ran them. The VM prints UTC. The journal:

```
Oct 10 09:43:29 kineticvm systemd[1]: Starting SF 3000: carry on procedure procedure-proof after a reboot...
Oct 10 09:43:29 kineticvm python3[1312]:   → the package manager is busy — waiting 60s before the snapshot (attempt 2 of 30)
Oct 10 09:44:29 kineticvm python3[1312]:   → the package manager is busy — waiting 60s before the snapshot (attempt 3 of 30)
Oct 10 09:45:29 kineticvm python3[1312]: → taking a Timeshift snapshot of /dev/sda2 before step 1 (machine: 22.10 packages:ec3d46dddee73727)
Oct 10 09:45:56 kineticvm crontab[1375]: (root) LIST (root)
Oct 10 09:45:56 kineticvm crontab[1376]: (root) LIST (root)
Oct 10 09:45:57 kineticvm python3[1312]:   → writing the snapshot to disk
```

The holder was let go between 09:44:29 and 09:45:29, and the engine took the
lock at its next try. The second session asked apt for the lock again while
the snapshot ran:

```
rht@kineticvm:~$ sudo apt-get check
E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 1312 (python3)
N: Be aware that removing the lock file is not a solution and may break your system.
E: Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend), is another process using it?
```

Process 1312 is the engine: the journal's `python3[1312]`. So the engine
waited while another program held the lock, then held it itself while
Timeshift copied.

The journal stops at "writing the snapshot to disk". The rest came within the
same second: line 10's step started at 09:45:57.41, and the reboot's broadcast
says 09:45:57. So `sync` took under a second. The reboot closed the session
before `journalctl` showed the last lines, and the restore later rolled the
journal back to the snapshot.

**The restore did not wait.** Nothing held the lock by then. Step `three`
started at 09:46:21.19, and the restore at 09:47:21.20, as its limit ran out.

**Timeshift's own logs** (`snapshot.log` and `restore.log`) were copied off as
well, but are not kept here.

- The snapshot linked to `2026-10-09_14-06-41` and took 27 s.
- The restore sent 21 MB. It deleted `one`, `two` and their folder, and
  touched nothing under the engine's excludes and nothing inside `/home`. It
  reinstalled GRUB ("No error reported"), found only kernel 5.19.0-21, and
  rebooted. This time its log ends cleanly, with no zero bytes.

**Run D again**, at 15:21 IST, with the same two dead sources. The engine
refused with exit 9 before the box, and `--status` still showed Run B. The
person who ran it reported every output as expected: both sources named. apt's
own output is in the copied `apt-check.log`:

```
Err:7 http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite Release
  404  Not Found [IP: 162.213.35.94 80]
Ign:1 http://sf3000-no-such-host.invalid/ubuntu kinetic InRelease
Err:1 http://sf3000-no-such-host.invalid/ubuntu kinetic InRelease
  Could not resolve 'sf3000-no-such-host.invalid'
Reading package lists...
E: The repository 'http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite Release' does not have a Release file.
```

The engine's parser (`apt_problems`), given that output on the host, returns
the two lines it reports:

```
E: The repository 'http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite Release' does not have a Release file.
Err:1 http://sf3000-no-such-host.invalid/ubuntu kinetic InRelease  Could not resolve 'sf3000-no-such-host.invalid'
```

The same log holds the first Run D's output, from 2026-10-09 at 22:03 IST. It
has the same shape, and the parser names both sources from it too.

### `ubuntu-22.10-2026-10-10-2.jsonl`

Visit 1 of the lab's upgrade: `release-upgrade-to-23.04`, with
`--take-snapshot`, on `KineticServer`. The machine went from Ubuntu 22.10 to
23.04, the first real upgrade the engine has run. The VM started from
`repos-fixed`, restored at 16:26 IST on 2026-10-10 (the time the host made the
new disk image). The clone was pulled
to `768cb37`. The automatic updates were left on, as on the lab's machines.
Copied off at 17:05 IST. The name ends in `-2` because the morning's file
already has this date.

- Line 1: `installed`. After the y, the engine installed Timeshift with apt:
  196 new packages and 2 upgraded, 87.6 MB in 43 s.
- Line 2: `healed` (`to-23.04`). The detect read 2210 before. The step exited
  0 at its first attempt, the engine rebooted, and the check read 2304
  (23.04, and `dpkg --audit` empty). Snapshot `2026-10-10_11-03-25`.

**The timeline** (UTC; add 05:30 for IST):

| time | what |
|------|------|
| 11:00:07 | the apt check: all four kinetic sources on old-releases |
| 11:01:41 | the y; Timeshift installed by 11:03:23 |
| 11:03:25 | the first snapshot: 133 s. Nothing held apt's lock, so no wait |
| 11:05:40 | the step: `apt-get full-upgrade` first, 137 upgraded and 5 new (kernel 5.19.0-46), 493 MB in 3 min 19 s |
| 11:15:03 | the 23.04 upgrader starts, from `lunar.tar.gz` ("Good signature from Ubuntu Archive Automatic Signing Key (2018)") |
| 11:17:08 | its download, about 680 MB, until 11:21:51 |
| 11:22:53 | its main install, until 11:33:09; obsolete packages removed by 11:33:52 |

From the apt check to the end of the upgrader took 34 minutes, then a reboot
and the check. The person who ran it reported about 30 minutes in all.

**The upgrader's own logs** (`/var/log/dist-upgrade`, copied off but not kept
here). `main.log` has one ERROR, and it is the expected path:

```
2026-10-10 11:15:32,880 ERROR No valid mirror found
```

The upgrader first looked for 23.04 on the main archive and on the country
mirror (`gb.archive.ubuntu.com`). Both answered 404, so it found no mirror for
any line. It then asks whether to rewrite `sources.list` anyway. The
non-interactive frontend answers yes to every question, so it rewrote all ten
lines, `-security`, `-updates` and `-backports` included, to 23.04 on
old-releases. A person running the upgrader by hand would have been asked.

Two DEBUG lines the person who ran it asked about. Both were read in the
upgrader's source (`DistUpgradeController.py`, lunar):

- **"Not an UEFI system".** On a machine that boots with BIOS, as this VM
  does, the upgrader skips its EFI check. On a UEFI machine, such as the lab's,
  it checks that `/boot/efi` is mounted read-write, and refuses to upgrade if
  not ("EFI System Partition (ESP) not usable").
- **"failed to determine user upgrading".** Running as root, the upgrader
  looks for `SUDO_UID`, then `PKEXEC_UID`, to find who started it. It uses that
  user to ask the desktop not to lock the screen during the upgrade. The
  engine's service has neither, so it skips that. On a desktop, the screen may
  lock during an upgrade. The upgrade carries on.

**The step's output** (`release-upgrade-to-23.04-to-23.04.log`, copied off but
not kept here) has no error that stopped anything:

- dpkg removed `cron` "anyway as you requested", because 23.04 splits it into
  `cron` and `cron-daemon-common`. It installed both straight after.
- `error: cannot refresh "lxd": snap "lxd" assumes unsupported features:
  snapd2.75`. The upgrader tried to refresh the `lxd` snap. Today's snap store
  wants a newer snapd than 23.04's 2.59. The snap keeps its old revision, and
  the upgrade went on.
- "Could not execute systemctl" from `deb-systemd-invoke`, after the
  `multipath-tools` and `snapd` packages: their restarts were refused, such as
  "Failed to restart snapd.mounts-pre.target: Operation refused". The output
  itself says one of these "can be safely ignored" (LP: #2035098).

### `ubuntu-22.10-2026-10-10-3.jsonl`

Visit 2 of the lab's upgrade: `release-upgrade-to-23.10`, with
`--take-snapshot`, on `KineticServer`. The machine went from Ubuntu 23.04 to
23.10. It started where visit 1 left it, with no restore in between. The clone
was pulled to `1050e99`. The automatic updates were on. Copied off at 19:00
IST. The name ends in `-3` because two files already have this date. It keeps
`22.10` in its name, as visit 1's does: it is the 22.10 VM.

- Lines 1 and 2 are visit 1's file again, unchanged.
- Line 3: `healed` (`to-23.10`). The detect read 2304 before. The step exited
  0 at its first attempt, the engine rebooted, and the check read 2310 (23.10,
  and `dpkg --audit` empty). Snapshot `2026-10-10_13-08-30`.

There is no `install` line: Timeshift was already there.

**The timeline** (UTC; add 05:30 for IST):

| time | what |
|------|------|
| 13:07:45 | the apt check: all four lunar sources on old-releases |
| 13:08:30 | the snapshot: 129 s. It began 45 s after the apt check, the y included. A wait for apt's lock lasts 60 s, so there was none |
| 13:10:41 | the step: `apt-get full-upgrade` had nothing to do (0 upgraded) |
| 13:10:48 | the 23.10 upgrader starts, from `mantic.tar.gz` ("Good signature from Ubuntu Archive Automatic Signing Key (2018)") |
| 13:12:17 | its download, about 875 MB, until 13:18:05 |
| 13:18:06 | a dry run, 2 s; then libc6 alone, until 13:18:21 |
| 13:18:45 | its main install, until 13:26:41; obsolete packages removed by 13:27:12 |

From the apt check to the end of the upgrader took 20 minutes, then a reboot
and the check. The logs were copied off at 13:30:43 (19:00 IST), after the
record was written, so the visit took under 23 minutes in all. Visit 1 took longer
because it also installed Timeshift and 22.10's 137 pending updates.

**The upgrader's own logs** (`/var/log/dist-upgrade`, copied off but not kept
here). At its start, the upgrader moved visit 1's logs into a dated
subfolder, `20261010-1310`. The `main.log` there is identical to visit 1's
copy. The new `main.log` has the same one ERROR as visit 1's:

```
2026-10-10 13:11:08,195 ERROR No valid mirror found
```

The upgrader looked for 23.10 on the main archive and on
`gb.archive.ubuntu.com`. Both answered 404, so the non-interactive yes moved
all ten apt lines to 23.10 on old-releases, as in visit 1. "Not an UEFI
system" and "failed to determine user upgrading" appear again, for the same
reasons. Before its download, it found 11.6 GB free on `/` and needed 1.2 GB.

**The upgrader installs in three passes.** Both visits show it. Read in the
upgrader's source (`DistUpgradeController.py`, mantic):

- a dry run, with dpkg swapped for `/bin/true`. It installs nothing, but apt's
  `history.log` still lists it.
- libc6 alone, with `libc-bin` and `locales`. If this pass fails, the upgrader
  stops with exit 1 ("Upgrade incomplete"), and the engine restores the
  snapshot.
- everything else.

**The step's output** (`release-upgrade-to-23.10-to-23.10.log`, copied off but
not kept here) has no error that stopped anything:

- `/etc/grub.d/10_linux: 1: version_find_latest: not found`, once, when
  `mdadm`'s setup rebuilt GRUB's menu. GRUB 2.12's library was unpacked by
  then, but the old menu script was still in place: dpkg replaces a config
  file only when its package is set up. Once GRUB was set up, its menu was
  rebuilt three more times, each listing the new 6.5 kernel.
- "Warning: Stopping ssh.service, but it can still be activated by:
  ssh.socket", when `openssh-server` was upgraded. `ssh.socket` starts the SSH
  server when someone connects. Visit 1 did not show this. SSH worked after
  the reboot: the logs were copied off over it.
- "Could not execute systemctl", once, after `snapd`. It is the same refused
  restart as in visit 1.
- No snap error this time. The upgrader left the `lxd` snap alone ("Snap lxd
  is not tracking the release channel").

The clean-up removed 27 packages that nothing needed any more. Among them
were the 5.19 kernel, `binutils` and `python3-setuptools`. It kept the
running 6.2 kernel. `python3-yaml` and `python3-jsonschema` were upgraded, not
removed, so the engine can still start the next visit.

### `ubuntu-22.10-2026-10-10-4.jsonl`

Visit 3 of the lab's upgrade: `release-upgrade-to-24.04`, with
`--take-snapshot`, on `KineticServer`. The machine went from Ubuntu 23.10 to
24.04 LTS, the first upgrade through plain `do-release-upgrade`. It started
where visit 2 left it, with no restore in between. The clone was pulled to
`83d21f6`. The automatic updates were on. Copied off at 21:06 IST. The name
ends in `-4` because three files already have this date.

- Lines 1 to 3 are visit 2's file again, unchanged.
- Line 4: `healed` (`to-24.04`). The detect read 2310 before. The step exited
  0 at its first attempt, the engine rebooted, and the check read 2404
  (24.04, and `dpkg --audit` empty). Snapshot `2026-10-10_14-28-46`.

**The timeline** (UTC; add 05:30 for IST):

| time | what |
|------|------|
| 14:28:38 | the apt check: all four mantic sources on old-releases |
| 14:28:46 | the snapshot: 124 s. It began 8 s after the apt check, the y included, so there was no wait for apt's lock |
| 14:30:52 | the step: `apt-get full-upgrade` had nothing to do (0 upgraded) |
| 14:31:03 | the 24.04 upgrader starts (release-upgrader 24.04.29), fetched and checked by `do-release-upgrade` |
| 14:31:17 | all ten apt lines move from old-releases to `gb.archive.ubuntu.com` |
| 14:32:35 | its download, about 1.3 GB, until 14:42:14 |
| 14:42:15 | a dry run, 4 s; then libc6 alone, until 14:42:33 |
| 14:43:07 | its main install, until 14:54:37; obsolete packages removed by 14:55:21 |
| 14:55:26 | apt's sources moved to the deb822 format; the upgrader ends |

From the apt check to the end of the upgrader took 27 minutes, then a reboot
and the check.

**`do-release-upgrade`'s own lines are not in the step's output.** It printed
"Checking for a new Ubuntu release" and checked the upgrader's signature, but
none of that reached the log. Once the check passes, it replaces itself with
the upgrader (`os.execv`, read in `DistUpgradeFetcherCore.py`, mantic). Python
drops its unwritten output at that moment. The upgrader runs only after the
signature check has passed, so the check passed. When it fails,
`do-release-upgrade` exits normally, and its message does reach the log.

**The upgrader's own logs** (`/var/log/dist-upgrade`, copied off but not kept
here). At its start, it moved visit 2's logs into a dated subfolder,
`20261010-1431`, next to visit 1's. Both are identical to the earlier copies.
The new `main.log` has no ERROR:

- "transition from old-release.u.c to http://gb.archive.ubuntu.com/ubuntu",
  ten times. The upgrader found 24.04 on the country mirror for the VM's
  locale (`en_GB`), so all ten apt lines left old-releases, `-security`
  included. Until now this was read in the source only.
- `migrateToDeb822Sources()` at the end. The lines now live in
  `/etc/apt/sources.list.d/ubuntu.sources`, in the deb822 format, not in
  `/etc/apt/sources.list`.
- Two WARNINGs, about one config file: "got a conffile-prompt from dpkg for
  file: '/etc/fwupd/fwupd.conf'", then "replied no". The file had been
  changed since it was installed, so dpkg asked whether to replace it. The
  non-interactive frontend kept the machine's version.
- "Not an UEFI system" and "failed to determine user upgrading" again. The
  snaps were left alone, as in visit 2.
- Its `uname` line shows the machine was running 23.10's kernel, 6.5.0-44:
  visit 2's reboot had used its new kernel.
- Before its download, it found 12.7 GB free on `/` and needed 2.9 GB.

**The step's output** (`release-upgrade-to-24.04-to-24.04.log`, copied off but
not kept here) has no error that stopped anything:

- dpkg's prompt for `fwupd.conf`, answered `n`: keep the current version.
- 37 "dpkg: warning: unable to delete old directory '/lib/...'". 24.04's
  packages moved their files from `/lib` to `/usr/lib`. On this machine `/lib`
  already points to `/usr/lib`, so the old folders still hold the new files.
  Two "is the same as several new files" warnings come from the same move.
- `grub-install success for /dev/sda`: GRUB 2.12 written to the disk. Its
  menu lists the new kernel, 6.8.0-146.
- No "Could not execute systemctl" and no snap error this time.

The clean-up removed 42 packages, among them Python 3.11. That was the
interpreter the engine's service was running on; visit 1 had removed 3.10 the
same way. The engine was already loaded, so it still reached its reboot, and
the check ran on 24.04's Python 3.12. `python3-yaml` was upgraded, and
`python3-jsonschema` kept its version, so the engine can still start visit
4.

### `ubuntu-22.10-2026-10-10-5.jsonl`

Runs E and F of `tests/procedure-proof/`: the last upgrade,
`release-upgrade-to-26.04`, made to fail on purpose on `KineticServer`, now
on 24.04. A helper, `fill-disk.py`, filled the disk at the moment each run
needed. The clone was pulled to `584199a`. Before the runs, 24.04 was brought
fully up to date and rebooted, visit 3's snapshot was deleted, and a
VirtualBox snapshot, `at-24.04`, was taken. Copied off at 00:08 IST on
2026-10-11; both runs were on 2026-10-10. The name ends in `-5` because four
files already have this date.

- Lines 1 to 4 are visit 3's file again, unchanged.
- Line 5, Run E: `rolled_back`, `failure: fix_failed` (`to-26.04`). The
  step exited 1. The engine restored its snapshot `2026-10-10_17-53-28`, and
  after the reboot the machine matched it: `ok: back to 24.04
  packages:d778793cbf7390c2`.
- Line 6, Run F: the same outcome, from snapshot `2026-10-10_18-11-25`, with
  the same fingerprint.

There is no record for step 1, `updates`. Both times its check found nothing
waiting, so the engine counted the step as done and skipped it. A skipped
step writes no record. This was the first time that check ran on a machine.
Its other branch, with updates waiting, has not been seen.

**Run E: the upgrader refuses** (UTC; add 05:30 for IST):

| time | what |
|------|------|
| 17:52:50 | the apt check: the four noble sources, on `gb.archive.ubuntu.com` |
| 17:53:28 | the snapshot: 186 s. A full copy, since visit 3's had been deleted |
| 17:56:41 | step 2 starts: `do-release-upgrade`. The helper fills the disk, leaving 500 MB for ordinary users |
| 17:56:50 | the 26.04 upgrader starts (release-upgrader 26.04.25) |
| 17:57:55 | it finds 365 MB free on `/` and needs 2,734 MB. It refuses: "Not enough free disk space" |
| 17:57:59 | it has put the apt sources back, and exits 1. The engine starts the restore |

The restore sent 158 MB. It deleted the 26.04 package lists the upgrader had
fetched, and the helper's fill file. From the apt check to the restore took 5
minutes. The machine was back on 24.04 before Run F began.

This was the first restore to keep the upgrader's logs (§16 of DESIGN.md).
Neither restore touched `/var/log/dist-upgrade`, the engine's folders,
`/boot/efi` or the unit file. Run E's `main.log` still says why it stopped:
"The upgrade needs a total of 2,734 M free space on disk '/'". Run F's
upgrader then moved it into a dated folder, `20261010-1812`.

**Run F: the disk fills during the install:**

| time | what |
|------|------|
| 18:11:16 | the apt check |
| 18:11:25 | the snapshot: 41 s, linked to Run E's, which was still there |
| 18:12:11 | step 2 starts |
| 18:12:21 | the upgrader starts |
| 18:13:25 | it finds 11.6 GB free, and needs 2.7 GB. Its download, 1.4 GB, until 18:25:46 |
| 18:25:47 | a dry run, 3 s |
| 18:26:12 | dpkg starts the install. A minute in, the helper fills the disk, root's share too |
| 18:27:15 | dpkg is failing package after package: "No space left on device". The upgrader logs "Could not install the upgrades" |
| 18:27:18 | it exits 1. The engine frees its reserve and starts the restore |
| 18:27:19 | a dpkg the upgrader left running ends (below) |
| 18:27:27 | the upgrader's install process tries again, and finds dpkg's lock held by the engine |

Before the disk filled, dpkg had unpacked 140 packages and set up 55. Among
them were 26.04's `libc6` and `perl-base`. Then it failed 31 packages in a
row. So the restore undid a real half-upgrade. It deleted 4,260 files and
folders the upgrade had added, and rewrote about 7,000 files. After the
reboot the check matched the fingerprint.

The disk was full when the step failed. The engine freed its 256 MB reserve,
wrote its state and started Timeshift: the reserve's first use on a machine.

**What Run F found: the install outlived the upgrader.** The upgrader runs
dpkg from a child process, in a session of its own (`pty.fork()`). Its parent
copies the child's output to the step's log. On the full disk, one of the
parent's writes failed. Most likely it was that copy, whose last flush has no
`try` around it (read in `DistUpgradeViewNonInteractive.py`, resolute). So
the parent stopped waiting, logged its error and exited 1. The child and its
dpkg kept going.

The engine saw the exit and started the restore. It waits only for apt's
frontend lock, and nobody held that any more. dpkg held its other lock,
`/var/lib/dpkg/lock`. It used the space the engine had just freed to unpack
four more packages (`friendly-recovery`, `initramfs-tools`,
`initramfs-tools-core`, `libext2fs2t64`), and ended at 18:27:19, a second
after Timeshift started. At 18:27:27 the child hit an input/output error,
most likely printing its own error to the terminal its parent had closed. It
fell into the upgrader's retry. The retry asked for the frontend lock and
found it held by "process 1608 (python3)". That is the engine, which holds
that lock from the start of the restore to the reboot. So the retry installed
nothing. The child then ran the upgrader's three post-install scripts, in the
middle of the restore, and stopped.

The restore still came out right. dpkg ended while rsync was most likely
still listing files, and the check after the reboot matched. But that was
timing, not design. The engine does not wait for dpkg's inner lock, and
nothing stops what a failed step leaves running. DESIGN.md §15 has the
question.

**The logs.** Run F's step log ends with nine "--- Logging error ---" lines:
the upgrader could not write to the full disk. Its `main.log` has the error
twice: first the parent's, then the child's. The engine's own messages went
to the journal, which each restore rolled back.
