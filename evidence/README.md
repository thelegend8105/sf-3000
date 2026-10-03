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
