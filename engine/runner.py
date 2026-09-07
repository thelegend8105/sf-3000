#!/usr/bin/env python3
"""
SF 3000 — engine (single machine).

Detect path (unchanged):
  * loads playbooks from a directory
  * validates each against schema/playbook.schema.json
  * runs the READ-ONLY detect command
  * evaluates the expect predicate  -> HEALTHY / PROBLEM / ERROR

Fix path (P1, opt-in via --fix <id>):
  confirm -> [snapshot if gated] -> fix -> verify -> log -> rollback on failure

Fixes NEVER run unless --fix names a playbook explicitly. Without it this is
still a read-only reporting tool.

What it deliberately does NOT do yet:
  * take snapshots (mechanism unratified — the gate refuses instead of guessing)
  * match free-text complaints to playbooks (that's the matcher layer)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
    from jsonschema import Draft7Validator
except ImportError:
    sys.exit("Missing deps. Run: pip install pyyaml jsonschema --break-system-packages")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "playbook.schema.json"
DEFAULT_LOG = ROOT / "logs" / "runs.jsonl"

HEALTHY, PROBLEM, ERROR = "HEALTHY", "PROBLEM", "ERROR"
SKIPPED = "SKIPPED"   # not for this machine — deliberately NOT checked

DETECT_TIMEOUT = 30    # read-only probes are quick
FIX_TIMEOUT = 600      # apt-get install on a slow VM is not

# Outcomes describe the final state of the MACHINE (see DESIGN.md section 16).
HEALED = "healed"                    # fix ran, predicate flipped
FIX_FAILED = "fix_failed"            # fix errored, nothing undone
VERIFY_FAILED = "verify_failed"      # fix ran, predicate did not flip, nothing undone
VERIFY_ERROR = "verify_error"        # fix ran, verify could not measure — state unknown
ROLLED_BACK = "rolled_back"          # undo ran and succeeded
ROLLBACK_FAILED = "rollback_failed"  # undo attempted and failed — worst case
DECLINED = "declined"                # problem found, fix offered, human said no
BLOCKED = "blocked"                  # fix could not START — retryable, machine untouched

# "Could not start" is not "ran and did not work" — the same conflation the
# verify_error outcome closed. A package manager whose lock is held by
# unattended-upgrades or the Software Updater has changed nothing, and the
# correct advice is to wait, not to call for manual attention.
#
# Matched on the MESSAGE, never on the exit code alone: apt exits 100 for
# genuine failures too, and a false "blocked" would tell someone to sit and
# wait while their machine actually needs help. Narrow on purpose — an
# unrecognised lock message falls through to fix_failed, which is the safe
# direction to be wrong in.
LOCK_CONTENTION_PATTERNS = (
    "could not get lock",
    "unable to acquire the dpkg frontend lock",
    "unable to lock the administration directory",
    "unable to lock directory",
    "waiting for cache lock",
    "failed to obtain the transaction lock",   # dnf
)


def is_lock_contention(*streams) -> bool:
    """True when a package manager refused to start because its lock is held."""
    blob = " ".join(s for s in streams if s).lower()
    return any(pattern in blob for pattern in LOCK_CONTENTION_PATTERNS)


try:  # keep the tick/cross glyphs from crashing a cp1252 console (Windows)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _marks():
    try:
        "✓✗→".encode(sys.stdout.encoding or "ascii")
        return {"HEALTHY": "✓", "PROBLEM": "✗",
                "ERROR": "!", "SKIPPED": "-", "arrow": "→"}
    except (UnicodeEncodeError, LookupError):
        return {"HEALTHY": "OK", "PROBLEM": "XX", "ERROR": "!!",
                "SKIPPED": "--", "arrow": "->"}


MARK = _marks()


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_playbooks(pb_dir: Path, validator: Draft7Validator):
    """Load + validate every .yaml in pb_dir. A playbook that fails schema
    validation is rejected outright — the library is the trust anchor."""
    playbooks, errors = [], []
    for path in sorted(pb_dir.glob("*.yaml")):
        pb = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema_errs = sorted(validator.iter_errors(pb), key=lambda e: e.path)
        if schema_errs:
            msgs = "; ".join(e.message for e in schema_errs)
            errors.append(f"{path.name}: {msgs}")
        else:
            playbooks.append(pb)
    return playbooks, errors


def run_detect(pb: dict):
    """Run the read-only detect command; return (measurement, proc, err)."""
    cmd = pb["detect"]["command"]
    produces = pb["detect"]["produces"]
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=DETECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, None, "detect timed out"

    out = proc.stdout.strip()
    try:
        if produces == "integer":
            measurement = int(out)
        elif produces == "line_count":
            measurement = len([ln for ln in out.splitlines() if ln.strip()])
        elif produces == "exit_code":
            measurement = proc.returncode
        else:  # string
            measurement = out
    except ValueError:
        return None, proc, f"could not parse output as {produces}: {out!r}"
    return measurement, proc, None


def evaluate(pb: dict, measurement, proc) -> bool:
    """Return True if the machine is HEALTHY for this playbook."""
    pred = pb["expect"]["predicate"]
    val = pb["expect"].get("value")

    if pred == "exit_zero":
        return proc.returncode == 0
    if pred == "less_than":
        return measurement < val
    if pred == "greater_than":
        return measurement > val
    if pred == "equals":
        return measurement == val
    if pred == "not_equals":
        return measurement != val
    if pred == "contains":
        return str(val) in str(measurement)
    if pred == "not_contains":
        return str(val) not in str(measurement)
    if pred == "regex_match":
        return re.search(str(val), str(measurement)) is not None
    raise ValueError(f"unknown predicate: {pred}")


def assess(pb: dict):
    """Full read: (status, measurement, detail). diagnose() wraps this."""
    measurement, proc, err = run_detect(pb)
    if err:
        return ERROR, None, err
    healthy = evaluate(pb, measurement, proc)
    return (HEALTHY if healthy else PROBLEM), measurement, f"measured={measurement!r}"


def diagnose(pb: dict):
    status, _measurement, detail = assess(pb)
    return status, detail


OS_RELEASE = Path("/etc/os-release")


def current_distro(path: Path = OS_RELEASE):
    """Ask the machine which distro it is, via /etc/os-release ID=.

    Returns None when it cannot be determined. None is deliberate: the engine
    then skips distro-specific playbooks and says so, rather than assuming a
    distro and picking apt-get on a dnf box.

    ID_LIKE is intentionally NOT used as a fallback. Mint reporting
    ID_LIKE=ubuntu means Ubuntu commands will *probably* work there, and
    "probably" is not the standard the rest of this engine holds to.
    """
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ID="):
                return line.split("=", 1)[1].strip().strip("\"'").lower() or None
    except OSError:
        pass
    return None


def current_os() -> str:
    """This machine, in the vocabulary applies_to.os uses."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def applies(pb: dict, host_os: str, distro: str):
    """Is this playbook meant for this machine? Returns (bool, reason).

    A playbook that does not apply is never run. Its detect command may well
    still execute here and return something plausible — a linux df on a
    Windows box will happily measure the wrong disk — and a confident wrong
    answer is worse than no answer.
    """
    want_os = pb["applies_to"]["os"]
    if want_os != host_os:
        return False, f"for {want_os}, this machine is {host_os}"
    distros = pb["applies_to"].get("distros")
    if distros:
        if distro is None:
            return False, (f"for {'/'.join(distros)}, and this machine's distro "
                           "could not be determined")
        if distro not in distros:
            return False, f"for {'/'.join(distros)}, this machine is {distro}"
    return True, None


def pick_fix(pb: dict, distro: str):
    fix = pb.get("fix", {})
    return fix.get(distro) or fix.get("default")


def pick_reverse(pb: dict, distro: str):
    """The undo command, when reverse.strategy is 'command'."""
    cmds = pb["reverse"].get("command", {})
    return cmds.get(distro) or cmds.get("default")


def needs_snapshot(pb: dict) -> bool:
    """Gate from DESIGN.md section 16: destructive risk OR snapshot_only undo."""
    return pb["risk"] == "destructive" or pb["reverse"]["strategy"] == "snapshot_only"


def has_privilege() -> bool:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None:
        return geteuid() == 0
    try:  # Windows
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_command(cmd: str, timeout: int):
    """Run a vetted command verbatim. Returns (exit_code, stdout, stderr).
    The command is never edited — not even to add sudo (DESIGN.md section 16)."""
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout}s"


def confirm(pb: dict, fix_cmd: str, snapshot: bool) -> bool:
    strategy = pb["reverse"]["strategy"]
    print()
    print("=" * 64)
    print(f"  {pb['id']}")
    print(f"  {pb['description']}")
    print("-" * 64)
    print(f"  command   : {fix_cmd}")
    print(f"  risk      : {pb['risk']}")
    print(f"  undo      : {strategy}")
    if strategy == "none":
        print("              (no undo — if this fails you fix it by hand)")
    print(f"  snapshot  : {'yes' if snapshot else 'no'}")
    print(f"  source    : {pb.get('source', 'unrecorded')}")
    print("=" * 64)
    try:
        return input("Run this fix? [y/N] ").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def rollback(pb: dict, distro: str, record: dict):
    """Undo a fix. Returns ROLLED_BACK / ROLLBACK_FAILED, or None when there is
    nothing to undo (the caller then keeps its own failure outcome)."""
    strategy = pb["reverse"]["strategy"]
    record["rollback_method"] = strategy

    if strategy == "none":
        record["rollback_result"] = "nothing to undo"
        print(f"  {MARK['arrow']} no undo exists for this playbook — "
              "the machine needs manual attention")
        return None

    if strategy == "snapshot_only":
        record["rollback_result"] = "unimplemented"
        print(f"  {MARK['arrow']} rollback needs a snapshot restore, "
              "which is not built yet")
        return ROLLBACK_FAILED

    undo = pick_reverse(pb, distro)
    if not undo:
        record["rollback_result"] = f"no undo command for distro {distro!r}"
        print(f"  {MARK['arrow']} no undo command for {distro}")
        return ROLLBACK_FAILED

    print(f"  {MARK['arrow']} rolling back: {undo}")
    code, out, err = run_command(undo, FIX_TIMEOUT)
    record["rollback_command"] = undo
    record["rollback_exit_code"] = code
    if code == 0:
        record["rollback_result"] = "ok"
        print(f"  {MARK['arrow']} rollback succeeded")
        return ROLLED_BACK
    if is_lock_contention(out, err):
        # Still rollback_failed — the machine really does still carry the
        # unproven change. But the undo is retryable, and "run this again in a
        # minute" is a different instruction from "you are on your own".
        record["rollback_result"] = f"blocked (retryable): {err or code}"
        print(f"  {MARK['arrow']} ROLLBACK BLOCKED — another process holds the "
              "package manager lock")
        print(f"  {MARK['arrow']} the change is still in place. Once that "
              f"process finishes, undo it with:  {undo}")
        return ROLLBACK_FAILED
    record["rollback_result"] = f"failed: {err or code}"
    print(f"  {MARK['arrow']} ROLLBACK FAILED ({err or code})")
    return ROLLBACK_FAILED


def log_run(record: dict, log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def fix_one(pb: dict, distro: str, log_path: Path, host_os: str) -> int:
    """Run the full lifecycle for one playbook. Returns a process exit code."""
    # Wrong machine: refuse before anything, including detect.
    ok, why = applies(pb, host_os, distro)
    if not ok:
        print(f"{pb['id']} does not apply to this machine ({why}). Refusing to fix.")
        return 5

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "playbook_id": pb["id"],
        "distro": distro,
        "detect_before": None,
        "confirmed": False,
        "snapshot_taken": False,
        "snapshot_id": None,
        "fix_command": None,
        "fix_exit_code": None,
        "verify_after": None,
        "verify_error": None,
        "blocked_reason": None,
        "outcome": None,
        "rollback_method": None,
        "rollback_result": None,
    }

    fix_cmd = pick_fix(pb, distro)
    if not fix_cmd:
        if "fix" not in pb:
            print(f"{pb['id']} is detect-only — it has no fix by design.")
        elif distro is None:
            print(f"{pb['id']} keys its fix by distro, and this machine's "
                  "distro could not be determined.")
        else:
            print(f"{pb['id']} has no fix command for distro {distro!r}.")
        return 2

    # Privilege: refuse, never escalate (DESIGN.md section 16).
    if pb.get("requires_privilege") and not has_privilege():
        print(f"{pb['id']} requires administrative privilege and this engine "
              "is not running with it.")
        print("Re-run under sudo. The engine will not add sudo to a vetted "
              "command on your behalf.")
        return 3

    # Snapshot gate — refuse rather than fix unprotected.
    snapshot = needs_snapshot(pb)
    if snapshot:
        print(f"{pb['id']} requires a snapshot before fixing "
              f"(risk={pb['risk']}, undo={pb['reverse']['strategy']}), and the "
              "snapshot mechanism is not built yet. Refusing to fix.")
        return 4

    status, measurement, detail = assess(pb)
    record["detect_before"] = measurement
    if status == ERROR:
        print(f"{MARK['ERROR']} detect failed: {detail}")
        return 1
    if status == HEALTHY:
        print(f"{MARK['HEALTHY']} {pb['id']} is already healthy ({detail}). "
              "Nothing to do.")
        return 0

    print(f"{MARK['PROBLEM']} {pb['id']}: {detail}")
    if not confirm(pb, fix_cmd, snapshot):
        # A decline is a real event: the engine found a problem, offered a
        # vetted fix, and a person said no. Worth recording — a fix that is
        # repeatedly declined is telling you something about the fix.
        record["fix_command"] = fix_cmd
        record["outcome"] = DECLINED
        log_run(record, log_path)
        print(f"Cancelled. Nothing was run. (logged to {log_path})")
        return 0
    record["confirmed"] = True

    print(f"\n  {MARK['arrow']} running: {fix_cmd}")
    code, out, err = run_command(fix_cmd, FIX_TIMEOUT)
    record["fix_command"] = fix_cmd
    record["fix_exit_code"] = code

    if code != 0 and is_lock_contention(out, err):
        # The fix never started, so the machine is unchanged — and an undo here
        # would be a change, not a reversal. For net-tools-missing that undo is
        # `apt-get remove -y net-tools`, which would remove a package this run
        # never installed and the user may have had all along.
        record["blocked_reason"] = err or out or f"exit={code}"
        record["outcome"] = BLOCKED
        print(f"  {MARK['arrow']} could not start: another process is using "
              "the package manager")
        print(f"  {MARK['arrow']} nothing was changed. Wait for it to finish "
              "(usually a minute or two) and run this again.")
    elif code != 0:
        print(f"  {MARK['arrow']} fix failed (exit={code}) {err}")
        record["outcome"] = rollback(pb, distro, record) or FIX_FAILED
    else:
        print(f"  {MARK['arrow']} fix exited 0 — verifying")
        v_status, v_measure, v_detail = assess(pb)
        if v_status == HEALTHY:
            record["verify_after"] = v_measure
            print(f"  {MARK['HEALTHY']} verified healthy ({v_detail})")
            record["outcome"] = HEALED
        elif v_status == ERROR:
            # "Could not measure" is not "still broken" — say so, and do not
            # leave an unproven change in place (DESIGN.md section 16).
            record["verify_error"] = v_detail
            print(f"  {MARK['ERROR']} fix ran but verify could not measure "
                  f"the machine ({v_detail}) — its state is unknown")
            record["outcome"] = rollback(pb, distro, record) or VERIFY_ERROR
        else:
            # Exit 0 is not success. The predicate decides.
            record["verify_after"] = v_measure
            print(f"  {MARK['PROBLEM']} fix ran but the machine is still "
                  f"unhealthy ({v_detail})")
            record["outcome"] = rollback(pb, distro, record) or VERIFY_FAILED

    log_run(record, log_path)
    print(f"\noutcome: {record['outcome']}  (logged to {log_path})")
    return 0 if record["outcome"] == HEALED else 1


def main():
    ap = argparse.ArgumentParser(description="SF 3000 engine")
    ap.add_argument("--playbooks", default=str(ROOT / "playbooks"))
    ap.add_argument("--distro", default=current_distro(),
                    help="override the detected distro (testing)")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--fix", metavar="ID",
                    help="actually run the fix for this playbook id (asks first)")
    ap.add_argument("--log", default=str(DEFAULT_LOG),
                    help="JSONL run log (default: logs/runs.jsonl)")
    ap.add_argument("--os", dest="host_os", default=current_os(),
                    help="override the detected OS (testing)")
    args = ap.parse_args()

    validator = Draft7Validator(load_schema())
    playbooks, errors = load_playbooks(Path(args.playbooks), validator)

    if errors:
        print("SCHEMA ERRORS — these playbooks were rejected:")
        for e in errors:
            print(f"  {MARK['PROBLEM']} {e}")
        print()

    print(f"Loaded {len(playbooks)} valid playbook(s).")
    if args.validate_only:
        return 1 if errors else 0

    if args.fix:
        chosen = next((p for p in playbooks if p["id"] == args.fix), None)
        if chosen is None:
            print(f"No valid playbook with id {args.fix!r}.")
            return 2
        return fix_one(chosen, args.distro, Path(args.log), args.host_os)

    problems = skipped = 0
    identity = f"{args.host_os}/{args.distro}" if args.distro else args.host_os
    print(f"\nRunning checks on {identity}:\n" + "-" * 60)
    for pb in playbooks:
        ok, why = applies(pb, args.host_os, args.distro)
        if not ok:
            skipped += 1
            print(f"{MARK[SKIPPED]} [{SKIPPED:7}] {pb['id']:22} not checked — {why}")
            continue
        status, detail = diagnose(pb)
        print(f"{MARK[status]} [{status:7}] {pb['id']:22} {detail}")
        if status == PROBLEM:
            problems += 1
            fix = pick_fix(pb, args.distro)
            print(f"            {MARK['arrow']} {pb['description']}")
            print(f"            {MARK['arrow']} risk={pb['risk']} "
                  f"undo={pb['reverse']['strategy']} "
                  f"privilege={pb['requires_privilege']}")
            print(f"            {MARK['arrow']} DRY-RUN fix ({args.distro}): {fix}")
    print("-" * 60)
    summary = f"{problems} problem(s) found."
    if skipped:
        summary += f" {skipped} playbook(s) skipped — not for this machine."
    print(f"{summary} (No fixes were executed.)")
    if problems:
        print("Run one for real with:  --fix <id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
