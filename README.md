# Project: SF 3000 — starter slice

A vetted-playbook engine that diagnoses (and, later, fixes) OS problems for
non-technical users. The intelligence about *what command to run* lives in a
reviewed **playbook library**, not in the model at runtime. The model's job is
to *match and explain*, never to author commands that touch a machine.

## What's here (the first vertical slice)

```
schema/playbook.schema.json   the contract every playbook must satisfy
playbooks/*.yaml              three real, vetted entries
engine/runner.py             loads + validates + runs detect + diagnoses
```

This slice does **detect only**. It runs each playbook's read-only `detect`
command, checks the result against `expect`, and reports HEALTHY / PROBLEM /
ERROR. For a PROBLEM it prints the vetted fix as a **dry-run** — it never
executes a fix. That belongs to the safety layer (next).

## Run it

```bash
pip install pyyaml jsonschema --break-system-packages
python3 engine/runner.py --distro ubuntu        # full detect + diagnose pass
python3 engine/runner.py --validate-only        # just check the library is sound
```

## Anatomy of a playbook

Every entry is one problem: how to detect it, what "healthy" looks like, and
how to fix it. The key idea is that `expect` is a **predicate, not a fixed
string** — real machines never produce byte-identical output, so "healthy" is
a *rule* the measurement must satisfy (under a threshold, matches a regex,
exit code zero, …).

| field                | purpose                                                      |
|----------------------|--------------------------------------------------------------|
| `detect.command`     | READ-ONLY probe. Must never mutate the system.               |
| `detect.produces`    | how to read the output: integer / string / exit_code / line_count |
| `expect.predicate`   | the healthy rule (less_than, equals, exit_zero, regex_match…) |
| `risk`               | safe / moderate / destructive — drives confirmation & snapshots |
| `requires_privilege` | needs sudo/admin                                             |
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
- **Safety layer** — confirmation, snapshot-before-destructive, logging,
  reversibility. Wraps fix execution; not built yet.
- **Matcher (LLM)** — maps a free-text complaint to candidate playbooks using
  `symptoms`. Retrieval, not authoring. Plugs in above the engine.
- **CLI/TUI** — plain-language in, readable status out.

## Not built yet (on purpose)

Fix execution, the safety layer, the LLM matcher, Fedora/Windows fix
*verification* (needs real machines), and the fleet dashboard.
