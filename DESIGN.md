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

- Our lab systems ran an already end-of-support (EOS) version of Ubuntu. A
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
- **Reversibility** — each playbook states whether an undo exists; prefer fixes
  that can be undone.
- **Logging** — every command run (and its output) is recorded, so there's a
  trail if something goes wrong.
- **Verify after fix** — never assume success; re-run the check to prove it.

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
- **Phase 1 — Safe single machine.** Add the safety layer so a confirmed,
  logged, reversible fix can actually run and be verified. Grow the library of
  Ubuntu playbooks from problems we've really solved. Test by making and
  breaking throwaway VMs.
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

A working Phase-0 skeleton:
- `schema/playbook.schema.json` — the rulebook (enforced; rejects bad entries).
- `playbooks/` — three real playbooks (disk-full, failed-services,
  missing-tool).
- `engine/runner.py` — loads, validates, runs detects, diagnoses; prints fixes
  as dry-run only (never executes them yet).

It ran on the Ubuntu test sandbox: two checks came back healthy, one flagged a
real problem and printed (but did not run) the fix.

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
- How much should the AI explain to the user vs. keep simple?
- How do we test playbooks safely without breaking real machines (throwaway
  VMs/containers)?
- What's the very first *real* problem we want fixed end-to-end (fix included,
  not just detected)?

---

*Last updated: revision 3 — reconciled with the rough-plan sketch. The AI agent
is now a "perhaps introduce novelty" direction, not part of the MVP; MVP
complaint-matching is deterministic (no AI); roadmap split into Build-Out-MVP
and Perhaps-Introduce-Novelty to mirror the sketch. Open questions (§15) left
unanswered on purpose. Add, cut, and correct anything that doesn't match what we
actually intend to build.*
