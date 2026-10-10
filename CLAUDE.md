# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SF 3000 diagnoses and fixes OS problems from a reviewed library of
playbooks. The engine runs only the commands written in the library. It never
composes one. README.md is the user guide. DESIGN.md is the record of
decisions: §16 holds every settled rule with the reason for it, and §15 holds
what is still open. Read §16 before changing how the engine behaves.

## Commands

There is no build step, no linter config and no pytest. The engine needs only
pyyaml and jsonschema (3 or later). Each test is a plain script: it prints
`ok` or `FAIL` per check and exits non-zero if any check fails. A script runs
all of its checks; there is no way to run a single one.

Development happens on Windows. There, `python3` is the Microsoft Store stub,
so use the `py` launcher (or `python`, which is 3.12):

```bash
py -3 tests/procedure-proof/test_procedure.py   # procedures
py -3 tests/lifecycle-proof/test_outcomes.py    # the --fix lifecycle
py -3 tests/blocked-proof/test_blocked.py       # the blocked branch
py -3 engine/runner.py --validate-only          # schema-check playbooks/
py -3 engine/runner.py --validate-only --playbooks candidates --procedures candidates/procedures
```

Also run the tests on Python 3.10, because that is what the lab's 22.10
machines run. This machine's 3.10 has no pyyaml or jsonschema, so install them
into a folder outside the repo and point `PYTHONPATH` at it:

```bash
py -3.10 -m pip install --target <dir> pyyaml jsonschema
PYTHONPATH=<dir> py -3.10 tests/procedure-proof/test_procedure.py
```

A sweep on Windows reports every playbook SKIPPED, since they are all
`applies_to.os: linux`. Nothing on this machine runs a fix for real.

## Architecture

The whole engine is one file, `engine/runner.py`. It has three paths:

- **Sweep** (the default): load and schema-check the playbooks, run each
  `detect`, test the result against the `expect` predicate, and report
  HEALTHY / PROBLEM / ERROR / SKIPPED. Fixes are only printed. Read-only.
- **`--fix <id>`** (`fix_one`): confirm, fix, wait if `verify.settle_seconds`
  asks, verify by re-running the detect, log, and roll back when the fix does
  not prove itself. Records go to the clone's `logs/runs.jsonl` (gitignored).
- **`--run <id>`** (procedures): check that `apt-get update` reaches every
  source, confirm once, install Timeshift if `--take-snapshot` needs it, copy
  the engine and the procedure into `/var/lib/sf3000/`, and install
  `sf3000-procedure.service`. The service runs `runner.py --resume` at each
  boot (`resume_procedure`). A phase in `/var/lib/sf3000/state.json` carries
  the work across reboots. Records go to `/var/log/sf3000/runs.jsonl`.

What takes several files to see:

- **Three tiers of entries.** `playbooks/` is loaded by default, and each
  entry there has healed on a VM. `candidates/` is unproven and loads only with
  `--playbooks candidates`. `candidates/procedures/` loads only with
  `--procedures candidates/procedures`; the default `procedures/` directory
  does not exist. The loaders glob `*.yaml` without recursing, and that is what
  keeps the procedures out of a candidates load. An entry moves up a tier only
  after VM runs.
- **Outcomes describe the machine, not the command.** A fix that exits 0 but
  leaves the predicate unhealthy has failed. `blocked` means the package
  manager never started, so nothing gets rolled back. The full list and the
  reason for each is in DESIGN.md §16.
- **The `--resume` path uses the standard library only.** A release upgrade
  can remove pyyaml or jsonschema halfway through, so they are imported in
  `try` blocks and checked in `main()`, after `--resume`, `--status` and
  `--cancel` have been handled.
- **Snapshots are Timeshift in rsync mode.** `SNAPSHOT_EXCLUDES` keeps the
  engine's state, logs and unit files out of the snapshot. Otherwise a restore
  would roll the procedure back to step 1 and it would loop. `/boot/efi` is
  excluded too: on the lab's dual-boot machines it holds Windows' boot files.
  So is `/var/log/dist-upgrade`, so a failed upgrade's own logs survive the
  restore.
  A restore counts as done only when a fingerprint (the release plus every
  installed package version) matches the one taken before the snapshot.
  Timeshift's exit code is never trusted. The engine holds dpkg's two locks
  (the frontend's, then dpkg's own) from the fingerprint to the end of the
  snapshot, and through the restore, and syncs the snapshot to disk before
  step 1. While a procedure runs, a 256 MB file in the state directory
  (`make_reserve`) keeps room on the disk. Every failure path frees it before
  its first write (`release_reserve`), so a full disk cannot stop the
  restore. Before a restore, and before that free, `fail_step` kills every
  other process in the service's cgroup (`stop_leftovers`): the release
  upgrader's install runs in a session of its own and can outlive it.
- **The offline tests patch `runner`'s module-level names**: functions such
  as `run_command`, `run_logged`, `assess` and `systemctl`, and path
  constants such as `PROCEDURE_STATE_DIR`, pointed at a temp folder.
  `test_procedure.py` models the machine as a dict and a reboot as another
  `--resume`, and it loads the real procedures from `candidates/procedures/`.
  Renaming or inlining those names breaks the tests.

## Rules not to weaken

- A detect never writes to the machine. It may query a server.
  `apt-get update` writes apt's lists, so it is not allowed in a detect.
- The engine never adds `sudo` to a vetted command. Without privilege it
  refuses.
- A playbook with a `fix` must have a `verify`.
- The snapshot gate: `risk: destructive` or `reverse.strategy: snapshot_only`.
- A busy package manager is recognised by apt's message, never by the exit
  code alone (`LOCK_CONTENTION_PATTERNS`).
- The engine installs what it needs only with apt, only after a y. Never pip:
  an upgrade moves `/usr/bin/python3` to a version that cannot see pip's
  modules.

## Proving things on a VM

The offline tests prove branch logic only. "Proven" means seen on a real
machine, with the record in `evidence/`.

- The test machines are VirtualBox VMs on this Windows host
  (`E:\VirtualBox\VBoxManage.exe`). Each has SSH on a localhost port: 2222 is
  26.04, 2223 is 22.10. The tracker's "Ubuntu versions" sheet lists them all.
- **The user runs the VMs.** Do not SSH into one or drive it from a tool call.
  Hand over exact commands, each with a sentence saying what it does and why,
  and let the user report back. Read-only VBoxManage queries from the host are
  fine.
- A VM gets the code by cloning from GitHub, so a change reaches it only once
  pushed. Local `master` has no upstream and the remote branch is `main`:
  `git push origin master:main`. Push only when the user says so.
- Restoring a VM snapshot deletes the VM's run log. Copy the log into
  `evidence/` first. Copy it verbatim, never edit it, and follow the naming in
  `evidence/README.md`.
- Each VM-test folder in `tests/` has a README with the exact steps and the
  records to expect.

## Keeping the docs in step

A behaviour change or a VM result is recorded in the same commit, in each of
these that it touches:

- DESIGN.md: a §16 entry with the reason, §12 and §13 status, and the
  revision note at the bottom.
- README.md's status block.
- `docs/sf3000-tracker.xlsx`. Its Verified columns take values only from a
  real VM run.
- The playbook's `source:` line, which says where it was tested.
- `evidence/README.md`, for each new evidence file.

Run records are in UTC. The project's dates (docs, tracker, commits) are IST,
UTC+05:30.

The docs and commit messages are written in plain, short sentences, one idea
each. Commit messages say why, and say what was tested: the offline check
counts, or which VM run.
