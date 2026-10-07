# eos-proof — VM run for `eos-release-dead-repos`

**Ran green on 2026-10-07** on Ubuntu 22.10 (kinetic), commit `4410bee`, on
`KineticServer`. Records: `evidence/ubuntu-22.10-2026-10-07.jsonl`. The hash
checks, the hand undo and `apt-get update` write no record, so
`evidence/README.md` writes them up. The entry stays in `candidates/`: it
still has to heal on 24.10 and stay HEALTHY on 20.04 and 25.04.

**The detect changed after that run.** It counted commented-out lines too,
and now it doesn't. So 22.10 runs once more, from the `clean-baseline`
snapshot: the steps are the same, and the count should be 10, not 20.

Nothing here is induced. The VM is broken the way the lab machines were: it
was installed with the network cable unplugged, so apt still points at
archive.ubuntu.com, which no longer serves this release. Run from
`~/sf-3000`, on a fresh clone.

**Preconditions:**

- The release is past its end of life, and its packages have moved to
  old-releases. True for 22.10 and 24.10. On 20.04 and 25.04 the detect must
  say HEALTHY instead, and there is nothing to fix.
- The VM can reach the internet. The detect asks archive.ubuntu.com and
  old-releases.ubuntu.com whether they serve this release. With no network it
  reports ERROR, not HEALTHY.

```bash
# 1. The machine is broken the way the lab's were
sudo apt-get update               # errors: "does not have a Release file"

# 2. Detect: PROBLEM, and it changes nothing
sha256sum /etc/apt/sources.list   # note this hash
python3 engine/runner.py --playbooks candidates   # eos-release-dead-repos: PROBLEM
sha256sum /etc/apt/sources.list   # the same hash
# Ignore the other candidates' lines (wifi-down, display-gpu-driver).

# 3. First fix
time sudo python3 engine/runner.py --playbooks candidates --fix eos-release-dead-repos
# answer y. Expect: healed.
grep -v '^#' /etc/apt/sources.list | grep .   # every line on old-releases.ubuntu.com
sudo apt-get update               # clean

# 4. The undo, by hand, using the playbook's own reverse text
python3 -c 'import yaml; print(yaml.safe_load(open("candidates/eos-release-dead-repos.yaml"))["reverse"]["command"]["ubuntu"])' > /tmp/eos-undo.sh
cat /tmp/eos-undo.sh; sudo sh /tmp/eos-undo.sh
sha256sum /etc/apt/sources.list   # equals the hash from step 2

# 4b. Active lines fixed by hand, comments left old: must read HEALTHY
#     (one-line sources.list only; added after the 2026-10-07 run)
sudo cp /etc/apt/sources.list /tmp/sources.list.orig
sudo sed -i -E -e '/^deb /s#https?://[a-z0-9.-]*archive\.ubuntu\.com/ubuntu#http://old-releases.ubuntu.com/ubuntu#' -e '/^deb /s#https?://security\.ubuntu\.com/ubuntu#http://old-releases.ubuntu.com/ubuntu#' /etc/apt/sources.list
grep -c '^# deb-src http://archive' /etc/apt/sources.list   # 10: the comments still say archive
python3 engine/runner.py --playbooks candidates   # eos-release-dead-repos: HEALTHY, measured=0
sudo cp /tmp/sources.list.orig /etc/apt/sources.list
sha256sum /etc/apt/sources.list   # back to the hash from step 2

# 5. Second fix: the undo really put the problem back
python3 engine/runner.py --playbooks candidates   # PROBLEM again
time sudo python3 engine/runner.py --playbooks candidates --fix eos-release-dead-repos
# answer y. Expect: healed again.
sudo apt-get update               # clean

# 6. Save the evidence (run on Windows, from the repo; use the VM's own port)
#    scp -P 2223 rht@127.0.0.1:sf-3000/logs/runs.jsonl evidence/ubuntu-22.10-<YYYY-MM-DD>.jsonl
#    (naming rules in evidence/README.md)
```

Four things to know when reading the output:

- **The count is active lines only.** The 22.10 server install has 10 `deb`
  lines, each with a `# deb-src` twin carrying the same old address. The
  detect skips the twins and counts 10. The fix rewrites them anyway, so a
  twin uncommented later is right too. (The 2026-10-07 run predates this and
  counted 20.)
- **Verify checks the file, not apt.** It proves the addresses were
  rewritten. Only `apt-get update` proves apt works again, and that writes to
  `/var/lib/apt/lists`, so it can't be part of a read-only check. Run it by
  hand, as steps 3 and 5 do.
- **The undo is not the engine's rollback.** The engine only rolls back when
  verify fails, and here it passed. Step 4 runs the same `reverse` text by
  hand, to prove it restores the original file byte for byte.
- **The second fix leaves a backup behind.** `sources.list.sf3000.bak` holds
  the original file. apt doesn't read it. It is what step 4 would restore.

On 24.10 and later the sources are expected in deb822 format, in
`/etc/apt/sources.list.d/ubuntu.sources`. Check with `ls /etc/apt/sources.list.d/`
first, then hash and read that file in steps 2 to 4 instead.
