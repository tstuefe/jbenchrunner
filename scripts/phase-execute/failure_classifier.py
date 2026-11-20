# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
failure_classifier.py — derive run-info.json's status_info for a failed run.

Used by execute.py after the benchmark JVM exited with a non-zero exit code
(spec-execute.md §4.2), and by scripts/helpers/migrate-status-info.py to
back-fill old runs. Timeouts ("timeout") and collector failures ("collector")
are recorded by execute.py directly and never reach this module.

Rules, first match wins:

  exit code is SIGABRT, and hs_err_pid*.log (OpenJDK)
  or javacore* (OpenJ9) exists in the result dir        -> "crash"
  exit code is a signal                                -> "signal <N>"
  last 100 lines of stderr.log mention OutOfMemoryError -> "oom"
  stderr.log reports an unsupported Java version        -> "version mismatch"
  otherwise                                            -> None

Only stderr.log is scanned: stdout.log holds the -XX:+PrintFlagsFinal dump,
whose flag names (e.g. CrashOnOutOfMemoryError) would match.
"""

import collections
import glob
import os
import re
import signal

_STDERR_TAIL_LINES = 100

# Matches "java.lang.OutOfMemoryError" and "OutOfMemoryError: ...", but not
# flag names like ExitOnOutOfMemoryError.
_OOM_RE = re.compile(r"(?<![A-Za-z])OutOfMemoryError")

_VERSION_MISMATCH_RES = (
    re.compile(r"Only Java versions .* are supported"),   # h2o
    re.compile(r"Java version .* is incompat"),           # cassandra ("incompatable")
)


def exit_signal(exit_code):
    """Return the signal number that killed the process, or None.

    Accepts both subprocess's encoding (-N) and a shell's (128+N).
    """
    if exit_code is None:
        return None
    if exit_code < 0:
        return -exit_code
    if 128 < exit_code < 128 + 65:
        return exit_code - 128
    return None


def _read_stderr(result_dir):
    path = os.path.join(result_dir, "stderr.log")
    try:
        with open(path, errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _has_crash_file(result_dir):
    return bool(glob.glob(os.path.join(result_dir, "hs_err_pid*.log"))
                or glob.glob(os.path.join(result_dir, "javacore*")))


def classify_failure(result_dir, exit_code):
    """Return the status_info tag for a run that exited with *exit_code* != 0,
    or None if no rule matches."""
    sig = exit_signal(exit_code)
    if sig == signal.SIGABRT and _has_crash_file(result_dir):
        return "crash"
    if sig is not None:
        return f"signal {sig}"

    stderr_lines = _read_stderr(result_dir)
    tail = collections.deque(stderr_lines, maxlen=_STDERR_TAIL_LINES)
    if any(_OOM_RE.search(line) for line in tail):
        return "oom"
    if any(r.search(line) for line in stderr_lines for r in _VERSION_MISMATCH_RES):
        return "version mismatch"
    return None
