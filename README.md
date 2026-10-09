# Project: SF 3000

A vetted-playbook engine that diagnoses and fixes OS problems for
non-technical users. The intelligence about *what command to run* lives in a
reviewed **playbook library**, not in the model at runtime. The model's job is
to *match and explain*, never to author commands that touch a machine.

## What's here

```
schema/playbook.schema.json   the contract every playbook must satisfy
schema/procedure.schema.json  the contract for procedures: ordered steps that survive reboots
playbooks/*.yaml              four entries, each proven on a VM
candidates/*.yaml             proposed entries, not yet proven — run only when named
candidates/procedures/        proposed procedures: 22.10 → 26.04, one procedure per release upgrade
engine/runner.py              the engine: validate, detect, diagnose, fix
tests/rollback-proof/         a VM fixture that exercises the rollback path on real apt
tests/branch-proof/           VM fixtures for the snapshot gate, declined, rollback_failed, verify_error
tests/systemd-proof/          throwaway services for the failed-systemd-units VM run
tests/eos-proof/              the steps for the eos-release-dead-repos VM runs
tests/procedure-proof/        a quick VM fixture and an offline proof for procedures
tests/blocked-proof/          an offline proof of the `blocked` branch
tests/lifecycle-proof/        an offline proof of every other lifecycle branch
docs/sf3000-tracker.xlsx      the backlog: every problem, its status, VM runs, test versions
evidence/                     run logs copied off the VMs, verbatim — the record behind every "proven"
```

By default the engine is **read-only**. It runs each playbook's `detect`
command, checks the result against `expect`, and reports HEALTHY / PROBLEM /
ERROR / SKIPPED. For a PROBLEM it prints the vetted fix as a **dry-run**.

Passing `--fix <id>` opts one playbook into the full lifecycle — confirm, fix,
verify, log, and roll back if the fix does not prove itself. Nothing else on
the machine is touched.

> **Status: every outcome of the fix lifecycle has run on a real machine.**
> `tests/rollback-proof/` has run green in a VM — a fix that exits 0 without
> flipping the predicate is caught and undone (`outcome: rolled_back`) —
> alongside a real repair that heals. `fix_failed` and the `reverse: none`
> branch were exercised for real on 2026-09-07, by an apt lock rather than by
> design. `verify_failed` followed on 2026-09-08, when a fix exited 0 without
> reclaiming enough, and `blocked` on 2026-09-09 with that lock held
> deliberately. The fixtures in `tests/branch-proof/` covered the rest on
> 2026-10-04: `declined`, `rollback_failed`, `verify_error`, and the
> snapshot-gate refusal. On 2026-10-06 `tests/systemd-proof/` added the
> `settle_seconds` wait and the privilege refusal. The run records are in
> `evidence/`. Not yet run on a real machine: an undo stopped by a
> package-manager lock.
>
> **`disk-root-near-full` has healed three times** — its current command on
> 2026-09-09 (92% → 86%), and two earlier versions on 2026-09-08 (90% → 85%).
> Its reclaim is the apt cache and journals older than 7 days only: it does not
> touch user data and will not rescue a disk filled by the user's own files.
>
> **`failed-systemd-units` is back in the library (2026-10-06).** Its old fix
> cleared the very flag its detect counted, so it could report `healed` on a
> broken machine. The restart-based fix that replaced it passed on the VM: a
> service whose cause had gone healed, and one that crashed 10 seconds after
> its restart was caught by the 30-second wait. It restarts every failed
> service it lists, so read the list before answering y.

## Run it

```bash
sudo apt-get install python3-yaml python3-jsonschema   # Ubuntu; under sudo the engine offers this itself
pip install pyyaml jsonschema                          # elsewhere, such as a dev box

python3 engine/runner.py                    # detect + diagnose everything
python3 engine/runner.py --validate-only    # check the library, run nothing
sudo python3 engine/runner.py --fix <id>    # actually fix one problem (asks first)

python3 engine/runner.py --playbooks candidates   # include unproven entries
python3 tests/blocked-proof/test_blocked.py       # offline branch proofs —
python3 tests/lifecycle-proof/test_outcomes.py    #   no VM needed, run anywhere
python3 tests/procedure-proof/test_procedure.py
```

Some work is too long for one fix. A release upgrade takes an hour or more
and ends in a reboot. A **procedure** is an ordered list of steps, each with
its own check, command, time limit and verify. You confirm once; the engine
then hands the work to a system service, which carries on after every
reboot and removes itself when the procedure finishes or stops. Its records
go to `/var/log/sf3000/runs.jsonl`.

The lab's move from 22.10 to 26.04 is four procedures, one upgrade each, run
one per visit with someone at the machine
([candidates/procedures/README.md](candidates/procedures/README.md)):

```bash
sudo python3 engine/runner.py --procedures candidates/procedures \
     --run release-upgrade-to-23.04 --take-snapshot  # visit 1; asks first
python3 engine/runner.py --status                    # where it is: keep the machine on until done
journalctl -fu sf3000-procedure                      # watch it
sudo python3 engine/runner.py --cancel               # stop it between steps
```

Before it asks anything, the engine checks that `apt-get update` reaches
every source, and refuses with nothing changed if one is dead. A procedure
that cannot be undone needs a snapshot first. `--take-snapshot` has the
engine take one with Timeshift before step 1, installing Timeshift with apt
first if it is missing. If a step fails, the engine restores that snapshot by
itself, and the boot after checks that the machine really is back.

The snapshot leaves out `/home`. It also leaves out `/boot/efi`: on a
dual-boot machine that is Windows' EFI partition too, and a restore must not
rewrite Windows' boot files. If the machine goes down in the middle of a
step, the boot after restores nothing: it may be a boot nobody is watching.
Running the same command again offers the restore, and `--cancel` leaves the
machine as it is. `--snapshot <name>` records a snapshot you took yourself
(a VM snapshot, a disk image), which the engine cannot restore.

The procedure machinery ran on the 22.10 VM on 2026-10-08 and 2026-10-09
(`tests/procedure-proof/`, all four runs passed). That covered a reboot, a
time limit, the engine's own snapshot and its automatic restore, a power cut
mid-step, and the apt check. The four upgrades have not run yet.

The engine works out what machine it is on by itself — the OS from the
platform, the distro from `/etc/os-release`. Playbooks that are not for this
machine are reported as `SKIPPED` rather than run. `--os` and `--distro`
override the detection for testing; you should not need them in normal use.

Fixes only ever run when `--fix` names one explicitly, and only after you
confirm. A playbook that needs privilege is refused unless the engine is
already running with it — the engine will not add `sudo` to a vetted command
on your behalf.

## Anatomy of a playbook

Every entry is one problem: how to detect it, what "healthy" looks like, and
how to fix it. The key idea is that `expect` is a **predicate, not a fixed
string** — real machines never produce byte-identical output, so "healthy" is
a *rule* the measurement must satisfy (under a threshold, matches a regex,
exit code zero, …).

| field                | purpose                                                      |
|----------------------|--------------------------------------------------------------|
| `applies_to.os`      | which machines this is for — others report SKIPPED, never run |
| `detect.command`     | READ-ONLY probe. Must never mutate the system.               |
| `detect.produces`    | how to read the output: integer / string / exit_code / line_count |
| `expect.predicate`   | the healthy rule (less_than, equals, exit_zero, regex_match…) |
| `risk`               | safe / moderate / destructive — drives confirmation & snapshots |
| `requires_privilege` | the *fix* needs sudo/admin — detect never does                |
| `reverse.strategy`   | how to undo: `command` / `snapshot_only` / `none`            |
| `reverse.command`    | the undo command(s), keyed by distro — required for `command` |
| `fix.<distro>`       | vetted fix command, keyed by distro (or `default`)          |
| `verify.rerun`       | re-run detect after a fix to *prove* recovery — **mandatory whenever `fix` is present** |
| `verify.settle_seconds` | wait this long after the fix, then re-check once — for effects that take time or may not hold |
| `source`             | provenance — who vetted it and where it was tested          |

The library is the trust anchor: entries that fail the schema are rejected
outright (try `--validate-only` against a broken file to see it bite).

## Adding a playbook

1. Copy an existing `.yaml`, change `id`/`description`.
2. Write a **read-only** `detect` command and pick `produces`.
3. Express healthy as an `expect` predicate.
4. Add vetted `fix` command(s) per distro, plus a `verify` (mandatory with a `fix`).
   Set `risk` and `reverse` honestly — if a fix can delete data or break boot it is
   `destructive` + `snapshot_only`.
5. `python3 engine/runner.py --validate-only` must pass before it's committed.

## How the work divides

- **Schema + library** — owns this contract and grows the vetted playbooks.
  (You vet the actual fix commands; you've run them on real machines.)
- **Engine** — the detect→evaluate→diagnose loop (this file). Distro-agnostic,
  testable on Ubuntu.
- **Safety layer** — confirmation, logging, reversibility. Wraps fix
  execution; built, but see the status note above. Snapshot-before-destructive
  is still a gate that refuses rather than a mechanism that runs.
- **Matcher** — maps a free-text complaint to candidate playbooks using
  `symptoms`. Deterministic keyword matching in the MVP; retrieval, never
  authoring. Plugs in above the engine.
- **CLI/TUI** — plain-language in, readable status out.

## Not built yet (on purpose)

Snapshots for single fixes (`--fix` still refuses a destructive fix; only
procedures can take one, with Timeshift), the matcher, the CLI/TUI,
Fedora/Windows fix *verification* (needs real machines), and the fleet
dashboard.
