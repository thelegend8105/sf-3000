# procedure-proof — VM runs for the procedure machinery

**Runs A to D passed on the 22.10 VM on 2026-10-08 and 2026-10-09.** The
records are in `evidence/ubuntu-22.10-2026-10-09.jsonl`, with notes in
`evidence/README.md`. **Runs B and D passed again on 2026-10-10**, after the
engine began to hold dpkg's lock and name every dead source. That Run B had
the lock held by another program first (below). Its records are in
`evidence/ubuntu-22.10-2026-10-10.jsonl`. `test_procedure.py` passes
offline too (stubbed machine, no VM; on Python 3.12 and 3.10). These runs
prove the same machinery on a real machine, with fixtures that take minutes,
before any real upgrade relies on it. Runs E and F, at the end, fail a real
upgrade on purpose. They have not run yet.

Two fixtures leave marker files in `/var/lib/sf3000-proof/`.

`procedure-proof.yaml` has three steps:

- **one** touches a marker, then the engine reboots and verifies after the boot.
- **two** touches a marker and is verified at once.
- **three** sleeps 5 minutes against a 1-minute limit. The engine must stop
  it, so marker `three` must never appear.

`cut-off-proof.yaml` has one step, **long**. It touches `started`, runs for
10 minutes, then touches `finished`. That leaves time to power the VM off in
the middle of it.

Four runs:

| Run | what it proves |
|-----|----------------|
| A | No snapshot: step three's failure stops the procedure. |
| B | `--take-snapshot`: the engine installs Timeshift itself, then restores its snapshot when step three fails. |
| C | A step cut off by a power-off: the boot after restores **nothing** and waits. Running the same command again offers the restore. |
| D | A dead apt source: the engine refuses before asking anything. |

Use the 22.10 VM from its `repos-fixed` snapshot. Timeshift must not be
installed yet, because Run B installs it, and that needs a working apt.

```bash
# 0. Setup (on Windows): restore repos-fixed, start the VM, ssh -p 2223 rht@127.0.0.1
#    Every login starts in ~, so begin each one with: cd ~/sf-3000
cd ~/sf-3000 && git pull && git log --oneline -1
python3 tests/procedure-proof/test_procedure.py | tail -1   # all checks passed
which timeshift || echo "no timeshift: good"
# Switch off the automatic updates, and wait until neither is running. They
# catch up within an hour of a boot. The engine holds apt's lock while it
# snapshots or restores, so they would only make it wait.
sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
systemctl is-active apt-daily.service apt-daily-upgrade.service   # inactive, twice

# --- Run A: no snapshot --------------------------------------------------
sudo python3 engine/runner.py --procedures tests/procedure-proof --run procedure-proof
# First "checking that apt can update from every source", then a tick.
# The box lists 3 steps, 1 reboot. Answer y. It prints "started" and returns.
journalctl -fu sf3000-procedure      # watch step one run; the VM then reboots

# After the reboot, ssh back in:
python3 engine/runner.py --status    # phase failed; stopped: step three ... time limit of 1 min
journalctl -b -u sf3000-procedure --no-pager -o short-iso
#   this boot: "back after rebooting for one", one verified, two verified,
#   three running, then stopped at its time limit
ls /var/lib/sf3000-proof/            # one two
systemctl status sf3000-procedure    # "could not be found": the service removed itself
sleep 300; ls /var/lib/sf3000-proof/ # still only one two: the sleep really was killed

# --- Run B: --take-snapshot, Timeshift installed by the engine -------------
sudo rm -rf /var/lib/sf3000-proof
sudo python3 engine/runner.py --procedures tests/procedure-proof --run procedure-proof --take-snapshot
# The box has an "install   : timeshift" line, and the snapshot line says
# "(not /home, not /boot/efi)". Answer y: "installing timeshift with apt"
# (apt's output goes to /var/log/sf3000/install.log, not the screen), then
# "installed timeshift", then "started".
journalctl -fu sf3000-procedure      # "taking a Timeshift snapshot" (minutes), then one, then a reboot

# The VM reboots TWICE: once after step one; then, after three fails,
# Timeshift restores and reboots by itself. Between the two, watch with
# journalctl -fu sf3000-procedure: the restore rolls those lines back out of
# the journal. After the second reboot, ssh back in:
python3 engine/runner.py --status    # stopped: ... Restored Timeshift snapshot <name>
ls /var/lib/sf3000-proof             # "No such file or directory": restored away
which timeshift                      # still there: installed before the snapshot
sudo timeshift --list                # the snapshot, comment "sf3000 procedure-proof ..."
sudo python3 -c 'import json; print(*json.load(open("/etc/timeshift/timeshift.json"))["exclude"], sep="\n")'
#   the engine's six, ending "/boot/efi/***" and "/var/log/dist-upgrade/***".
#   Timeshift may list its own after them, such as /home/rht/**: a first
#   snapshot rewrites this file.

# --- Run B with dpkg's lock held (as on 2026-10-10) -------------------------
# Optional: shows the engine wait for the lock, then hold it. Before the --run,
# in a second ssh session, hold the lock the way apt does, until Enter:
#   sudo python3 -c "import fcntl, os; fd = os.open('/var/lib/dpkg/lock-frontend', os.O_RDWR | os.O_CREAT, 0o640); fcntl.lockf(fd, fcntl.LOCK_EX); input('holding dpkg lock - press Enter to let go ')"
sudo apt-get check                   # refused: "It is held by process N (python3)"
# Then run Run B's --run line. The journal says "the package manager is busy —
# waiting 60s before the snapshot (attempt 2 of 30)". Press Enter in the
# second session. When "taking a Timeshift snapshot" appears, run
# sudo apt-get check there again: refused, and N is now the engine's own,
# the number in python3[N].

# --- Run C: cut off by a power-off ------------------------------------------
sudo python3 engine/runner.py --procedures tests/procedure-proof --run cut-off-proof --take-snapshot
# Answer y. Then wait for step long to start (after the snapshot, minutes):
journalctl -fu sf3000-procedure      # until "long: ... running"
ls /var/lib/sf3000-proof             # started
# Now, on Windows, power the VM off (not a shutdown):
#   E:\VirtualBox\VBoxManage.exe controlvm KineticServer poweroff
# Start it again, ssh back in:
python3 engine/runner.py --status    # phase cut_off; "nothing was undone: the engine waits for you"
ls /var/lib/sf3000-proof             # started, and no finished: nothing was undone
systemctl status sf3000-procedure    # "could not be found": the service removed itself
journalctl -b -u sf3000-procedure --no-pager -o short-iso
#   "step long was cut off ... Nothing was undone; the engine waits for a person"
sudo python3 engine/runner.py --procedures tests/procedure-proof --run cut-off-proof --take-snapshot
# It says step long was cut off and offers the restore. Answer n:
#   "Nothing was done". Run the same command again and answer y:
#   "restoring under the system service". The VM reboots by itself. ssh back in:
python3 engine/runner.py --status    # stopped: step long: the machine went down ... Restored Timeshift snapshot <name>
ls /var/lib/sf3000-proof             # "No such file or directory": restored away

# --- Run D: a dead apt source -------------------------------------------
printf 'deb http://sf3000-no-such-host.invalid/ubuntu kinetic main\ndeb http://old-releases.ubuntu.com/ubuntu sf3000-no-such-suite main\n' \
  | sudo tee /etc/apt/sources.list.d/sf3000-dead.list
sudo python3 engine/runner.py --procedures tests/procedure-proof --run procedure-proof --take-snapshot; echo "exit $?"
# exit 9: "apt cannot update from every source, so nothing was started",
# and both dead sources named: the missing suite (E: ... does not have a
# Release file) and the dead host (its Err: line). No box, no y/N, nothing
# staged.
python3 engine/runner.py --status    # still Run C's: nothing new was started
sudo rm /etc/apt/sources.list.d/sf3000-dead.list

# --- Save the evidence (on Windows, from the repo) --------------------------
#   scp -P 2223 rht@127.0.0.1:/var/log/sf3000/runs.jsonl evidence/ubuntu-22.10-<YYYY-MM-DD>.jsonl
#   (naming rules in evidence/README.md; add -2, -3 if the name is taken)
```

Expected records in `/var/log/sf3000/runs.jsonl`, 8 lines:

| Run | step    | outcome       | what else to check                              |
|-----|---------|---------------|-------------------------------------------------|
| A   | one     | `healed`      | `rebooted: true`, `verify_after: 1`             |
| A   | two     | `healed`      | `rebooted: false`                               |
| A   | three   | `fix_failed`  | `fix_exit_code: null` (stopped at the limit)    |
| B   | install | `installed`   | `fix_command` ends `apt-get -y install timeshift`, `fix_exit_code: 0` |
| B   | one     | `healed`      | `snapshot_taken: true`, `snapshot_id` = the Timeshift name |
| B   | two     | `healed`      |                                                 |
| B   | three   | `rolled_back` | `failure: fix_failed`, `rollback_method: timeshift`, `rollback_result: "ok: back to ..."` |
| C   | long    | `rolled_back` | `failure: interrupted`, `rollback_requested` set (the y), `rollback_result: "ok: back to ..."` |

Run D writes no record. It changed nothing, like the other refusals.

The 2026-10-09 file has 9 records, not 8. Run B's first try also wrote
`snapshot_failed`, when Timeshift refused `--tags O` (fixed since; DESIGN
§16). The 2026-10-10 file has 12: the same 9, then Run B's one, two and
three again. Timeshift was already installed by then, so there is no second
`install` record. To list the records on the VM:

```bash
python3 -c 'import json; [print(r.get("step_id"), r["outcome"], r.get("failure") or "", r.get("rollback_result") or "") for r in map(json.loads, open("/var/log/sf3000/runs.jsonl"))]'
```

Things to know when reading the output:

- **The procedure's records are not in `~/sf-3000/logs/`.** The service writes
  as root at boot, so it writes only to root-owned places:
  `/var/log/sf3000/runs.jsonl` for records, `/var/log/sf3000/<id>-<step>.log`
  for each step's output, plus `snapshot.log`, `restore.log`, `apt-check.log`
  and `install.log`.
- **After a restore, the journal forgets the run.** Timeshift rolls back
  `/var/log/journal` with everything else. `/var/log/sf3000/` is excluded from
  the snapshot, so the engine's own records survive. That is on purpose: it is
  how the boot after the restore knows what happened. Read the journal before
  answering y in Run C.
- **The engine adds six excludes to `/etc/timeshift/timeshift.json`.** They
  are its state, its logs, its service file and enable link, `/boot/efi`,
  and the release upgrader's logs (`/var/log/dist-upgrade`, since
  2026-10-10; the runs before had the first five). The engine changes nothing
  else in that file. Timeshift itself rewrites it in its own layout when it
  estimates the size of a first snapshot. The VM boots with BIOS, so it has
  no `/boot/efi`, and here that exclude only shows up in the file. The
  restore leaving a shared EFI partition alone needs an EFI VM.
- **Run A's step three reads `fix_failed`, not `interrupted`.** The time limit
  stopped it, and the engine was still running to record that. `interrupted`
  is for a machine that went down mid-step: Run C.
- **Two systemd lines look wrong but are not.** "Current command vanished
  from the unit file" (in yellow), and a "Started sf3000-procedure.service."
  after "Deactivated successfully". Both come from the engine deleting its own
  service file while the service is still running. The service does not
  start again.
- **apt's summary can leave a dead source out.** Once one source fails hard,
  such as a suite with no Release file, apt 2.5.3 prints no "Failed to fetch"
  line for any source. A dead host then appears only on an `Err:` line. The
  engine adds those lines; on 2026-10-09, before it did, Run D named only one
  source.
- **"the package manager is busy — waiting 60s before the snapshot"** (or
  "before the restore"): another program holds apt's lock. The engine holds
  that lock while it snapshots or restores, so it waits its turn first.
- **"writing the snapshot to disk"** comes after every snapshot. Timeshift
  does not flush its copy itself.
- **The journal can stop at "writing the snapshot to disk".** The snapshot's
  line, step one and the reboot can all come within one second. The reboot
  then closes the session before `journalctl` shows them. The records show
  that they ran.
- **`restore.log` can end in zero bytes.** Timeshift's restore ends in
  `reboot -f`, which can cut off the file's last write.
- **A restore resets the timestamp of `/home/rht`**, the folder itself.
  Timeshift leaves out what is inside a home folder, not the folder.
- **Not proven here:** the engine installing python3-jsonschema itself. A
  server VM has it already, because cloud-init depends on it. The lab's
  desktops do not, and neither will an EFI desktop VM.

After these runs comes the upgrade itself: the four procedures in
`candidates/procedures/`, one at a time, from `repos-fixed` with
`--take-snapshot`. Their README has the order.

## Runs E and F: a real upgrade that fails

Runs A to D fail a test step, on a machine the step has not really changed.
E and F fail a real upgrade: `release-upgrade-to-26.04`, on the 22.10 VM once
visits 1 to 3 have brought it to 24.04. `fill-disk.py` fills the root disk
at the moment each run needs. Not run yet.

| Run | what it proves |
|-----|----------------|
| E | The upgrader refuses: too little space, found before its download. It installs nothing and exits 1, and the engine restores its snapshot. |
| F | The disk fills up during the install, and dpkg fails partway. The engine frees its reserve, restores a half-upgraded machine, and checks that it is back. |

Both should end `rolled_back`, with the machine back on 24.04 as it was.

The upgrader measures free space as an ordinary user sees it. On ext4, root
can use a further 5% that users cannot. E fills only what users see, so the
upgrader refuses while the engine and Timeshift still have room. F fills it
all, for root too.

```bash
# 0. Setup. Room first: delete visit 3's Timeshift snapshot.
cd ~/sf-3000 && git pull && git log --oneline -1
python3 tests/procedure-proof/test_procedure.py | tail -1   # all checks passed
sudo timeshift --list                 # visit 3's: 2026-10-10_14-28-46
sudo timeshift --delete --snapshot 2026-10-10_14-28-46 --scripted
# Then a VirtualBox snapshot, to go back to if F breaks the VM:
sudo poweroff
#   (Windows) E:\VirtualBox\VBoxManage.exe snapshot KineticServer take at-24.04
#   Start the VM again and ssh back in.

# --- Run E: the upgrader refuses --------------------------------------------
# Second session first; it waits:
sudo python3 tests/procedure-proof/fill-disk.py before-check
# First session:
sudo python3 engine/runner.py --procedures candidates/procedures --run release-upgrade-to-26.04 --take-snapshot
# Answer y. The second session prints "filled ... MB" once to-26.04 starts.
journalctl -fu sf3000-procedure
#   "to-26.04: ... running", then within minutes "its command failed (exit 1)",
#   "freed the engine's 256 MB reserve", "restoring Timeshift snapshot ...".
# After the reboot:
python3 engine/runner.py --status     # stopped: ... Restored Timeshift snapshot <name>
grep -m1 "Not enough free disk space" /var/log/dist-upgrade/main.log
ls /var/tmp/sf3000-fill               # No such file: the restore deleted it
grep VERSION= /etc/os-release         # 24.04

# --- Run F: the disk fills during the install ---------------------------------
sudo python3 tests/procedure-proof/fill-disk.py mid-install    # second session
sudo python3 engine/runner.py --procedures candidates/procedures --run release-upgrade-to-26.04 --take-snapshot
# Answer y. The upgrader downloads for about 10 minutes, then installs.
# A minute into the install the second session prints "filled ... MB", and
# dpkg starts to fail. Then, as in E: exit 1, the reserve freed, the restore,
# and a reboot. After it:
python3 engine/runner.py --status     # stopped: ... Restored Timeshift snapshot <name>
grep VERSION= /etc/os-release; sudo dpkg --audit   # 24.04, and nothing from dpkg
ls /var/tmp/sf3000-fill               # No such file

# --- Copy the logs off (on Windows, from the repo) --------------------------
#   scp -r -P 2223 rht@127.0.0.1:/var/log/sf3000 logs/kinetic-runs-e-f
#   and /var/log/dist-upgrade the same way as after each visit.
```

Expected new records, after visit 3's four:

| Run | step | outcome | what else to check |
|-----|------|---------|--------------------|
| E | to-26.04 | `rolled_back` | `failure: fix_failed`, `fix_exit_code: 1`, `rollback_result: "ok: back to 24.04 packages:..."` |
| F | to-26.04 | `rolled_back` | the same, from a half-upgraded machine |

If updates were waiting, each run first records `updates` as `healed`. The
restore then undoes them too, since the snapshot came before step 1.

If F ends `rollback_failed`, or the VM does not come back, copy what logs you
can. Then power the VM off and go back:
`E:\VirtualBox\VBoxManage.exe snapshot KineticServer restore at-24.04`.

Things to know:

- **F's logs may stop short.** When the disk fills, the step's log and the
  upgrader's logs are on the full disk too. Their last lines may be missing.
  The engine's record is written after it frees its reserve.
- **E's upgrader stops before it downloads.** Its `main.log` ends with "Not
  enough free disk space" and "view.abort called". It puts the old apt
  sources back before it exits.
- **After F, the machine has been restored twice.** Visit 4 can then run on
  it as it is, which is what a lab machine would do after a failed visit.
