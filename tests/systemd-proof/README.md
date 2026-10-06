# systemd-proof — VM run for `failed-systemd-units`

**Ran green on 2026-10-06** on Ubuntu 26.04, commit `10cb1fd`, and the
playbook moved into the library. Records:
`evidence/ubuntu-26.04-2026-10-06.jsonl`. The privilege refusal and the
journal check leave no record, so `evidence/README.md` writes them up. The
steps below repeat any time. That run used `--playbooks candidates`, because
the entry was still a candidate. It is in `playbooks/` now, so the commands
below leave that out.

Three throwaway services that put the restart fix through its three cases.
Run on a clean VM, from `~/sf-3000`. Revert the snapshot afterwards.

**Precondition:** the machine has no other failed services. The fix restarts
every failed, non-oneshot service it finds, not only these three. Step 1
checks this.

```bash
# 1. Baseline — the failed-systemd-units line must say HEALTHY, measured=0.
#    Ignore the other lines; net-tools-missing usually says PROBLEM on a fresh VM.
python3 engine/runner.py

# 2. Install the test units (no output)
sudo cp tests/systemd-proof/*.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3. Proof C — a failed oneshot is ignored
sudo systemctl start sf3000-oneshot          # "Job ... failed" — on purpose
python3 engine/runner.py                     # still HEALTHY, measured=0
# Leave it failed. Steps 4 and 5 must not restart it; step 6 checks.

# 4. Proof A — a service whose cause is gone heals
sudo systemctl start sf3000-recovers; sleep 1; systemctl is-failed sf3000-recovers   # failed
python3 engine/runner.py                     # PROBLEM, measured=1 (sf3000-recovers.service)
sudo touch /run/sf3000-ok                    # the cause goes away

# 4b. Without sudo the fix must refuse: no y/N question, exit=3
python3 engine/runner.py --fix failed-systemd-units; echo "exit=$?"

time sudo python3 engine/runner.py --fix failed-systemd-units
# answer y. Expect: it still finds the problem (so 4b changed nothing),
# waits 30s, then outcome healed.

# 5. Proof B — a service that crashes 10s after starting is caught by the wait
sudo systemctl start sf3000-crashes-late
sleep 15; systemctl is-failed sf3000-crashes-late    # failed
time sudo python3 engine/runner.py --fix failed-systemd-units
# answer y. Expect: waits 30s, then verify_failed, listing sf3000-crashes-late.service.

# 6. Proof C, part two — every start of the three services, with times.
#    sf3000-oneshot must start once, in step 3, and never again.
sudo journalctl -u sf3000-oneshot -u sf3000-recovers -u sf3000-crashes-late --no-pager -o short-iso

# 7. Save the evidence BEFORE reverting (run on Windows, from the repo)
#    scp -P 2222 rht@127.0.0.1:sf-3000/logs/runs.jsonl evidence/ubuntu-26.04-<YYYY-MM-DD>.jsonl
#    (naming rules in evidence/README.md)
```

Three things to know when reading the output:

- `systemctl start` returns as soon as a `Type=simple` service has forked.
  So starting `sf3000-recovers` prints nothing. It fails a moment later.
- The proof that the wait happened is step 5's outcome, not the stopwatch.
  The restart works, and only a check made 10 seconds or more later can see
  the crash. `time`'s `real` also counts how long you took to answer y.
- Step 4b writes no record, by design: the engine refuses before it logs
  anything. Keep its terminal output.
