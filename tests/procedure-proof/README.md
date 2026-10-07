# procedure-proof — VM run for the procedure machinery

**Not yet run on a VM.** `test_procedure.py` passes offline (stubbed machine,
no VM). These steps prove the same machinery on a real one, with a fixture
that takes minutes, before any real upgrade relies on it.

`procedure-proof.yaml` has three steps that touch marker files in
`/var/lib/sf3000-proof/`:

- **one** touches a marker, then the engine reboots and verifies after the boot.
- **two** touches a marker and is verified at once.
- **three** sleeps 5 minutes against a 1-minute limit. The engine must stop
  it, so marker `three` must never appear.

Two runs. Run A has no snapshot, so step three's failure stops the procedure.
Run B uses `--take-snapshot`, so the engine restores its Timeshift snapshot and
the markers vanish.

Use the 22.10 VM from its `repos-fixed` snapshot: Run B installs Timeshift,
which needs a working apt.

```bash
# 0. Setup (on Windows): restore repos-fixed, start the VM, ssh -p 2223 rht@127.0.0.1
cd ~/sf-3000 && git pull && git log --oneline -1
python3 tests/procedure-proof/test_procedure.py | tail -1   # all checks passed (on Python 3.10 too)

# --- Run A: no snapshot --------------------------------------------------
sudo python3 engine/runner.py --procedures tests/procedure-proof --run procedure-proof
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

# --- Run B: --take-snapshot ----------------------------------------------
sudo apt-get install -y timeshift
sudo rm -rf /var/lib/sf3000-proof
sudo python3 engine/runner.py --procedures tests/procedure-proof --run procedure-proof --take-snapshot
# The box now says the engine takes a Timeshift snapshot first. Answer y.
journalctl -fu sf3000-procedure      # "taking a Timeshift snapshot" (minutes), then one, then a reboot

# The VM reboots TWICE: once after step one; then, after three fails,
# Timeshift restores and reboots by itself. After the second, ssh back in:
python3 engine/runner.py --status    # stopped: ... Restored Timeshift snapshot <name>
ls /var/lib/sf3000-proof             # "No such file or directory": restored away
sudo timeshift --list                # the snapshot, comment "sf3000 procedure-proof ..."
cat /var/log/sf3000/runs.jsonl       # Run A's 3 records, then Run B's 3

# --- Save the evidence (on Windows, from the repo) --------------------------
#   scp -P 2223 rht@127.0.0.1:/var/log/sf3000/runs.jsonl evidence/ubuntu-22.10-<YYYY-MM-DD>.jsonl
#   (naming rules in evidence/README.md; add -2, -3 if the name is taken)
```

Expected records in `/var/log/sf3000/runs.jsonl`, 6 lines:

| Run | step  | outcome       | what else to check                              |
|-----|-------|---------------|-------------------------------------------------|
| A   | one   | `healed`      | `rebooted: true`, `verify_after: 1`             |
| A   | two   | `healed`      | `rebooted: false`                               |
| A   | three | `fix_failed`  | `fix_exit_code: null` (stopped at the limit)    |
| B   | one   | `healed`      | `snapshot_taken: true`, `snapshot_id` = the Timeshift name |
| B   | two   | `healed`      |                                                 |
| B   | three | `rolled_back` | `failure: fix_failed`, `rollback_method: timeshift`, `rollback_result: "ok: back to ..."` |

Things to know when reading the output:

- **The procedure's records are not in `~/sf-3000/logs/`.** The service writes
  as root at boot, so it writes only to root-owned places:
  `/var/log/sf3000/runs.jsonl` for records, `/var/log/sf3000/<id>-<step>.log`
  for each step's output, plus `snapshot.log` and `restore.log`.
- **After Run B's restore, the journal forgets the run.** Timeshift rolls back
  `/var/log/journal` with everything else. `/var/log/sf3000/` is excluded from
  the snapshot, so the engine's own records survive. That is on purpose: it is
  how the boot after the restore knows what happened.
- **The engine adds four excludes to `/etc/timeshift/timeshift.json`**: its
  state, its logs, and its service file and enable link. Nothing else in that
  file is changed.
- **Run A's step three reads `fix_failed`, not `interrupted`.** The time limit
  stopped it, and the engine was still running to record that. `interrupted`
  is for a machine that went down mid-step; the offline test covers it.
