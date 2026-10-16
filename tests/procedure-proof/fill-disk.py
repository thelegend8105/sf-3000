#!/usr/bin/env python3
"""
Fill the root disk at a chosen moment of a real upgrade: Runs E and F in
README.md here, and Run 2 in tests/efi-proof/README.md. Test tooling for a
VM. Never run it on a machine you care about.

    sudo python3 tests/procedure-proof/fill-disk.py before-check [STEP]
    sudo python3 tests/procedure-proof/fill-disk.py mid-install [STEP]

STEP is the upgrade step to wait for: to-26.04 if left out (Runs E and F),
to-23.04 for the EFI proof. Start it in a second session before the --run.
It waits until the engine's state says that step is running, then:

  before-check  fills / at once, leaving 500 MB to ordinary users. The
                upgrader measures free space the way an ordinary user sees it,
                so it refuses before it downloads anything. root keeps the
                disk's reserved blocks, so the engine and Timeshift still have
                room: this run is about the upgrader's own refusal.
  mid-install   waits until the upgrader starts (the step touches
                /run/sf3000-upgrade-start just before it), then until the
                step's log shows dpkg unpacking after that, then 60 s more,
                then fills / for root too, leaving 1 MB. dpkg fails partway,
                on a half-upgraded machine, and the engine has only its
                reserve to restore with. Waiting for the upgrader matters for
                to-23.04, whose step runs apt's full-upgrade first: dpkg
                unpacking there is not the upgrade.

The fill is one file, /var/tmp/sf3000-fill. It is not in the engine's
snapshot, so the restore deletes it. If no restore runs, delete it by hand:
sudo rm /var/tmp/sf3000-fill
"""

import json
import os
import sys
import time
from pathlib import Path

STATE = Path("/var/lib/sf3000/state.json")
LOG_DIR = Path("/var/log/sf3000")
UPGRADE_START = Path("/run/sf3000-upgrade-start")
FILL = Path("/var/tmp/sf3000-fill")
MB = 1024 * 1024


def step_running(step: str):
    """The engine's state while step runs, else None."""
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (state.get("phase") == "running"
            and (state.get("record") or {}).get("step_id") == step):
        return state
    return None


def upgrader_started(since: float) -> bool:
    try:
        return UPGRADE_START.stat().st_mtime >= since
    except OSError:
        return False


def log_size(log: Path) -> int:
    return log.stat().st_size if log.exists() else 0


def unpacking_since(log: Path, offset: int) -> bool:
    """dpkg has unpacked something since offset. The upgrader's dry run
    unpacks nothing."""
    try:
        with log.open("rb") as fh:
            fh.seek(offset)
            return b"\nUnpacking " in fh.read()
    except OSError:
        return False


def free_mb(st, for_root: bool) -> int:
    return (st.f_bfree if for_root else st.f_bavail) * st.f_frsize // MB


def fill(leave: int, for_root: bool):
    st = os.statvfs("/")
    size = (st.f_bfree if for_root else st.f_bavail) * st.f_frsize - leave
    if size <= 0:
        print(f"only {free_mb(st, for_root)} MB free already: nothing to fill")
        return
    fd = os.open(FILL, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.posix_fallocate(fd, 0, size)
    finally:
        os.close(fd)
    st = os.statvfs("/")
    print(f"{time.strftime('%H:%M:%S')} filled {size // MB} MB into {FILL}. Free "
          f"now: {free_mb(st, False)} MB for users, {free_mb(st, True)} MB for root",
          flush=True)


def main():
    if (len(sys.argv) not in (2, 3)
            or sys.argv[1] not in ("before-check", "mid-install")):
        sys.exit(__doc__)
    step = sys.argv[2] if len(sys.argv) == 3 else "to-26.04"
    if os.geteuid() != 0:
        sys.exit("Run it with sudo.")
    since = time.time()
    print(f"{time.strftime('%H:%M:%S')} waiting for step {step} to start ...", flush=True)
    while not (state := step_running(step)):
        time.sleep(1)
    print(f"{time.strftime('%H:%M:%S')} it is running", flush=True)
    if sys.argv[1] == "before-check":
        fill(500 * MB, for_root=False)
        return
    log = LOG_DIR / f"{state['procedure_id']}-{step}.log"
    print("waiting for the upgrader to start ...", flush=True)
    while not upgrader_started(since):
        time.sleep(1)
    offset = log_size(log)
    print(f"{time.strftime('%H:%M:%S')} it has; waiting for dpkg to unpack ...", flush=True)
    while not unpacking_since(log, offset):
        time.sleep(2)
    print(f"{time.strftime('%H:%M:%S')} dpkg is unpacking; filling in 60 s", flush=True)
    time.sleep(60)
    fill(1 * MB, for_root=True)


if __name__ == "__main__":
    main()
