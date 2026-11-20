# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
machine_state.py — best-effort snapshot of machine "stabilization" state.

Mirrors the settings toggled by prepare-for-benchmarks.sh (machine
stabilization, in this directory): CPU governor, turbo boost, CPU frequency cap, ASLR, perf_event_paranoid,
a handful of stopped services, swap, and memory overcommit — plus /proc/cmdline.

stabilization_warnings() checks a subset of this state (the CPU governor and,
when perf is enabled, perf_event_paranoid) and returns human-readable warnings,
printed once at batch start.

This module only reads state; it never sets anything, and every field it reads
is world-readable, so capturing it needs no privileges. It is best-effort:
a field that can't be read (e.g. no intel_pstate directory on an AMD box) is
recorded as None rather than raising — this is diagnostic data for later
analysis of stabilization effectiveness, and must never fail a benchmark run.

Per-CPU settings (governor, scaling_max_freq) are recorded as the set of
distinct values seen across CPUs plus a `uniform` flag, not one entry per
CPU, to stay compact on high-core-count hosts.
"""

import glob
import os
import subprocess

_INTEL_NO_TURBO_PATH = "/sys/devices/system/cpu/intel_pstate/no_turbo"
_AMD_BOOST_PATH = "/sys/devices/system/cpu/cpufreq/boost"
_PROC_SWAPS_PATH = "/proc/swaps"
_CPU_GOVERNOR_GLOB = "/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor"
_PERF_EVENT_PARANOID_PATH = "/proc/sys/kernel/perf_event_paranoid"

_STABILIZED_SERVICES = [
    "irqbalance",
    "bluetooth",
    "cups",
    "avahi-daemon",
    "avahi-daemon.socket",
    "cron",
]


def _read_first_line(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().strip()
    except OSError:
        return None


def _read_int(path: str):
    value = _read_first_line(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _distinct_values(paths: list, reader) -> dict:
    values = []
    for path in paths:
        value = reader(path)
        if value is not None and value not in values:
            values.append(value)
    return {"values": values, "uniform": len(values) <= 1}


def _cpu_governor():
    paths = sorted(glob.glob(_CPU_GOVERNOR_GLOB))
    if not paths:
        return None
    return _distinct_values(paths, _read_first_line)


def _cpu_scaling_max_freq_khz():
    paths = sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_max_freq"))
    if not paths:
        return None
    return _distinct_values(paths, _read_int)


def _turbo_boost():
    if os.path.isfile(_INTEL_NO_TURBO_PATH):
        no_turbo = _read_int(_INTEL_NO_TURBO_PATH)
        return {
            "mechanism": "intel",
            "disabled": None if no_turbo is None else (no_turbo == 1),
        }
    if os.path.isfile(_AMD_BOOST_PATH):
        boost = _read_int(_AMD_BOOST_PATH)
        return {
            "mechanism": "amd",
            "disabled": None if boost is None else (boost == 0),
        }
    return {"mechanism": None, "disabled": None}


def _service_status(name: str):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return None
    return result.stdout.strip() or None


def _swap_off():
    try:
        with open(_PROC_SWAPS_PATH, encoding="utf-8") as fh:
            # First line is a header; any further line means swap is active.
            lines = fh.readlines()
    except OSError:
        return None
    return len(lines) <= 1


def stabilization_warnings(perf_enabled: bool = False) -> list:
    """Return warnings (strings) for stabilization settings that look wrong.

    Linux only; the caller decides whether to call it. Checks that every
    CPU's scaling governor is 'performance' and, when *perf_enabled*, that
    perf_event_paranoid is 0, the level machine stabilization sets so that
    the unprivileged perf collector counts kernel events too (PRD.md D-85).
    Never raises.
    """
    warnings = _governor_warnings()
    if perf_enabled:
        level = _read_int(_PERF_EVENT_PARANOID_PATH)
        if level is not None and level > 0:
            effect = {1: "no whole-CPU counting",
                      2: "perf counts user space only, not kernel events"}.get(
                          level, "perf cannot count at all")
            warnings.append(
                f"WARNING: machine not stabilized: perf_event_paranoid is {level}, "
                f"not 0 ({effect})."
            )
    return warnings


def _governor_warnings() -> list:
    paths = sorted(glob.glob(_CPU_GOVERNOR_GLOB))
    if not paths:
        return ["WARNING: could not verify CPU governor (no cpufreq in sysfs)."]
    governors = [_read_first_line(path) for path in paths]
    not_performance = [g for g in governors if g != "performance"]
    if not not_performance:
        return []
    seen = []
    for g in governors:
        g = g if g is not None else "<unreadable>"
        if g not in seen:
            seen.append(g)
    return [
        f"WARNING: machine not stabilized: CPU governor is not 'performance' "
        f"on {len(not_performance)} of {len(governors)} CPUs "
        f"(seen: {', '.join(seen)})."
    ]


def capture_stabilization_state() -> dict:
    """Return a best-effort snapshot of machine stabilization state.

    Safe to call unconditionally (no sudo mode required); every field is
    None/omitted-equivalent rather than raising when unavailable.
    """
    return {
        "cmdline": _read_first_line("/proc/cmdline"),
        "cpu": {
            "governor": _cpu_governor(),
            "scaling_max_freq_khz": _cpu_scaling_max_freq_khz(),
            "turbo_boost": _turbo_boost(),
        },
        "kernel": {
            "randomize_va_space": _read_int("/proc/sys/kernel/randomize_va_space"),
            "perf_event_paranoid": _read_int(_PERF_EVENT_PARANOID_PATH),
        },
        "memory": {
            "swap_off": _swap_off(),
            "overcommit_memory": _read_int("/proc/sys/vm/overcommit_memory"),
            "overcommit_ratio": _read_int("/proc/sys/vm/overcommit_ratio"),
        },
        "services": {name: _service_status(name) for name in _STABILIZED_SERVICES},
    }
