#!/usr/bin/env python3
"""
Fill the root disk at a chosen moment of a real upgrade: Runs E and F in
README.md here. Test tooling for a VM. Never run it on a machine you care
about.

    sudo python3 tests/procedure-proof/fill-disk.py before-check
    sudo python3 tests/procedure-proof/fill-disk.py mid-install

Start it in a second session before the --run. It waits until the engine's
state says step to-26.04 is running, then:

  before-check  fills / at once, leaving 500 MB to ordinary users. The
                upgrader measures free space the way an ordinary user sees it,
                so it refuses before it downloads anything. root keeps the
                disk's reserved blocks, so the engine and Timeshift still have
                room: this run is about the upgrader's own refusal.
  mid-install   waits until the step's log shows dpkg unpacking, then 60 s
                more, then fills / for root too, leaving 1 MB. dpkg fails
                partway, on a half-upgraded machine, and the engine has only
                its reserve to restore with.

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
STEP = "to-26.04"
STEP_LOG = Path("/var/log/sf3000/release-upgrade-to-26.04-to-26.04.log")
FILL = Path("/var/tmp/sf3000-fill")
MB = 1024 * 1024


def step_running() -> bool:
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (state.get("phase") == "running"
            and (state.get("record") or {}).get("step_id") == STEP)


def unpacking_since(offset: int) -> bool:
    """dpkg has unpacked something in this run: earlier runs' lines, before
    offset, do not count. The upgrader's dry run unpacks nothing."""
    try:
        with STEP_LOG.open("rb") as fh:
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
    if len(sys.argv) != 2 or sys.argv[1] not in ("before-check", "mid-install"):
        sys.exit(__doc__)
    if os.geteuid() != 0:
        sys.exit("Run it with sudo.")
    offset = STEP_LOG.stat().st_size if STEP_LOG.exists() else 0
    print(f"{time.strftime('%H:%M:%S')} waiting for step {STEP} to start ...", flush=True)
    while not step_running():
        time.sleep(1)
    print(f"{time.strftime('%H:%M:%S')} it is running", flush=True)
    if sys.argv[1] == "before-check":
        fill(500 * MB, for_root=False)
        return
    print("waiting for dpkg to unpack ...", flush=True)
    while not unpacking_since(offset):
        time.sleep(2)
    print(f"{time.strftime('%H:%M:%S')} dpkg is unpacking; filling in 60 s", flush=True)
    time.sleep(60)
    fill(1 * MB, for_root=True)


if __name__ == "__main__":
    main()
