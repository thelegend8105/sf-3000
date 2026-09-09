#!/usr/bin/env python3
"""
Proof for the `blocked` outcome. Run:  python tests/blocked-proof/test_blocked.py

Unlike tests/rollback-proof/, this needs no VM and no apt. What it proves is
BRANCH LOGIC, not machine behaviour, and the branch it guards is the dangerous
one: when a fix is blocked by a package-manager lock the fix never started, so
the machine is unchanged, so the engine must NOT roll back. Rolling back there
would not be a reversal — it would be the run's only change to the machine.
The concrete harm case is net-tools-missing, whose undo is
`apt-get remove -y net-tools`: a spurious rollback would remove a package this
run never installed and the user may have had all along.

No pytest — the engine's only deps are pyyaml and jsonschema, and this keeps it
that way. Exits non-zero on the first failure.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
import runner  # noqa: E402

# The message apt actually produced on the VM, 2026-09-07.
REAL_APT_LOCK = ("E: Could not get lock /var/cache/apt/archives/lock. "
                 "It is held by process 1844 (unattended-upgr)")

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {label}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {label}")


def playbook(strategy="none"):
    pb = {
        "id": "blocked-proof",
        "description": "fixture",
        "applies_to": {"os": "linux", "distros": ["ubuntu"]},
        "detect": {"command": "true", "produces": "integer"},
        "expect": {"predicate": "less_than", "value": 90},
        "risk": "moderate",
        "requires_privilege": False,
        "reverse": {"strategy": strategy},
        "fix": {"ubuntu": "apt-get clean"},
        "verify": {"rerun": "detect"},
    }
    if strategy == "command":
        pb["reverse"]["command"] = {"ubuntu": "apt-get remove -y net-tools"}
    return pb


def drive(pb, fix_result, undo_result=None):
    """Run fix_one with the shell stubbed out. Returns (record, undo_ran)."""
    seen = {"record": None, "undo_ran": False}
    calls = []

    def fake_run_command(cmd, timeout):
        calls.append(cmd)
        if len(calls) == 1:
            return fix_result
        seen["undo_ran"] = True
        return undo_result

    runner.run_command = fake_run_command
    runner.assess = lambda p: (runner.PROBLEM, 99, "measured=99")
    runner.confirm = lambda p, c, s: True
    runner.log_run = lambda rec, path: seen.__setitem__("record", rec)

    with tempfile.TemporaryDirectory() as td:
        runner.fix_one(pb, "ubuntu", Path(td) / "runs.jsonl", "linux")
    return seen["record"], seen["undo_ran"]


print("1. is_lock_contention matches what apt really said")
check("real apt lock message", runner.is_lock_contention("", REAL_APT_LOCK), True)
check("dpkg frontend lock",
      runner.is_lock_contention("", "E: Unable to acquire the dpkg frontend lock"), True)
check("message on stdout not stderr", runner.is_lock_contention(REAL_APT_LOCK, ""), True)

print("2. and does NOT match a genuine failure (a false 'blocked' tells someone")
print("   to wait while their machine actually needs help)")
check("no space left", runner.is_lock_contention("", "E: No space left on device"), False)
check("unmet dependencies",
      runner.is_lock_contention("", "E: Unmet dependencies. Try 'apt --fix-broken install'"), False)
check("empty streams", runner.is_lock_contention("", ""), False)
check("exit 100 alone is not enough", runner.is_lock_contention("", "exit=100"), False)

print("3. a locked fix is BLOCKED, and nothing is undone")
rec, undo_ran = drive(playbook("command"), (100, "", REAL_APT_LOCK))
check("outcome", rec["outcome"], runner.BLOCKED)
check("undo did NOT run", undo_ran, False)
check("rollback_method untouched", rec["rollback_method"], None)
check("blocked_reason recorded", rec["blocked_reason"], REAL_APT_LOCK)
check("fix_exit_code recorded", rec["fix_exit_code"], 100)

print("4. regression: a real failure still rolls back as before")
rec, undo_ran = drive(playbook("command"),
                      (100, "", "E: No space left on device"), (0, "", ""))
check("outcome", rec["outcome"], runner.ROLLED_BACK)
check("undo ran", undo_ran, True)
check("blocked_reason stays empty", rec["blocked_reason"], None)

print("5. a locked UNDO is still rollback_failed, but flagged retryable")
rec, _ = drive(playbook("command"),
               (100, "", "E: No space left on device"), (100, "", REAL_APT_LOCK))
check("outcome", rec["outcome"], runner.ROLLBACK_FAILED)
check("result says retryable",
      rec["rollback_result"].startswith("blocked (retryable)"), True)

print("6. reverse: none is unaffected — blocked still means blocked")
rec, undo_ran = drive(playbook("none"), (100, "", REAL_APT_LOCK))
check("outcome", rec["outcome"], runner.BLOCKED)
check("no 'needs manual attention' path taken", rec["rollback_method"], None)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S)")
    sys.exit(1)
print("all checks passed")
