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

- **Run D**, just before 22:18 IST. Two dead sources were added: a host under
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
