# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# collector_probe.py — can a machine-dependent collector run on this machine?
#
# Usage:
#   from common.collector_probe import unavailable_reason
#   reason = unavailable_reason("pidstat")   # None if it can run, else why not
#
# Used by jbr-prepare to drop collectors this machine cannot run (spec-prepare.md
# §3 step 1d-collectors, PRD.md D-82) and by the collector tests to skip
# themselves. Only the collectors that depend on the machine are probed
# (cpu_temp, pidstat, proc, perf); all others are always reported runnable
# (the jcmd-based ones depend on the JDK, which `restrict_to` covers).

import os
import shutil
import subprocess

_READ_CPU_TEMP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "phase-execute", "collectors", "read-cpu-temp.sh")


def _probe_cpu_temp():
    """One sensor read with the script cpu-temp-logger.py uses."""
    try:
        result = subprocess.run(["bash", _READ_CPU_TEMP],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"CPU temperature not readable: {e}"
    if result.returncode != 0:
        detail = result.stderr.strip() or "read-cpu-temp.sh failed"
        return f"CPU temperature not readable: {detail}"
    return None


def _probe_binary(name, package):
    if shutil.which(name) is None:
        return f"'{name}' not found on PATH (install {package})"
    return None


def _probe_proc():
    """The files proc-logger.py reads, for this process."""
    for name in ("stat", "status"):
        if not os.path.isfile(f"/proc/{os.getpid()}/{name}"):
            return f"no Linux procfs (/proc/<pid>/{name} missing)"
    return None


_PROBES = {
    "cpu_temp": _probe_cpu_temp,
    "pidstat": lambda: _probe_binary("pidstat", "sysstat"),
    "proc": _probe_proc,
    "perf": lambda: _probe_binary("perf", "linux-perf or perf-tools"),
}


def unavailable_reason(collector: str):
    """Return None if `collector` (a short name, spec-prepare.md §1.4.1) can
    run on this machine, else a human-readable reason why not."""
    probe = _PROBES.get(collector)
    return probe() if probe is not None else None
