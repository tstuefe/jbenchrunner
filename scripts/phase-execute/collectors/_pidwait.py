# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-execute/collectors/_pidwait.py
# Shared helper for the Python collectors: check whether the monitored JVM pid still exists,
# and sleep between samples without missing its disappearance for a whole interval.
# Imported by cpu-temp-logger.py and proc-logger.py
# (spec-execute.md 5.1, PRD.md D-59). Not a script.

import os
import time

# Longest stretch a collector sleeps without checking the pid, in seconds. Keeps the
# reaction time to a vanished pid at about a second, however large the interval is.
SLICE_SECS = 1.0


def pid_alive(pid):
    """Return True if the process still exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sleep_while_alive(pid, seconds):
    """Sleep for *seconds*, waking at least every SLICE_SECS to check *pid*.

    Returns False as soon as the process is gone, True if the full time
    elapsed and it is still alive. (``waitpid`` cannot be used: the JVM is a
    child of the runner, not of the collector.)
    """
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return pid_alive(pid)
        time.sleep(min(SLICE_SECS, remaining))
        if not pid_alive(pid):
            return False
