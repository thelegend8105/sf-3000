#!/usr/bin/env python3
"""
Offline proof of the fix lifecycle's branches. Run:
    python tests/lifecycle-proof/test_outcomes.py

Like tests/blocked-proof/, this needs no VM: the shell, the detect and the
prompt are stubbed, so what it proves is BRANCH LOGIC — which outcome each
situation produces, and above all what does NOT run. It complements the VM
fixtures in tests/branch-proof/, which prove the same branches on a real
machine; it does not replace them.

No pytest — the engine's only deps are pyyaml and jsonschema, and this keeps
it that way. Exits non-zero if any check fails.
"""

import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
import runner  # noqa: E402

FAILURES = []
STUBBED = ("run_command", "assess", "confirm", "log_run", "has_privilege",
           "run_detect")
ORIGINAL = {name: getattr(runner, name) for name in STUBBED}
ORIGINAL_TIME = runner.time


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {label}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {label}")


def playbook(**over):
    pb = {
        "id": "lifecycle-proof",
        "description": "fixture",
        "applies_to": {"os": "linux", "distros": ["ubuntu"]},
        "detect": {"command": "true", "produces": "integer"},
        "expect": {"predicate": "less_than", "value": 90},
        "risk": "moderate",
        "requires_privilege": False,
        "reverse": {"strategy": "command",
                    "command": {"ubuntu": "undo-command"}},
        "fix": {"ubuntu": "fix-command"},
        "verify": {"rerun": "detect"},
    }
    pb.update(over)
    return pb


def drive(pb, *, detects, runs=(), answer=True, privileged=True,
          host_os="linux", distro="ubuntu"):
    """Run fix_one with everything external stubbed.

    detects: the (status, measurement, detail) each assess() call returns, in
    order. runs: the (code, out, err) each run_command() call returns, in
    order. Returns what happened, including every event in sequence.
    """
    seen = {"record": None, "events": []}
    detects, runs = list(detects), list(runs)

    def fake_assess(p):
        seen["events"].append("detect")
        return detects.pop(0)

    def fake_run(cmd, timeout):
        seen["events"].append(f"run:{cmd}")
        return runs.pop(0)

    def fake_confirm(p, c, s):
        seen["events"].append("confirm")
        return answer

    runner.assess = fake_assess
    runner.run_command = fake_run
    runner.confirm = fake_confirm
    runner.log_run = lambda rec, path: seen.__setitem__("record", rec)
    runner.has_privilege = lambda: privileged
    runner.time = SimpleNamespace(
        sleep=lambda s: seen["events"].append(f"sleep:{s}"))
    try:
        with tempfile.TemporaryDirectory() as td, redirect_stdout(io.StringIO()):
            rc = runner.fix_one(pb, distro, Path(td) / "runs.jsonl", host_os)
    finally:
        for name, fn in ORIGINAL.items():
            setattr(runner, name, fn)
        runner.time = ORIGINAL_TIME
    seen["rc"] = rc
    return SimpleNamespace(**seen)


BROKEN = (runner.PROBLEM, 99, "measured=99")
FIXED = (runner.HEALTHY, 10, "measured=10")
UNREADABLE = (runner.ERROR, None, "could not parse output as integer: 'x'")
OK, FAIL = (0, "", ""), (1, "", "E: something broke")


print("1. declined: logged, nothing runs")
r = drive(playbook(), detects=[BROKEN], answer=False)
check("outcome", r.record["outcome"], runner.DECLINED)
check("confirmed", r.record["confirmed"], False)
check("fix_command recorded", r.record["fix_command"], "fix-command")
check("nothing ran", [e for e in r.events if e.startswith("run:")], [])
check("exit code", r.rc, 0)

print("2. healed: fix, verify, done")
r = drive(playbook(), detects=[BROKEN, FIXED], runs=[OK])
check("outcome", r.record["outcome"], runner.HEALED)
check("verify_after", r.record["verify_after"], 10)
check("exit code", r.rc, 0)

print("3. verify_error with an undo: recorded as such, and the fix is undone")
r = drive(playbook(), detects=[BROKEN, UNREADABLE], runs=[OK, OK])
check("verify_error field set", r.record["verify_error"], UNREADABLE[2])
check("verify_after stays null", r.record["verify_after"], None)
check("undo ran", "run:undo-command" in r.events, True)
check("outcome", r.record["outcome"], runner.ROLLED_BACK)

print("4. verify_error with reverse: none -> the outcome itself")
r = drive(playbook(reverse={"strategy": "none"}),
          detects=[BROKEN, UNREADABLE], runs=[OK])
check("outcome", r.record["outcome"], runner.VERIFY_ERROR)
check("exit code", r.rc, 1)

print("5. rollback_failed: fix heals nothing, undo fails")
r = drive(playbook(), detects=[BROKEN, BROKEN], runs=[OK, (7, "", "deliberate")])
check("outcome", r.record["outcome"], runner.ROLLBACK_FAILED)
check("rollback_result", r.record["rollback_result"], "failed: deliberate")
check("rollback_exit_code", r.record["rollback_exit_code"], 7)
check("exit code", r.rc, 1)

print("6. verify_failed with reverse: none stands as the outcome")
r = drive(playbook(reverse={"strategy": "none"}),
          detects=[BROKEN, BROKEN], runs=[OK])
check("outcome", r.record["outcome"], runner.VERIFY_FAILED)

print("7. fix_failed with reverse: none")
r = drive(playbook(reverse={"strategy": "none"}), detects=[BROKEN], runs=[FAIL])
check("outcome", r.record["outcome"], runner.FIX_FAILED)
check("no verify after a failed fix", r.events.count("detect"), 1)

print("8. snapshot gate: destructive is refused before ANYTHING runs")
r = drive(playbook(risk="destructive"), detects=[BROKEN])
check("exit code", r.rc, 4)
check("no events at all", r.events, [])
check("nothing logged", r.record, None)
r = drive(playbook(reverse={"strategy": "snapshot_only"}), detects=[BROKEN])
check("snapshot_only undo is gated too", r.rc, 4)
check("snapshot_only: no events", r.events, [])

print("9. privilege: refused, never escalated")
r = drive(playbook(requires_privilege=True), detects=[BROKEN], privileged=False)
check("exit code", r.rc, 3)
check("no events", r.events, [])

print("10. wrong machine: refused before detect")
r = drive(playbook(), detects=[BROKEN], host_os="windows")
check("wrong OS exit code", r.rc, 5)
check("wrong OS: no events", r.events, [])
r = drive(playbook(), detects=[BROKEN], distro="fedora")
check("wrong distro exit code", r.rc, 5)

print("11. detect-only: no fix, nothing runs")
pb = playbook()
del pb["fix"], pb["verify"]
r = drive(pb, detects=[BROKEN])
check("exit code", r.rc, 2)
check("no events", r.events, [])

print("12. already healthy: nothing runs, nothing logged")
r = drive(playbook(), detects=[FIXED])
check("exit code", r.rc, 0)
check("only the detect ran", r.events, ["detect"])
check("nothing logged", r.record, None)

print("13. settle_seconds: wait AFTER the fix, BEFORE the one verify")
r = drive(playbook(verify={"rerun": "detect", "settle_seconds": 30}),
          detects=[BROKEN, FIXED], runs=[OK])
check("order", r.events,
      ["detect", "confirm", "run:fix-command", "sleep:30", "detect"])
check("recorded", r.record["settle_seconds"], 30)
r = drive(playbook(), detects=[BROKEN, FIXED], runs=[OK])
check("no settle -> no sleep", any(e.startswith("sleep") for e in r.events), False)
check("no settle -> recorded null", r.record["settle_seconds"], None)
r = drive(playbook(verify={"rerun": "detect", "settle_seconds": 30}),
          detects=[BROKEN], runs=[FAIL, OK])   # OK = the undo
check("a failed fix does not wait", any(e.startswith("sleep") for e in r.events), False)

print("14. line_count detail names what was found")
units = [f"svc{i}.service" for i in range(1, 8)]
CASES = [
    (units[:2], "measured=2 (svc1.service, svc2.service)"),
    (units, "measured=7 (svc1.service, svc2.service, svc3.service, "
            "svc4.service, svc5.service, ... (+2 more))"),
    ([], "measured=0"),
]
for found, want in CASES:
    proc = SimpleNamespace(stdout="\n".join(found) + "\n", returncode=0)
    runner.run_detect = lambda p, proc=proc, n=len(found): (n, proc, None)
    try:
        _, _, detail = runner.assess(playbook(
            detect={"command": "x", "produces": "line_count"},
            expect={"predicate": "equals", "value": 0}))
    finally:
        runner.run_detect = ORIGINAL["run_detect"]
    check(f"{len(found)} found", detail, want)

print("15. sweep: a detect-only PROBLEM is not offered as a fix")
with tempfile.TemporaryDirectory() as td:
    (Path(td) / "detect-only.yaml").write_text(
        "id: detect-only-proof\n"
        "description: fixture\n"
        "applies_to: {os: linux, distros: [ubuntu]}\n"
        "detect: {command: 'true', produces: integer}\n"
        "expect: {predicate: less_than, value: 90}\n"
        "risk: safe\n"
        "requires_privilege: false\n"
        "reverse: {strategy: none}\n"
        "source: fixture\n", encoding="utf-8")
    runner.assess = lambda p: BROKEN
    argv, sys.argv = sys.argv, ["runner.py", "--playbooks", td,
                                "--os", "linux", "--distro", "ubuntu"]
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            runner.main()
    finally:
        sys.argv = argv
        runner.assess = ORIGINAL["assess"]
    text = out.getvalue()
    check("says detect-only", "detect-only" in text, True)
    check("no 'DRY-RUN fix' line", "DRY-RUN fix" in text, False)
    check("no --fix hint", "--fix <id>" in text, False)
    check("still counted as a problem", "1 problem(s) found" in text, True)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S)")
    sys.exit(1)
print("all checks passed")
