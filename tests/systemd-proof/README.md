# systemd-proof — VM run for `failed-systemd-units`

Three throwaway services that put the restart fix through its three cases.
Run on a clean VM, from `~/sf-3000`. Revert the snapshot afterwards.

**Precondition:** the machine has no other failed services. The fix restarts
every failed, non-oneshot service it finds, not only these three. Step 1
checks this.

```bash
# 1. Baseline — must be HEALTHY, measured=0
python3 engine/runner.py --playbooks candidates

# 2. Install the test units
sudo cp tests/systemd-proof/*.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3. Proof C — a failed oneshot is ignored
sudo systemctl start sf3000-oneshot          # fails on purpose
systemctl --failed                           # lists sf3000-oneshot.service
python3 engine/runner.py --playbooks candidates   # still HEALTHY, measured=0
# Leave it failed. Steps 4 and 5 must not restart it.

# 4. Proof A — a service whose cause is gone heals
sudo systemctl start sf3000-recovers         # fails: /run/sf3000-ok is missing
python3 engine/runner.py --playbooks candidates   # PROBLEM, lists sf3000-recovers.service
sudo touch /run/sf3000-ok                    # the cause goes away
time sudo python3 engine/runner.py --playbooks candidates --fix failed-systemd-units
# answer y. Expect: waits 30s, then outcome healed.

# 5. Proof B — a service that crashes after 10s is caught by the wait
sudo systemctl start sf3000-crashes-late
sleep 15                                     # let it crash
time sudo python3 engine/runner.py --playbooks candidates --fix failed-systemd-units
# answer y. Expect: waits 30s, then verify_failed, listing sf3000-crashes-late.service.

# 6. Proof C, part two — the oneshot was never restarted
systemctl status sf3000-oneshot              # still failed, same "since" time as step 3

# 7. Save the evidence BEFORE reverting (run on Windows)
#    scp -P 2222 rht@127.0.0.1:sf-3000/logs/runs.jsonl evidence/ubuntu-26.04-<date>.jsonl
#    (from the repo; naming rules in evidence/README.md)
```

Run the fixes under `time`. The wall-clock total (just over 30s) is the
evidence that the wait actually happened.
