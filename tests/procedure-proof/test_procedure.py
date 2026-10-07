#!/usr/bin/env python3
"""
Offline proof of the procedure lifecycle. Run:
    python tests/procedure-proof/test_procedure.py

No VM: the machine is a dict, a reboot is the harness calling --resume again,
and every shell command, systemctl call and Timeshift run is stubbed. What it
proves is BRANCH LOGIC — which outcome each situation produces, what the
state file says at each boot, and above all what does NOT run. The real
upgrade procedure (candidates/procedures/) and the VM fixture
(procedure-proof.yaml) are loaded from disk, so their step checks are tested
with the numbers actually in them. README.md has the VM run, which proves the
same machinery on a real machine.

No pytest, like the other offline proofs. Exits non-zero if any check fails.
"""

import copy
import json
import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "engine"))
import runner  # noqa: E402
import yaml  # noqa: E402
from jsonschema import Draft7Validator  # noqa: E402

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {label}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {label}")


UPGRADE_PATH = REPO / "candidates" / "procedures" / "release-upgrade-to-26.04.yaml"
FIXTURE_PATH = REPO / "tests" / "procedure-proof" / "procedure-proof.yaml"
UPGRADE = yaml.safe_load(UPGRADE_PATH.read_text(encoding="utf-8"))
FIXTURE = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))

RELEASES = {"kinetic": 2210, "lunar": 2304, "mantic": 2310, "noble": 2404,
            "resolute": 2604, "focal": 2004}
NEXT = {"kinetic": "lunar", "lunar": "mantic", "mantic": "noble", "noble": "resolute"}


class Rebooted(Exception):
    """Timeshift's `reboot -f`, or a power cut: the process just stops."""


class Sim:
    """A fake machine plus everything the engine touches outside Python."""

    def __init__(self, td, codename="kinetic", privileged=True, systemd=True,
                 timeshift=True, devices=("/dev/sda2", "uuid-1", "/dev/sda")):
        self.td = Path(td)
        self.machine = {"codename": codename, "markers": set(), "packages": 1,
                        "dpkg_clean": True}
        self.snapshot = None
        self.privileged, self.systemd, self.timeshift = privileged, systemd, timeshift
        self.devices = devices
        self.service_active = False
        self.answer = True
        self.events = []
        self.step_results = {}     # step key -> list of results to hand out
        self.restore_result = "reboots"   # reboots | exit0 | exit1 | abort
        self.create_ok = True
        self.power_cut_on = None   # step key whose command is cut off mid-run

    # --- the machine, as the step checks see it -----------------------------
    def measure(self, pb):
        cmd = pb["detect"]["command"]
        m = self.machine
        if "VERSION_CODENAME" in cmd:
            return m["codename"]
        if "VERSION_ID" in cmd:
            return RELEASES[m["codename"]] if m["dpkg_clean"] else 0
        for name in ("one", "two", "three"):
            if f"sf3000-proof/{name}" in cmd:
                return 1 if name in m["markers"] else 0
        raise AssertionError(f"unknown detect: {cmd}")

    def assess(self, pb):
        measurement = self.measure(pb)
        healthy = runner.evaluate(pb, measurement, None)
        return (runner.HEALTHY if healthy else runner.PROBLEM, measurement,
                f"measured={measurement!r}")

    def fingerprint(self):
        m = self.machine
        return f"{m['codename']} packages:{m['packages']} markers:{sorted(m['markers'])}"

    # --- commands ------------------------------------------------------------
    def step_key(self, cmd):
        for key in ("lunar.tar.gz", "mantic.tar.gz"):
            if key in cmd:
                return key.split(".")[0]
        if "do-release-upgrade" in cmd:
            return NEXT[self.machine["codename"]]
        for name in ("one", "two", "three"):
            if f"sf3000-proof/{name}" in cmd:
                return name
        raise AssertionError(f"unknown step command: {cmd}")

    def run_logged(self, cmd, timeout, log_file):
        if isinstance(cmd, list) and cmd[:2] == ["timeshift", "--create"]:
            self.events.append("snapshot")
            if not self.create_ok:
                return 1, "E: not enough space"
            self.snapshot = copy.deepcopy(self.machine)
            return 0, "Tagged snapshot '2026-10-07_12-00-00': ondemand\n"
        if isinstance(cmd, list) and cmd[:2] == ["timeshift", "--restore"]:
            self.events.append("restore")
            if self.restore_result == "exit1":
                return 1, "E: rsync failed"
            if self.restore_result == "abort":
                return 0, "Aborted."
            self.machine = copy.deepcopy(self.snapshot)
            if self.restore_result == "reboots":
                self.events.append("reboot -f")
                raise Rebooted()
            return 0, "restored"
        key = self.step_key(cmd)
        self.events.append(f"run:{key}")
        if self.power_cut_on == key:
            self.power_cut_on = None
            raise Rebooted()
        results = self.step_results.get(key)
        result = results.pop(0) if results else "ok"
        if result == "ok":
            self.apply(key)
            return 0, "done"
        if result == "noop":
            return 0, "exited 0 but changed nothing"
        if result == "timeout":
            return None, "still going"
        if result == "locked":
            return 100, "E: Could not get lock /var/lib/dpkg/lock-frontend"
        return 1, "E: something broke"

    def apply(self, key):
        m = self.machine
        if key in ("one", "two", "three"):
            m["markers"].add(key)
        else:
            m["codename"] = key
            m["packages"] += 1

    def systemctl(self, *args):
        self.events.append("systemctl " + " ".join(args))
        return 0

    def reboot(self):
        self.events.append("reboot")

    # --- install the stubs ---------------------------------------------------
    def __enter__(self):
        self.saved = {n: getattr(runner, n) for n in (
            "assess", "run_logged", "systemctl", "reboot_machine", "has_privilege",
            "systemd_running", "timeshift_installed", "snapshot_devices",
            "machine_fingerprint", "service_active", "confirm_procedure", "read_cmd",
            "PROCEDURE_STATE_DIR", "PROCEDURE_LOG_DIR", "PROCEDURE_UNIT_PATH",
            "TIMESHIFT_CONF", "time", "BLOCKED_RETRIES")}
        runner.assess = self.assess
        runner.run_logged = self.run_logged
        runner.systemctl = self.systemctl
        runner.reboot_machine = self.reboot
        runner.has_privilege = lambda: self.privileged
        runner.systemd_running = lambda: self.systemd
        runner.timeshift_installed = lambda: self.timeshift
        runner.snapshot_devices = lambda: self.devices
        runner.machine_fingerprint = self.fingerprint
        runner.service_active = lambda: self.service_active
        runner.confirm_procedure = lambda *a: (self.events.append("confirm"), self.answer)[1]
        runner.read_cmd = lambda *a: ""
        runner.PROCEDURE_STATE_DIR = self.td / "var-lib-sf3000"
        runner.PROCEDURE_LOG_DIR = self.td / "var-log-sf3000"
        runner.PROCEDURE_UNIT_PATH = self.td / runner.PROCEDURE_UNIT
        runner.TIMESHIFT_CONF = self.td / "timeshift" / "timeshift.json"
        runner.time = SimpleNamespace(sleep=lambda s: self.events.append(f"sleep {s}"))
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(runner, name, value)

    # --- driving -------------------------------------------------------------
    def start(self, proc=UPGRADE, path=UPGRADE_PATH, snapshot=None, take=True):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = runner.start_procedure(proc, path, "ubuntu", "linux", snapshot,
                                        take, self.td / "clone-runs.jsonl")
        self.output = out.getvalue()
        return rc

    def boot_until_stopped(self, limit=12):
        """Run --resume, and again after every reboot, until it stops asking
        for one. Returns how many boots it took."""
        boots = 0
        while boots < limit:
            boots += 1
            before = len(self.events)
            out = io.StringIO()
            try:
                with redirect_stdout(out):
                    runner.resume_procedure()
            except Rebooted:
                continue
            finally:
                self.output = out.getvalue()
            if "reboot" not in self.events[before:]:
                return boots
        raise AssertionError("never stopped")

    def state(self):
        return runner.read_state()

    def records(self):
        log = runner.procedure_log_path()
        if not log.exists():
            return []
        return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]

    def outcomes(self):
        return [(r.get("step_id"), r["outcome"]) for r in self.records()]


def staged(sim):
    return (runner.PROCEDURE_STATE_DIR / "procedure.json").exists()


print("Schemas: both entries validate, and the two kinds do not mix")
pschema = runner.load_schema(runner.PROCEDURE_SCHEMA_PATH)
v = Draft7Validator(pschema)
check("upgrade procedure is valid", list(v.iter_errors(UPGRADE)), [])
check("fixture is valid", list(v.iter_errors(FIXTURE)), [])
check("playbook schema rejects a procedure",
      bool(list(Draft7Validator(runner.load_schema()).iter_errors(UPGRADE))), True)
bad = copy.deepcopy(FIXTURE)
del bad["steps"][0]["timeout_minutes"]
check("a step without a time limit is rejected", bool(list(v.iter_errors(bad))), True)
bad = copy.deepcopy(FIXTURE)
bad["steps"][0]["timeout_minutes"] = 721
check("a time limit over 12 hours is rejected", bool(list(v.iter_errors(bad))), True)
bad = copy.deepcopy(FIXTURE)
bad["requires_privilege"] = False
check("a procedure must require privilege", bool(list(v.iter_errors(bad))), True)

print("\nThe upgrade's checks: from each release, the first step to do, and its precondition")
for codename, want in [("kinetic", "to-23.04"), ("lunar", "to-23.10"),
                       ("mantic", "to-24.04"), ("noble", "to-26.04"),
                       ("resolute", None)]:
    with tempfile.TemporaryDirectory() as td, Sim(td, codename=codename) as sim:
        first = next((s for s in UPGRADE["steps"]
                      if sim.assess(s)[0] != runner.HEALTHY), None)
        check(f"{codename}: first step to do", first and first["id"], want)
        if first:
            check(f"{codename}: its precondition holds",
                  sim.assess(first["requires"])[0], runner.HEALTHY)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.machine["dpkg_clean"] = False
    check("half-installed packages read as 'not reached' (0)",
          sim.measure(UPGRADE["steps"][0]), 0)

print("\n--run refuses before changing anything")
cases = [
    ("no privilege -> 3", dict(privileged=False), {}, 3),
    ("cannot be undone, no snapshot -> 4", {}, dict(take=False), 4),
    ("--take-snapshot without Timeshift -> 8", dict(timeshift=False), {}, 8),
    ("devices unknown -> 8", dict(devices=None), {}, 8),
    ("no systemd -> 7", dict(systemd=False), {}, 7),
    ("focal: first step needs kinetic -> 5", dict(codename="focal"), {}, 5),
    ("already on 26.04 -> 0, nothing to do", dict(codename="resolute"), {}, 0),
]
for label, sim_kw, start_kw, want in cases:
    with tempfile.TemporaryDirectory() as td, Sim(td, **sim_kw) as sim:
        check(label, sim.start(**start_kw), want)
        check(f"  {label.split(' ->')[0]}: nothing staged or installed",
              (staged(sim), [e for e in sim.events if e.startswith("systemctl")]),
              (False, []))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.write_state({"procedure_id": "x", "phase": "rebooting"})
    check("another procedure in progress -> 6", sim.start(), 6)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.answer = False
    check("declined -> 0", sim.start(), 0)
    check("declined: nothing staged", staged(sim), False)
    rec = json.loads((sim.td / "clone-runs.jsonl").read_text(encoding="utf-8"))
    check("declined: logged as declined", rec["outcome"], runner.DECLINED)

print("\n--run accepted: staged, service installed, nothing run yet")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    check("exit 0", sim.start(snapshot="repos-fixed"), 0)
    d = runner.PROCEDURE_STATE_DIR
    check("engine copied", (d / "engine" / "runner.py").read_bytes(),
          Path(runner.__file__).read_bytes())
    check("procedure text copied", (d / "procedure.yaml").read_bytes(),
          UPGRADE_PATH.read_bytes())
    check("procedure stored as JSON", json.loads((d / "procedure.json").read_text()), UPGRADE)
    unit = runner.PROCEDURE_UNIT_PATH.read_text(encoding="utf-8")
    check("unit runs the root-owned copy with --resume",
          f"ExecStart=/usr/bin/python3 -I {d / 'engine' / 'runner.py'} --resume" in unit, True)
    check("unit leaves a running step alone if the engine stops", "KillMode=process" in unit, True)
    check("systemctl calls", [e for e in sim.events if e.startswith("systemctl")],
          ["systemctl daemon-reload", f"systemctl enable {runner.PROCEDURE_UNIT}",
           f"systemctl start --no-block {runner.PROCEDURE_UNIT}"])
    st = sim.state()
    check("state: pending at step 0", (st["phase"], st["step"]), ("pending", 0))
    check("state: both snapshots recorded",
          (st["snapshot_id"], st["engine_snapshot"]["device"], st["engine_snapshot"]["name"]),
          ("repos-fixed", "/dev/sda2", None))
    check("no step command ran", [e for e in sim.events if e.startswith("run:")], [])

print("\nHappy path, 22.10 to 26.04 with --take-snapshot")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.start()
    boots = sim.boot_until_stopped()
    check("5 runs of the service: the hand-off, then 4 boots", boots, 5)
    check("order of events", [e for e in sim.events if not e.startswith("systemctl")],
          ["confirm", "snapshot", "run:lunar", "reboot", "run:mantic", "reboot",
           "run:noble", "reboot", "run:resolute", "reboot"])
    check("machine ends on resolute", sim.machine["codename"], "resolute")
    check("records", sim.outcomes(), [("to-23.04", "healed"), ("to-23.10", "healed"),
                                      ("to-24.04", "healed"), ("to-26.04", "healed")])
    r = sim.records()[0]
    check("record: verified after the reboot", (r["rebooted"], r["detect_before"], r["verify_after"]),
          (True, 2210, 2304))
    check("record: names the engine's snapshot", (r["snapshot_taken"], r["snapshot_id"]),
          (True, "2026-10-07_12-00-00"))
    check("record: time limit in seconds", r["timeout_seconds"], 360 * 60)
    st = sim.state()
    check("state: done", st["phase"], "done")
    check("fingerprint taken before the snapshot",
          st["engine_snapshot"]["fingerprint"], "kinetic packages:1 markers:[]")
    check("service removed at the end", sim.events[-2:],
          [f"systemctl disable {runner.PROCEDURE_UNIT}", "systemctl daemon-reload"])
    check("unit file deleted", runner.PROCEDURE_UNIT_PATH.exists(), False)
    cfg = json.loads(runner.TIMESHIFT_CONF.read_text(encoding="utf-8"))
    check("Timeshift config created with the engine's excludes",
          (cfg["exclude"], cfg["btrfs_mode"], cfg["backup_device_uuid"]),
          (runner.SNAPSHOT_EXCLUDES, "false", "uuid-1"))

print("\nA machine already partway up (23.04) skips what is done")
with tempfile.TemporaryDirectory() as td, Sim(td, codename="lunar") as sim:
    sim.start()
    sim.boot_until_stopped()
    check("records", [s for s, _ in sim.outcomes()], ["to-23.10", "to-24.04", "to-26.04"])
    check("history notes the skipped step", sim.state()["history"][0],
          {"step": "to-23.04", "outcome": "already done"})

print("\nStep 2 fails: restore, reboot, verify the machine is back")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["mantic"] = ["fail"]
    sim.start()
    sim.boot_until_stopped()
    check("events", [e for e in sim.events if not e.startswith("systemctl")],
          ["confirm", "snapshot", "run:lunar", "reboot", "run:mantic", "restore", "reboot -f"])
    check("records", sim.outcomes(), [("to-23.04", "healed"), ("to-23.10", "rolled_back")])
    r = sim.records()[1]
    check("record keeps why it failed", (r["failure"], r["fix_exit_code"]), ("fix_failed", 1))
    check("record: restore method and result",
          (r["rollback_method"], r["rollback_result"].startswith("ok: back to kinetic")),
          ("timeshift", True))
    check("machine back on kinetic", sim.machine["codename"], "kinetic")
    st = sim.state()
    check("state: failed, says it was restored",
          (st["phase"], "Restored Timeshift snapshot" in st["stopped_because"]), ("failed", True))
    check("service removed", runner.PROCEDURE_UNIT_PATH.exists(), False)
    check("step 3 never ran", "run:noble" in sim.events, False)

print("\nThe upgrader exits 0 but nothing changed: caught after the reboot, restored")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["noop"]
    sim.start()
    sim.boot_until_stopped()
    check("records", sim.outcomes(), [("to-23.04", "rolled_back")])
    check("failure", sim.records()[0]["failure"], "verify_failed")

print("\nPower cut in the middle of step 1: interrupted, restored, never re-run")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.power_cut_on = "lunar"
    sim.start()
    sim.boot_until_stopped()
    check("step 1 ran once", sim.events.count("run:lunar"), 1)
    check("records", sim.outcomes(), [("to-23.04", "rolled_back")])
    check("failure", sim.records()[0]["failure"], "interrupted")

print("\nTime limit reached: stopped, restored")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["timeout"]
    sim.start()
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("outcome / failure / exit code", (r["outcome"], r["failure"], r["fix_exit_code"]),
          ("rolled_back", "fix_failed", None))

print("\nPackage manager busy: waits and retries; if it never frees up, no restore")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["locked", "locked"]
    sim.start()
    sim.boot_until_stopped()
    check("healed on the third attempt", (sim.records()[0]["outcome"],
                                          sim.records()[0]["fix_attempts"]), ("healed", 3))
    check("waited twice", sim.events.count(f"sleep {runner.BLOCKED_WAIT}"), 2)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.BLOCKED_RETRIES = 3
    sim.step_results["lunar"] = ["locked"] * 3
    sim.start()
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("outcome blocked after 3 attempts", (r["outcome"], r["fix_attempts"]), ("blocked", 3))
    check("no restore for a step that changed nothing", "restore" in sim.events, False)
    check("state failed, service removed",
          (sim.state()["phase"], runner.PROCEDURE_UNIT_PATH.exists()), ("failed", False))

print("\nThe restore itself goes wrong")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["fail"]
    sim.restore_result = "exit1"
    sim.start()
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("restore exits 1: rollback_failed at once, no reboot",
          (r["outcome"], "reboot" in sim.events[sim.events.index("restore"):]),
          ("rollback_failed", False))
    check("says a person is needed", "needs a person" in sim.state()["stopped_because"], True)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["ok", "fail"]
    sim.step_results["mantic"] = ["fail"]
    sim.restore_result = "abort"
    sim.start()
    sim.boot_until_stopped()
    r = sim.records()[-1]
    check("restore exits 0 without restoring: engine reboots, the check catches it",
          (r["outcome"], sim.events[sim.events.index("restore") + 1]), ("rollback_failed", "reboot"))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["fail"]
    sim.restore_result = "exit0"
    sim.start()
    sim.boot_until_stopped()
    check("restore exits 0 and did restore: engine reboots, rolled_back",
          sim.records()[0]["outcome"], "rolled_back")

print("\nOnly a named snapshot: the engine cannot restore it, so it stops and names it")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["fail"]
    sim.start(snapshot="repos-fixed", take=False)
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("outcome fix_failed, no restore", (r["outcome"], "restore" in sim.events),
          ("fix_failed", False))
    check("rollback names the snapshot", ("repos-fixed" in r["rollback_result"],
                                          r["rollback_method"]), (True, "snapshot_only"))
    check("record: no snapshot taken, the named one kept",
          (r["snapshot_taken"], r["snapshot_id"], r["snapshot_named"]),
          (False, "repos-fixed", "repos-fixed"))

print("\nThe snapshot cannot be taken: no step runs")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.create_ok = False
    sim.start()
    sim.boot_until_stopped()
    check("records", sim.outcomes(), [("snapshot", "snapshot_failed")])
    check("no step ran", [e for e in sim.events if e.startswith("run:")], [])
    check("state failed, service removed",
          (sim.state()["phase"], runner.PROCEDURE_UNIT_PATH.exists()), ("failed", False))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.start()
    st = sim.state()
    st["phase"] = "snapshotting"
    runner.write_state(st)
    sim.boot_until_stopped()
    check("a boot mid-snapshot: stops, no step ran",
          (sim.state()["phase"], [e for e in sim.events if e.startswith("run:")]), ("failed", []))

print("\nThe VM fixture, through the same harness")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["three"] = ["timeout"]
    sim.start(proc=FIXTURE, path=FIXTURE_PATH, take=False)
    sim.boot_until_stopped()
    check("without a snapshot: one, two healed; three stopped at its limit",
          sim.outcomes(), [("one", "healed"), ("two", "healed"), ("three", "fix_failed")])
    check("markers left", sorted(sim.machine["markers"]), ["one", "two"])
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["three"] = ["timeout"]
    sim.start(proc=FIXTURE, path=FIXTURE_PATH, take=True)
    sim.boot_until_stopped()
    check("with --take-snapshot: three rolled back",
          sim.outcomes(), [("one", "healed"), ("two", "healed"), ("three", "rolled_back")])
    check("markers gone", sorted(sim.machine["markers"]), [])

print("\n--resume, --status and --cancel")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    with redirect_stdout(io.StringIO()):
        rc = runner.resume_procedure()
    check("--resume with no state: exit 0", rc, 0)
    check("  and removes the service", sim.events[0], f"systemctl disable {runner.PROCEDURE_UNIT}")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.start()
    sim.service_active = True
    with redirect_stdout(io.StringIO()):
        rc = runner.cancel_procedure()
    check("--cancel while the service works: refused (6)", rc, 6)
    sim.service_active = False
    with redirect_stdout(io.StringIO()):
        rc = runner.cancel_procedure()
    check("--cancel between steps: 0", rc, 0)
    check("  state cancelled, service removed",
          (sim.state()["phase"], runner.PROCEDURE_UNIT_PATH.exists()), ("cancelled", False))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = runner.procedure_status()
    check("--status: 0", rc, 0)
    check("  shows the phase", "phase     : cancelled" in out.getvalue(), True)

print("\nThe engine itself crashes at boot: the procedure stops, the service goes")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.start()

    def boom(pb):
        raise ValueError("unexpected")
    runner.assess = boom
    argv, sys.argv = sys.argv, ["runner.py", "--resume"]
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = runner.main()
    finally:
        sys.argv = argv
    check("--resume exits 0 even so", rc, 0)
    check("state failed, says the engine failed",
          (sim.state()["phase"], "engine itself failed" in sim.state()["stopped_because"]),
          ("failed", True))
    check("service removed", runner.PROCEDURE_UNIT_PATH.exists(), False)

print("\nTimeshift config: merged, never clobbered")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.TIMESHIFT_CONF.parent.mkdir(parents=True)
    runner.TIMESHIFT_CONF.write_text(json.dumps({
        "backup_device_uuid": "theirs", "btrfs_mode": "false",
        "schedule_daily": "true", "exclude": ["/opt/big/***"]}))
    runner.prepare_timeshift_config("uuid-1")
    cfg = json.loads(runner.TIMESHIFT_CONF.read_text())
    check("their device, schedule and exclude kept, ours added",
          (cfg["backup_device_uuid"], cfg["schedule_daily"], cfg["exclude"]),
          ("theirs", "true", ["/opt/big/***"] + runner.SNAPSHOT_EXCLUDES))
    runner.prepare_timeshift_config("uuid-1")
    check("running twice adds nothing", json.loads(runner.TIMESHIFT_CONF.read_text())["exclude"],
          ["/opt/big/***"] + runner.SNAPSHOT_EXCLUDES)
    runner.TIMESHIFT_CONF.write_text(json.dumps({"btrfs_mode": "true"}))
    try:
        runner.prepare_timeshift_config("uuid-1")
        check("btrfs mode refused", "no error", "RuntimeError")
    except RuntimeError:
        check("btrfs mode refused", "RuntimeError", "RuntimeError")

print("\nThe excludes cover exactly what a restore must not roll back")
check("state dir, log dir, unit, enable link", runner.SNAPSHOT_EXCLUDES, [
    f"{runner.PROCEDURE_STATE_DIR.as_posix()}/***", f"{runner.PROCEDURE_LOG_DIR.as_posix()}/***",
    runner.PROCEDURE_UNIT_PATH.as_posix(),
    f"/etc/systemd/system/multi-user.target.wants/{runner.PROCEDURE_UNIT}"])
check("restore names the GRUB disk and answers yes",
      runner.snapshot_restore_cmd({"name": "N", "device": "/dev/sda2", "grub_device": "/dev/sda"}),
      ["timeshift", "--restore", "--snapshot", "N", "--target-device", "/dev/sda2",
       "--grub-device", "/dev/sda", "--scripted", "--yes"])

if FAILURES:
    print(f"\n{len(FAILURES)} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")
