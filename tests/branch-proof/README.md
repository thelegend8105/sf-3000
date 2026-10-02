# branch-proof — VM run for the outcomes a passing run never reaches

Three fixtures, one per branch. None uses apt or the network, and none leaves
a change behind, so each runs in seconds and can be repeated. Run from
`~/sf-3000`, under sudo like every other VM run, so all records land in the
same `logs/runs.jsonl`.

```bash
P="--playbooks tests/branch-proof"

# 1. Snapshot gate — must refuse, exit 4, and run nothing
sudo python3 engine/runner.py $P --fix snapshot-gate-proof; echo "exit=$?"
ls /tmp/sf3000-snapshot-gate-breach          # must say: No such file

# 2. declined — answer N
sudo python3 engine/runner.py $P --fix rollback-failed-proof
tail -1 logs/runs.jsonl                      # outcome declined, confirmed false

# 3. rollback_failed — answer y
sudo python3 engine/runner.py $P --fix rollback-failed-proof; echo "exit=$?"
tail -1 logs/runs.jsonl                      # outcome rollback_failed, exit=1

# 4. verify_error — set up first, then answer y
sudo sh -c 'echo 99 > /tmp/sf3000-verify-error-proof'
sudo python3 engine/runner.py $P --fix verify-error-proof
tail -1 logs/runs.jsonl                      # verify_error set, outcome rolled_back
cat /tmp/sf3000-verify-error-proof           # 99 again

# 5. rollback-proof, twice in a row — it no longer depends on net-tools
dpkg -s cowsay >/dev/null 2>&1 && echo "STOP: cowsay is installed"
sudo python3 engine/runner.py --playbooks tests/rollback-proof --fix rollback-proof
sudo python3 engine/runner.py --playbooks tests/rollback-proof --fix rollback-proof
# answer y both times. Expect rolled_back twice, and cowsay absent afterwards.
```

Copy `logs/runs.jsonl` to Windows before you revert the VM.
