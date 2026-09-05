#!/usr/bin/env python3
"""
SF 3000 — minimal engine (single-machine, detect-only vertical slice).

What it does today:
  * loads playbooks from a directory
  * validates each against schema/playbook.schema.json
  * runs the READ-ONLY detect command
  * evaluates the expect predicate  -> HEALTHY / PROBLEM / ERROR
  * for a PROBLEM, prints the vetted fix as a DRY-RUN (never executes it)

What it deliberately does NOT do yet:
  * run any fix (that belongs to the safety layer: confirm, snapshot, log)
  * match free-text complaints to playbooks (that's the LLM matcher layer)
Both plug into this same contract later.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
    from jsonschema import Draft7Validator
except ImportError:
    sys.exit("Missing deps. Run: pip install pyyaml jsonschema --break-system-packages")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "playbook.schema.json"

HEALTHY, PROBLEM, ERROR = "HEALTHY", "PROBLEM", "ERROR"


def load_schema():
    return json.loads(SCHEMA_PATH.read_text())


def load_playbooks(pb_dir: Path, validator: Draft7Validator):
    """Load + validate every .yaml in pb_dir. A playbook that fails schema
    validation is rejected outright — the library is the trust anchor."""
    playbooks, errors = [], []
    for path in sorted(pb_dir.glob("*.yaml")):
        pb = yaml.safe_load(path.read_text())
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
                              text=True, timeout=30)
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


def diagnose(pb: dict):
    measurement, proc, err = run_detect(pb)
    if err:
        return ERROR, err
    healthy = evaluate(pb, measurement, proc)
    detail = f"measured={measurement!r}"
    return (HEALTHY if healthy else PROBLEM), detail


def pick_fix(pb: dict, distro: str):
    fix = pb.get("fix", {})
    return fix.get(distro) or fix.get("default")


def main():
    ap = argparse.ArgumentParser(description="SF 3000 engine (detect-only slice)")
    ap.add_argument("--playbooks", default=str(ROOT / "playbooks"))
    ap.add_argument("--distro", default="ubuntu", help="target distro for fix selection")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()

    validator = Draft7Validator(load_schema())
    playbooks, errors = load_playbooks(Path(args.playbooks), validator)

    if errors:
        print("SCHEMA ERRORS — these playbooks were rejected:")
        for e in errors:
            print(f"  ✗ {e}")
        print()

    print(f"Loaded {len(playbooks)} valid playbook(s).")
    if args.validate_only:
        return 1 if errors else 0

    problems = 0
    print("\nRunning checks:\n" + "-" * 60)
    for pb in playbooks:
        status, detail = diagnose(pb)
        mark = {"HEALTHY": "✓", "PROBLEM": "✗", "ERROR": "!"}[status]
        print(f"{mark} [{status:7}] {pb['id']:22} {detail}")
        if status == PROBLEM:
            problems += 1
            fix = pick_fix(pb, args.distro)
            print(f"            → {pb['description']}")
            print(f"            → risk={pb['risk']} undo={pb['reverse']['strategy']} "
                  f"privilege={pb['requires_privilege']}")
            print(f"            → DRY-RUN fix ({args.distro}): {fix}")
    print("-" * 60)
    print(f"{problems} problem(s) found. (No fixes were executed.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
