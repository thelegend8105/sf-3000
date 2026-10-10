#!/usr/bin/env python3
"""
SF 3000 — engine (single machine).

Detect path (unchanged):
  * loads playbooks from a directory
  * validates each against schema/playbook.schema.json
  * runs the READ-ONLY detect command
  * evaluates the expect predicate  -> HEALTHY / PROBLEM / ERROR

Fix path (P1, opt-in via --fix <id>):
  confirm -> [snapshot if gated] -> fix -> [settle] -> verify -> log
  -> rollback on failure

Procedure path (opt-in via --run <id>):
  check apt -> confirm once -> [install Timeshift] -> hand off to a system
  service -> for each step: check -> run -> [reboot] -> verify -> log,
  carrying on after each reboot

Fixes NEVER run unless --fix names a playbook explicitly, and procedures never
run unless --run names one. Without either this is still a read-only
reporting tool.

What it deliberately does NOT do yet:
  * take a snapshot before a single fix (--fix still refuses a destructive
    one; only procedures take snapshots, with Timeshift)
  * match free-text complaints to playbooks (that's the matcher layer)
"""

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Missing deps are handled in main(), not here: with apt, the engine offers to
# install them (ensure_engine_deps). The procedure service runs this file at
# boot with --resume, partway through a release upgrade that may have just
# replaced or removed these packages; that path reads JSON and needs only the
# standard library. Imported one at a time, so the engine can name which one
# is missing.
try:
    import yaml
except ImportError:
    yaml = None
try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "playbook.schema.json"
PROCEDURE_SCHEMA_PATH = ROOT / "schema" / "procedure.schema.json"
DEFAULT_LOG = ROOT / "logs" / "runs.jsonl"

HEALTHY, PROBLEM, ERROR = "HEALTHY", "PROBLEM", "ERROR"
SKIPPED = "SKIPPED"   # not for this machine — deliberately NOT checked

DETECT_TIMEOUT = 30    # read-only probes are quick
FIX_TIMEOUT = 600      # apt-get install on a slow VM is not
FOUND_SHOWN = 5        # line_count detects: how many found lines to print

# Outcomes describe the final state of the MACHINE (see DESIGN.md section 16).
HEALED = "healed"                    # fix ran, predicate flipped
FIX_FAILED = "fix_failed"            # fix errored, nothing undone
VERIFY_FAILED = "verify_failed"      # fix ran, predicate did not flip, nothing undone
VERIFY_ERROR = "verify_error"        # fix ran, verify could not measure — state unknown
ROLLED_BACK = "rolled_back"          # undo ran and succeeded
ROLLBACK_FAILED = "rollback_failed"  # undo attempted and failed — worst case
DECLINED = "declined"                # problem found, fix offered, human said no
BLOCKED = "blocked"                  # package manager never ran — retryable

# "Could not start" is not "ran and did not work" — the same conflation the
# verify_error outcome closed. A package manager whose lock is held by
# unattended-upgrades or the Software Updater has changed nothing, and the
# correct advice is to wait, not to call for manual attention.
#
# This outcome carries the behaviour on its own. The VM run of 2026-09-09 held
# /var/cache/apt/archives/lock for 90s against a 60s `DPkg::Lock::Timeout` and
# apt failed instantly: the option does not cover that lock, so nothing makes
# apt wait for the lock unattended-upgrades actually takes. See DESIGN.md §16.
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


def load_schema(path: Path = SCHEMA_PATH):
    return json.loads(path.read_text(encoding="utf-8"))


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
    detail = f"measured={measurement!r}"
    if pb["detect"]["produces"] == "line_count" and measurement:
        # A bare count hides WHAT was found. For failed-systemd-units that is
        # the list of services a fix would restart, and the person answering
        # y/N should see it before they answer.
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        shown = ", ".join(lines[:FOUND_SHOWN])
        if len(lines) > FOUND_SHOWN:
            shown += f", ... (+{len(lines) - FOUND_SHOWN} more)"
        detail += f" ({shown})"
    return (HEALTHY if healthy else PROBLEM), measurement, detail


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


def ask_yes(question: str) -> bool:
    """One y/N question. Anything but y, including no terminal to ask on, is no."""
    try:
        return input(question).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


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
    # Wrapped, not printed raw: a long provenance line runs to a dozen terminal
    # rows and pushes command/risk/undo — the things actually being authorised —
    # up out of sight above the prompt.
    print(textwrap.fill(pb.get("source", "unrecorded"), width=64,
                        initial_indent="  source    : ",
                        subsequent_indent="              "))
    print("=" * 64)
    return ask_yes("Run this fix? [y/N] ")


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
        "settle_seconds": None,
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
        # The package manager never ran, so an undo here would be a change,
        # not a reversal. For net-tools-missing that undo is `apt-get remove -y
        # net-tools`, which would remove a package this run never installed and
        # the user may have had all along.
        #
        # A compound fix may still have completed an earlier lock-free step —
        # disk-root-near-full vacuums the journal before apt — so the message
        # below claims only that the PACKAGE MANAGER changed nothing, never
        # that the run as a whole did.
        record["blocked_reason"] = err or out or f"exit={code}"
        record["outcome"] = BLOCKED
        print(f"  {MARK['arrow']} could not start: another process is using "
              "the package manager")
        print(f"  {MARK['arrow']} the package manager never ran, so it "
              "changed nothing. Wait for it to finish (usually a minute or "
              "two), then run this again.")
    elif code != 0:
        print(f"  {MARK['arrow']} fix failed (exit={code}) {err}")
        record["outcome"] = rollback(pb, distro, record) or FIX_FAILED
    else:
        settle = pb.get("verify", {}).get("settle_seconds")
        if settle:
            # Wait, then check ONCE. Not "retry until healthy": a restarted
            # service that crashes ten seconds later is healthy at second one,
            # and retrying would accept that first good reading. The question
            # is whether the fix still holds after the wait.
            record["settle_seconds"] = settle
            print(f"  {MARK['arrow']} fix exited 0 — waiting {settle}s for the "
                  "machine to settle, then verifying")
            time.sleep(settle)
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


# --- Procedures --------------------------------------------------------------
#
# A procedure is an ordered list of steps, each shaped like a small playbook:
# a read-only check of whether its goal is reached, a command, and the same
# check again as its verify. A release upgrade is the case it exists for: an
# upgrade, then a reboot, then the check, an hour or more in all. No terminal
# should have to survive that, so after one y/N the engine hands the work to a
# system service. The service runs a root-owned copy of this file at every
# boot until the last step is verified or the procedure stops, then removes
# itself. The lab's upgrade is four procedures of one step each, one per
# visit, each run with the person there (candidates/procedures/README.md).
#
# Rules that carry the safety of fix_one over to a run with no terminal:
#   * Before the y/N, apt must update from every source; after it, anything
#     the run needs (Timeshift) is installed, in the foreground, before the
#     hand-off. A dead source found later would fail the first step after the
#     snapshot, and so turn into a restore.
#   * The machine is asked, not the state file. A step whose check already
#     passes is skipped; one that is pending only because the file says so
#     is not trusted.
#   * Nothing runs twice by itself. A boot that finds a step still marked
#     "running" means the machine went down mid-step: `interrupted`. With the
#     engine's snapshot, that boot does not restore it: it may be a boot
#     nobody is watching. The engine waits, and the next --run offers the
#     restore (hold_cut_off, offer_undo).
#   * A failed step is undone if the engine can undo it. With --take-snapshot
#     the engine takes a Timeshift snapshot before step 1 and, when a step
#     fails, restores it and reboots; the boot after checks that the machine
#     really is back. Without one, the procedure stops and names the snapshot
#     the person took.
#   * Every stop removes the service. A failed procedure must never retry
#     itself at every boot.

PROCEDURE_STATE_DIR = Path("/var/lib/sf3000")     # root-owned: the copy and state
PROCEDURE_LOG_DIR = Path("/var/log/sf3000")       # root-owned: records and step output
PROCEDURE_UNIT = "sf3000-procedure.service"
PROCEDURE_UNIT_PATH = Path("/etc/systemd/system") / PROCEDURE_UNIT
SYSTEM_PYTHON = "/usr/bin/python3"   # the interpreter that survives an upgrade
BLOCKED_RETRIES = 30                 # boot-time apt jobs can hold the lock a while
BLOCKED_WAIT = 60
# Set aside in the state directory while a procedure runs, and freed first
# when a step fails. A step that fails because the disk filled up leaves no
# room for anything, and the engine writes its state before it starts the
# restore: on a full disk that write would fail, and nothing would be
# restored. Freeing this first leaves room for the state, and for Timeshift
# to start. The snapshot leaves the state directory out, so it is not copied.
RESERVE_BYTES = 256 * 1024 * 1024

TIMESHIFT_CONF = Path("/etc/timeshift/timeshift.json")
SNAPSHOT_TIMEOUT = 2 * 3600
RESTORE_TIMEOUT = 2 * 3600
# Left out of the engine's snapshots, and so left alone by their restore
# (Timeshift's restore skips whatever the snapshot excluded):
#   * the procedure's own state, logs and service. Otherwise a restore would
#     roll back the state file, and the next boot would start the procedure
#     again from step 1.
#   * /boot/efi. On the lab's dual-boot machines it is the Windows disk's EFI
#     partition, holding Windows' boot files as well as Ubuntu's, and a
#     restore must not rewrite Windows'. Timeshift's restore reinstalls GRUB
#     afterwards, which writes only Ubuntu's own folder there, as every GRUB
#     update does.
#   * the release upgrader's logs. It writes why it failed only there (its
#     main.log), not to the output the engine keeps. Left out, they survive
#     the restore that follows a failed upgrade.
SNAPSHOT_EXCLUDES = [
    "/var/lib/sf3000/***",
    "/var/log/sf3000/***",
    f"/etc/systemd/system/{PROCEDURE_UNIT}",
    f"/etc/systemd/system/multi-user.target.wants/{PROCEDURE_UNIT}",
    "/boot/efi/***",
    "/var/log/dist-upgrade/***",
]

APT_CHECK_TIMEOUT = 15 * 60
APT_CHECK_OUTPUT = 1 << 20    # read all of it: a dead source's Err: line comes early
APT_SHOWN = 10                # the failing sources printed before "... (+N more)"
APT_INSTALL_TIMEOUT = 30 * 60
# What `apt-get update` prints when a source failed. It exits 0 when a source
# cannot be reached (it keeps that source's old lists and only warns), so its
# messages are read as well as its exit code. Warnings that are not failures,
# such as a source listed twice, do not match. English, so apt runs under
# LC_ALL=C.
APT_FAILURE = re.compile(r"^(Err:|E: |W: Failed to fetch |W: Some index files failed)")

INTERRUPTED = "interrupted"            # the machine went down while a step ran — state unknown
SNAPSHOT_FAILED = "snapshot_failed"    # the engine's snapshot could not be taken — nothing ran
INSTALLED = "installed"                # the engine installed something it needs, with apt
INSTALL_FAILED = "install_failed"      # ...and apt failed; nothing else was run

PENDING, RUNNING, REBOOTING = "pending", "running", "rebooting"
SNAPSHOTTING, RESTORING = "snapshotting", "restoring"
# A step was cut off and the engine waits for a person (hold_cut_off); then
# the person asked for the restore (offer_undo).
CUT_OFF, RESTORE_ASKED = "cut_off", "restore_asked"
DONE, FAILED, CANCELLED = "done", "failed", "cancelled"
FINISHED = (DONE, FAILED, CANCELLED)

UNIT_TEXT = """\
[Unit]
Description=SF 3000: carry on procedure {procedure_id} after a reboot
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart={python} -I {runner} --resume
TimeoutStartSec=infinity
# If the engine itself stops, leave the step's command running: killing a
# release upgrade halfway is worse than letting it finish.
KillMode=process

[Install]
WantedBy=multi-user.target
"""


def load_procedures(proc_dir: Path, validator):
    """Like load_playbooks, against the procedure schema. Returns
    ([(procedure, path)], errors). A missing directory holds no procedures."""
    procedures, errors = [], []
    if not proc_dir.is_dir():
        return procedures, errors
    for path in sorted(proc_dir.glob("*.yaml")):
        proc = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema_errs = sorted(validator.iter_errors(proc), key=lambda e: e.path)
        if schema_errs:
            msgs = "; ".join(e.message for e in schema_errs)
            errors.append(f"{path.name}: {msgs}")
        else:
            procedures.append((proc, path))
    return procedures, errors


def pick_step_command(step: dict, distro: str):
    run = step.get("run", {})
    return run.get(distro) or run.get("default")


def state_path() -> Path:
    return PROCEDURE_STATE_DIR / "state.json"


def read_state():
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(state: dict):
    """Write atomically and to disk: a power cut must leave either the old
    state or the new one, never half of each."""
    PROCEDURE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROCEDURE_STATE_DIR / "state.json.tmp"
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(state, indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, state_path())


def procedure_log_path() -> Path:
    # Never the clone's logs/: the service writes as root at boot, and a path
    # inside a user's home is a path that user can point somewhere else.
    return PROCEDURE_LOG_DIR / "runs.jsonl"


def reserve_path() -> Path:
    return PROCEDURE_STATE_DIR / "reserve"


def make_reserve():
    """Set RESERVE_BYTES aside: real blocks (posix_fallocate), not a sparse
    file, so that deleting it really frees them."""
    fd = os.open(reserve_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if hasattr(os, "posix_fallocate"):
            os.posix_fallocate(fd, 0, RESERVE_BYTES)
        else:                  # not Linux: only the offline tests come here
            os.ftruncate(fd, RESERVE_BYTES)
    finally:
        os.close(fd)


def release_reserve() -> bool:
    """Free the reserve; True if there was one. Never raises: it runs first on
    the paths where the disk may be full."""
    try:
        reserve_path().unlink()
        return True
    except OSError:
        return False


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def systemctl(*args) -> int:
    proc = subprocess.run(["systemctl", *args], capture_output=True, text=True)
    return proc.returncode


def service_active() -> bool:
    """Is the service doing something right now? A oneshot service that is
    running its command is "activating", not "active", and `is-active` exits
    non-zero for that — so read the state, not the exit code."""
    proc = subprocess.run(["systemctl", "is-active", PROCEDURE_UNIT],
                          capture_output=True, text=True)
    return proc.stdout.strip() in ("active", "activating", "deactivating", "reloading")


def reboot_machine():
    systemctl("reboot")


def systemd_running() -> bool:
    return Path("/run/systemd/system").is_dir()


def timeshift_installed() -> bool:
    return shutil.which("timeshift") is not None


def install_service(procedure_id: str):
    runner = PROCEDURE_STATE_DIR / "engine" / "runner.py"
    PROCEDURE_UNIT_PATH.write_text(UNIT_TEXT.format(
        procedure_id=procedure_id, python=SYSTEM_PYTHON, runner=runner),
        encoding="utf-8")
    systemctl("daemon-reload")
    systemctl("enable", PROCEDURE_UNIT)
    systemctl("start", "--no-block", PROCEDURE_UNIT)


def remove_service():
    systemctl("disable", PROCEDURE_UNIT)
    try:
        PROCEDURE_UNIT_PATH.unlink()
    except FileNotFoundError:
        pass
    systemctl("daemon-reload")


def make_engine_dirs():
    """The engine's root-owned directories. It writes there as root, at boot
    too, so it refuses to write through a symlink a user could have planted."""
    for d in (PROCEDURE_STATE_DIR, PROCEDURE_STATE_DIR / "engine", PROCEDURE_LOG_DIR):
        if d.is_symlink():
            raise OSError(f"{d} is a symlink; refusing to write through it")
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o755)


def stage_procedure(proc: dict, proc_path: Path):
    """Copy what the service will run into a root-owned place. The service
    runs at boot as root, so it must not run anything a user can edit — and it
    must run exactly the text the person approved, even if the clone changes."""
    make_engine_dirs()
    shutil.copyfile(Path(__file__).resolve(),
                    PROCEDURE_STATE_DIR / "engine" / "runner.py")
    shutil.copyfile(proc_path, PROCEDURE_STATE_DIR / "procedure.yaml")
    (PROCEDURE_STATE_DIR / "procedure.json").write_text(
        json.dumps(proc, indent=2), encoding="utf-8")


def run_logged(cmd, timeout: int, log_file: Path, tail_bytes: int = 8192):
    """Run a command, output to a file. Returns (exit_code, tail): the last
    tail_bytes of THIS run's output only. The file keeps every attempt, and a
    lock message left by the attempt before must not read as this one's.
    exit_code is None when the time limit stopped it. A string runs through
    the shell verbatim; a list runs without one. Either way it gets its own
    process group, so the limit stops all of it, not only the shell."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    shown = cmd if isinstance(cmd, str) else " ".join(cmd)
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== {now_utc()} running:\n{shown}\n===\n")
        fh.flush()
        start = os.fstat(fh.fileno()).st_size
        proc = subprocess.Popen(cmd, shell=isinstance(cmd, str),
                                stdin=subprocess.DEVNULL, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            code = None
    with log_file.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(start, fh.tell() - tail_bytes))
        tail = fh.read().decode("utf-8", "replace")
    return code, tail


def run_step(cmd: str, timeout: int, log_file: Path, tail_bytes: int = 8192):
    """Run a step, waiting out a held package-manager lock. Returns
    (exit_code, tail, attempts). Retrying is safe only because `blocked`
    means the package manager never started (see LOCK_CONTENTION_PATTERNS);
    at boot, apt's own daily jobs often hold the lock for a few minutes."""
    attempts = 0
    while True:
        attempts += 1
        code, tail = run_logged(cmd, timeout, log_file, tail_bytes)
        if code not in (0, None) and is_lock_contention(tail) \
                and attempts < BLOCKED_RETRIES:
            print(f"  {MARK['arrow']} the package manager is busy — waiting "
                  f"{BLOCKED_WAIT}s, then trying again (attempt {attempts + 1} "
                  f"of {BLOCKED_RETRIES})", flush=True)
            time.sleep(BLOCKED_WAIT)
            continue
        return code, tail, attempts


# --- apt: checked first, and the engine's own tools ---------------------------
#
# Both run in the foreground, with the person there, before anything is handed
# to the service. The engine installs only what it needs to work (pyyaml and
# jsonschema to read its library, Timeshift to take its snapshots), only with
# apt, and only after a y.

def apt_available() -> bool:
    return shutil.which("apt-get") is not None


def check_apt():
    """Can apt update its lists from every source? Returns (ok, problems),
    problems being apt's own lines about what failed. A release upgrade starts
    with this same update; a dead source would fail it there, after the
    snapshot, and so turn into a restore. Found here, it costs a minute and
    changes nothing but apt's lists."""
    make_engine_dirs()
    log_file = PROCEDURE_LOG_DIR / "apt-check.log"
    print(f"{MARK['arrow']} checking that apt can update from every source "
          f"(apt-get update; output in {log_file})", flush=True)
    code, tail, attempts = run_step(["env", "LC_ALL=C", "apt-get", "update"],
                                    APT_CHECK_TIMEOUT, log_file, APT_CHECK_OUTPUT)
    if code is None:
        return False, [f"apt-get update did not finish in {APT_CHECK_TIMEOUT // 60} min"]
    if code != 0 and is_lock_contention(tail):
        return False, [f"the package manager stayed busy through {attempts} "
                       "attempts; try again in a few minutes"]
    found = [ln.strip() for ln in tail.splitlines() if APT_FAILURE.match(ln.strip())]
    problems = apt_problems(tail) or found
    if code != 0 and not problems:
        problems = [f"apt-get update exited {code}"]
    if not problems:
        print(f"  {MARK['HEALTHY']} apt updated from every source", flush=True)
    return not problems, problems


def apt_problems(output: str):
    """The sources apt-get update could not use, in apt's own words. Its
    summary lines (E: and "W: Failed to fetch") name the source and the
    reason. But once one source fails hard, such as a suite with no Release
    file, apt prints no "Failed to fetch" line for any source (apt-pkg/update.cc,
    2.5.3). A host that cannot be reached is then named only on its Err: line,
    "Err:5 http://host/ubuntu kinetic InRelease", with the reason on the line
    under it. So each Err: line whose source no summary line names is kept
    too, joined to its reason."""
    lines = [ln.strip() for ln in output.splitlines()]
    raw = output.splitlines()
    summary = [ln for ln in lines if ln.startswith(("E: ", "W: Failed to fetch "))]
    problems = list(summary)
    for i, ln in enumerate(lines):
        if not ln.startswith("Err:"):
            continue
        parts = ln.split()
        if len(parts) >= 3 and any(f"{parts[1]} {parts[2]} " in s
                                   or f"{parts[1]}/dists/{parts[2]}/" in s for s in summary):
            continue
        under = raw[i + 1] if i + 1 < len(raw) else ""
        reason = under.strip() if under[:1].isspace() else ""
        named = f"{ln}  {reason}" if reason else ln
        if named not in problems:
            problems.append(named)
    return problems


def report_apt_failure(problems):
    print(f"{MARK['PROBLEM']} apt cannot update from every source, so nothing "
          "was started:")
    for line in problems[:APT_SHOWN]:
        print(f"    {line}")
    if len(problems) > APT_SHOWN:
        print(f"    ... (+{len(problems) - APT_SHOWN} more)")
    print(f"  {MARK['arrow']} fix or remove those sources (/etc/apt/sources.list "
          "and /etc/apt/sources.list.d/), then run this again. Nothing was "
          "installed or upgraded.")
    print(f"  {MARK['arrow']} apt's full output: {PROCEDURE_LOG_DIR / 'apt-check.log'}")


def install_packages(packages):
    """apt-get install in the foreground, waiting out a held lock. Returns
    (outcome, record fields, tail of apt's output)."""
    make_engine_dirs()
    log_file = PROCEDURE_LOG_DIR / "install.log"
    cmd = ["env", "LC_ALL=C", "DEBIAN_FRONTEND=noninteractive",
           "apt-get", "-y", "install", *packages]
    print(f"{MARK['arrow']} installing {', '.join(packages)} with apt "
          f"(output in {log_file})", flush=True)
    code, tail, attempts = run_step(cmd, APT_INSTALL_TIMEOUT, log_file)
    if code == 0:
        outcome = INSTALLED
    elif code is not None and is_lock_contention(tail):
        outcome = BLOCKED
    else:
        outcome = INSTALL_FAILED
    fields = {"fix_command": " ".join(cmd), "fix_exit_code": code,
              "fix_attempts": attempts, "step_log": str(log_file)}
    return outcome, fields, tail


def install_record(playbook_id: str, procedure_id, distro, fields: dict,
                   outcome: str) -> dict:
    return {"timestamp": now_utc(), "playbook_id": playbook_id,
            "procedure_id": procedure_id, "step_id": "install", "distro": distro,
            "confirmed": True, "snapshot_taken": False, "snapshot_id": None,
            **fields, "outcome": outcome}


def report_install_failure(packages, outcome: str, fields: dict, tail: str):
    if outcome == BLOCKED:
        print(f"{MARK['PROBLEM']} could not install {', '.join(packages)}: the "
              f"package manager stayed busy through {fields['fix_attempts']} "
              "attempts. It never ran. Try again in a few minutes.")
    else:
        code = fields["fix_exit_code"]
        how = ("timed out" if code is None else
               "exited 0, but it is still not installed" if code == 0 else
               f"exited {code}")
        print(f"{MARK['PROBLEM']} could not install {', '.join(packages)}: apt "
              f"{how}. Nothing else was run.")
        for line in [ln for ln in tail.strip().splitlines() if ln.strip()][-FOUND_SHOWN:]:
            print(f"    {line}")
    print(f"  {MARK['arrow']} apt's full output: {fields['step_log']}")


def engine_deps_missing():
    """The Debian packages for what the engine cannot import."""
    return [pkg for pkg, module in (("python3-yaml", yaml),
                                    ("python3-jsonschema", Draft7Validator))
            if module is None]


def load_engine_deps():
    """Import pyyaml and jsonschema again, after apt has installed them."""
    global yaml, Draft7Validator
    importlib.invalidate_caches()
    if yaml is None:
        try:
            yaml = importlib.import_module("yaml")
        except ImportError:
            pass
    if Draft7Validator is None:
        try:   # jsonschema before 3 has no Draft7Validator
            Draft7Validator = importlib.import_module("jsonschema").Draft7Validator
        except (ImportError, AttributeError):
            pass


def ensure_engine_deps(distro) -> bool:
    """pyyaml and jsonschema read and check every playbook and procedure. With
    apt and sudo, offer to install the missing ones; otherwise say how.

    Never pip on a machine with apt: the service runs /usr/bin/python3 after
    each upgrade, and a release upgrade moves it to a newer Python that does
    not see modules pip installed for the old one. apt's packages move with it."""
    missing = engine_deps_missing()
    if not missing:
        return True
    names = " and ".join(missing)
    if not apt_available():
        print("The engine needs pyyaml and jsonschema (3 or later), and they are "
              "not installed. Install them, for example:  pip install pyyaml jsonschema")
        return False
    it = "it" if len(missing) == 1 else "them"
    if not has_privilege():
        print(f"The engine needs {names}, which {'is' if it == 'it' else 'are'} "
              f"not installed. Run it with sudo and it offers to install {it}, "
              f"or install {it} yourself:  sudo apt-get install {' '.join(missing)}")
        return False
    print(f"The engine needs {names} to read and check its playbooks and "
          f"procedures, and {'it is' if it == 'it' else 'they are'} not installed.")
    ok, problems = check_apt()
    if not ok:
        report_apt_failure(problems)
        return False
    if not ask_yes(f"Install {' '.join(missing)} with apt now? [y/N] "):
        print("Nothing was installed.")
        return False
    outcome, fields, tail = install_packages(missing)
    log_run(install_record("engine/install", None, distro, fields, outcome),
            procedure_log_path())
    if outcome != INSTALLED:
        report_install_failure(missing, outcome, fields, tail)
        return False
    load_engine_deps()
    still = engine_deps_missing()
    if still:
        print(f"{MARK['PROBLEM']} apt installed {names}, but the engine still "
              f"cannot use {' and '.join(still)}.")
        return False
    print(f"  {MARK['HEALTHY']} installed {names}", flush=True)
    return True


# --- The engine's own snapshots (Timeshift, rsync mode) ----------------------
#
# The lab's machines are plain ext4, with no LVM or btrfs, so an instant
# filesystem snapshot is not available. Timeshift's rsync mode copies the
# system (not /home) into /timeshift on a disk, hard-linking what an earlier
# snapshot already holds, and its restore puts the files back, reinstalls GRUB
# and reboots. It can also restore from a live USB if the machine stops
# booting, which no snapshot of the engine's own making could offer.

def read_cmd(*args) -> str:
    """A read-only query. Returns its stripped stdout, or "" on any failure."""
    try:
        proc = subprocess.run(list(args), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def snapshot_devices():
    """(root partition, its UUID, the disk GRUB lives on), or None when any of
    them cannot be worked out — then the engine will not take a snapshot it
    could not restore."""
    root = read_cmd("findmnt", "-n", "-o", "SOURCE", "/")
    uuid = read_cmd("findmnt", "-n", "-o", "UUID", "/")
    disk = read_cmd("lsblk", "-n", "-d", "-o", "PKNAME", root) if root.startswith("/dev/") else ""
    if not (root.startswith("/dev/") and uuid and disk):
        return None
    return root, uuid, f"/dev/{disk.splitlines()[0].strip()}"


def machine_fingerprint() -> str:
    """The release and every installed package with its version. A restore has
    worked when this matches what it was when the snapshot was taken."""
    release = ""
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("VERSION_ID="):
                release = line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    packages = read_cmd("dpkg-query", "-W", "-f", "${Package} ${Version} ${Status}\n")
    digest = hashlib.sha256(packages.encode("utf-8")).hexdigest()[:16]
    return f"{release} packages:{digest}"


def prepare_timeshift_config(root_uuid: str):
    """Add the engine's excludes to Timeshift's config, creating a minimal
    rsync-mode config if there is none. Anything already there is kept."""
    if TIMESHIFT_CONF.exists():
        cfg = json.loads(TIMESHIFT_CONF.read_text(encoding="utf-8"))
        if str(cfg.get("btrfs_mode", "false")).lower() == "true":
            raise RuntimeError("Timeshift is set to btrfs mode; the engine only "
                               "uses rsync mode")
    else:
        cfg = {"backup_device_uuid": root_uuid, "parent_device_uuid": "",
               "do_first_run": "false", "btrfs_mode": "false",
               "include_btrfs_home_for_backup": "false",
               "include_btrfs_home_for_restore": "false",
               "stop_cron_emails": "true",
               "schedule_monthly": "false", "schedule_weekly": "false",
               "schedule_daily": "false", "schedule_hourly": "false",
               "schedule_boot": "false",
               "count_monthly": "2", "count_weekly": "3", "count_daily": "5",
               "count_hourly": "6", "count_boot": "5",
               "date_format": "%Y-%m-%d %H:%M:%S",
               "exclude": [], "exclude-apps": []}
    if not cfg.get("backup_device_uuid"):
        cfg["backup_device_uuid"] = root_uuid
    excludes = cfg.setdefault("exclude", [])
    for path in SNAPSHOT_EXCLUDES:
        if path not in excludes:
            excludes.append(path)
    TIMESHIFT_CONF.parent.mkdir(parents=True, exist_ok=True)
    TIMESHIFT_CONF.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def snapshot_create_cmd(snap: dict) -> list:
    # No --tags. Timeshift 22.06.5 refuses "--tags O" ("Unknown value"): its
    # check leaves O out, though its help lists it. --create tags a snapshot
    # O, on-demand, by itself.
    return ["timeshift", "--create", "--rsync", "--snapshot-device", snap["device"],
            "--comments", snap["comment"], "--scripted"]


def snapshot_restore_cmd(snap: dict) -> list:
    # --grub-device always: an upgrade reinstalls GRUB, and restoring the old
    # /boot/grub under a newer boot sector can leave the machine unbootable.
    # Timeshift 22.06 also waits for a typed answer without it or --skip-grub.
    return ["timeshift", "--restore", "--snapshot", snap["name"],
            "--target-device", snap["device"], "--grub-device", snap["grub_device"],
            "--scripted", "--yes"]


SNAPSHOT_NAME = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")


def take_snapshot(snap: dict):
    """Returns (name, detail). name is None when no snapshot was made."""
    try:
        prepare_timeshift_config(snap["uuid"])
    except (OSError, ValueError, RuntimeError) as e:
        return None, f"could not prepare Timeshift's config: {e}"
    log_file = PROCEDURE_LOG_DIR / "snapshot.log"
    code, tail = run_logged(snapshot_create_cmd(snap), SNAPSHOT_TIMEOUT, log_file)
    if code != 0:
        return None, (f"timeshift --create {'timed out' if code is None else f'exited {code}'}"
                      f"; see {log_file}")
    m = re.search(r"Tagged snapshot '(" + SNAPSHOT_NAME.pattern + ")'", tail)
    if m:
        return m.group(1), "created"
    # Fall back to the listing: our comment is unique to this run.
    for line in read_cmd("timeshift", "--list", "--scripted").splitlines():
        if snap["comment"] in line:
            m = SNAPSHOT_NAME.search(line)
            if m:
                return m.group(0), "created (found by its comment)"
    return None, f"timeshift exited 0 but the new snapshot was not found; see {log_file}"


# --- Keeping packages still while the system is copied -------------------------
#
# After a restore, the engine checks the machine against a fingerprint of its
# installed packages, taken just before the snapshot. If unattended-upgrades,
# the Software Updater or a person's apt changed packages in between, or during
# the restore, that check would fail a good restore, or the copy would hold a
# half-installed package. All of them take dpkg's frontend lock before they
# change anything, so the engine holds that lock around the fingerprint and the
# snapshot, and around the restore.

DPKG_FRONTEND_LOCK = Path("/var/lib/dpkg/lock-frontend")


def try_package_manager_lock():
    """One try at dpkg's frontend lock, taken the way apt takes it (fcntl, not
    flock). Returns the open descriptor that holds it, -1 on a machine with no
    dpkg, or None while another program holds it."""
    if not DPKG_FRONTEND_LOCK.parent.is_dir():
        return -1
    import fcntl   # Linux only, like the rest of the procedure path
    fd = os.open(DPKG_FRONTEND_LOCK, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def hold_package_manager(before: str):
    """Wait for dpkg's frontend lock and keep it. Returns what to hand to
    release_package_manager(), or None when the lock stayed taken through
    BLOCKED_RETRIES tries."""
    for attempt in range(1, BLOCKED_RETRIES + 1):
        fd = try_package_manager_lock()
        if fd is not None:
            return fd
        if attempt < BLOCKED_RETRIES:
            print(f"  {MARK['arrow']} the package manager is busy — waiting "
                  f"{BLOCKED_WAIT}s before {before} (attempt {attempt + 1} of "
                  f"{BLOCKED_RETRIES})", flush=True)
            time.sleep(BLOCKED_WAIT)
    return None


def release_package_manager(fd):
    if fd is not None and fd >= 0:
        os.close(fd)          # closing the descriptor drops the lock


def sync_disks():
    """Write out everything still waiting in memory (sync(2)). Timeshift does
    not, after a snapshot: for half a minute or so, part of the copy may exist
    only in memory, and a power cut then would leave a damaged snapshot for a
    restore to copy back."""
    if hasattr(os, "sync"):
        os.sync()


def step_record(state: dict, step: dict, cmd: str) -> dict:
    snap = state.get("engine_snapshot")
    return {
        "timestamp": now_utc(),
        "playbook_id": f"{state['procedure_id']}/{step['id']}",
        "procedure_id": state["procedure_id"],
        "step_id": step["id"],
        "distro": state["distro"],
        "detect_before": None,
        "confirmed": True,              # once, for the whole procedure
        "snapshot_taken": bool(snap and snap.get("name")),
        "snapshot_id": (snap["name"] if snap and snap.get("name")
                        else state["snapshot_id"]),
        "snapshot_named": state["snapshot_id"],   # the person's own, if any
        "fix_command": cmd,
        "fix_exit_code": None,
        "fix_attempts": None,
        "timeout_seconds": step["timeout_minutes"] * 60,
        "step_log": None,
        "rebooted": False,
        "settle_seconds": None,
        "verify_after": None,
        "verify_error": None,
        "blocked_reason": None,
        "failure": None,
        "outcome": None,
        "rollback_method": None,
        "rollback_result": None,
    }


def verify_step(step: dict, record: dict) -> str:
    status, measurement, detail = assess(step)
    if status == HEALTHY:
        record["verify_after"] = measurement
        print(f"  {MARK['HEALTHY']} {step['id']} verified ({detail})", flush=True)
        return HEALED
    if status == ERROR:
        record["verify_error"] = detail
        print(f"  {MARK['ERROR']} {step['id']} ran, but its check could not "
              f"measure the machine ({detail})", flush=True)
        return VERIFY_ERROR
    record["verify_after"] = measurement
    print(f"  {MARK['PROBLEM']} {step['id']} ran, but its goal is not reached "
          f"({detail})", flush=True)
    return VERIFY_FAILED


def stop_procedure(state: dict, phase: str, reason: str):
    release_reserve()
    state["phase"] = phase
    state["stopped_because"] = reason
    state["finished"] = now_utc()
    write_state(state)
    remove_service()
    print(f"{MARK['PROBLEM']} procedure {state['procedure_id']} stopped: {reason}",
          flush=True)
    snap = state.get("engine_snapshot")
    if snap and snap.get("name") and phase == FAILED:
        print(f"  {MARK['arrow']} the engine's snapshot is Timeshift {snap['name']}",
              flush=True)
    if state.get("snapshot_id"):
        print(f"  {MARK['arrow']} the snapshot named at the start: "
              f"{state['snapshot_id']}", flush=True)


def fail_step(state: dict, record: dict, failure: str, reason: str, changed=True):
    """A step did not succeed. Undo it with the engine's snapshot when there is
    one and the step may have changed something; otherwise stop."""
    # Before anything is written: the step may have failed because the disk
    # is full, and then the state written below could not be.
    if release_reserve():
        print(f"  {MARK['arrow']} freed the engine's {RESERVE_BYTES >> 20} MB "
              "reserve, so there is room to record this and restore", flush=True)
    record["failure"] = failure
    snap = state.get("engine_snapshot")
    if changed and snap and snap.get("name"):
        begin_restore(state, record, f"step {record['step_id']}: {reason}")
        return
    record["outcome"] = failure
    if state["reverse_strategy"] == "snapshot_only" and changed:
        record["rollback_method"] = "snapshot_only"
        record["rollback_result"] = ("not attempted: the engine cannot restore "
                                     f"snapshot {state['snapshot_id']!r}")
    log_run(record, procedure_log_path())
    stop_procedure(state, FAILED, f"step {record['step_id']}: {reason}")


def begin_restore(state: dict, record: dict, reason: str):
    """Restore the engine's snapshot. Timeshift reboots when it finishes; the
    boot after verifies (resume_procedure, phase RESTORING). The step's record
    is written then, with the restore's result as its outcome.

    dpkg's lock comes first, and only then does the phase say RESTORING. A
    machine that goes down while the engine waits for the lock has restored
    nothing, and the boot after must not check a restore that never ran."""
    snap = state["engine_snapshot"]
    record["rollback_method"] = "timeshift"
    cmd = snapshot_restore_cmd(snap)
    print(f"{MARK['PROBLEM']} {reason}", flush=True)
    lock = hold_package_manager("the restore")
    if lock is None:
        record["outcome"] = ROLLBACK_FAILED
        record["rollback_result"] = (f"not started: the package manager stayed busy "
                                     f"through {BLOCKED_RETRIES} attempts. Timeshift "
                                     f"snapshot {snap['name']} is untouched")
        log_run(record, procedure_log_path())
        stop_procedure(state, FAILED, f"{reason}; the restore could not start, because "
                       "the package manager stayed busy. Timeshift snapshot "
                       f"{snap['name']} is untouched: restore it with Timeshift by hand.")
        return
    state.update(phase=RESTORING, record=record, stopped_because=reason,
                 restore_started=now_utc())
    write_state(state)
    print(f"  {MARK['arrow']} restoring Timeshift snapshot {snap['name']}, then "
          "rebooting", flush=True)
    log_file = PROCEDURE_LOG_DIR / "restore.log"
    code, _tail = run_logged(cmd, RESTORE_TIMEOUT, log_file)
    # Still here: Timeshift did not reboot. Exit 0 is not proof — it also exits
    # 0 when it gives up on input — so a clean exit still gets the reboot and
    # the check after it. A failed exit may have left a half-copied system;
    # say so and stop rather than reboot into it.
    if code == 0:
        reboot_machine()      # dpkg's lock goes with this process
        return
    release_package_manager(lock)
    record["outcome"] = ROLLBACK_FAILED
    record["rollback_result"] = (f"timeshift --restore "
                                 f"{'timed out' if code is None else f'exited {code}'}"
                                 f"; see {log_file}")
    log_run(record, procedure_log_path())
    stop_procedure(state, FAILED, f"{reason}; the restore failed too — the "
                   "machine needs a person (Timeshift can restore from a live USB)")


def finish_restore(state: dict) -> int:
    """First boot after a restore: is the machine really back?"""
    record = state["record"]
    snap = state["engine_snapshot"]
    now = machine_fingerprint()
    if now == snap["fingerprint"]:
        record["outcome"] = ROLLED_BACK
        record["rollback_result"] = f"ok: back to {now}"
        log_run(record, procedure_log_path())
        print(f"{MARK['HEALTHY']} restored: the machine is back as it was before "
              f"step 1 ({now})", flush=True)
        stop_procedure(state, FAILED, f"{state['stopped_because']}. Restored "
                       f"Timeshift snapshot {snap['name']}.")
        return 1
    record["outcome"] = ROLLBACK_FAILED
    record["rollback_result"] = (f"after the restore the machine reads {now}, "
                                 f"not {snap['fingerprint']}")
    log_run(record, procedure_log_path())
    stop_procedure(state, FAILED, f"{state['stopped_because']}. The restore of "
                   f"{snap['name']} did not bring the machine back (it reads "
                   f"{now}). It needs a person.")
    return 1


def hold_cut_off(state: dict, record: dict) -> int:
    """A boot found a step still marked running: the machine went down
    mid-step. The engine's snapshot could undo it now, but this boot may be one
    nobody is watching, such as the next person to switch the machine on. An
    undo is a long restore that ends in a forced reboot, and one cut off in
    turn can leave a machine that does not start. So the engine notes the
    cut-off, removes its service and waits. The next --run offers the restore
    (offer_undo); --cancel leaves the machine as it is. The step's record is
    written when one of them decides its outcome."""
    release_reserve()        # the restore, if the person asks for one, needs room
    record["failure"] = INTERRUPTED
    state.update(phase=CUT_OFF, record=record, cut_off_seen=now_utc(),
                 stopped_because=(f"step {record['step_id']}: the machine went "
                                  "down while it was running, so its state is "
                                  "unknown"))
    write_state(state)
    remove_service()
    print(f"{MARK['PROBLEM']} step {record['step_id']} was cut off: the machine "
          "went down while it was running. Nothing was undone; the engine waits "
          "for a person.", flush=True)
    print(f"  {MARK['arrow']} to restore Timeshift snapshot "
          f"{state['engine_snapshot']['name']}: run the same --run command "
          "again with sudo (it asks first)", flush=True)
    print(f"  {MARK['arrow']} to leave the machine as it is: sudo python3 "
          "engine/runner.py --cancel", flush=True)
    return 1


def offer_undo(state: dict) -> int:
    """--run on a machine whose last step was cut off: offer the restore that
    hold_cut_off held back. The restore runs under the service, like every
    other: a terminal closed halfway through a restore must not stop it."""
    record = state.get("record") or {}
    snap = state.get("engine_snapshot") or {}
    if state["phase"] == RESTORE_ASKED and service_active():
        print(f"{state['procedure_id']}: the restore you asked for is running. "
              "Keep the machine on; it reboots by itself when it is done. "
              "See --status.")
        return 6
    print(f"{MARK['PROBLEM']} {state['procedure_id']}: step {record.get('step_id')} "
          "was cut off: the machine went down while it was running. Its state "
          "is unknown, and nothing has been undone.")
    if not snap.get("name"):
        print("There is no snapshot of the engine's to restore. Leave the machine "
              "as it is with --cancel, and check it by hand.")
        return 1
    if not systemd_running():
        print("Restoring needs systemd, to check the machine after the reboot, "
              "and this machine is not running it. Refusing.")
        return 7
    if not timeshift_installed():
        print("Restoring needs Timeshift, and it is no longer installed. Refusing.")
        return 8
    print(f"  {MARK['arrow']} the engine can restore Timeshift snapshot "
          f"{snap['name']}, taken just before the step, then reboot and check "
          f"the machine is back as it was ({snap['fingerprint']}).")
    print(f"  {MARK['arrow']} that takes a while, and the machine must stay on "
          "until it has rebooted.")
    if not ask_yes("Restore it now? [y/N] "):
        print("Nothing was done. Run this again to restore it, or leave the "
              "machine as it is with:  sudo python3 engine/runner.py --cancel")
        return 0
    record["rollback_requested"] = now_utc()
    state.update(phase=RESTORE_ASKED, record=record)
    write_state(state)
    install_service(state["procedure_id"])
    print(f"\n  {MARK['arrow']} restoring under the system service "
          f"{PROCEDURE_UNIT}. The machine reboots by itself when it is done; "
          "keep it on until then.")
    print(f"  {MARK['arrow']} watch it:   journalctl -fu {PROCEDURE_UNIT}")
    print(f"  {MARK['arrow']} where it is: python3 engine/runner.py --status")
    return 0


def resume_procedure() -> int:
    """The service's entry point: carry on from wherever the state says."""
    state = read_state()
    if state is None or state["phase"] in FINISHED:
        print("No procedure in progress. Removing the service.", flush=True)
        release_reserve()
        remove_service()
        return 0
    proc = json.loads((PROCEDURE_STATE_DIR / "procedure.json").read_text(encoding="utf-8"))
    steps = proc["steps"]

    if state["phase"] == RESTORING:
        return finish_restore(state)
    if state["phase"] == RESTORE_ASKED:
        # A person asked for the restore that a cut-off held back (offer_undo).
        begin_restore(state, state["record"], state["stopped_because"])
        return 1
    if state["phase"] == CUT_OFF:
        # Waiting for a person. The service removed itself when it held the
        # cut-off; if it runs anyway, it must not act.
        print("A step was cut off and the engine is waiting for a person; doing "
              "nothing. See --status.", flush=True)
        remove_service()
        return 1

    snap = state.get("engine_snapshot")
    if snap and not snap.get("name"):
        if state["phase"] == SNAPSHOTTING:
            stop_procedure(state, FAILED, "the machine went down while the "
                           "snapshot was being taken. No step had run.")
            return 1
        state["phase"] = SNAPSHOTTING
        write_state(state)
        lock = hold_package_manager("the snapshot")
        if lock is None:
            log_run({"timestamp": now_utc(),
                     "playbook_id": f"{state['procedure_id']}/snapshot",
                     "procedure_id": state["procedure_id"], "step_id": "snapshot",
                     "distro": state["distro"], "confirmed": True,
                     "snapshot_taken": False, "snapshot_id": None,
                     "blocked_reason": (f"the package manager stayed busy through "
                                        f"{BLOCKED_RETRIES} attempts"),
                     "outcome": BLOCKED},
                    procedure_log_path())
            stop_procedure(state, FAILED, "the package manager stayed busy, so no "
                           "snapshot was taken and no step was run. Start the "
                           "procedure again later.")
            return 1
        try:
            snap["fingerprint"] = machine_fingerprint()
            print(f"{MARK['arrow']} taking a Timeshift snapshot of {snap['device']} "
                  f"before step 1 (machine: {snap['fingerprint']})", flush=True)
            name, detail = take_snapshot(snap)
            if name is not None:
                # Until this returns, the phase still says SNAPSHOTTING: a power
                # cut now stops the procedure instead of trusting the copy.
                print(f"  {MARK['arrow']} writing the snapshot to disk", flush=True)
                sync_disks()
        finally:
            release_package_manager(lock)
        if name is None:
            log_run({"timestamp": now_utc(),
                     "playbook_id": f"{state['procedure_id']}/snapshot",
                     "procedure_id": state["procedure_id"], "step_id": "snapshot",
                     "distro": state["distro"], "confirmed": True,
                     "snapshot_taken": False, "snapshot_id": None,
                     "fix_command": " ".join(snapshot_create_cmd(snap)),
                     "outcome": SNAPSHOT_FAILED, "verify_error": detail},
                    procedure_log_path())
            stop_procedure(state, FAILED, f"no snapshot, so no step was run: {detail}")
            return 1
        snap["name"] = name
        state["phase"] = PENDING
        write_state(state)
        print(f"  {MARK['HEALTHY']} snapshot {name} {detail}", flush=True)

    while state["step"] < len(steps):
        step = steps[state["step"]]
        record = state.get("record")

        if state["phase"] == RUNNING:
            if snap and snap.get("name"):
                return hold_cut_off(state, record)
            fail_step(state, record, INTERRUPTED,
                      "the machine went down while it was running, so its "
                      "state is unknown")
            return 1

        if state["phase"] == REBOOTING:
            print(f"{MARK['arrow']} back after rebooting for {step['id']} — "
                  "verifying", flush=True)
            outcome = verify_step(step, record)
            if outcome != HEALED:
                fail_step(state, record, outcome, "its check did not pass after the reboot")
                return 1
            record["outcome"] = HEALED
            log_run(record, procedure_log_path())
            state["history"].append({"step": step["id"], "outcome": HEALED})
            state.update(step=state["step"] + 1, phase=PENDING, record=None)
            write_state(state)
            continue

        # PENDING: ask the machine whether this step is still needed.
        status, measurement, detail = assess(step)
        if status == ERROR:
            stop_procedure(state, FAILED,
                           f"step {step['id']}: its check failed ({detail}). "
                           "Nothing was run.")
            return 1
        if status == HEALTHY:
            print(f"{MARK['HEALTHY']} {step['id']} already done ({detail})", flush=True)
            state["history"].append({"step": step["id"], "outcome": "already done"})
            state.update(step=state["step"] + 1, phase=PENDING, record=None)
            write_state(state)
            continue

        if "requires" in step:
            r_status, _r_measure, r_detail = assess(step["requires"])
            if r_status != HEALTHY:
                stop_procedure(state, FAILED, f"step {step['id']}: its "
                               f"precondition does not hold ({r_detail}). "
                               "Nothing was run.")
                return 1

        cmd = pick_step_command(step, state["distro"])
        record = step_record(state, step, cmd)
        record["detect_before"] = measurement
        log_file = PROCEDURE_LOG_DIR / f"{state['procedure_id']}-{step['id']}.log"
        record["step_log"] = str(log_file)
        # Marked BEFORE the command starts, so a boot that finds this mark
        # knows the step was cut off rather than never begun.
        state.update(phase=RUNNING, record=record)
        write_state(state)

        print(f"{MARK['arrow']} {step['id']}: {step['description']} — running "
              f"(limit {step['timeout_minutes']} min, output in {log_file})",
              flush=True)
        code, tail, attempts = run_step(cmd, step["timeout_minutes"] * 60, log_file)
        record["fix_exit_code"] = code
        record["fix_attempts"] = attempts

        if code is None:
            fail_step(state, record, FIX_FAILED,
                      f"stopped at its time limit of {step['timeout_minutes']} min")
            return 1
        if code != 0 and is_lock_contention(tail):
            record["blocked_reason"] = (tail.strip().splitlines()[-1]
                                        if tail.strip() else f"exit={code}")
            fail_step(state, record, BLOCKED,
                      f"the package manager stayed busy through {attempts} "
                      "attempts. It never ran, so this step changed nothing; "
                      "start the procedure again later", changed=False)
            return 1
        if code != 0:
            fail_step(state, record, FIX_FAILED,
                      f"its command failed (exit {code}); see {log_file}")
            return 1

        if step["reboot_after"]:
            record["rebooted"] = True
            state.update(phase=REBOOTING, record=record)
            write_state(state)
            print(f"{MARK['arrow']} {step['id']} exited 0 — rebooting, then "
                  "verifying", flush=True)
            reboot_machine()
            return 0

        outcome = verify_step(step, record)
        if outcome != HEALED:
            fail_step(state, record, outcome, "its check did not pass")
            return 1
        record["outcome"] = HEALED
        log_run(record, procedure_log_path())
        state["history"].append({"step": step["id"], "outcome": HEALED})
        state.update(step=state["step"] + 1, phase=PENDING, record=None)
        write_state(state)

    release_reserve()
    state.update(phase=DONE, record=None, finished=now_utc())
    write_state(state)
    remove_service()
    print(f"{MARK['HEALTHY']} procedure {state['procedure_id']} finished: every "
          "step verified.", flush=True)
    snap = state.get("engine_snapshot")
    if snap and snap.get("name"):
        print(f"  {MARK['arrow']} Timeshift snapshot {snap['name']} is kept. "
              f"Delete it when you no longer need it: timeshift --delete "
              f"--snapshot {snap['name']}", flush=True)
    return 0


def confirm_procedure(proc: dict, plan: list, distro: str, snapshot: str,
                      engine_snap, install=()) -> bool:
    to_do = [s for s, done in plan if not done]
    reboots = sum(1 for s in to_do if s["reboot_after"])
    print()
    print("=" * 64)
    print(f"  {proc['id']}")
    print(f"  {proc['description']}")
    print("-" * 64)
    for n, (step, done) in enumerate(plan, 1):
        state = "already done" if done else (
            f"to do — limit {step['timeout_minutes']} min"
            + (", then reboot" if step["reboot_after"] else ""))
        print(f"  {n}. {step['id']}: {step['description']}")
        print(f"     {state}")
        if not done:
            print(textwrap.indent(pick_step_command(step, distro), "       | "))
    print("-" * 64)
    print(f"  risk      : {proc['risk']}")
    print(f"  undo      : {proc['reverse']['strategy']}")
    if install:
        print(f"  install   : {', '.join(install)}, with apt, before anything else")
    if engine_snap:
        print(f"  snapshot  : the engine takes one with Timeshift before step 1,")
        print(f"              of {engine_snap['device']} (not /home, not /boot/efi).")
        print("              If a step fails, it restores that snapshot and")
        print("              reboots by itself, then checks the machine is back.")
        print("              If the machine goes down mid-step, it waits for")
        print("              you: run this command again to restore.")
    if snapshot:
        print(f"  named     : {snapshot} (yours — the engine can neither take "
              "nor restore it)")
    print(textwrap.fill(proc.get("source", "unrecorded"), width=64,
                        initial_indent="  source    : ",
                        subsequent_indent="              "))
    print("=" * 64)
    first = f"it installs {', '.join(install)}, then " if install else "it "
    print(f"Once you answer y, {first}runs by itself: {len(to_do)} step(s), "
          f"{reboots} reboot(s). It carries on after each reboot and removes "
          "itself when it finishes or stops. Keep the machine on until then.")
    return ask_yes("Run this procedure? [y/N] ")


def start_procedure(proc: dict, proc_path: Path, distro: str, host_os: str,
                    snapshot: str, take_snapshot_too: bool, log_path: Path) -> int:
    """--run: check, confirm, then hand off to the service. Returns an exit code."""
    ok, why = applies(proc, host_os, distro)
    if not ok:
        print(f"{proc['id']} does not apply to this machine ({why}). Refusing to run.")
        return 5
    for step in proc["steps"]:
        if not pick_step_command(step, distro):
            print(f"{proc['id']}: step {step['id']} has no command for distro {distro!r}.")
            return 2
    if not has_privilege():
        print(f"{proc['id']} requires administrative privilege and this engine "
              "is not running with it.")
        print("Re-run under sudo. The engine will not add sudo to a vetted "
              "command on your behalf.")
        return 3
    state = read_state()
    if state is not None and state["phase"] in (CUT_OFF, RESTORE_ASKED):
        # The last procedure here was cut off, and the boot after held its
        # restore back for a person (hold_cut_off). This run is that person.
        return offer_undo(state)
    if needs_snapshot(proc) and not (snapshot or take_snapshot_too):
        print(f"{proc['id']} cannot be undone (risk={proc['risk']}, "
              f"undo={proc['reverse']['strategy']}). Refusing to run without a "
              "snapshot. Either let the engine take one (--take-snapshot), or "
              "take one yourself and name it (--snapshot <name>).")
        return 4
    engine_snap = None
    install = []
    if take_snapshot_too:
        if not timeshift_installed():
            if not apt_available():
                print("--take-snapshot uses Timeshift, which is not installed, "
                      "and this machine has no apt to install it with. Install "
                      "Timeshift first.")
                return 8
            install.append("timeshift")   # after the y, before anything else
        devices = snapshot_devices()
        if devices is None:
            print("--take-snapshot could not work out the root partition and "
                  "the disk GRUB is on, so it could not restore a snapshot "
                  "safely. Refusing to run.")
            return 8
        root, uuid, grub = devices
        engine_snap = {"tool": "timeshift", "device": root, "uuid": uuid,
                       "grub_device": grub, "name": None, "fingerprint": None,
                       "comment": f"sf3000 {proc['id']} {now_utc()}"}
    if not systemd_running():
        print(f"{proc['id']} needs systemd to carry on after a reboot, and this "
              "machine is not running it. Refusing to run.")
        return 7
    if state is not None and state["phase"] not in FINISHED:
        print(f"Procedure {state['procedure_id']} is already in progress "
              f"(phase {state['phase']}). See --status; --cancel stops it "
              "between steps.")
        return 6

    # The plan: ask the machine which steps are already done. Read-only.
    plan = []
    for step in proc["steps"]:
        status, _measurement, detail = assess(step)
        if status == ERROR:
            print(f"{MARK['ERROR']} {proc['id']}: the check for step {step['id']} "
                  f"failed ({detail}). Refusing to run.")
            return 1
        plan.append((step, status == HEALTHY))
    first = next((s for s, done in plan if not done), None)
    if first is None:
        print(f"{MARK['HEALTHY']} {proc['id']}: every step is already done. "
              "Nothing to do.")
        return 0
    if "requires" in first:
        r_status, _r_measure, r_detail = assess(first["requires"])
        if r_status != HEALTHY:
            print(f"{proc['id']}: the first step to do, {first['id']}, does not "
                  f"apply to this machine ({r_detail}). Refusing to run.")
            return 5

    if apt_available():
        ok, problems = check_apt()
        if not ok:
            report_apt_failure(problems)
            return 9

    if not confirm_procedure(proc, plan, distro, snapshot, engine_snap, install):
        log_run({
            "timestamp": now_utc(),
            "playbook_id": proc["id"],
            "procedure_id": proc["id"],
            "distro": distro,
            "confirmed": False,
            "snapshot_id": snapshot,
            "outcome": DECLINED,
        }, log_path)
        print(f"Cancelled. Nothing was run. (logged to {log_path})")
        return 0

    if install:
        outcome, fields, tail = install_packages(install)
        if outcome == INSTALLED and not timeshift_installed():
            outcome = INSTALL_FAILED
            fields["verify_error"] = "apt exited 0, but timeshift is still not found"
        log_run(install_record(f"{proc['id']}/install", proc["id"], distro,
                               fields, outcome), procedure_log_path())
        if outcome != INSTALLED:
            report_install_failure(install, outcome, fields, tail)
            return 8
        print(f"  {MARK['HEALTHY']} installed {', '.join(install)}", flush=True)

    stage_procedure(proc, proc_path)
    try:
        make_reserve()
    except OSError as e:
        release_reserve()
        print(f"{proc['id']}: could not set aside {RESERVE_BYTES >> 20} MB in "
              f"{PROCEDURE_STATE_DIR} ({e.strerror or e}). The engine keeps that "
              "much free while a procedure runs, so that it can still restore "
              "if the disk fills up. Free some space and run this again. No "
              "step was run.")
        return 10
    write_state({
        "procedure_id": proc["id"],
        "description": proc["description"],
        "distro": distro,
        "snapshot_id": snapshot,
        "engine_snapshot": engine_snap,
        "reverse_strategy": proc["reverse"]["strategy"],
        "started": now_utc(),
        "step": 0,
        "phase": PENDING,
        "record": None,
        "history": [],
    })
    install_service(proc["id"])
    print(f"\n  {MARK['arrow']} started. It now runs as the system service "
          f"{PROCEDURE_UNIT}, so closing this terminal does not stop it.")
    print(f"  {MARK['arrow']} keep the machine on until --status says it has "
          "finished or stopped")
    print(f"  {MARK['arrow']} watch it:   journalctl -fu {PROCEDURE_UNIT}")
    print(f"  {MARK['arrow']} where it is: python3 engine/runner.py --status")
    print(f"  {MARK['arrow']} records go to {procedure_log_path()}")
    return 0


def procedures_that_apply(procedures, distro: str, host_os: str):
    """The ids of the procedures whose next step can run on this machine now.
    Read-only: their checks only. For after --run named one that does not
    apply, such as the upgrade after the one this machine needs."""
    ids = []
    for proc, _path in procedures:
        if not applies(proc, host_os, distro)[0]:
            continue
        for step in proc["steps"]:
            status = assess(step)[0]
            if status == HEALTHY:
                continue
            if (status == PROBLEM and "requires" in step
                    and assess(step["requires"])[0] == HEALTHY):
                ids.append(proc["id"])
            break
    return ids


def procedure_status() -> int:
    state = read_state()
    if state is None:
        print("No procedure has been started on this machine.")
        return 0
    print(f"procedure : {state['procedure_id']}")
    print(f"phase     : {state['phase']}")
    print(f"started   : {state['started']} (UTC)")
    snap = state.get("engine_snapshot")
    if snap:
        print(f"snapshot  : Timeshift {snap.get('name') or '(not taken yet)'} "
              f"of {snap['device']}")
    if state.get("snapshot_id"):
        print(f"named     : {state['snapshot_id']}")
    for h in state["history"]:
        print(f"  {MARK['HEALTHY']} {h['step']}: {h['outcome']}")
    if state["phase"] not in FINISHED + (RESTORING, RESTORE_ASKED):
        proc = json.loads((PROCEDURE_STATE_DIR / "procedure.json").read_text(encoding="utf-8"))
        if state["step"] < len(proc["steps"]):
            print(f"  {MARK['arrow']} now: {proc['steps'][state['step']]['id']} "
                  f"({state['phase']})")
    if state.get("stopped_because"):
        print(f"stopped   : {state['stopped_because']}")
    if state["phase"] == CUT_OFF:
        print(f"  {MARK['arrow']} nothing was undone: the engine waits for you")
        print(f"  {MARK['arrow']} to restore the snapshot: run the same --run "
              "command again with sudo (it asks first)")
        print(f"  {MARK['arrow']} to leave the machine as it is: "
              "sudo python3 engine/runner.py --cancel")
    elif state["phase"] in (RESTORE_ASKED, RESTORING):
        print(f"  {MARK['arrow']} restoring Timeshift snapshot "
              f"{(snap or {}).get('name')}: keep the machine on; it reboots by "
              "itself when it is done")
    print(f"records   : {procedure_log_path()}")
    return 0


def cancel_procedure() -> int:
    if not has_privilege():
        print("--cancel requires administrative privilege. Re-run under sudo.")
        return 3
    state = read_state()
    if state is None or state["phase"] in FINISHED:
        print("No procedure in progress.")
        release_reserve()
        remove_service()
        return 0
    if service_active():
        print(f"{state['procedure_id']} is working right now (phase "
              f"{state['phase']}). Stopping it halfway could leave the machine "
              "broken, so --cancel refuses. Run it again once it is between steps.")
        return 6
    if state["phase"] in (RUNNING, RESTORING):
        print(f"{state['procedure_id']} was cut off in phase {state['phase']}; "
              "the next boot will deal with it. --cancel refuses.")
        return 6
    if state["phase"] in (CUT_OFF, RESTORE_ASKED):
        # A cut-off step the person chooses not to restore: the machine stays
        # as the cut-off left it, and the record says so.
        record = state.get("record") or {}
        name = (state.get("engine_snapshot") or {}).get("name")
        record["outcome"] = INTERRUPTED
        record["rollback_result"] = (f"not attempted: cancelled by hand; Timeshift "
                                     f"snapshot {name} kept")
        log_run(record, procedure_log_path())
        stop_procedure(state, CANCELLED, f"step {record.get('step_id')} was cut "
                       "off, and the machine was left as it is (cancelled by hand)")
        print(f"  {MARK['arrow']} Timeshift snapshot {name} is kept. Timeshift "
              "can still restore it by hand.")
        return 0
    stop_procedure(state, CANCELLED, "cancelled by hand between steps")
    return 0


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
    ap.add_argument("--procedures", default=str(ROOT / "procedures"),
                    help="directory of procedures (default: procedures/)")
    ap.add_argument("--run", metavar="ID",
                    help="start a multi-step procedure (asks first; carries on "
                         "by itself across reboots)")
    ap.add_argument("--take-snapshot", action="store_true",
                    help="with --run: take a Timeshift snapshot before step 1 "
                         "(installing Timeshift with apt if it is missing), and "
                         "restore it by itself if a step fails")
    ap.add_argument("--snapshot", metavar="NAME",
                    help="with --run: the snapshot or backup you took yourself")
    ap.add_argument("--status", action="store_true",
                    help="show the procedure on this machine, if any")
    ap.add_argument("--cancel", action="store_true",
                    help="stop a procedure between steps, or leave a cut-off "
                         "step as it is instead of restoring it")
    ap.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    # These three read and write only JSON, so they work even when an upgrade
    # has just replaced or removed pyyaml and jsonschema.
    if args.resume:
        # The outcome is in the records and the state file. Exit 0 either way:
        # a non-zero exit would leave a "failed" unit behind after the service
        # has removed itself.
        try:
            resume_procedure()
        except Exception as e:
            # Never leave a broken service to retry at every boot. First free
            # the reserve: what failed may have been a write to a full disk.
            release_reserve()
            print(f"{MARK['ERROR']} the engine itself failed: {e!r}", flush=True)
            traceback.print_exc()
            try:
                state = read_state()
                if state is not None and state.get("phase") not in FINISHED:
                    stop_procedure(state, FAILED, f"the engine itself failed ({e!r}); "
                                   "the machine needs a person")
                else:
                    remove_service()
            except Exception:
                remove_service()
        return 0
    if args.status:
        return procedure_status()
    if args.cancel:
        return cancel_procedure()

    if not ensure_engine_deps(args.distro):
        return 1

    validator = Draft7Validator(load_schema())
    playbooks, errors = load_playbooks(Path(args.playbooks), validator)
    procedures, proc_errors = load_procedures(
        Path(args.procedures), Draft7Validator(load_schema(PROCEDURE_SCHEMA_PATH)))

    if errors:
        print("SCHEMA ERRORS — these playbooks were rejected:")
        for e in errors:
            print(f"  {MARK['PROBLEM']} {e}")
        print()
    if proc_errors:
        print("SCHEMA ERRORS — these procedures were rejected:")
        for e in proc_errors:
            print(f"  {MARK['PROBLEM']} {e}")
        print()

    print(f"Loaded {len(playbooks)} valid playbook(s).")
    if procedures or proc_errors:
        print(f"Loaded {len(procedures)} valid procedure(s).")
    if args.validate_only:
        return 1 if (errors or proc_errors) else 0

    if (args.take_snapshot or args.snapshot) and not args.run:
        print("--take-snapshot and --snapshot go with --run.")
        return 2
    if args.run:
        found = next(((p, path) for p, path in procedures if p["id"] == args.run), None)
        if found is None:
            print(f"No valid procedure with id {args.run!r} in {args.procedures}.")
            return 2
        proc, path = found
        rc = start_procedure(proc, path, args.distro, args.host_os,
                             args.snapshot, args.take_snapshot, Path(args.log))
        if rc == 5:
            fits = [i for i in procedures_that_apply(procedures, args.distro,
                                                     args.host_os) if i != proc["id"]]
            if fits:
                print(f"  {MARK['arrow']} on this machine, this applies now: "
                      f"{', '.join(fits)}")
        return rc

    if args.fix:
        chosen = next((p for p in playbooks if p["id"] == args.fix), None)
        if chosen is None:
            print(f"No valid playbook with id {args.fix!r}.")
            return 2
        return fix_one(chosen, args.distro, Path(args.log), args.host_os)

    problems = fixable = skipped = 0
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
            if fix is None:
                # Printing "DRY-RUN fix: None" here read as a fix that exists
                # and does nothing. Say what is actually true instead.
                why = ("detect-only — it has no fix by design; a person needs "
                       "to look at this" if "fix" not in pb
                       else f"no fix command for distro {args.distro!r}")
                print(f"            {MARK['arrow']} {why}")
                continue
            fixable += 1
            print(f"            {MARK['arrow']} risk={pb['risk']} "
                  f"undo={pb['reverse']['strategy']} "
                  f"privilege={pb['requires_privilege']}")
            print(f"            {MARK['arrow']} DRY-RUN fix ({args.distro}): {fix}")
    print("-" * 60)
    summary = f"{problems} problem(s) found."
    if skipped:
        summary += f" {skipped} playbook(s) skipped — not for this machine."
    print(f"{summary} (No fixes were executed.)")
    if fixable:
        print("Run one for real with:  --fix <id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
