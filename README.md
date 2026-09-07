# Project: SF 3000

A vetted-playbook engine that diagnoses and fixes OS problems for
non-technical users. The intelligence about *what command to run* lives in a
reviewed **playbook library**, not in the model at runtime. The model's job is
to *match and explain*, never to author commands that touch a machine.

## What's here

```
schema/playbook.schema.json   the contract every playbook must satisfy
playbooks/*.yaml              three real, vetted entries
engine/runner.py              the engine: validate, detect, diagnose, fix
tests/rollback-proof/         a fixture that exercises the rollback path
```

By default the engine is **read-only**. It runs each playbook's `detect`
command, checks the result against `expect`, and reports HEALTHY / PROBLEM /
ERROR / SKIPPED. For a PROBLEM it prints the vetted fix as a **dry-run**.

Passing `--fix <id>` opts one playbook into the full lifecycle — confirm, fix,
verify, log, and roll back if the fix does not prove itself. Nothing else on
the machine is touched.

> **Status: the fix lifecycle is written but not yet proven.** Every step of it
> exists and is reviewed, but the path has not been exercised end-to-end on a
> real machine, so treat Phase 1 as unfinished. `tests/rollback-proof/` is the
> fixture that closes this out; see DESIGN.md §12.

## Run it

```bash
pip install pyyaml jsonschema --break-system-packages

python3 engine/runner.py                    # detect + diagnose everything
python3 engine/runner.py --validate-only    # check the library, run nothing
sudo python3 engine/runner.py --fix <id>    # actually fix one problem (asks first)
```

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

Snapshots (the gate refuses destructive fixes rather than guessing a
mechanism), the matcher, the CLI/TUI, Fedora/Windows fix *verification* (needs
real machines), and the fleet dashboard.
