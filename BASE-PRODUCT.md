# SF 3000 — Base Product (MVP)

*Companion to `DESIGN.md`. This document covers **only the base product** — the
single-machine MVP with **no AI in the loop**. For the full project vision and
the "perhaps introduce novelty" directions (AI agent, multi-computer connect,
lab deployment, extras), see `DESIGN.md`.*

---

## 1. Scope recap (what "base product" means)

One machine at a time. A **deterministic matcher** (keyword/symptom lookup, no
AI) narrows a plain-language complaint to candidate playbooks; the **engine**
runs each playbook's read-only *detect*, checks it against a *predicate*, and —
through the **safety layer** (confirm · snapshot · log · reversible) — applies
and *verifies* the vetted *fix*. A thin **CLI/TUI** is the front door. The
trusted commands live in the **playbook library + schema**, never in software at
runtime. Both run modes ship in the MVP: complaint-driven and proactive sweep.
Ubuntu first; Fedora mirrors it later; Windows and anything AI-flavoured are
out of scope here.

---

## 2. Review of competitor apps

**Why look:** to see what already exists, learn what it does well, and spot the
gaps SF 3000 should fill — and to avoid the patterns (opaque "boosts", upsell,
bundled junk) that make the existing tools untrustworthy for non-technical
users. Grouped by category below, with what we *take* and what we *reject*.

### 2.1 Consumer cleaners / "PC optimizers" (Windows)

- **Microsoft PC Manager** — Microsoft's own free maintenance app. It's
  essentially a friendly dashboard that gathers tools already built into
  Windows (one-click "boost" to clear temp files and free memory, storage
  cleanup, startup-app management, a health check, security shortcuts). Its
  real strengths are that it's free, first-party, ad-free and doesn't push a
  paid tier or use scare tactics — which alone puts it ahead of most rivals.
  Its weaknesses are the interesting part for us: it mostly *duplicates*
  existing Windows features, doesn't manage the machine in any deep or
  intelligent way, and — critically — doesn't always explain what it's changing
  or why.
- **CCleaner, IObit Advanced SystemCare, iolo System Mechanic, AVG TuneUp, Wise
  Care 365, Ashampoo WinOptimizer, Restoro/Fortect, etc.** — the paid
  third-party optimizer market. One-click junk removal, registry "repair",
  startup tuning, and (for some) "repair corrupted system files" claims.
  Patterns to avoid: opaque whole-system "boost" with vague performance claims;
  heavy upsell prompts; some installers get flagged as *potentially unwanted
  programs* because they bundle extra offers; and leftover scheduled
  tasks/registry keys after uninstall.

**Take:** the simple, big-button front door and one-click ease.
**Reject:** the opacity and the black-box "make everything faster" sweep. Our
unit of action is a *specific, named problem* with a read-only detect, a vetted
fix, and a verify — never an unexplained bulk cleanup.

### 2.2 Built-in OS repair & troubleshooters

- **Windows:** Get Help / Troubleshooters, `sfc /scannow`, `DISM
  /RestoreHealth`, Startup Repair, the Windows Update troubleshooter, Storage
  Sense. Free and trustworthy, but narrow, siloed, and usually silent about
  what they actually did.
- **Ubuntu/Linux:** `apport` / `ubuntu-bug`, `unattended-upgrades`, `dpkg
  --configure -a` / `apt -f install`, **Boot-Repair**, **Timeshift**, and
  rescue distros like SystemRescue / Rescatux.

These aren't really competitors — they're our **ingredients**. Many vetted
fixes will simply wrap these exact built-in commands. The gap SF 3000 fills is
the *orchestration* around them: diagnosis by predicate, a safety layer, plain-
language confirmation, and a verify step.

Two worth studying directly:
- **Timeshift** is the model for our snapshot/undo idea on Linux — it creates
  rsync- or BTRFS-based filesystem snapshots and can restore a broken system
  from a live USB. It's a strong candidate to *wrap* inside the safety layer
  rather than reinvent.
- **Boot-Repair** is powerful but, by its own users' accounts, "a gamble" on
  modern UEFI/multi-boot setups — it can fix GRUB or mangle it. That's exactly
  the argument for our snapshot-before + verify-after discipline.

### 2.3 Single-purpose Linux utilities

**Stacer** (system optimizer/monitor), **BleachBit** (cleanup), **smartmontools**
(disk health), **Timeshift** (snapshots). Each does one thing; none combines
detect → fix → verify against a *curated, reviewed library* with a safety layer.

### 2.4 Automation / config management (the architectural cousins)

**Ansible** (YAML "playbooks", agentless, declarative desired-state),
**Puppet**, **Chef**, **SaltStack**. These share our core idea — reviewed,
versioned, declarative recipes applied by tooling — and even the word
*playbook*. But they're built for **expert sysadmins managing servers/fleets**:
you author your own content, there's no consumer front door, no plain-language
confirmation, and no curated library of *consumer-OS repairs*. We borrow the
concept and aim it at a non-technical person on their own machine.

### 2.5 Remote monitoring & management (the fleet competitors)

**NinjaOne, Atera, Datto RMM, ConnectWise, Microsoft Intune** — subscription
tools for IT pros/MSPs to watch and fix many machines at once. This is where
the *multi-computer* novelty would eventually compete; **out of scope for the
base product**, listed here only so the map is complete.

### 2.6 Where SF 3000 sits

| Category | Examples | Strength | Gap SF 3000 fills |
|---|---|---|---|
| Consumer optimizers | PC Manager, CCleaner, SystemCare | Easy, one-click | Opaque; whole-system "boost", not per-problem, no verify |
| Built-in repair | SFC/DISM, Boot-Repair, apt tools | Trusted, precise | Siloed, no diagnosis/orchestration, silent about changes |
| Linux utilities | Stacer, BleachBit, Timeshift | Focused, reliable | Single-purpose; no library + safety loop |
| Config management | Ansible, Puppet, Salt | Declarative "playbooks" | Built for experts/fleets; no consumer front door |
| RMM (fleet) | NinjaOne, Atera, Intune | Manage many machines | IT-pro tooling; subscription; not single-machine repair |

**Our niche:** a curated, reviewed library of *specific* OS problems, each with
a read-only detect, a predicate for "healthy", a vetted + reversible fix, and a
verify step — wrapped in plain-language confirmation for a non-technical person
on a single machine. No one above fills that cleanly.

### 2.7 What to actually dig into next (the review work item)

- Try **Microsoft PC Manager**'s health-check flow and copy its plain-language,
  big-button UX (and its no-upsell restraint).
- Study **Timeshift** internals to decide whether to wrap it for the Linux
  snapshot layer.
- Read how **Ansible** structures playbooks and guarantees *idempotency* — the
  same discipline our detect/verify needs.
- Note how built-in **troubleshooters** phrase actions to users, and do better
  on the "explain what changed" point every optimizer fails.

*Sources consulted (Aug 2026): Microsoft PC Manager coverage on KTAR/Data
Doctors, TechSpot, positioniseverything; TechRadar & thehightechsociety PC-
optimizer reviews; FOSSLinux/TechRadar Linux rescue guides; linuxmint/timeshift
on GitHub. Product facts change — re-check before citing in any writeup.*

---

## 3. VMs — the make-and-break test environment

We never test a fix on a real machine first. Instead we **make and break
throwaway VMs**: take a clean snapshot, deliberately induce the problem, run the
full detect → check → fix → verify loop, then roll the VM back. This is the
practical form of the safety model, and the place every playbook earns its
"tested" provenance before it can merge.

Each subsection below is a **playbook list** — the problems we can (or plan to)
fix on that OS. Every entry will eventually become a full playbook carrying the
schema fields (`symptoms`, `detect`, `expect`, `fix`, `verify`, `risk`,
`reverse`, `source`); the tables give the core of each. Fixes are *sketches*
here — the exact commands get vetted on a VM before they're trusted.

*(Fedora will mirror the Ubuntu list later: the detects are largely identical;
only the package-layer fix forks from `apt` to `dnf`.)*

### 3.1 Ubuntu — playbook list

| Problem | Detect (read-only) | "Healthy" = | Fix (sketch) | Risk |
|---|---|---|---|---|
| Root disk full | `df -P /` → % used | under 90% | `apt-get clean`; `journalctl --vacuum-time=7d`; `apt-get autoremove --purge` | moderate |
| `/boot` full (kernel updates fail) | `df -P /boot` → % used | under 80% | remove old kernels via `apt-get autoremove --purge` | moderate |
| Failed systemd services | `systemctl --failed` | 0 failed units | inspect, then `systemctl restart` / `enable` the unit | moderate |
| Interrupted dpkg / half-configured pkgs | `dpkg --audit`; `apt-get check` | exit 0, none half-configured | `dpkg --configure -a`; `apt-get -f install` | moderate |
| Unmet/broken dependencies | `apt-get -s -f install` | nothing to fix | `apt-get -f install` | moderate |
| Missing tool/package | `command -v <tool>` / `dpkg -s <pkg>` | exit 0 (present) | `apt-get install <pkg>` | safe |
| End-of-support release / dead repos *(the origin story)* | `lsb_release -sc` + EOL check + `apt-get update` result | release supported **and** update succeeds | point sources to `old-releases.ubuntu.com`, or pin/downgrade specific packages: `apt-get install <pkg>=<version>` | moderate |
| `apt update` failing (bad/expired sources, keys) | `apt-get update` exit code + error pattern (`NO_PUBKEY`, etc.) | exit 0 | fix per cause: refresh keys / correct sources / fix clock | safe–moderate |
| Clock out of sync (breaks TLS/apt) | `timedatectl` → "synchronized" | synced, within skew | `timedatectl set-ntp true` | safe |
| Slow boot | `systemd-analyze`; `systemd-analyze blame` | under threshold (e.g. < 60s) | disable the offending unit: `systemctl disable <unit>` | moderate |
| Wi-Fi down / missing driver | `nmcli device`; `rfkill list`; `lspci -k` | adapter present, unblocked, up | unblock rfkill / install firmware-driver package | moderate |

*Boot-level candidates for later (need a live/recovery context, not a running
machine): broken GRUB / won't boot — snapshot-first, destructive, Boot-Repair-
style GRUB reinstall.*

### 3.2 Windows — playbook list

| Problem | Detect (read-only) | "Healthy" = | Fix (sketch) | Risk |
|---|---|---|---|---|
| Disk bloat / low space | `Get-Volume C` free space | free above threshold (e.g. > 15%) | Disk Cleanup / `cleanmgr`; clear `%TEMP%`; Storage Sense | moderate |
| Missing/disabled Wi-Fi driver | `Get-NetAdapter`; `Get-PnpDevice` (wireless) | adapter present & "Up" | enable adapter; reinstall driver via `pnputil` | moderate |
| Slow boot / heavy startup | `Get-CimInstance Win32_StartupCommand`; startup impact | boot under threshold | disable heavy startup entries | safe |
| System file corruption | `sfc /verifyonly`; `DISM /Online /Cleanup-Image /ScanHealth` | no integrity violations | `DISM /RestoreHealth` then `sfc /scannow` | moderate |
| Windows Update stuck/failing | update service state + last error code | update completes | reset update components (stop `wuauserv`/`bits`/`cryptsvc`, rename `SoftwareDistribution` & `catroot2`, restart) | moderate |
| Temp / cache bloat | size of `%TEMP%`, Prefetch, Delivery Optimization cache | under threshold | clear those caches | safe |
| No network connectivity (stack reset) | `ipconfig`; DNS resolve; ping | resolves + pings | `ipconfig /flushdns`; `netsh winsock reset`; `netsh int ip reset` *(reboot)* | moderate |
| `Windows.old` / update leftovers eating space | presence + size of leftover dirs | absent / under threshold | `cleanmgr` system-file cleanup / Storage Sense | moderate |
| Missing runtime (VC++ redistributable) — app won't start | check installed redistributables | required version present | install the redistributable | safe |

---

## 4. Note on turning this list into playbooks

This list is the **menu**, not the commitment. Each row becomes a real playbook
only after its exact detect/fix are vetted by making-and-breaking a VM and its
provenance line is filled in. The single-machine library *is* the thing that
scales later — so the work here (grow the library, prove each fix on a VM) is
the same work that powers every "perhaps introduce novelty" direction in
`DESIGN.md`.

*The very first problem to take fully end-to-end (fix included, not just
detected) is still open — see `DESIGN.md` §15. This list is where that choice
gets made.*
