# evidence — run logs copied off the test VMs

Each file here is a copy of a VM's `logs/runs.jsonl`, made before the VM was
reverted. A revert deletes that log, and `logs/` is gitignored, so this folder
is the only lasting record of what the engine actually did on a machine.

**Rules**

- Copy the file as it is. Never edit a record, even to fix a typo.
- One file per copy, named `<distro>-<release>-<date of the last run>.jsonl`.
- The tracker's Run log sheet points at these files, one row per record.

Copy it off the VM like this (on Windows, from the repo):

    scp -P 2222 rht@127.0.0.1:sf-3000/logs/runs.jsonl evidence/ubuntu-26.04-<date>.jsonl

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
