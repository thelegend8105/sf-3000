# Project: SF 3000 — Design Document

*The single source of truth for what we're building and how. Living document —
edit freely.*

---

## 1. In one paragraph

SF 3000 is a tool that diagnoses and fixes common computer problems for people
who aren't technical. Instead of an AI inventing commands to run on someone's
machine, all the trusted fixes live in a reviewed **library of recipes** (we
call each recipe a *playbook*). The tool only ever *picks the right recipe* and
runs its vetted checks and fixes — it never makes up commands. The **MVP (base
product)** does this on one machine with no AI at all; smarter AI-driven
matching, plain-language explanation, and watching many machines from one
dashboard are *tentative later directions* built on top of that base, not part
of it.

---

## 2. Why we're building it (motivation)

- Our lab systems ran an already end-of-support (EOS) version of Ubuntu
  (22.10, Kinetic Kudu — end of life 2023-07-20). A
  full upgrade would have taken more time than we had, so we had to *downgrade
  packages* to get things working with our programs — a fiddly, manual fix done
  under time pressure.
- Fixing computers by hand, one at a time, doesn't scale. Using an AI coding
  tool on a single machine helps *that* machine, but you can't be everywhere.
- We've personally used an AI tool to fix real Windows problems (disk bloat,
  missing wifi drivers, slow boot). So we know the approach works — we just
  want to package it so it's safe, repeatable, and usable by non-experts.

This is a learning project first, with a real-world tool as the goal.

---

## 3. Goals and non-goals

**Goals**
- Diagnose common OS problems automatically and safely.
- Apply *vetted*, human-reviewed fixes — never improvised ones.
- Be usable by someone non-technical (plain language, safe defaults).
- Work across Linux distros (Ubuntu first, Fedora next) and later Windows.
- Grow from one machine into a fleet view — as a feature built *on top of* the
  single-machine base, not baked into it.

**Non-goals (for now)**
- Not a general-purpose AI agent that freely runs whatever it decides.
- Not trying to fix *every* problem — just a growing library of known ones.
- Not a replacement for a real sysadmin on genuinely novel/complex failures.

---

## 4. The core design decision

**The trusted knowledge lives in the playbook library, not in software at
runtime.** The tool matches a situation to an existing, reviewed playbook and
runs its vetted checks and fixes — it never authors the commands that touch a
machine. In the MVP the matching is a deterministic lookup; if we later add the
AI agent, its job is *still* only to select a playbook (and explain it), never
to write commands.

Why this matters: it turns "an AI guessing with root access" into "a reviewed
checklist a computer runs for you." The risk shifts from *the model going
rogue* (which it can't, since it doesn't write the commands) to *a bad recipe
getting into the library* — and that's a risk we control with human review.

---

## 5. Key concepts (glossary)

- **Playbook** — one recipe for one problem: how to check for it, what a
  healthy result looks like, and the vetted command to fix it.
- **Library** — the whole collection of playbooks. The trust anchor.
- **Schema** — the rulebook that says what fields a valid playbook must have.
  A computer uses it to reject sloppy or unsafe-looking entries automatically.
- **Detect** — the *read-only* check command. Safe to run anytime; never
  changes the machine.
- **Expect** — the rule that says what "healthy" looks like. Crucially this is
  a *rule*, not a fixed answer (see §6).
- **Fix** — the vetted command that repairs the problem. Runs only after
  confirmation, and only for a real, confirmed problem.
- **Verify** — re-run the detect afterward to *prove* the fix worked.
- **Safety layer** — the part that confirms, backs up, logs, and guards fixes.
- **Matcher** — turns a plain-language complaint into a short list of candidate
  playbooks. In the MVP this is a deterministic keyword/symptom lookup; an AI
  agent doing this smarter is a later "novelty," not part of the MVP.
- **Fleet** — a later novelty: many machines watched from one dashboard.

---

## 6. The one subtle idea: "healthy" is a rule, not a fixed answer

The obvious version of this project is "compare the machine's output to the
perfect output." That breaks immediately, because no two healthy machines
produce identical output — disk usage, hostnames, kernel versions, and
hardware IDs all differ.

So "healthy" is expressed as a **predicate** — a rule the result must satisfy:

- *under a threshold* (disk less than 90% full)
- *equals a value* (zero failed services)
- *exit code is zero* (the package is installed)
- *matches / doesn't match a pattern* (a specific error is absent)

Diagnosis is then simply: run the detect, check if the result satisfies the
rule. If it doesn't, this playbook's problem is present.

---

## 7. The architecture (the pieces)

```
        plain-language complaint
                 │
                 ▼
        ┌──────────────────┐
        │  Matcher          │  MVP: deterministic keyword/symptom lookup
        └──────────────────┘  (retrieval, never command-writing)
                 │
                 ▼
        ┌──────────────────┐        ┌─────────────────────┐
        │     Engine        │◄──────►│  Playbook Library    │
        │ detect→check→     │        │  + Schema (rulebook) │
        │ diagnose→fix→verify│       └─────────────────────┘
        └──────────────────┘
                 │  (a fix is proposed)
                 ▼
        ┌──────────────────┐
        │   Safety layer    │  confirm · snapshot · log · reversible
        └──────────────────┘
                 │
                 ▼
        ┌──────────────────┐
        │     CLI / TUI     │  plain in, readable status out
        └──────────────────┘

  ─ ─ Perhaps introduce novelty (all built ON TOP of the MVP) ─ ─ ─ ─
   · AI agent          — smarter/conversational matching + plain-language
                         explanation of diagnoses (replaces the deterministic
                         matcher; only ever *selects*, never writes commands)
   · Multi-computer connect — one dashboard running the engine over many machines
   · Play with lab computers — deploy against the real lab fleet
   · Extra features    — TBD
```

**MVP vs. "perhaps introduce novelty."** The **MVP (the base product)** is
everything in the main pipeline above: a deterministic matcher, the engine, the
playbook library + schema, the safety layer, and the CLI — a self-contained tool
that diagnoses and fixes *one* machine, with no AI in the loop. The dashed
cluster below is a set of *tentative* directions layered on top once the MVP is
solid: an **AI agent** (smart matching and plain-language explanation),
**multi-computer connect**, **deploying on the lab machines**, and other extras.
None of these is committed, and the MVP works without any of them.

**Playbook library + schema** — the recipes and the rulebook that validates
them. This is the heart of the project.

**Engine** — runs the loop for one machine: run the detect, check it against
the rule, report healthy/problem, and (through the safety layer) apply and
verify a fix. It's distro-agnostic, so it can be built and tested on any Linux.

**Safety layer** — turns a raw fix command into a safe action: show the user
what will happen, take a snapshot/backup before anything destructive, log
everything, and prefer reversible changes. This is what lets a non-technical
person say "yes" safely — the *tool*, not the user, judges the danger via the
playbook's `risk` field.

**Matcher** — reads the user's complaint ("it's slow and nags about space") and
narrows the library to a few candidate playbooks by matching against their
`symptoms` text. In the MVP this is a plain deterministic keyword/symptom lookup
— no AI. It only *selects*; it never writes commands. (Swapping in an **AI
agent** for smarter, conversational matching is one of the "perhaps introduce
novelty" directions, not part of the MVP.)

**CLI / TUI** — the thin front door. Keep it simple; the intelligence lives in
the library and engine.

**Fleet dashboard (novelty, later)** — runs the same engine across many machines
and shows per-machine status ("diagnosing X / fixing Y"). One of the on-top
directions; deferred until the single-machine MVP is solid.

---

## 8. How a real run works (two modes)

**Complaint-driven.** User types what's wrong → Matcher (deterministic
keyword/symptom lookup in the MVP) picks candidate playbooks → Engine runs those
detects → any that fail their rule are the real problems → tool reports which
playbook flagged, shows its vetted plain-language confirmation, and asks
permission → Safety layer applies the fix → Verify re-runs the detect to confirm.

(Note: the MVP shows the *confirmation* in plain language, but does not narrate
every diagnosis, and does no AI matching. Conversational matching and
plain-language explanation of diagnoses are "perhaps introduce novelty"
directions, not part of the MVP run.)

**Proactive sweep.** No complaint needed. The Engine just runs *every*
applicable playbook's detect on the machine; anything that fails its rule is a
diagnosis. This is exactly what the fleet dashboard runs at scale later — the
single-machine library *is* the thing that scales.

---

## 9. Cross-platform strategy

- **Ubuntu first** — it's the story that started this, commands are
  deterministic, and it's easy to test.
- **Fedora second** — a friend has a Fedora machine to test on. Bringing it in
  early stops the design from silently assuming Ubuntu.
- **Windows later** — most valuable real-world target, but more fiddly
  (permissions, UAC). It becomes playbook set #N, not day one.

The trick: many *detects* are identical across distros (checking disk, failed
services, boot time), so those are shared. Only the *fix* diverges at the
package layer (`apt` on Ubuntu vs `dnf` on Fedora). So a single playbook holds
the shared check and forks only the genuinely different fix commands, keyed by
distro.

---

## 10. Safety model

- **Detect is always read-only.** It can be run freely; it never changes
  anything. This is the single most important safety rule.
- **Risk levels** on each playbook: *safe* fixes can run quietly; *moderate*
  fixes need explicit confirmation; *destructive* fixes need a snapshot/backup
  taken first.
- **Confirmation in plain language** before anything that modifies the system.
  In the base product this text is a *reviewed field on the playbook* — written
  and vetted alongside the fix — not something the AI generates at runtime.
- **Reversibility** — each playbook states *how* it is undone via its `reverse`
  block: an explicit undo `command`, `snapshot_only` (the only way back is a
  restore point), or `none` (nothing meaningful to undo). Prefer fixes that
  carry a real undo command.
- **Never escalate privilege on the user's behalf.** A playbook marked
  `requires_privilege` is run as-is; the engine refuses to start rather than
  prepending `sudo` to a vetted command (see §16).
- **Logging** — every command run (and its output) is recorded, so there's a
  trail if something goes wrong.
- **Verify after fix** — never assume success; re-run the check to prove it.
  A check that *errors* has not proved anything either, and is reported as
  such rather than as a failed fix (see §16).
- **Procedures carry these rules across reboots.** Work too long for one fix
  (a release upgrade) is a procedure: ordered steps, each with its own check,
  time limit and verify. The person confirms once; a system service runs a
  root-owned copy of the engine at each boot until the last step verifies or
  the procedure stops, then removes itself. A procedure that cannot be undone
  starts only with a snapshot: the engine's own (Timeshift), which it
  restores by itself when a step fails, or one the person names (see §16).
  - Before it asks, the engine checks that apt can update from every source.
  - It installs only what it needs to work (Timeshift, pyyaml, jsonschema),
    only with apt, and only after a y.
  - Its snapshot leaves out `/boot/efi`, which on a dual-boot machine holds
    Windows' boot files too.
  - While it takes its snapshot or restores it, it holds dpkg's lock, so
    nothing can change packages in the middle. The snapshot is written to
    disk before step 1 starts.
  - A step cut off by a crash or a power-off is never re-run by itself.
    Nor is it restored at a boot nobody may be watching: the engine waits
    for the person, who chooses between restoring and leaving the machine
    as it is.

---

## 11. Trust and vetting (what makes the library "vetted")

- Every playbook has a **source/provenance** line: who reviewed it and where it
  was tested.
- **Schema validation** rejects malformed entries automatically — a playbook
  that doesn't meet the rulebook can't load.
- Entries are treated like **reviewed code**: proposed via review, tested
  against real Ubuntu/Fedora before merging, and **versioned** so we can see
  when a fix changed and why.
- The honest risk is a *bad or overreaching playbook* entering the library, or
  a detect rule that misfires and flags a problem that isn't there. So the
  review bar on entries — and keeping detect strictly read-only — is where the
  real safety work lives.

---

## 12. Roadmap

Mirrors the two clusters in the rough plan: first **build out the MVP**, then
*perhaps* branch out into novelty. Both run modes (complaint-driven and
proactive sweep) live in the MVP.

**Build out the MVP (base product).**
- **Phase 0 — Skeleton (done).** Schema + a few real playbooks + an engine that
  loads, validates, runs detects, and diagnoses. Fixes shown as dry-run only.
  Proven working on Ubuntu.
- **Phase 1 — Safe single machine (every outcome proven).** The safety layer
  exists and works: `tests/rollback-proof/` has run green in a VM — a fix that
  exits 0 without flipping the predicate is caught and undone — alongside a
  real repair that heals. On 2026-10-04 the fixtures in `tests/branch-proof/`
  ran green too, covering the branches a passing run never reaches
  (`declined`, `rollback_failed`, `verify_error`, the snapshot-gate refusal).
  On 2026-10-06 the reworked `failed-systemd-units` proved itself too, with
  the first real `verify.settle_seconds` wait, and returned to the library.
  On 2026-10-07 `eos-release-dead-repos` healed on 22.10, the lab's own
  release; its detect then changed to skip comments (§16) and healed there
  again. The same day, procedures and the engine's own snapshots were built
  for the lab's real goal, an in-place upgrade from 22.10 to 26.04. On
  2026-10-08 the upgrade became four procedures, one per supervised visit.
  The engine also gained an apt check before anything runs, installs
  Timeshift itself, leaves the shared EFI partition out of its snapshots, and
  waits for a person after a cut-off. On 2026-10-08 and 2026-10-09 that
  machinery ran on the 22.10 VM (`tests/procedure-proof/`, Runs A to D): a
  reboot and the check after it, a time limit, Timeshift installed by the
  engine, its snapshot restored by itself, a power cut held for a person, and
  the apt check's refusal. On 2026-10-10 Runs B and D ran again with the
  fixes that followed (§16). The engine waited while another program held
  dpkg's lock, then held it through the snapshot, and the apt check named
  both dead sources. Remaining: the four upgrades on that VM, then an
  EFI desktop VM, finish proving
  `eos-release-dead-repos` (24.10 must heal; 20.04 and 25.04 must stay
  healthy), and grow the library of Ubuntu playbooks from problems we've
  really solved. The test releases are in `docs/sf3000-tracker.xlsx`.
- **Phase 2 — Front door.** A simple CLI/TUI, with deterministic
  keyword/symptom matching for the complaint-driven mode (no AI). Alongside:
  map out commands + use cases and review competitor apps to keep growing the
  library.
- **Phase 3 — Second platform.** Bring in Fedora, verifying fixes on the real
  machine. Confirm the distro-fork design holds.

**Perhaps introduce novelty (built on top — tentative, not committed).**
- **AI agent** — swap the deterministic matcher for smarter, conversational
  matching, and add plain-language explanation of diagnoses. It still only
  *selects*; it never authors commands.
- **Multi-computer connect (fleet).** A dashboard running the same engine
  across many machines with per-machine status.
- **Play with the lab computers.** Deploy against the real lab fleet that
  started this whole thing.
- **Extra features / Windows.** Windows arrives here as a further platform
  (playbook set #N, per §9), plus whatever else earns its place.

---

## 13. What already exists

- `schema/playbook.schema.json` — the rulebook (enforced; rejects bad entries).
- `playbooks/` — four playbooks, each proven on a VM (disk-full,
  missing-tool, boot-partition-full, failed-services); `candidates/` holds
  proposed entries that run only when named.
- `schema/procedure.schema.json` — the rulebook for procedures.
- `candidates/procedures/` — the 22.10 → 26.04 upgrade as four procedures,
  one upgrade each (`release-upgrade-to-23.04`, `-23.10`, `-24.04`,
  `-26.04`), never run.
- `engine/runner.py` — loads, validates, identifies the machine, runs detects,
  diagnoses, and prints fixes as dry-run. With `--fix <id>` it also runs the
  P1 lifecycle: confirm, fix, settle (when asked), verify, log, roll back.
  With `--run <id>` it starts a procedure (`--take-snapshot` or
  `--snapshot <name>` when it cannot be undone), after checking apt and
  installing Timeshift if it is missing; `--status` and `--cancel` follow it.
  Under sudo it offers to install its own Python packages with apt.
- `tests/rollback-proof/` — a fixture whose only job is to make the rollback
  path actually execute, on real apt. Repeatable since 2026-10-02.
- `tests/branch-proof/` — VM fixtures for the snapshot gate, `declined`,
  `rollback_failed` and `verify_error`.
- `tests/systemd-proof/` — throwaway services for the `failed-systemd-units`
  VM run.
- `tests/eos-proof/` — the steps for the `eos-release-dead-repos` VM runs.
  Nothing is induced: an end-of-life release installed offline is already
  broken the way the lab's machines were.
- `tests/procedure-proof/` — VM fixtures for procedures (a reboot, a time
  limit, a snapshot and its restore, a step cut off by a power-off) and an
  offline proof of every procedure branch.
- `tests/blocked-proof/`, `tests/lifecycle-proof/` — offline proofs of every
  lifecycle branch.
- `docs/sf3000-tracker.xlsx` — the backlog, its status, the VM run log and the
  Ubuntu releases to test on.
- `evidence/` — the VMs' run logs, copied off before a revert and never edited.

**Proven on a real machine:** the detect path, including SKIPPED reporting on
a non-matching host; and the fix lifecycle's main outcomes — `healed` and
`rolled_back`, the latter reached through the verify-failure branch that
triggers the undo. `fix_failed` and the `reverse: none` branch were reached on
2026-09-07 by an apt lock rather than by design. `verify_failed` stood as a
final outcome on 2026-09-08, when `disk-root-near-full` was pushed to 95%,
further than its reclaim can recover, and again on 2026-09-09 and 2026-09-10,
when `boot-partition-full` had no autoremovable kernel left. `blocked` came on
2026-09-09 with the lock held deliberately. The rest came from the fixtures in
`tests/branch-proof/` on 2026-10-04: `declined`, `rollback_failed`, and
`verify_error` (recorded, then undone) — and the snapshot gate's refusal, which
refused with exit 4 before running anything. With those, every outcome the
engine can record has been seen on a real machine. On 2026-10-06
`tests/systemd-proof/` added the `verify.settle_seconds` wait: a service that
crashed ten seconds after its restart was caught only because the check waited
30 seconds. The same session saw the privilege refusal, which refused with
exit 3 before running the check. The gate refuses rather than snapshots: there
is nothing to take a snapshot with until the snapshot layer is built. On
2026-10-07 the engine ran on a second release for the first time: on 22.10,
the candidate `eos-release-dead-repos` healed four times, twice before its
detect changed (§16) and twice after, with its undo run by hand between each
pair.

The evidence is the engine's `logs/runs.jsonl`. That file is gitignored, so it
exists only on the machine that ran it — which is why its absence on a dev box
is not evidence of anything — and a snapshot revert deletes it. The VM's logs
are copied off into `evidence/` before each revert. The first copy, made on
2026-10-03, starts at 2026-09-07 23:35 IST: something reset the log earlier
that evening, and restoring `clean-baseline` brings no older log back. The runs
before that survive only as transcriptions in the messages of commits 541cb09
and a42e25c. The snapshot gate and the privilege refusal write no record by
design; their results are the terminal output, confirmed by the person who ran
them.

**Procedures ran on a real machine for the first time on 2026-10-08 and
2026-10-09**, on the 22.10 VM (`tests/procedure-proof/`, Runs A to D). Seen
there: a step verified after the reboot it asked for, a step stopped at its
time limit (`fix_failed`), the engine installing Timeshift with apt
(`installed`), its own snapshot restored by itself after a failed step
(`rolled_back`), and a step cut off by a power-off. The boot after that cut
restored nothing and waited, and the restore ran only when the person asked
(`rolled_back`, `failure: interrupted`). The apt check refused a dead source
with exit 9, before asking anything. Run B's first snapshot failed on a
Timeshift bug (§16), and the engine stopped without running a step
(`snapshot_failed`). The records are in
`evidence/ubuntu-22.10-2026-10-09.jsonl`.

**On 2026-10-10 Runs B and D ran again**, with the engine holding dpkg's lock
and naming every dead source (§16). In Run B another program held the lock
first. The engine waited two minutes, then took the lock and held it while
Timeshift copied: apt, asked for the lock during the snapshot, named the
engine's own process. The snapshot was written to disk in under a second, and
the restore and its check passed again (`rolled_back`). Run D named both dead
sources. The records are in `evidence/ubuntu-22.10-2026-10-10.jsonl`.

**Not yet seen on a real machine:** an undo stopped by a package-manager lock
(`rollback_result: blocked (retryable)`). It passes offline. Nor have these
procedure branches: `rollback_failed`, `install_failed`, `blocked`, a check
that fails after its reboot, `--cancel` after a cut-off, and a wait for
dpkg's lock before a restore (the wait was seen before a snapshot only). Nor
have the four upgrade procedures themselves.

---

## 14. How the work divides

- **Library + schema owner** — owns the rulebook and grows the vetted
  playbooks. Vets the actual fix commands (needs someone who's run them on real
  machines).
- **Engine owner** — the detect→check→diagnose→fix→verify loop. Testable on
  Ubuntu.
- **Safety owner** — confirmation, snapshot, logging, reversibility.
- **Matcher owner** — the MVP's deterministic keyword/symptom selection (and,
  later, the AI-agent novelty if we pursue it).
- **Interface owner** — the CLI/TUI.

The library owner and engine owner need to agree the schema first, because it's
the contract everything else plugs into.

---

## 15. Open questions (to decide together)

- Where does the library live and how are entries reviewed (a Git repo with
  pull requests)?
- How do we take a safe "snapshot" before a destructive fix on each OS?
  *Partly answered 2026-10-07 (§16): on Ubuntu, Timeshift in rsync mode, for
  procedures. Single fixes still refuse.*
- How much should the AI explain to the user vs. keep simple?
- How do we test playbooks safely without breaking real machines (throwaway
  VMs/containers)?
- What's the very first *real* problem we want fixed end-to-end (fix included,
  not just detected)?

---

## 16. Decisions log

§15 holds what is still open. This holds what has been settled, with the reason,
so neither list has to be guessed at later.

**Schema v2 — `reverse` replaces the `reversible` boolean.** A boolean could say
that an undo existed but not *what it was*, which is useless to a runner that has
to actually perform the undo. `reverse.strategy` is one of `command` (an explicit
undo command, supplied in `reverse.command`), `snapshot_only` (destructive; the
only way back is a restore point), or `none` (the fix only touches regenerable
state — cache, old journals, a failed-state flag).

**`verify` is mandatory whenever a `fix` exists.** A fix that cannot be checked
has no business running unattended. A detect-only playbook — no `fix`, no
`verify` — remains valid, and is the intended shape for "report it, never touch
it" cases such as a full *user* data partition.

**A fix that exits 0 but does not flip the predicate is a failure.** Exit status
describes the command; the predicate describes the machine. Only the second one
matters.

**Snapshot gate: `risk == destructive` OR `reverse.strategy == snapshot_only`.**
This falls out of the two fields above, so it is runner behaviour rather than a
schema field. Safe and `command`-reversible fixes skip the snapshot.

**Confirmation is a plain CLI `y/N` prompt at P1.** The nicer TUI is P2; there is
no point investing in presentation before the lifecycle underneath it is proven.

**Logging is JSON Lines, one record per fix run.** Chosen so the eventual fleet
dashboard can consume the same records unchanged, with no reformatting step.

**2026-09-05 — The engine refuses rather than escalating privilege.** When a
playbook is marked `requires_privilege` and the engine is not already running
with the necessary rights, it stops with a clear message telling the user to
re-run under `sudo`. It does **not** prepend `sudo` itself.

*Why:* the project's core rule (§4) is that no vetted command is authored or
edited outside review. Prepending `sudo` is an edit — small, but it makes the
command that runs differ from the command a human approved, and it is exactly
the kind of convenience that erodes the guarantee the whole design rests on.
Refusing keeps the executed command byte-identical to the reviewed one, and
makes the privilege decision the user's explicit act rather than the tool's
silent one.

**2026-09-05 — A playbook that does not match the machine is reported as
SKIPPED, not silently omitted.** The engine checks `applies_to` before running
a detect, and refuses `--fix` on a non-matching playbook.

*Why:* until now `applies_to` was declared but never read, so every playbook
ran on every machine. Running the Linux disk playbook on Windows did not fail
— a `df` on the PATH measured the wrong disk and returned HEALTHY. A confident
wrong answer is worse than an error, because nothing signals that the check was
meaningless. Skipping silently would have the same flaw at a smaller scale, so
"we did not check this" is printed as distinctly as "we checked this and it is
fine."

**2026-09-05 — A declined fix is logged.** When the engine finds a problem,
offers a vetted fix, and the person says no, that is written to the log with
outcome `declined` and `confirmed: false`.

*Why:* the record already carried a `confirmed` field, but declines returned
before reaching the log, so the field was `true` in every record that existed
and therefore recorded nothing. Beyond making the field mean something, a fix
that is repeatedly declined is evidence about the fix — that it is wrong, or
frightening, or badly explained — and that signal is only visible if the
refusals are counted alongside the runs.

**2026-09-05 — The engine identifies the machine itself.** The OS comes from
the platform and the distro from `/etc/os-release`; `--os` and `--distro` remain
only as testing overrides.

*Why:* `--distro` previously defaulted to `ubuntu` on every machine, so half the
machine's identity was discovered and half was assumed. On Fedora that assumption
selects `apt-get` — and because `ubuntu` is a member of most playbooks' `distros`
list, the `applies_to` check would pass first and nothing would warn. A tool that
diagnoses machines should not need to be told what machine it is on.

When the distro cannot be determined the engine reports it as unknown and skips
distro-specific playbooks, rather than falling back to a guess. `ID_LIKE` is
deliberately not used to widen a match: Mint declaring `ID_LIKE=ubuntu` means
Ubuntu commands will probably work there, and probably is not the standard the
rest of the engine holds to.

**2026-09-05 — `rollback_failed` and `declined` are added to the run outcomes.**
Outcomes are `healed`, `fix_failed`, `verify_failed`, `rolled_back`,
`rollback_failed`, and `declined` (plus `verify_error`, added below).

*Why:* the original four had no way to record a failed fix whose undo *also*
failed. That is the worst state the system can reach — the machine has been
changed, the change did not work, and the change could not be taken back — and
it was the one state the log could not describe. It is also precisely what
someone reading the logs later most needs to find. The outcome field records
the final state of the machine; the rollback method and its result are recorded
alongside it as separate fields.

**2026-09-07 — A verify that cannot measure is `verify_error`, not
`verify_failed`.** When the post-fix check errors — a detect that timed out,
output that will not parse — the engine records outcome `verify_error`, puts
the reason in a `verify_error` field, leaves `verify_after` null, and undoes
the fix as it would for a verify that came back unhealthy.

*Why:* "the machine is still unhealthy" and "we could not tell" are different
facts, and only the first is a measurement. Conflating them printed a
confident wrong cause and wrote `verify_after: null` with outcome
`verify_failed` — indistinguishable in the log from a verify that genuinely
measured nothing. The change is still undone rather than left in place: §10's
rule is to *prove* recovery, and an unproven change sitting on a machine whose
state is unknown is precisely what rollback exists to prevent. Undoing returns
it to the state detect actually measured.

**2026-09-07 — `requires_privilege` describes the fix, not the detect.** The
flag is playbook-level, but the engine consults it only before running a fix;
detects remain runnable unprivileged either way. `failed-systemd-units` had it
set `false` on the strength of its detect, while its fix
(`systemctl reset-failed`) goes through polkit and fails without root.

*Why:* this flag is what the never-escalate guard reads, so setting it from the
detect's needs quietly disarmed that guard. Instead of "re-run under sudo", the
fix ran, exited 1, found `reverse.strategy: none` and reported `fix_failed` —
safe, but the wrong cause, and the user is told the fix is broken rather than
that they need privilege. A field that claims something untrue about a vetted
command is exactly the library-trust risk §11 names, so the schema now records
which half of the playbook the flag governs.

**2026-09-08 — A fix that could not START is `blocked`, not `fix_failed`, and
nothing is rolled back.** When a package manager refuses to run because another
process holds its lock, the engine records outcome `blocked`, puts the message
in a `blocked_reason` field, and tells the user to wait and run it again. It
does **not** invoke the undo. A locked *undo* is still `rollback_failed` — the
machine really does still carry the unproven change — but its
`rollback_result` is marked `blocked (retryable)` and the user is given the
undo command to re-run.

*Why:* this is the third instance of the same conflation §16 has now corrected
twice — *could not do it* reported as *did it and it did not work*. Hit for
real on the VM on 2026-09-07: `unattended-upgrades` held
`/var/cache/apt/archives/lock`, apt exited 100 before doing anything, and the
engine told a non-technical user their machine "needs manual attention" when
the truth was "wait ninety seconds". It affects every apt-based playbook, and
on real machines the Software Updater causes it just as readily.

Skipping the rollback is the load-bearing half. A blocked fix changed nothing,
so an undo would not be a reversal — it would be the run's only modification to
the machine. For `net-tools-missing` the undo is `apt-get remove -y net-tools`,
which would strip a package the run never installed and the user may have had
all along: the engine would break a machine it was asked to check. Detection is
deliberately narrow, matching the lock message rather than apt's exit 100, which
it also returns for genuine failures; an unrecognised lock message falls through
to `fix_failed`, which is the safe direction to be wrong in.

The vetted commands were amended in the same change, which is a §4 edit and was
approved as one: `-o DPkg::Lock::Timeout=60` makes apt wait for the lock rather
than fail instantly, in `disk-root-near-full`, `boot-partition-full` and both
halves of `net-tools-missing`. The two mechanisms are complements — the timeout
makes contention rare, `blocked` makes it honest when it happens anyway, and
only `blocked` covers the rollback path. That the timeout covers the *archives*
lock specifically, and not only the dpkg frontend lock it is usually documented
against, was unproven when this was written — the 2026-09-09 entry below records
the run that disproved it, and with it the "complements" claim in this
paragraph.

**2026-09-08 — `disk-root-near-full` reclaims the journal before the apt
cache.** Its fix is now `journalctl --vacuum-time=7d && apt-get clean` (the
`-o DPkg::Lock::Timeout=60` this entry originally carried was dropped on
2026-09-09; see below).

*Why:* the two halves reclaim independently and neither needs the other, but
only `apt-get clean` takes a lock. With apt first, the 2026-09-07 lock
contention forfeited *both* reclaims, including the journal vacuum that would
have succeeded untouched. Reordering costs nothing when the fix works — the
total reclaim is identical — and banks the lock-free half when it does not.
`&&` is kept rather than `;` so a blocked apt still propagates a non-zero exit:
with `;` the exit code would be `journalctl`'s, the engine would log
`fix_exit_code: 0` for a run where apt never ran, and a partial failure would
be invisible in the log.

**2026-09-09 — `DPkg::Lock::Timeout` does not cover the apt *archives* lock. The
flag is dropped from `disk-root-near-full` and kept, with its limit recorded, on
the other two.** Measured on the VM: with `/var/cache/apt/archives/lock` held for
90 seconds against a 60-second timeout, `apt-get clean` failed instantly instead
of waiting. The option governs the dpkg lock. The archives lock is a different
lock, and it is the one `unattended-upgrades` takes first.

*Why it matters:* the 2026-09-08 entry called the timeout and `blocked`
complements — "the timeout makes contention rare, `blocked` makes it honest when
it happens anyway". Half of that is now false. Nothing makes apt wait for the
lock that actually fails, so contention is exactly as common as it was on
2026-09-07, and `blocked` is not a complement but the entire mechanism. The same
run vetted it: outcome `blocked`, correct advice, no rollback, on a real machine.

The flag is removed from `disk-root-near-full`, whose only apt command is
`apt-get clean` — proven decorative there, and a decorative option inside a
vetted command is the §11 trust problem in miniature. It stays on
`boot-partition-full` and both halves of `net-tools-missing`, whose commands do
take the dpkg lock as well, where it can still help and cannot hurt; each
`source:` now records that it does nothing against an archives-lock holder.

`blocked`'s user-facing message was corrected in the same change. It said
"nothing was changed", which the journal-first reorder had made false —
`journalctl --vacuum-time=7d` runs and succeeds before apt is reached. It now
says the package manager never ran and changed nothing, which is the claim the
engine can actually stand behind.

**2026-10-02 — `failed-systemd-units` restarts services instead of clearing
their flag, and leaves the library until a VM run proves it.** Its fix was
`systemctl reset-failed`. That clears the failed flag — the very thing the
detect counted — so the predicate flipped whether or not any service had been
repaired. The engine could report `healed` on a machine that was still broken,
with `reverse: none`, after erasing the one piece of evidence that something
was wrong. The new fix restarts the failed services and checks again after a
30-second wait.

*Why restart, and why the wait:* a restarted service that stays up really is
repaired, so the predicate describes the machine again. But for `Type=simple`
units `systemctl restart` exits 0 as soon as the process forks, even if it dies
a second later, so a check made straight away would accept a service that is
about to fail. The scope is narrowed on purpose: services only, loaded units
only, and never `Type=oneshot`, because restarting a oneshot re-runs its job,
and for `apt-daily-upgrade.service` that job installs upgrades. Nobody agreeing
to "restart failed services" agreed to that. A new fix that has never run does
not belong in the trusted library (§11), so the entry moved to `candidates/`.
It also claimed a 24.04 test that left no record, and that claim was not
carried over. It returned to the library on 2026-10-06, once that VM run had
passed (§13).

**2026-10-02 — `verify.settle_seconds`: wait, then check once.** An optional
field (1–300). After a fix exits 0 the engine waits that long, then re-runs
detect a single time, and records the wait in the run log. It is deliberately
not a retry loop. A loop asks "did it ever look healthy?" — and a service that
crashes ten seconds after starting looks healthy at second one. The question is
whether the fix still holds after the wait. It first ran on a real machine on
2026-10-06 (§13).

**2026-10-02 — A detect may ask a server, as long as it changes nothing on the
machine.** `eos-release-dead-repos` decided "the repos are dead" from the
release's end-of-life date alone. Checked against the live servers: 20.04 is
past its standard end-of-life but still on the main archive (LTS releases stay
there through ESM), and 25.04 and 25.10 were past end-of-life but not yet
moved. The detect fired on all of them, and the fix would have pointed apt at
old-releases, which does not carry them — causing the very failure it names.
The detect now reports a problem only when archive.ubuntu.com returns 404 for
the release *and* old-releases returns 200. If it cannot reach them it reports
ERROR, not healthy.

*Why this is still a read-only detect:* §10's rule protects the machine.
Fetching a Release file's headers changes nothing here; `apt-get update`, which
the original sketch used, writes to `/var/lib/apt/lists` and so does not
qualify.

**2026-10-02 — The tracker moves into the repo.** The backlog spreadsheet was
a team handoff sheet kept outside the repo. The project now has one person
testing, so it became `docs/sf3000-tracker.xlsx` and changes in the same commits
as the playbooks it describes. Its Verified columns still take values only from
a real VM run.

**2026-10-07 — A detect counts only what apt reads.** On the 22.10 VM,
`eos-release-dead-repos` counted 20 old addresses where apt uses 10: each
`deb` line has a commented-out `deb-src` twin, and the detect counted both.
The fix rewrites both, so it healed. But on a machine whose active lines were
already fixed by hand, the comments alone would have read as a problem. The
detect now ignores everything after `#`, as apt does. A detect that measures
more than the system acts on can call a healthy machine broken. The changed
detect ran on 22.10 the same day: it counted 10, and with only the comments
left on the old address it read healthy.

**2026-10-07 — Procedures: multi-step work runs under a system service.** The
lab's real goal is moving a machine from 22.10 to 26.04 in place: four
upgrades, a reboot after each, hours in all. One fix cannot hold that — the
600-second limit kills it, nothing survives the reboot, and no terminal stays
open that long. A procedure is a new kind of entry with its own schema: ordered
steps, each with a read-only check of whether its goal is reached, a command,
a time limit of up to 12 hours, and an optional reboot. The person confirms
once. The engine then copies itself and the procedure into
`/var/lib/sf3000/` (root-owned, so a user cannot change what runs as root at
boot, and the approved text cannot change underneath it), installs
`sf3000-procedure.service`, and starts it. At each boot the service asks the
machine which steps are done, verifies the one it rebooted for, and runs the
next. It removes itself when the procedure finishes or stops.

*Why each rule:* the state file is marked before a step's command starts, so
a boot that finds a step still "running" knows it was cut off — that is the
new outcome `interrupted`, and the step is never re-run by itself. The resume
path reads JSON and needs only the standard library, because an upgrade may
replace or remove pyyaml and jsonschema partway. The service writes its
records to `/var/log/sf3000/`, never into a user's clone, since a root process
writing to a user-controlled path at boot is a path that user can redirect.
`KillMode=process`: if the engine itself dies, the step's command keeps
running, because killing an upgrade halfway is worse than letting it finish.

**2026-10-07 — The snapshot gate opens for procedures, two ways.** A procedure
that cannot be undone (`risk: destructive` or `snapshot_only`) still refuses
to start without a snapshot. The person either names one they took
(`--snapshot <name>`: a VM snapshot or disk image, recorded but not restorable
by the engine) or lets the engine take one (`--take-snapshot`). The lab's
machines are plain ext4, with no LVM or btrfs, so the engine uses Timeshift in
rsync mode: it copies the system (not `/home`) into `/timeshift`, its restore
reinstalls GRUB, and it can restore from a live USB when a machine no longer
boots. Single fixes (`--fix`) still refuse a destructive fix.

**2026-10-07 — A failed step is restored automatically, and the restore is
verified.** Chosen by the person who runs the lab, over the alternative of a
restore command run by hand. When a step fails, times out, is cut off, or its
check does not pass after the reboot, the service restores the engine's
snapshot; Timeshift reboots when it finishes. The boot after compares the
machine with a fingerprint taken just before the snapshot: the release and
every installed package with its version. A match is `rolled_back`; anything
else is `rollback_failed`, and the procedure stops for a person. Timeshift's
exit code is not trusted, because it also exits 0 when it gives up. Two cases
restore nothing: a step blocked by a busy package manager (it never ran), and
a precondition that does not hold (nothing was run).

*Why the engine's files are excluded from its snapshots:* Timeshift's restore
leaves alone whatever the snapshot excluded. Without that, a restore would roll
back the procedure's own state file to "step 1 pending", and the machine would
start the upgrade again, fail again and restore again, forever. The engine adds
its state directory, its log directory, and its service file and enable link to
Timeshift's exclude list, and changes nothing else in Timeshift's config.

**2026-10-08 — The lab's upgrade is four procedures, one per supervised
visit.** The person who runs the lab works in slots of 2 to 2.5 hours, after
which the lab assistant switches the machines off, and wants the engine to
run only while they watch. The four-step procedure carried straight on to the
next upgrade after each reboot: past the slot, and without them. It is now
four procedures of one step each, `release-upgrade-to-23.04` to
`release-upgrade-to-26.04`. Each visit's command names the upgrade it runs,
and each takes its own snapshot, so a restore goes back one release, not all
the way to 22.10. When `--run` names an upgrade that does not apply yet, the
engine names the one that does. Multi-step procedures still work.

*Why four procedures rather than a "stop after one step" option:* no new
engine logic, and the command says what it will do.

**2026-10-08 — A step cut off mid-run waits for a person.** This replaces the
cut-off case of the automatic restore above. A boot that finds a step still
marked running no longer restores at once. That boot may be the next person
to switch the machine on. A restore is a long copy that ends in a forced
reboot, and a restore cut off in turn can leave a machine that does not
start. So the boot notes the cut-off (phase `cut_off`), removes the service
and does nothing else.

The next `--run`, under sudo, offers the restore with a y/N. The restore then
runs under the service, so closing the terminal cannot stop it. `--cancel`
instead leaves the machine as it is and records the step as `interrupted`.
Failures during a run are still restored at once, because the person is
there: a step that fails, times out, or fails its check after the reboot.

**2026-10-08 — The snapshot leaves out `/boot/efi`.** The lab's machines
dual-boot Windows. Their `/boot/efi` is the Windows disk's EFI partition, so
it holds Windows' boot files too, and the person who runs the lab says
Windows must not be touched. Timeshift adds no mount under `/boot` to its
excludes, so it copies `/boot/efi` into its snapshots, and its restore would
write the partition back.

Its restore leaves alone whatever the snapshot excluded: it runs rsync
without `--delete-excluded` (read in Timeshift 22.06.5). So the engine adds
`/boot/efi/***` to its excludes. After a restore, Timeshift reinstalls GRUB,
which writes only Ubuntu's own folder on that partition, as every GRUB update
does. This has not yet run on an EFI machine.

**2026-10-08 — apt is checked before anything runs.** One of the lab's
machines lists a third-party repository with no 22.10, and a mirror that
does not exist. `apt-get update` fails there, and an upgrade starts with
`apt-get update`, so it would fail after the snapshot and turn into a
restore. The engine now runs `apt-get update` before the y/N, and refuses
with exit 9, naming the failing sources.

`apt-get update` exits 0 when a source cannot be reached, so the engine reads
its messages, not only its exit code. Warnings that are not failures, such as
a source listed twice, do not count.

*Why this does not break §10's read-only rule:* that rule is for detects.
This check is part of `--run`, under sudo, and it writes only apt's lists.

**2026-10-08 — The engine installs what it needs, with apt, after a y.** The
person who runs the lab wants as little technical work by hand as possible.
`--take-snapshot` now installs Timeshift when it is missing. The plan says
so, and the install comes after the y and before anything else, in the
foreground. It is recorded as `installed` or `install_failed`. Under sudo, the
engine also offers to install python3-yaml and python3-jsonschema when it
cannot import them.

*Why apt and never pip:* the service runs `/usr/bin/python3` after each
upgrade. A release upgrade moves that to a newer Python, which does not see
modules pip installed for the old one.

**2026-10-08 — A step's output is read one attempt at a time.** The engine
reads the end of a step's output to tell "the package manager was busy" from
"it failed". That output came from a log file that keeps every attempt. So a
lock message left by the attempt before could make a real failure read as
busy, and a step that had changed something would have been left without a
restore. It now reads only the current attempt. Found while building the apt
check, and covered offline.

**2026-10-09 — The engine's snapshot command passes no `--tags`.** Run B on
the 22.10 VM stopped at its snapshot: Timeshift 22.06.5 answered
`--tags O` with "Unknown value specified for option --tags (O)", and listed
O among the values it expects. Its check accepts B, H, D, W and M only. The
same check is in 22.11.2, 23.07.1 and 24.01.1, the versions the upgrades
will meet on their way to 24.04; 25.12.4 fixed it. Every one of them tags a
snapshot taken with `--create` as on-demand by itself, so the option is
dropped. The engine did what it should: no snapshot, so no step ran
(`snapshot_failed`). The offline proof had stubbed Timeshift and accepted
any options, which is how this got past it. It now checks the command's
exact options, as it already did for the restore.

The restore command was read again against the same five versions. Up to
24.01.1, `--scripted` does not skip the restore's questions. The engine
gives Timeshift no input, so each question takes its default, and
`--grub-device` makes reinstalling GRUB the default. 25.12.4 skips the
questions under `--scripted`, with the same defaults.

**2026-10-09 — The apt check names every dead source.** Run D on the 22.10 VM
added two dead sources, and the engine refused, as it should. But it named
only one. Once one source fails hard, such as a suite with no Release file,
apt prints no "Failed to fetch" line for any source (`apt-pkg/update.cc`,
2.5.3). A host that cannot be reached is then named only on its `Err:` line.
The engine kept only the summary lines, so it left that host out. It now also
keeps each `Err:` line, with its reason, whose source no summary line names.
It also reads all of apt's output, not just the last 8 KB. Lab machine 1 has
exactly this mix: a mirror that does not exist, and a repository with no 22.10.
Run D ran again on 2026-10-10 and named both sources.

**2026-10-09 — The engine holds dpkg's lock while it snapshots or restores.**
After a restore, the engine compares the machine with a fingerprint of its
packages, taken just before the snapshot. If unattended-upgrades, the Software
Updater or a person's apt changed packages in between, or during the restore,
a good restore would fail its check, or the copy would hold a half-installed
package. All of them take dpkg's frontend lock before they change anything.
So the engine takes it too, as apt does (fcntl), and holds it twice:

- from the fingerprint to the end of the snapshot
- from just before the restore until the reboot

If another program holds the lock, the engine waits, as it does for a busy
step: up to 30 tries, a minute apart. If it is still busy before the
snapshot, nothing has run, and the outcome is `blocked`. If it is still busy
before the restore, the outcome is `rollback_failed`, and the snapshot is
left untouched for a restore by hand.

The engine takes the lock before the phase says RESTORING. So a machine that
goes down while waiting for it counts as a cut-off, held for a person, not as
a restore to check. On the 22.10 VM, the automatic updates were switched off
for the runs instead. `repos-fixed` and the lab's machines have them on.

Seen on the 22.10 VM on 2026-10-10, in Run B again. A second session held the
lock. The engine waited two tries, took the lock at the next one after it was
let go, and held it while Timeshift copied: apt, asked for the lock then,
named the engine's own process. The wait before a restore has not been seen.

**2026-10-09 — The snapshot is written to disk before step 1.** Timeshift does
not flush its copy when it finishes (read in 22.06.5), and neither did the
engine. So for half a minute or so, part of a new snapshot could exist only in
memory. A power cut then would leave a damaged copy for a restore to put back.
The engine now runs `sync` after the snapshot, and the phase stays SNAPSHOTTING
until `sync` returns. A power cut before then stops the procedure, with no step
run. On the 22.10 VM on 2026-10-10, the `sync` after a 27-second snapshot took
under a second.

---

*Last updated: revision 21 — Runs B and D ran again on the 22.10 VM with
revision 20's fixes (2026-10-10). The engine waited for dpkg's lock while
another program held it, then held it through the snapshot; the snapshot was
synced; both dead sources were named. §12, §13 and §16 updated.
Revision 20 — the apt check names every dead source; dpkg's
lock is held around the snapshot and the restore; the snapshot is synced to
disk before step 1 (2026-10-09). Built and passing offline on Python 3.12 and
3.10, not yet run on the VM; §10 and §16 updated.
Revision 19 — recorded the procedure runs on the 22.10 VM
(2026-10-08 and 2026-10-09, Runs A to D, all passed); §12 and §13 updated.
Revision 18 — the snapshot command drops `--tags O`, which
Timeshift 22.06.5 to 24.01.1 refuse; found when Run B's snapshot failed on
the 22.10 VM (2026-10-09); §16 updated.
Revision 17 — the lab's upgrade split into four procedures,
one per supervised visit; apt checked before anything runs; Timeshift and the
engine's Python packages installed with apt after a y; `/boot/efi` left out
of snapshots; a cut-off step waits for a person; a step's output read one
attempt at a time (2026-10-08). Built and passing offline on Python 3.12 and
3.10, not yet run on a VM; §10, §12, §13 and §16 updated.
Revision 16 — procedures, the engine's own Timeshift
snapshots and automatic restore (2026-10-07), built and passing offline, not
yet run on a VM; §10, §12, §13, §15 and §16 updated.
Revision 15 — the comment-skipping detect re-ran on 22.10
(2026-10-07): healed twice, and read healthy with only comments left old;
§12, §13 and §16 updated.
Revision 14 — `eos-release-dead-repos`' detect now counts
only active lines, not comments (2026-10-07); §12 and §16 updated.
Revision 13 — recorded Session 3 (2026-10-07):
`eos-release-dead-repos` healed twice on 22.10, the first engine runs on a
release other than 26.04; it stays a candidate until 24.10, 20.04 and 25.04
have run; §12 and §13 updated.
Revision 12 — recorded Session 2 (2026-10-06):
`failed-systemd-units` proved its restart fix on the VM and returned to the
library, and the `verify.settle_seconds` wait and the privilege refusal were
seen on a real machine for the first time; §12, §13 and §16 updated.
Revision 11 — recorded Session 1 (2026-10-04): the fixtures in
`tests/branch-proof/` and `tests/rollback-proof/` ran green, so every outcome
the engine can record, and the snapshot gate's refusal, has now been seen on a
real machine; §12 and §13 updated, and the branches still unseen named.
Revision 10 — reconciled §13 with the 26.04 VM's run log, copied
off on 2026-10-03 into `evidence/`. It showed `verify_failed` first standing on
2026-09-08, not 2026-09-09; `disk-root-near-full` healing twice before the run
recorded as its first; and `boot-partition-full`'s amended command healing on
2026-09-08, where the record said it never had. Revision 9 — recorded the 2026-09-09 `verify_failed` run;
the 2026-10-02 decisions on `failed-systemd-units`, `verify.settle_seconds`,
server checks in a detect and the tracker; the lab's release (22.10); and the
new test fixtures. Revision 8 — recorded the 2026-09-09 VM result:
`DPkg::Lock::Timeout` does not cover the apt archives lock, and `blocked` earned
its first real-machine run; reconciled §13 with it. Revision 7 — recorded the
`blocked` outcome and the apt-lock command amendments of 2026-09-08. Revision 6 — corrected §12/§13, which revision 5 wrongly
recorded as "never run end-to-end": the run log in the VM shows the rollback
proof passing on 2026-09-05, minutes after the fixture was committed, and again
on 2026-09-07. Revision 5 — reconciled §12/§13 with the code and recorded the
`verify_error` and `requires_privilege` decisions of 2026-09-07. Revision 4 — added §16 (decisions log) recording the
schema v2 model, the privilege and rollback-outcome decisions of 2026-09-05,
and brought §10 in line with the `reverse` block. Revision 3 — reconciled with the rough-plan sketch. The AI agent
is now a "perhaps introduce novelty" direction, not part of the MVP; MVP
complaint-matching is deterministic (no AI); roadmap split into Build-Out-MVP
and Perhaps-Introduce-Novelty to mirror the sketch. Open questions (§15) left
unanswered on purpose. Add, cut, and correct anything that doesn't match what we
actually intend to build.*
