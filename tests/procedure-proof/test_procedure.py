#!/usr/bin/env python3
"""
Offline proof of the procedure lifecycle. Run:
    python tests/procedure-proof/test_procedure.py

No VM: the machine is a dict, a reboot is the harness calling --resume again,
and every shell command, apt run, systemctl call and Timeshift run is stubbed.
What it proves is BRANCH LOGIC — which outcome each situation produces, what
the state file says at each boot, and above all what does NOT run. The real
upgrade procedures (candidates/procedures/) and the VM fixture
(procedure-proof.yaml) are loaded from disk, so their step checks are tested
with the numbers actually in them. README.md has the VM runs, which prove the
same machinery on a real machine.

No pytest, like the other offline proofs. Exits non-zero if any check fails.
"""

import copy
import json
import io
import os
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


PROC_DIR = REPO / "candidates" / "procedures"
UPGRADE_IDS = ["release-upgrade-to-23.04", "release-upgrade-to-23.10",
               "release-upgrade-to-24.04", "release-upgrade-to-26.04"]
UPGRADES = {}
for _id in UPGRADE_IDS:
    _path = PROC_DIR / f"{_id}.yaml"
    UPGRADES[_id] = (yaml.safe_load(_path.read_text(encoding="utf-8")), _path)
UPGRADE, UPGRADE_PATH = UPGRADES["release-upgrade-to-23.04"]    # visit 1
FIXTURE_PATH = REPO / "tests" / "procedure-proof" / "procedure-proof.yaml"
FIXTURE = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
CUTOFF_PATH = REPO / "tests" / "procedure-proof" / "cut-off-proof.yaml"
CUTOFF = yaml.safe_load(CUTOFF_PATH.read_text(encoding="utf-8"))
LEFTOVER_PATH = REPO / "tests" / "procedure-proof" / "leftover-proof.yaml"
LEFTOVER = yaml.safe_load(LEFTOVER_PATH.read_text(encoding="utf-8"))

RELEASES = {"kinetic": 2210, "lunar": 2304, "mantic": 2310, "noble": 2404,
            "resolute": 2604, "focal": 2004}
NEXT = {"kinetic": "lunar", "lunar": "mantic", "mantic": "noble", "noble": "resolute"}

# What apt-get update prints, for each way a source can be.
APT_UPDATE = {
    "ok": (0, "Hit:1 http://old-releases.ubuntu.com/ubuntu kinetic InRelease\n"
              "Reading package lists...\n"),
    # Machine 2 of the lab: a source listed twice. A warning, not a failure.
    "dupes": (0, "Hit:1 http://old-releases.ubuntu.com/ubuntu kinetic InRelease\n"
                 "W: Target Packages (main/binary-amd64/Packages) is configured "
                 "multiple times in /etc/apt/sources.list:1 and "
                 "/etc/apt/sources.list:51\n"),
    # Machine 1: a third-party repository with no 22.10. apt exits 100.
    "no-release": (100, "Err:6 https://pkg.cloudflareclient.com kinetic Release\n"
                        "  404  Not Found [IP: 104.18.0.1 443]\n"
                        "Reading package lists...\n"
                        "E: The repository 'https://pkg.cloudflareclient.com "
                        "kinetic Release' does not have a Release file.\n"
                        "N: Updating from such a repository can't be done "
                        "securely, and is therefore disabled by default.\n"),
    # Machine 1 again: a mirror that does not exist. apt exits 0 and warns.
    "unreachable": (0, "Err:1 http://in.old-releases.ubuntu.com/ubuntu kinetic InRelease\n"
                       "  Could not resolve 'in.old-releases.ubuntu.com'\n"
                       "Reading package lists...\n"
                       "W: Failed to fetch http://in.old-releases.ubuntu.com/ubuntu/"
                       "dists/kinetic/InRelease  Could not resolve "
                       "'in.old-releases.ubuntu.com'\n"
                       "W: Some index files failed to download. They have been "
                       "ignored, or old ones used instead.\n"),
    # Machine 1 as it really is: both at once. Once one source fails hard, apt
    # prints no "Failed to fetch" line for any source (apt-pkg/update.cc,
    # 2.5.3), so the dead mirror is named on its Err: line alone.
    "both": (100, "Err:1 http://in.old-releases.ubuntu.com/ubuntu kinetic InRelease\n"
                  "  Could not resolve 'in.old-releases.ubuntu.com'\n"
                  "Err:6 https://pkg.cloudflareclient.com kinetic Release\n"
                  "  404  Not Found [IP: 104.18.0.1 443]\n"
                  "Reading package lists...\n"
                  "E: The repository 'https://pkg.cloudflareclient.com "
                  "kinetic Release' does not have a Release file.\n"
                  "N: Updating from such a repository can't be done "
                  "securely, and is therefore disabled by default.\n"),
    "locked": (100, "E: Could not get lock /var/lib/apt/lists/lock. It is held "
                    "by process 1234 (apt-get)\n"),
    "timeout": (None, "Get:1 http://old-releases.ubuntu.com/ubuntu kinetic InRelease\n"),
}


class Rebooted(Exception):
    """Timeshift's `reboot -f`, or a power cut: the process just stops."""


class Sim:
    """A fake machine plus everything the engine touches outside Python."""

    def __init__(self, td, codename="kinetic", privileged=True, systemd=True,
                 timeshift=True, apt=True,
                 devices=("/dev/sda2", "uuid-1", "/dev/sda")):
        self.td = Path(td)
        # pending: updates waiting to be installed; kernel_pending: a kernel is
        # among them; reboot_pending: one is installed and waits for a reboot.
        self.machine = {"codename": codename, "markers": set(), "packages": 1,
                        "dpkg_clean": True, "pending": 0, "kernel_pending": False,
                        "reboot_pending": False}
        self.snapshot = None
        self.privileged, self.systemd, self.timeshift = privileged, systemd, timeshift
        self.apt = apt
        self.devices = devices
        self.service_active = False
        self.answer = True         # the procedure's y/N
        self.ask_answer = True     # every other y/N
        self.asked = []
        self.events = []
        self.step_results = {}     # step key -> list of results to hand out
        self.apt_updates = []      # apt-get update results to hand out (APT_UPDATE keys)
        self.installs = []         # apt-get install results: ok | fail | locked | noop
        self.restore_result = "reboots"   # reboots | exit0 | exit1 | abort
        self.create_ok = True
        self.power_cut_on = None   # step key whose command is cut off mid-run
        self.lock_tries = []       # each try of dpkg's lock: free | busy | cut (empty: free)
        self.lock_held = False
        self.under_lock = {}       # snapshot / restore -> was dpkg's lock held then?
        self.cut_on_sync = False   # a power cut while the snapshot is written out
        self.apt_tail_bytes = None # how much of apt-get update's output the check read
        self.disk_full = False     # a step filled the disk: True, or "hard" if even
                                   # freeing the reserve leaves no room
        self.all_output = ""       # every boot's output, not only the last one's
        self.leftovers = []        # what a failed step leaves running ("pid name")
        self.reserve_at_stop = None   # was the reserve still there when they were stopped?

    # --- the machine, as the step checks see it -----------------------------
    def measure(self, pb):
        cmd = pb["detect"]["command"]
        m = self.machine
        if "reboot-required.pkgs" in cmd:      # 26.04's step 1: what still waits
            return m["pending"] + (1 if m["reboot_pending"] else 0)
        if "VERSION_CODENAME" in cmd:
            return m["codename"]
        if "VERSION_ID" in cmd:
            return RELEASES[m["codename"]] if m["dpkg_clean"] else 0
        for name in ("one", "two", "three", "finished", "left"):
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
        self.events.append("fingerprint (locked)" if self.lock_held else "fingerprint")
        return f"{m['codename']} packages:{m['packages']} markers:{sorted(m['markers'])}"

    # --- commands ------------------------------------------------------------
    def step_key(self, cmd):
        for key in ("lunar.tar.gz", "mantic.tar.gz"):
            if key in cmd:
                return key.split(".")[0]
        if "do-release-upgrade" in cmd:
            return NEXT[self.machine["codename"]]
        if "full-upgrade" in cmd:              # 26.04's step 1, updates only
            return "updates"
        if "sf3000-proof/started" in cmd:
            return "long"
        if "sf3000-proof/left" in cmd:
            return "leave"
        for name in ("one", "two", "three"):
            if f"sf3000-proof/{name}" in cmd:
                return name
        raise AssertionError(f"unknown step command: {cmd}")

    def apt_get(self, cmd):
        if "update" in cmd:
            self.events.append("apt-get update")
            result = self.apt_updates.pop(0) if self.apt_updates else "ok"
            return APT_UPDATE[result]
        packages = cmd[cmd.index("install") + 1:]
        self.events.append("apt-get install " + " ".join(packages))
        result = self.installs.pop(0) if self.installs else "ok"
        if result == "ok":
            if "timeshift" in packages:
                self.timeshift = True
            return 0, "Setting up timeshift (22.06.5-1) ...\n"
        if result == "noop":
            return 0, "exited 0 but installed nothing\n"
        if result == "locked":
            return 100, "E: Could not get lock /var/lib/dpkg/lock-frontend\n"
        return 100, "E: Unable to locate package timeshift\n"

    def run_logged(self, cmd, timeout, log_file, tail_bytes=8192):
        if isinstance(cmd, list) and "apt-get" in cmd:
            if "update" in cmd:
                self.apt_tail_bytes = tail_bytes
            return self.apt_get(cmd)
        if isinstance(cmd, list) and cmd[:2] == ["timeshift", "--create"]:
            self.events.append("snapshot")
            self.under_lock["snapshot"] = self.lock_held
            if not self.create_ok:
                return 1, "E: not enough space"
            self.snapshot = copy.deepcopy(self.machine)
            return 0, "Tagged snapshot '2026-10-07_12-00-00': ondemand\n"
        if isinstance(cmd, list) and cmd[:2] == ["timeshift", "--restore"]:
            self.events.append("restore")
            self.under_lock["restore"] = self.lock_held
            if self.restore_result == "exit1":
                return 1, "E: rsync failed"
            if self.restore_result == "abort":
                return 0, "Aborted."
            self.machine = copy.deepcopy(self.snapshot)
            self.disk_full = False               # what filled it is gone again
            if self.restore_result == "reboots":
                self.events.append("reboot -f")
                self.machine["reboot_pending"] = False
                raise Rebooted()
            return 0, "restored"
        key = self.step_key(cmd)
        self.events.append(f"run:{key}")
        if self.power_cut_on == key:
            self.power_cut_on = None
            if key == "long":                    # its command's first half
                self.machine["markers"].add("started")
            self.machine["reboot_pending"] = False
            raise Rebooted()
        results = self.step_results.get(key)
        result = results.pop(0) if results else "ok"
        if result == "ok":
            if "full-upgrade" in cmd:            # it installs what waits, first
                self.install_updates()
            if "do-release-upgrade" in cmd and (self.machine["pending"]
                                                or self.machine["reboot_pending"]):
                # do-release-upgrade's own checks, read in its source (noble)
                return 1, ("You have not rebooted after updating a package which "
                           "requires a reboot. Please reboot before upgrading.")
            self.apply(key)
            return 0, "done"
        if result in ("disk-full", "disk-full-hard"):
            self.machine["packages"] += 1        # got partway, then the disk filled
            self.disk_full = True if result == "disk-full" else "hard"
            return 1, "E: Write error - write (28: No space left on device)"
        if result == "noop":
            return 0, "exited 0 but changed nothing"
        if result == "timeout":
            return None, "still going"
        if result == "locked":
            return 100, "E: Could not get lock /var/lib/dpkg/lock-frontend"
        if result == "fail-after-change":
            self.machine["packages"] += 1     # got partway, then broke
            return 1, "E: dpkg was interrupted"
        return 1, "E: something broke"

    def apply(self, key):
        m = self.machine
        if key in ("one", "two", "three"):
            m["markers"].add(key)
        elif key == "long":
            m["markers"].update({"started", "finished"})
        elif key == "updates":
            pass                                 # install_updates did it all
        else:
            m["codename"] = key
            m["packages"] += 1

    def install_updates(self):
        m = self.machine
        if m["pending"]:
            m["packages"] += 1
            m["pending"] = 0
        if m["kernel_pending"]:
            m["kernel_pending"] = False
            m["reboot_pending"] = True

    def disk_write(self, what):
        """Every write the engine makes to its state or its records. Once a step
        has filled the disk, a write fails while the reserve is still there."""
        if self.disk_full == "hard" or (self.disk_full and runner.reserve_path().exists()):
            self.events.append(f"write failed: {what}")
            raise OSError(28, "No space left on device")

    def systemctl(self, *args):
        self.events.append("systemctl " + " ".join(args))
        return 0

    def reboot(self):
        self.events.append("reboot")
        self.machine["reboot_pending"] = False

    def ask(self, question):
        self.asked.append(question)
        self.events.append("ask")
        return self.ask_answer

    def try_lock(self):
        result = self.lock_tries.pop(0) if self.lock_tries else "free"
        if result == "cut":
            self.machine["reboot_pending"] = False
            raise Rebooted()
        if result == "busy":
            self.events.append("lock busy")
            return None
        self.events.append("lock")
        self.lock_held = True
        return 7

    def release(self, fd):
        self.events.append("unlock")
        self.lock_held = False

    def stop_leftovers(self):
        self.events.append("stop leftovers")
        self.reserve_at_stop = runner.reserve_path().exists()
        stopped, self.leftovers = self.leftovers, []
        return stopped

    def sync(self):
        if self.cut_on_sync:
            self.cut_on_sync = False
            self.machine["reboot_pending"] = False
            raise Rebooted()
        self.events.append("sync")

    # --- install the stubs ---------------------------------------------------
    def __enter__(self):
        self.saved = {n: getattr(runner, n) for n in (
            "assess", "run_logged", "systemctl", "reboot_machine", "has_privilege",
            "systemd_running", "timeshift_installed", "snapshot_devices",
            "machine_fingerprint", "service_active", "confirm_procedure", "read_cmd",
            "apt_available", "ask_yes", "yaml", "Draft7Validator",
            "try_package_manager_lock", "release_package_manager", "sync_disks",
            "stop_leftovers",
            "PROCEDURE_STATE_DIR", "PROCEDURE_LOG_DIR", "PROCEDURE_UNIT_PATH",
            "TIMESHIFT_CONF", "time", "BLOCKED_RETRIES", "RESERVE_BYTES",
            "write_state", "log_run", "make_reserve")}
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
        runner.apt_available = lambda: self.apt
        runner.ask_yes = self.ask
        runner.try_package_manager_lock = self.try_lock
        runner.release_package_manager = self.release
        runner.sync_disks = self.sync
        runner.stop_leftovers = self.stop_leftovers
        runner.PROCEDURE_STATE_DIR = self.td / "var-lib-sf3000"
        runner.PROCEDURE_LOG_DIR = self.td / "var-log-sf3000"
        runner.PROCEDURE_UNIT_PATH = self.td / runner.PROCEDURE_UNIT
        runner.TIMESHIFT_CONF = self.td / "timeshift" / "timeshift.json"
        runner.time = SimpleNamespace(sleep=lambda s: self.events.append(f"sleep {s}"))
        runner.RESERVE_BYTES = 4096
        write_state, log_run = self.saved["write_state"], self.saved["log_run"]
        runner.write_state = lambda state: (self.disk_write("state"), write_state(state))[1]
        runner.log_run = lambda record, path: (self.disk_write("record"),
                                               log_run(record, path))[1]
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
            self.lock_held = False         # a reboot frees every lock
            before = len(self.events)
            out = io.StringIO()
            try:
                with redirect_stdout(out):
                    runner.resume_procedure()
            except Rebooted:
                continue
            finally:
                self.output = out.getvalue()
                self.all_output += self.output
            if "reboot" not in self.events[before:]:
                return boots
        raise AssertionError("never stopped")

    def call(self, fn, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fn(*args)
        self.output = out.getvalue()
        return rc

    def state(self):
        return runner.read_state()

    def records(self):
        log = runner.procedure_log_path()
        if not log.exists():
            return []
        return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]

    def outcomes(self):
        return [(r.get("step_id"), r["outcome"]) for r in self.records()]

    def ran(self):
        return [e for e in self.events if not e.startswith(
            ("systemctl", "sleep", "lock", "unlock", "sync", "fingerprint",
             "stop leftovers"))]


def staged(sim):
    return (runner.PROCEDURE_STATE_DIR / "procedure.json").exists()


print("Schemas: every entry validates, and the two kinds do not mix")
pschema = runner.load_schema(runner.PROCEDURE_SCHEMA_PATH)
v = Draft7Validator(pschema)
# One upgrade each. 26.04 installs the waiting updates and reboots first: its
# do-release-upgrade refuses while a kernel or libc6 update waits for a reboot.
STEP_IDS = {"release-upgrade-to-23.04": ["to-23.04"],
            "release-upgrade-to-23.10": ["to-23.10"],
            "release-upgrade-to-24.04": ["to-24.04"],
            "release-upgrade-to-26.04": ["updates", "to-26.04"]}
for pid, (proc, _path) in UPGRADES.items():
    check(f"{pid} is valid", list(v.iter_errors(proc)), [])
    check(f"{pid}: its steps, each with a reboot after it",
          [(s["id"], s["reboot_after"]) for s in proc["steps"]],
          [(i, True) for i in STEP_IDS[pid]])
check("release-upgrade-to-26.04: both steps require 24.04, so neither runs elsewhere",
      [s["requires"]["expect"]["value"] for s in UPGRADES["release-upgrade-to-26.04"][0]["steps"]],
      ["noble", "noble"])
check("  and its upgrade step updates no lists: it upgrades from step 1's",
      "apt-get update" in UPGRADES["release-upgrade-to-26.04"][0]["steps"][1]["run"]["ubuntu"],
      False)
check("fixture is valid", list(v.iter_errors(FIXTURE)), [])
check("cut-off fixture is valid", list(v.iter_errors(CUTOFF)), [])
check("leftover fixture is valid", list(v.iter_errors(LEFTOVER)), [])
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

print("\nThe upgrades' checks: on each release, exactly one applies, the next one")
listed = sorted(UPGRADES.values(), key=lambda pp: pp[0]["id"])
for codename, want in [("kinetic", ["release-upgrade-to-23.04"]),
                       ("lunar", ["release-upgrade-to-23.10"]),
                       ("mantic", ["release-upgrade-to-24.04"]),
                       ("noble", ["release-upgrade-to-26.04"]),
                       ("resolute", []), ("focal", [])]:
    with tempfile.TemporaryDirectory() as td, Sim(td, codename=codename) as sim:
        check(f"{codename}: applies now", runner.procedures_that_apply(listed, "ubuntu", "linux"), want)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.machine["dpkg_clean"] = False
    check("half-installed packages read as 'not reached' (0)",
          sim.measure(UPGRADE["steps"][0]), 0)
with tempfile.TemporaryDirectory() as td, Sim(td, codename="noble") as sim:
    check("not for another OS", runner.procedures_that_apply(listed, "ubuntu", "windows"), [])

print("\n--run refuses before changing anything")
cases = [
    ("no privilege -> 3", dict(privileged=False), {}, 3),
    ("cannot be undone, no snapshot -> 4", {}, dict(take=False), 4),
    ("--take-snapshot, no Timeshift and no apt to install it -> 8",
     dict(timeshift=False, apt=False), {}, 8),
    ("devices unknown -> 8", dict(devices=None), {}, 8),
    ("no systemd -> 7", dict(systemd=False), {}, 7),
    ("focal: the upgrade needs kinetic -> 5", dict(codename="focal"), {}, 5),
    ("already on 23.04 -> 0, nothing to do", dict(codename="lunar"), {}, 0),
]
for label, sim_kw, start_kw, want in cases:
    with tempfile.TemporaryDirectory() as td, Sim(td, **sim_kw) as sim:
        check(label, sim.start(**start_kw), want)
        check(f"  {label.split(' ->')[0]}: nothing staged, installed, set aside or asked",
              (staged(sim), [e for e in sim.events if e.startswith("systemctl")],
               [e for e in sim.events if e.startswith("apt-get install")],
               runner.reserve_path().exists(), "confirm" in sim.events),
              (False, [], [], False, False))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.write_state({"procedure_id": "x", "phase": "rebooting"})
    check("another procedure in progress -> 6", sim.start(), 6)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.answer = False
    check("declined -> 0", sim.start(), 0)
    check("declined: nothing staged or set aside", (staged(sim), runner.reserve_path().exists()),
          (False, False))
    rec = json.loads((sim.td / "clone-runs.jsonl").read_text(encoding="utf-8"))
    check("declined: logged as declined", rec["outcome"], runner.DECLINED)

print("\napt is checked first: a dead source stops the run before the y/N")
for label, updates, want in [
        ("a repository with no 22.10 (apt exits 100) -> 9", ["no-release"], 9),
        ("a mirror that does not exist (apt exits 0, warns) -> 9", ["unreachable"], 9),
        ("apt stays busy through every attempt -> 9", ["locked"] * 3, 9),
        ("apt-get update never finishes -> 9", ["timeout"], 9)]:
    with tempfile.TemporaryDirectory() as td, Sim(td, timeshift=False) as sim:
        runner.BLOCKED_RETRIES = 3
        sim.apt_updates = list(updates)
        check(label, sim.start(), want)
        check(f"  {label.split(' (')[0]}: not asked, nothing installed or staged",
              (sim.ran()[-1].startswith("apt-get update"), "confirm" in sim.events,
               staged(sim), sim.records()), (True, False, False, []))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.apt_updates = ["no-release"]
    sim.start()
    check("the failing source is named",
          "does not have a Release file" in sim.output, True)
    check("the advice says nothing was changed",
          "Nothing was installed or upgraded" in sim.output, True)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.apt_updates = ["unreachable"]
    sim.start()
    named = [ln.strip() for ln in sim.output.splitlines() if "in.old-releases" in ln]
    check("an unreachable mirror is named on one line, its summary's",
          (len(named), named[0].startswith("W: Failed to fetch"),
           "Some index files" in sim.output), (1, True, False))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.apt_updates = ["both"]
    check("a dead mirror and a repository with no 22.10 at once -> 9", sim.start(), 9)
    check("  both are named, though apt's own summary names only one",
          ("pkg.cloudflareclient.com" in sim.output, "in.old-releases.ubuntu.com" in sim.output,
           "Could not resolve" in sim.output), (True, True, True))
    check("  the check read all of apt's output, not only its end",
          sim.apt_tail_bytes, runner.APT_CHECK_OUTPUT)
# The shape of Run D's output on the 22.10 VM (2026-10-09), which named only
# the missing suite.
RUN_D = ("Hit:1 http://old-releases.ubuntu.com/ubuntu kinetic InRelease\n"
         "Err:5 http://sf3000-no-such-host.invalid/ubuntu kinetic InRelease\n"
         "  Temporary failure resolving 'sf3000-no-such-host.invalid'\n"
         "Ign:6 http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite InRelease\n"
         "Err:7 http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite Release\n"
         "  404  Not Found [IP: 185.125.190.37 80]\n"
         "Reading package lists...\n"
         "E: The repository 'http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite "
         "Release' does not have a Release file.\n")
check("Run D's output: each dead source named once",
      runner.apt_problems(RUN_D),
      ["E: The repository 'http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite "
       "Release' does not have a Release file.",
       "Err:5 http://sf3000-no-such-host.invalid/ubuntu kinetic InRelease  "
       "Temporary failure resolving 'sf3000-no-such-host.invalid'"])
check("a summary for kinetic-updates does not stand in for kinetic's Err: line",
      len(runner.apt_problems(
          "Err:1 http://h.example/ubuntu kinetic InRelease\n  Could not resolve 'h.example'\n"
          "W: Failed to fetch http://h.example/ubuntu/dists/kinetic-updates/InRelease  "
          "Could not resolve 'h.example'\n")), 2)
check("a clean update: nothing to name",
      runner.apt_problems(APT_UPDATE["ok"][1] + APT_UPDATE["dupes"][1]), [])
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.BLOCKED_RETRIES = 3
    sim.apt_updates = ["locked", "dupes"]
    check("busy once, then a source listed twice: not a failure -> started",
          sim.start(), 0)
    check("  it waited once, then ran", sim.ran()[:3],
          ["apt-get update", "apt-get update", "confirm"])
with tempfile.TemporaryDirectory() as td, Sim(td, apt=False) as sim:
    check("no apt here: no check, still started", (sim.start(), sim.ran()[0]), (0, "confirm"))

print("\nTimeshift missing: installed after the y, before anything else")
with tempfile.TemporaryDirectory() as td, Sim(td, timeshift=False) as sim:
    check("exit 0", sim.start(), 0)
    check("order: check apt, ask, install, then hand off",
          sim.ran(), ["apt-get update", "confirm", "apt-get install timeshift"])
    r = sim.records()[0]
    check("the install is recorded", (r["step_id"], r["outcome"], r["fix_exit_code"],
                                      r["playbook_id"]),
          ("install", "installed", 0, "release-upgrade-to-23.04/install"))
    check("then staged, with the service installed",
          (staged(sim), runner.PROCEDURE_UNIT_PATH.exists()), (True, True))
    sim.boot_until_stopped()
    check("and the snapshot uses it", sim.ran()[3:], ["snapshot", "run:lunar", "reboot"])
    check("records: install, then the upgrade", sim.outcomes(),
          [("install", "installed"), ("to-23.04", "healed")])
with tempfile.TemporaryDirectory() as td, Sim(td, timeshift=False) as sim:
    sim.answer = False
    sim.start()
    check("declined: nothing installed",
          [e for e in sim.events if e.startswith("apt-get install")], [])
for label, result, want_outcome in [("apt fails", "fail", "install_failed"),
                                    ("apt exits 0 but Timeshift is still missing",
                                     "noop", "install_failed"),
                                    ("the package manager stays busy", "locked", "blocked")]:
    with tempfile.TemporaryDirectory() as td, Sim(td, timeshift=False) as sim:
        runner.BLOCKED_RETRIES = 2
        sim.installs = [result] * 2
        check(f"{label} -> 8", sim.start(), 8)
        check(f"  {label}: recorded as {want_outcome}, nothing staged",
              (sim.outcomes(), staged(sim), runner.PROCEDURE_UNIT_PATH.exists()),
              ([("install", want_outcome)], False, False))
with tempfile.TemporaryDirectory() as td, Sim(td, timeshift=False) as sim:
    sim.installs = ["noop"]
    sim.start()
    check("exit 0 without Timeshift says so", sim.records()[0]["verify_error"],
          "apt exited 0, but timeshift is still not found")

print("\nThe y/N screen names what it installs and what the snapshot leaves out")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.confirm_procedure = sim.saved["confirm_procedure"]
    snap = {"device": "/dev/sda4"}
    rc = sim.call(runner.confirm_procedure, UPGRADE, [(UPGRADE["steps"][0], False)],
                  "ubuntu", None, snap, ["timeshift"])
    out = " ".join(sim.output.split())
    check("y answers yes", rc, True)
    check("install line", "install : timeshift, with apt, before anything else" in out, True)
    check("snapshot leaves out /home and /boot/efi", "(not /home, not /boot/efi)" in out, True)
    check("says a cut-off waits for the person", "it waits for you" in out, True)
    check("says it installs first", "it installs timeshift, then runs by itself" in out, True)

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
    check("says to keep the machine on", "keep the machine on" in sim.output, True)
    check("a reserve set aside in the state directory, its full size",
          (runner.reserve_path().parent == d, runner.reserve_path().stat().st_size),
          (True, runner.RESERVE_BYTES))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    def no_room():
        raise OSError(28, "No space left on device")
    runner.make_reserve = no_room
    check("no room for the reserve -> 10", sim.start(), 10)
    check("  no state, no service, no reserve; it says why",
          (sim.state(), runner.PROCEDURE_UNIT_PATH.exists(), runner.reserve_path().exists(),
           "could not set aside" in sim.output), (None, False, False, True))

print("\nOne visit, 22.10 to 23.04 with --take-snapshot")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.start()
    boots = sim.boot_until_stopped()
    check("2 runs of the service: the hand-off, then the boot after the upgrade", boots, 2)
    check("order of events", sim.ran(),
          ["apt-get update", "confirm", "snapshot", "run:lunar", "reboot"])
    check("machine ends on lunar", sim.machine["codename"], "lunar")
    check("records", sim.outcomes(), [("to-23.04", "healed")])
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
    check("the reserve is gone at the end", runner.reserve_path().exists(), False)
    cfg = json.loads(runner.TIMESHIFT_CONF.read_text(encoding="utf-8"))
    check("Timeshift config created with the engine's excludes",
          (cfg["exclude"], cfg["btrfs_mode"], cfg["backup_device_uuid"]),
          (runner.SNAPSHOT_EXCLUDES, "false", "uuid-1"))

print("\nFour visits, 22.10 to 26.04: one upgrade each, each with its own snapshot")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    for pid in UPGRADE_IDS:
        proc, path = UPGRADES[pid]
        check(f"{pid}: started", sim.start(proc=proc, path=path), 0)
        check(f"{pid}: done after one boot, service removed",
              (sim.boot_until_stopped(), sim.state()["phase"],
               runner.PROCEDURE_UNIT_PATH.exists()), (2, "done", False))
    check("machine ends on resolute", sim.machine["codename"], "resolute")
    check("each visit: apt checked, a snapshot, one upgrade, one reboot", sim.ran(),
          ["apt-get update", "confirm", "snapshot", "run:lunar", "reboot",
           "apt-get update", "confirm", "snapshot", "run:mantic", "reboot",
           "apt-get update", "confirm", "snapshot", "run:noble", "reboot",
           "apt-get update", "confirm", "snapshot", "run:resolute", "reboot"])
    check("records", sim.outcomes(), [("to-23.04", "healed"), ("to-23.10", "healed"),
                                      ("to-24.04", "healed"), ("to-26.04", "healed")])
    check("the last visit's snapshot is of 24.04",
          sim.state()["engine_snapshot"]["fingerprint"].split()[0], "noble")
with tempfile.TemporaryDirectory() as td, Sim(td, codename="lunar") as sim:
    check("the wrong visit refuses: 24.04 on a 23.04 machine -> 5",
          sim.start(*UPGRADES["release-upgrade-to-24.04"]), 5)
    check("  and nothing was staged or asked", (staged(sim), "confirm" in sim.events),
          (False, False))

print("\nVisit 4: waiting updates installed, and the machine rebooted, before the upgrade")
P26 = UPGRADES["release-upgrade-to-26.04"]
with tempfile.TemporaryDirectory() as td, Sim(td, codename="noble") as sim:
    check("nothing waiting: started", sim.start(*P26), 0)
    check("  done after one boot", sim.boot_until_stopped(), 2)
    check("  only the upgrade ran", sim.ran(),
          ["apt-get update", "confirm", "snapshot", "run:resolute", "reboot"])
    check("  records", sim.outcomes(), [("to-26.04", "healed")])
    check("  the updates step is noted as already done", sim.state()["history"][0],
          {"step": "updates", "outcome": "already done"})
with tempfile.TemporaryDirectory() as td, Sim(td, codename="noble") as sim:
    sim.machine.update(pending=3, kernel_pending=True)
    check("3 updates waiting, a kernel among them: started", sim.start(*P26), 0)
    check("  3 runs of the service: the hand-off, after the updates, after the upgrade",
          sim.boot_until_stopped(), 3)
    check("  the updates, a reboot, then the upgrade and its reboot", sim.ran(),
          ["apt-get update", "confirm", "snapshot", "run:updates", "reboot",
           "run:resolute", "reboot"])
    check("  records", sim.outcomes(), [("updates", "healed"), ("to-26.04", "healed")])
    r = sim.records()[0]
    check("  the updates step: 3 waiting before, none after its reboot",
          (r["detect_before"], r["verify_after"]), (3, 0))
    check("  machine on 26.04", sim.machine["codename"], "resolute")
with tempfile.TemporaryDirectory() as td, Sim(td, codename="noble") as sim:
    sim.machine["reboot_pending"] = True     # say the automatic updates put in a kernel
    sim.start(*P26)
    sim.boot_until_stopped()
    check("only a reboot waiting: the updates step reboots, then the upgrade",
          (sim.outcomes(), sim.ran()[3:]),
          ([("updates", "healed"), ("to-26.04", "healed")],
           ["run:updates", "reboot", "run:resolute", "reboot"]))
with tempfile.TemporaryDirectory() as td, Sim(td, codename="mantic") as sim:
    # 23.10 gets no updates any more, so this cannot happen on it. It shows what
    # one step does where updates still come: the full-upgrade puts in a kernel,
    # and do-release-upgrade then refuses, on every attempt.
    sim.machine.update(pending=2, kernel_pending=True)
    sim.start(*UPGRADES["release-upgrade-to-24.04"])
    sim.boot_until_stopped()
    check("one step, a kernel among the updates: do-release-upgrade refuses, restored",
          (sim.outcomes(), sim.records()[0]["failure"]),
          ([("to-24.04", "rolled_back")], "fix_failed"))
with tempfile.TemporaryDirectory() as td, Sim(td, codename="mantic") as sim:
    sim.machine["pending"] = 2
    check("26.04 on a 23.10 machine with updates waiting: refused, nothing installed -> 5",
          (sim.start(*P26), [e for e in sim.events if e.startswith("run:")]), (5, []))

print("\nThe upgrade fails: restore, reboot, verify the machine is back")
with tempfile.TemporaryDirectory() as td, Sim(td, codename="lunar") as sim:
    sim.step_results["mantic"] = ["fail-after-change"]
    sim.start(*UPGRADES["release-upgrade-to-23.10"])
    sim.boot_until_stopped()
    check("events", sim.ran(),
          ["apt-get update", "confirm", "snapshot", "run:mantic", "restore", "reboot -f"])
    check("records", sim.outcomes(), [("to-23.10", "rolled_back")])
    r = sim.records()[0]
    check("record keeps why it failed", (r["failure"], r["fix_exit_code"]), ("fix_failed", 1))
    check("record: restore method and result",
          (r["rollback_method"], r["rollback_result"].startswith("ok: back to lunar")),
          ("timeshift", True))
    check("machine back on lunar, as before", sim.fingerprint(), "lunar packages:1 markers:[]")
    st = sim.state()
    check("state: failed, says it was restored",
          (st["phase"], "Restored Timeshift snapshot" in st["stopped_because"]), ("failed", True))
    check("service removed, reserve freed",
          (runner.PROCEDURE_UNIT_PATH.exists(), runner.reserve_path().exists()), (False, False))

print("\nThe disk fills up mid-step: the reserve goes first, so the restore can start")
with tempfile.TemporaryDirectory() as td, Sim(td, codename="lunar") as sim:
    sim.step_results["mantic"] = ["disk-full"]
    sim.start(*UPGRADES["release-upgrade-to-23.10"])
    check("the reserve is there while the step runs", runner.reserve_path().exists(), True)
    sim.boot_until_stopped()
    check("events: the step, then the restore and its reboot", sim.ran(),
          ["apt-get update", "confirm", "snapshot", "run:mantic", "restore", "reboot -f"])
    check("no write failed: the reserve was freed before the first one",
          [e for e in sim.events if e.startswith("write failed")], [])
    check("records: restored, the machine checked", sim.outcomes(), [("to-23.10", "rolled_back")])
    check("it says it freed the reserve", "freed the engine's" in sim.all_output, True)
with tempfile.TemporaryDirectory() as td, Sim(td, codename="lunar") as sim:
    sim.step_results["mantic"] = ["disk-full-hard"]
    sim.start(*UPGRADES["release-upgrade-to-23.10"])
    argv, sys.argv = sys.argv, ["runner.py", "--resume"]
    out = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = runner.main()
    finally:
        sys.argv = argv
    check("still no room after that: --resume exits 0, the engine stops", rc, 0)
    check("  nothing restored; the reserve and the service gone; a person is needed",
          ("restore" in sim.events, runner.reserve_path().exists(),
           runner.PROCEDURE_UNIT_PATH.exists(), "engine itself failed" in out.getvalue()),
          (False, False, False, True))

print("\nWhat a failed step left running: stopped before the restore, and before the reserve goes")
# Run F on the VM (2026-10-10): the upgrader exited while its install process
# and a dpkg ran on into the restore.
with tempfile.TemporaryDirectory() as td, Sim(td, codename="lunar") as sim:
    sim.step_results["mantic"] = ["disk-full"]
    sim.leftovers = ["2719 python3", "7903 dpkg"]
    sim.start(*UPGRADES["release-upgrade-to-23.10"])
    sim.boot_until_stopped()
    check("stopped after the step, before the restore",
          [e for e in sim.events if e in ("run:mantic", "stop leftovers", "restore")],
          ["run:mantic", "stop leftovers", "restore"])
    check("  while the reserve was still set aside, so the room it frees is the restore's",
          sim.reserve_at_stop, True)
    r = sim.records()[0]
    check("  the record names them, and the restore went on as before",
          (r["leftovers_stopped"], r["outcome"]),
          (["2719 python3", "7903 dpkg"], "rolled_back"))
    check("  it says so", "stopped what the step left running: 2719 python3, 7903 dpkg"
          in sim.all_output, True)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["leave"] = ["fail"]
    sim.leftovers = ["4321 python3"]
    sim.start(LEFTOVER, LEFTOVER_PATH)
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("the VM fixture for it (Run G): restored, the leftover named",
          (sim.ran(), r["step_id"], r["outcome"], r["leftovers_stopped"]),
          (["apt-get update", "confirm", "snapshot", "run:leave", "restore", "reboot -f"],
           "leave", "rolled_back", ["4321 python3"]))
with tempfile.TemporaryDirectory() as td, Sim(td, codename="lunar") as sim:
    sim.step_results["mantic"] = ["fail-after-change"]
    sim.start(*UPGRADES["release-upgrade-to-23.10"])
    sim.boot_until_stopped()
    check("nothing left running: looked for, none named, nothing said",
          ("stop leftovers" in sim.events, sim.records()[0]["leftovers_stopped"],
           "stopped what the step left running" in sim.all_output), (True, None, False))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["timeout"]
    sim.start()
    sim.boot_until_stopped()
    check("a step stopped at its time limit: the same, before its restore",
          [e for e in sim.events if e in ("stop leftovers", "restore")],
          ["stop leftovers", "restore"])
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["fail"]
    sim.leftovers = ["4242 dpkg"]
    sim.start(snapshot="repos-fixed", take=False)
    sim.boot_until_stopped()
    check("no restore to follow (a named snapshot only): left to finish",
          ("stop leftovers" in sim.events, sim.records()[0]["leftovers_stopped"]),
          (False, None))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.BLOCKED_RETRIES = 3
    sim.step_results["lunar"] = ["locked"] * 3
    sim.start()
    sim.boot_until_stopped()
    check("a step that never started (blocked): nothing stopped",
          "stop leftovers" in sim.events, False)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.start()
    sim.boot_until_stopped()
    check("a step that succeeds: nothing stopped, and the record says null",
          ("stop leftovers" in sim.events, sim.records()[0]["leftovers_stopped"]),
          (False, None))

print("\nstop_leftovers: only the engine's service, never the engine itself")
CGROUP_NAMES = ("CGROUP_SELF", "CGROUP_ROOT", "PROC_ROOT", "time")
SERVICE = f"0::/system.slice/{runner.PROCEDURE_UNIT}\n"


def leftover_machine(td, group, pids, names):
    """Fake /proc and /sys/fs/cgroup in td. Returns the kill() to pass, and
    the list of pids it was called with. Killing a pid takes it out of the
    group; a pid listed in `spawns` starts another just before it goes."""
    root = Path(td)
    runner.CGROUP_SELF = root / "self-cgroup"
    runner.CGROUP_ROOT = root / "cgroup"
    runner.PROC_ROOT = root / "proc"
    runner.time = SimpleNamespace(sleep=lambda s: None)
    procs = None
    if group is not None:
        runner.CGROUP_SELF.write_text(group)
    if group and group.startswith("0::"):
        procs = runner.CGROUP_ROOT / group[3:].strip().lstrip("/") / "cgroup.procs"
        procs.parent.mkdir(parents=True)
        procs.write_text("".join(f"{p}\n" for p in pids))
    for pid, name in names.items():
        (runner.PROC_ROOT / str(pid)).mkdir(parents=True)
        (runner.PROC_ROOT / str(pid) / "comm").write_text(name + "\n")
    killed, spawns, gone = [], {}, set()

    def kill(pid):
        if pid in gone:
            raise ProcessLookupError(3, "No such process")
        killed.append(pid)
        left = [int(p) for p in procs.read_text().split() if int(p) != pid]
        left += spawns.get(pid, [])
        procs.write_text("".join(f"{p}\n" for p in left))
    kill.spawns, kill.gone = spawns, gone
    return kill, killed


saved_cgroup = {n: getattr(runner, n) for n in CGROUP_NAMES}
try:
    me = os.getpid()
    with tempfile.TemporaryDirectory() as td:
        kill, killed = leftover_machine(td, SERVICE, [me, 2719, 7903],
                                        {2719: "python3", 7903: "dpkg", 8000: "dpkg-deb"})
        kill.spawns[2719] = [8000]
        stopped = runner.stop_leftovers(kill)
        check("in the service: every other process stopped, the engine left alone",
              (sorted(killed), me in killed), ([2719, 7903, 8000], False))
        check("  one started while stopping is caught on the next look; each named",
              stopped, ["2719 python3", "7903 dpkg", "8000 dpkg-deb"])
    with tempfile.TemporaryDirectory() as td:
        kill, killed = leftover_machine(td, SERVICE, [me, 2719, 5000, 7903], {7903: "dpkg"})
        kill.gone.add(2719)
        check("one already gone: not named; one whose name cannot be read: '?'",
              runner.stop_leftovers(kill), ["5000 ?", "7903 dpkg"])
    with tempfile.TemporaryDirectory() as td:
        kill, killed = leftover_machine(td, "0::/user.slice/user-1000.slice/session-3.scope\n",
                                        [me, 4000], {4000: "bash"})
        check("in a person's session: nothing stopped", (runner.stop_leftovers(kill), killed),
              ([], []))
    with tempfile.TemporaryDirectory() as td:
        kill, killed = leftover_machine(td, None, [], {})
        check("no cgroup file (not Linux): nothing stopped", (runner.stop_leftovers(kill), killed),
              ([], []))
    with tempfile.TemporaryDirectory() as td:
        kill, killed = leftover_machine(td, "1:name=systemd:/system.slice/x.service\n", [], {})
        check("no cgroup v2 line: nothing stopped", (runner.stop_leftovers(kill), killed),
              ([], []))
finally:
    for _n, _v in saved_cgroup.items():
        setattr(runner, _n, _v)

print("\ndpkg's two locks: the frontend's, then dpkg's own; neither kept if one is busy")


class FakeLocks:
    """os.open/os.close and fcntl.lockf, for try_package_manager_lock only."""

    def __init__(self, busy):
        self.busy, self.opened, self.locked, self.closed = busy, {}, [], []

    def __getattr__(self, name):                 # everything else is the real os
        return getattr(os, name)

    def open(self, path, flags, mode=0o777):
        fd = 100 + len(self.opened)
        self.opened[fd] = Path(path).name
        return fd

    def close(self, fd):
        self.closed.append(self.opened[fd])

    def lockf(self, fd, op):
        if self.opened[fd] in self.busy:
            raise BlockingIOError(11, "Resource temporarily unavailable")
        self.locked.append(self.opened[fd])


saved_locks = {n: getattr(runner, n) for n in ("os", "DPKG_FRONTEND_LOCK", "DPKG_LOCK")}
saved_fcntl = sys.modules.get("fcntl")
try:
    with tempfile.TemporaryDirectory() as td:
        runner.DPKG_FRONTEND_LOCK = Path(td) / "lock-frontend"
        runner.DPKG_LOCK = Path(td) / "lock"
        for busy, want in [((), ([100, 101], ["lock-frontend", "lock"], [])),
                           (("lock",), (None, ["lock-frontend"], ["lock", "lock-frontend"])),
                           (("lock-frontend",), (None, [], ["lock-frontend"]))]:
            fake = FakeLocks(busy)
            runner.os = fake
            sys.modules["fcntl"] = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, lockf=fake.lockf)
            got = runner.try_package_manager_lock()
            check(f"busy: {', '.join(busy) or 'neither'} -> what it returns, what it "
                  "locked, what it let go", (got, fake.locked, fake.closed), want)
            if got:
                runner.release_package_manager(got)
                check("  letting go closes both", fake.closed, ["lock-frontend", "lock"])
finally:
    for _n, _v in saved_locks.items():
        setattr(runner, _n, _v)
    if saved_fcntl is None:
        sys.modules.pop("fcntl", None)
    else:
        sys.modules["fcntl"] = saved_fcntl

print("\nThe upgrader exits 0 but nothing changed: caught after the reboot, restored")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["noop"]
    sim.start()
    sim.boot_until_stopped()
    check("records", sim.outcomes(), [("to-23.04", "rolled_back")])
    check("failure", sim.records()[0]["failure"], "verify_failed")

print("\nPower cut mid-upgrade: the boot after restores nothing and waits for a person")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.power_cut_on = "lunar"
    sim.start()
    sim.boot_until_stopped()
    check("the upgrade ran once", sim.events.count("run:lunar"), 1)
    check("nothing restored", "restore" in sim.events, False)
    st = sim.state()
    check("state: cut off, the step's failure noted",
          (st["phase"], st["record"]["failure"], st["record"]["step_id"]),
          ("cut_off", "interrupted", "to-23.04"))
    check("no record yet: its outcome is not decided", sim.records(), [])
    check("service removed, so no later boot acts", runner.PROCEDURE_UNIT_PATH.exists(), False)
    check("reserve freed, for the restore the person may ask for",
          runner.reserve_path().exists(), False)
    check("the boot's message says how to go on",
          ("run the same --run command again" in sim.output, "--cancel" in sim.output),
          (True, True))
    sim.boot_until_stopped()
    check("a boot that runs it anyway does nothing",
          (sim.state()["phase"], "restore" in sim.events), ("cut_off", False))
    sim.call(runner.procedure_status)
    check("--status says nothing was undone, and how to go on",
          ("nothing was undone" in sim.output, "run the same --run command again" in sim.output),
          (True, True))

    sim.ask_answer = False
    check("--run again, the person says no -> 0", sim.start(), 0)
    check("  nothing done: still cut off, no service, not even an apt check",
          (sim.state()["phase"], runner.PROCEDURE_UNIT_PATH.exists(),
           sim.ran().count("apt-get update")), ("cut_off", False, 1))
    check("  the question was the restore", sim.asked, ["Restore it now? [y/N] "])

    sim.ask_answer = True
    check("--run again, the person says yes -> 0", sim.start(), 0)
    check("  restore asked for, handed to the service",
          (sim.state()["phase"], runner.PROCEDURE_UNIT_PATH.exists()), ("restore_asked", True))
    check("  nothing restored in the foreground", "restore" in sim.events, False)
    sim.service_active = True
    check("--run while the service restores: refused (6)", sim.start(), 6)
    sim.service_active = False
    boots = sim.boot_until_stopped()
    check("the service restores, reboots, and the boot after checks", boots, 2)
    check("records: one, rolled back", sim.outcomes(), [("to-23.04", "rolled_back")])
    r = sim.records()[0]
    check("record: interrupted, restored on request",
          (r["failure"], r["rollback_method"], bool(r.get("rollback_requested"))),
          ("interrupted", "timeshift", True))
    check("state: failed, says it was cut off and restored",
          (sim.state()["phase"], "went down" in sim.state()["stopped_because"],
           "Restored Timeshift snapshot" in sim.state()["stopped_because"]),
          ("failed", True, True))
    check("service removed", runner.PROCEDURE_UNIT_PATH.exists(), False)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.power_cut_on = "lunar"
    sim.start()
    sim.boot_until_stopped()
    check("--cancel on a cut-off step: 0", sim.call(runner.cancel_procedure), 0)
    r = sim.records()[0]
    check("  recorded as interrupted, nothing restored",
          (r["outcome"], r["rollback_result"].startswith("not attempted: cancelled by hand"),
           "restore" in sim.events), ("interrupted", True, False))
    check("  state cancelled; the snapshot named for a restore by hand",
          (sim.state()["phase"], "is kept" in sim.output), ("cancelled", True))
    check("a new --run after that starts normally",
          (sim.start(), sim.state()["phase"]), (0, "pending"))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.power_cut_on = "lunar"
    sim.start(snapshot="repos-fixed", take=False)
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("cut off with only a named snapshot: nothing to offer, stops at once",
          (sim.state()["phase"], r["outcome"], "repos-fixed" in r["rollback_result"]),
          ("failed", "interrupted", True))

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
    sim.step_results["lunar"] = ["fail-after-change"]
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
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.create_ok = False
    sim.start()
    sim.boot_until_stopped()
    check("no snapshot: nothing written to disk, the lock let go",
          ("sync" in sim.events, sim.lock_held), (False, False))

print("\napt's lock: held from the fingerprint to the end of the snapshot, and through the restore")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["three"] = ["timeout"]
    sim.start(FIXTURE, FIXTURE_PATH)
    sim.boot_until_stopped()
    check("records: one, two healed; three restored", sim.outcomes(),
          [("one", "healed"), ("two", "healed"), ("three", "rolled_back")])
    order = [e for e in sim.events if e in ("lock", "unlock", "fingerprint", "fingerprint (locked)",
                                            "snapshot", "sync", "run:one")][:6]
    check("lock, fingerprint, snapshot, written to disk, unlock, then step one", order,
          ["lock", "fingerprint (locked)", "snapshot", "sync", "unlock", "run:one"])
    i = sim.events.index("restore")
    check("the restore ran holding the lock, and kept it into the reboot",
          (sim.under_lock, sim.events[i - 1], "unlock" in sim.events[i:]),
          ({"snapshot": True, "restore": True}, "lock", False))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.lock_tries = ["busy", "busy"]
    sim.start()
    sim.boot_until_stopped()
    check("taken by another program twice: waited twice, then the snapshot and the upgrade",
          (sim.events.count("lock busy"), sim.events.count(f"sleep {runner.BLOCKED_WAIT}"),
           sim.outcomes()), (2, 2, [("to-23.04", "healed")]))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.BLOCKED_RETRIES = 3
    sim.lock_tries = ["busy"] * 3
    sim.start()
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("never free: no snapshot and no step, recorded as blocked",
          (r["step_id"], r["outcome"], "snapshot" in sim.events,
           [e for e in sim.events if e.startswith("run:")]), ("snapshot", "blocked", False, []))
    check("  state failed, says to start again later; service removed",
          (sim.state()["phase"], "Start the procedure again later" in sim.state()["stopped_because"],
           runner.PROCEDURE_UNIT_PATH.exists()), ("failed", True, False))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    runner.BLOCKED_RETRIES = 3
    sim.step_results["lunar"] = ["fail"]
    sim.lock_tries = ["free"] + ["busy"] * 3
    sim.start()
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("never free for the restore: not started, rollback_failed, snapshot untouched",
          (r["outcome"], r["rollback_result"].startswith("not started"), "restore" in sim.events),
          ("rollback_failed", True, False))
    check("  says to restore it by hand", "restore it with Timeshift by hand"
          in sim.state()["stopped_because"], True)
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["lunar"] = ["fail"]
    sim.lock_tries = ["free", "cut"]
    sim.start()
    sim.boot_until_stopped()
    check("a power cut while waiting to restore: nothing restored, held for a person",
          (sim.state()["phase"], "restore" in sim.events, sim.records()), ("cut_off", False, []))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.cut_on_sync = True
    sim.start()
    sim.boot_until_stopped()
    check("a power cut while the snapshot is written to disk: stops, no step ran",
          (sim.state()["phase"], "snapshot was being taken" in sim.state()["stopped_because"],
           [e for e in sim.events if e.startswith("run:")]), ("failed", True, []))

print("\nThe VM fixture, through the same harness")
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.step_results["three"] = ["timeout"]
    sim.start(proc=FIXTURE, path=FIXTURE_PATH, take=False)
    sim.boot_until_stopped()
    check("without a snapshot: one, two healed; three stopped at its limit",
          sim.outcomes(), [("one", "healed"), ("two", "healed"), ("three", "fix_failed")])
    check("markers left", sorted(sim.machine["markers"]), ["one", "two"])
with tempfile.TemporaryDirectory() as td, Sim(td, timeshift=False) as sim:
    sim.step_results["three"] = ["timeout"]
    sim.start(proc=FIXTURE, path=FIXTURE_PATH, take=True)
    sim.boot_until_stopped()
    check("with --take-snapshot, Timeshift installed first: three rolled back",
          sim.outcomes(), [("install", "installed"), ("one", "healed"), ("two", "healed"),
                           ("three", "rolled_back")])
    check("markers gone", sorted(sim.machine["markers"]), [])
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.power_cut_on = "three"
    sim.start(proc=FIXTURE, path=FIXTURE_PATH, take=True)
    sim.boot_until_stopped()
    check("cut off at three: waits, markers one and two still there",
          (sim.state()["phase"], sorted(sim.machine["markers"])), ("cut_off", ["one", "two"]))
    sim.start(proc=FIXTURE, path=FIXTURE_PATH, take=True)
    sim.boot_until_stopped()
    check("restored on request: markers gone",
          (sim.outcomes()[-1], sorted(sim.machine["markers"])),
          (("three", "rolled_back"), []))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    sim.power_cut_on = "long"
    sim.start(proc=CUTOFF, path=CUTOFF_PATH, take=True)
    sim.boot_until_stopped()
    check("cut-off fixture, powered off mid-step: waits, `started` left, nothing recorded",
          (sim.state()["phase"], sorted(sim.machine["markers"]), sim.records()),
          ("cut_off", ["started"], []))
    sim.ask_answer = False
    sim.start(proc=CUTOFF, path=CUTOFF_PATH, take=True)
    check("  n to the restore: nothing done", (sim.state()["phase"], "restore" in sim.events),
          ("cut_off", False))
    sim.ask_answer = True
    sim.start(proc=CUTOFF, path=CUTOFF_PATH, take=True)
    sim.boot_until_stopped()
    r = sim.records()[0]
    check("  y: restored, `started` gone, rolled_back after interrupted",
          (sorted(sim.machine["markers"]), r["step_id"], r["outcome"], r["failure"]),
          ([], "long", "rolled_back", "interrupted"))

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

print("\nmain(): --run names an upgrade that does not apply; the one that does is named")
with tempfile.TemporaryDirectory() as td, Sim(td, codename="kinetic") as sim:
    argv, sys.argv = sys.argv, ["runner.py", "--procedures", str(PROC_DIR), "--distro", "ubuntu",
                                "--os", "linux", "--run", "release-upgrade-to-24.04",
                                "--take-snapshot"]
    try:
        rc = sim.call(runner.main)
    finally:
        sys.argv = argv
    check("refused -> 5", rc, 5)
    check("  names release-upgrade-to-23.04",
          "this applies now: release-upgrade-to-23.04" in sim.output, True)

print("\nThe engine's own Python packages: installed with apt, after a y")
for missing in (["python3-jsonschema"], ["python3-yaml", "python3-jsonschema"]):
    with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
        runner.Draft7Validator = None
        if "python3-yaml" in missing:
            runner.yaml = None
        check(f"{' + '.join(missing)} missing: installed -> True",
              sim.call(runner.ensure_engine_deps, "ubuntu"), True)
        check("  order: check apt, ask, install", sim.ran(),
              ["apt-get update", "ask", "apt-get install " + " ".join(missing)])
        check("  imported afterwards, no restart",
              (runner.Draft7Validator is Draft7Validator, runner.yaml is yaml), (True, True))
        check("  recorded", (sim.records()[0]["playbook_id"], sim.records()[0]["outcome"]),
              ("engine/install", "installed"))
for label, sim_kw, setup, want_events, want_text in [
        ("without sudo: says how, changes nothing", dict(privileged=False), None, [],
         "sudo apt-get install python3-jsonschema"),
        ("without apt: says how, changes nothing", dict(apt=False), None, [],
         "pip install pyyaml jsonschema"),
        ("a dead source: stops before asking", {}, ("apt_updates", ["no-release"]),
         ["apt-get update"], "does not have a Release file"),
        ("the person says no: nothing installed", {}, ("ask_answer", False),
         ["apt-get update", "ask"], "Nothing was installed"),
        ("apt fails: says so", {}, ("installs", ["fail"]),
         ["apt-get update", "ask", "apt-get install python3-jsonschema"], "could not install")]:
    with tempfile.TemporaryDirectory() as td, Sim(td, **sim_kw) as sim:
        runner.Draft7Validator = None
        if setup:
            setattr(sim, *setup)
        check(f"{label} -> False", sim.call(runner.ensure_engine_deps, "ubuntu"), False)
        check(f"  {label.split(':')[0]}: events and message",
              (sim.ran(), want_text in sim.output), (want_events, True))
with tempfile.TemporaryDirectory() as td, Sim(td) as sim:
    check("nothing missing: nothing done -> True",
          (sim.call(runner.ensure_engine_deps, "ubuntu"), sim.ran()), (True, []))

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
    # A machine an older engine ran on has its five, not the upgrader's logs.
    older = runner.SNAPSHOT_EXCLUDES[:5]
    runner.TIMESHIFT_CONF.write_text(json.dumps({"btrfs_mode": "false", "exclude": older}))
    runner.prepare_timeshift_config("uuid-1")
    check("an older engine's five kept, the upgrader's logs added after them",
          json.loads(runner.TIMESHIFT_CONF.read_text())["exclude"],
          older + ["/var/log/dist-upgrade/***"])
    runner.TIMESHIFT_CONF.write_text(json.dumps({"btrfs_mode": "true"}))
    try:
        runner.prepare_timeshift_config("uuid-1")
        check("btrfs mode refused", "no error", "RuntimeError")
    except RuntimeError:
        check("btrfs mode refused", "RuntimeError", "RuntimeError")

print("\nThe excludes cover exactly what a restore must not touch")
check("state dir, log dir, unit, enable link, the EFI partition, the upgrader's logs",
      runner.SNAPSHOT_EXCLUDES, [
    f"{runner.PROCEDURE_STATE_DIR.as_posix()}/***", f"{runner.PROCEDURE_LOG_DIR.as_posix()}/***",
    runner.PROCEDURE_UNIT_PATH.as_posix(),
    f"/etc/systemd/system/multi-user.target.wants/{runner.PROCEDURE_UNIT}",
    "/boot/efi/***", "/var/log/dist-upgrade/***"])
check("the reserve is in the state directory, so the snapshot leaves it out",
      f"{runner.reserve_path().parent.as_posix()}/***" in runner.SNAPSHOT_EXCLUDES, True)
check("create passes no --tags: Timeshift 22.06.5 refuses O",
      runner.snapshot_create_cmd({"device": "/dev/sda2", "comment": "C"}),
      ["timeshift", "--create", "--rsync", "--snapshot-device", "/dev/sda2",
       "--comments", "C", "--scripted"])
check("restore names the GRUB disk and answers yes",
      runner.snapshot_restore_cmd({"name": "N", "device": "/dev/sda2", "grub_device": "/dev/sda"}),
      ["timeshift", "--restore", "--snapshot", "N", "--target-device", "/dev/sda2",
       "--grub-device", "/dev/sda", "--scripted", "--yes"])

print("\nrun_logged hands back this run's output only, never an earlier attempt's")
with tempfile.TemporaryDirectory() as td:
    log = Path(td) / "step.log"
    lock = "E: Could not get lock /var/lib/dpkg/lock-frontend"
    code, tail = runner.run_logged([sys.executable, "-c", f"print({lock!r})"], 60, log)
    check("first attempt: the lock message", (code, lock in tail), (0, True))
    code, tail = runner.run_logged(
        [sys.executable, "-c", "import sys; print('a real failure'); sys.exit(3)"], 60, log)
    check("second attempt: its own failure, no lock message from the first",
          (code, "a real failure" in tail, runner.is_lock_contention(tail)), (3, True, False))
    check("the file still keeps both", log.read_text(encoding="utf-8").count("running:"), 2)
    code, tail = runner.run_logged([sys.executable, "-c", "print('x' * 20000)"], 60, log)
    check("a long output: the last 8 KB by default", len(tail), 8192)
    code, tail = runner.run_logged([sys.executable, "-c", "print('x' * 20000)"], 60, log, 1 << 20)
    check("  all of it when asked", tail.count("x"), 20000)

if FAILURES:
    print(f"\n{len(FAILURES)} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")
