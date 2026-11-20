#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/post-process-proc-log.py
# Post-processes a proclog.log file (raw /proc/<pid>/stat and /proc/<pid>/status dumps written
# by proc-logger.py) into JSON Lines, one record per sample.
#
# Each sample in proclog.log looks like:
#
#   <ISO-8601 timestamp>
#   === /proc/<pid>/stat ===
#   <raw contents>
#   === /proc/<pid>/status ===
#   <raw contents>
#
# Record: {"timestamp": ..., "pid": ..., "proc_pid_stat": {...}, "status": {...}}
#
# Errors: a sample lacking either section, or a final sample cut off mid-write (file does not
# end in a newline), is warned about and skipped. No valid sample at all is a file-level
# error (exit 1).
#
# Usage: post-process-proc-log.py <input-proclog-log>
# The input may be zstd-compressed (proclog.log.zst, see collectors.compression).

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common.collector_files import open_collector_file  # noqa: E402
from common.collector_timestamp import TIMESTAMP_RE as _TIMESTAMP_RE  # noqa: E402


_DELIMITER_RE = re.compile(r"^=== /proc/(?P<pid>\d+)/(?P<file>stat|status) ===$")

# Maps raw /proc/<pid>/status key names to compliant JSON field names (see §5.8 / Addendum A.3).
# Rules: A-Za-z0-9_ only, snake_case, "pct" for %, "per_sec" for /s.
# A value of None means the key is explicitly ignored and silently dropped from the record.
# Keys not present in this table are translated algorithmically by _translate_status_key().
_STATUS_FIELD_MAP = {
    "Name":                        "name",
    "Umask":                       "umask",
    "State":                       "state",
    "Tgid":                        "tgid",
    "Ngid":                        "ngid",
    "Pid":                         "pid",
    "PPid":                        "ppid",
    "TracerPid":                   "tracer_pid",
    "Uid":                         "uid",
    "Gid":                         "gid",
    "FDSize":                      "fd_size",
    "Groups":                      "groups",
    "NStgid":                      "nstgid",
    "NSpid":                       "nspid",
    "NSpgid":                      "nspgid",
    "NSsid":                       "nssid",
    "Kthread":                     "kthread",
    "VmPeak":                      "vm_peak",
    "VmSize":                      "vm_size",
    "VmLck":                       "vm_lck",
    "VmPin":                       "vm_pin",
    "VmHWM":                       "vm_hwm",
    "VmRSS":                       "vm_rss",
    "RssAnon":                     "rss_anon",
    "RssFile":                     "rss_file",
    "RssShmem":                    "rss_shmem",
    "VmData":                      "vm_data",
    "VmStk":                       "vm_stk",
    "VmExe":                       "vm_exe",
    "VmLib":                       "vm_lib",
    "VmPTE":                       "vm_pte",
    "VmSwap":                      "vm_swap",
    "HugetlbPages":                "hugetlb_pages",
    "CoreDumping":                 "core_dumping",
    "THP_enabled":                 "thp_enabled",
    "untag_mask":                  None,   # arch-specific bitmask; not useful for analysis
    "Threads":                     "threads",
    "SigQ":                        "sigq",
    "SigPnd":                      "sig_pnd",
    "ShdPnd":                      "shd_pnd",
    "SigBlk":                      "sig_blk",
    "SigIgn":                      "sig_ign",
    "SigCgt":                      "sig_cgt",
    "CapInh":                      "cap_inh",
    "CapPrm":                      "cap_prm",
    "CapEff":                      "cap_eff",
    "CapBnd":                      "cap_bnd",
    "CapAmb":                      "cap_amb",
    "NoNewPrivs":                  None,   # security policy bit; not useful for analysis
    "Seccomp":                     None,   # security policy bit; not useful for analysis
    "Seccomp_filters":             None,   # security policy bit; not useful for analysis
    "Speculation_Store_Bypass":    None,   # arch-specific mitigation; not useful for analysis
    "SpeculationIndirectBranch":   None,   # arch-specific mitigation; not useful for analysis
    "x86_Thread_features":         None,   # arch-specific; not useful for analysis
    "x86_Thread_features_locked":  None,   # arch-specific; not useful for analysis
    "Cpus_allowed":                None,   # hex bitmask duplicate of Cpus_allowed_list
    "Cpus_allowed_list":           "cpus_allowed_list",
    "Mems_allowed":                None,   # hex bitmask duplicate of Mems_allowed_list
    "Mems_allowed_list":           "mems_allowed_list",
    "voluntary_ctxt_switches":     "voluntary_ctxt_switches",
    "nonvoluntary_ctxt_switches":  "nonvoluntary_ctxt_switches",
}


# Official field names from proc(5), in positional order (1-based in the man page)
STAT_FIELDS = [
    "pid",          # 1
    "comm",         # 2
    "state",        # 3
    "ppid",         # 4
    "pgrp",         # 5
    "session",      # 6
    "tty_nr",       # 7
    "tpgid",        # 8
    "flags",        # 9
    "minflt",       # 10
    "cminflt",      # 11
    "majflt",       # 12
    "cmajflt",      # 13
    "utime",        # 14
    "stime",        # 15
    "cutime",       # 16
    "cstime",       # 17
    "priority",     # 18
    "nice",         # 19
    "num_threads",  # 20
    "itrealvalue",  # 21
    "starttime",    # 22
    "vsize",        # 23
    "rss",          # 24
    "rsslim",       # 25
    "startcode",    # 26
    "endcode",      # 27
    "startstack",   # 28
    "kstkesp",      # 29
    "kstkeip",      # 30
    "signal",       # 31
    "blocked",      # 32
    "sigignore",    # 33
    "sigcatch",     # 34
    "wchan",        # 35
    "nswap",        # 36
    "cnswap",       # 37
    "exit_signal",  # 38
    "processor",    # 39
    "rt_priority",  # 40
    "policy",       # 41
    "delayacct_blkio_ticks",  # 42
    "guest_time",   # 43
    "cguest_time",  # 44
    "start_data",   # 45
    "end_data",     # 46
    "start_brk",    # 47
    "arg_start",    # 48
    "arg_end",      # 49
    "env_start",    # 50
    "env_end",      # 51
    "exit_code",    # 52
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Post-process a proclog.log (raw /proc/<pid>/stat and status dumps) into JSON Lines."
    )
    parser.add_argument("input", metavar="INPUT", help="Input proclog log file")
    return parser.parse_args()


def parse_stat(raw):
    """Parse the raw contents of /proc/<pid>/stat into a dict with official field names."""
    raw = raw.strip()
    # comm may contain spaces and is wrapped in parentheses; handle carefully
    # format: pid (comm) state field4 field5 ...
    rparen = raw.rfind(")")
    comm_part = raw[raw.index("(") + 1 : rparen]        # everything between ( and last )
    rest = raw[rparen + 2:].split()                      # fields after ') '
    tokens = [raw.split("(")[0].strip()] + [comm_part] + rest

    result = {}
    for i, name in enumerate(STAT_FIELDS):
        result[name] = tokens[i] if i < len(tokens) else ""
    return result


def parse_status(raw, warn, warned):
    """Parse the raw contents of /proc/<pid>/status into a dict with translated JSON field names.

    Keys present in _STATUS_FIELD_MAP are translated to their mapped name.
    Keys mapped to None are silently dropped without warning.
    Keys absent from the map are dropped; a warning is issued the first time each such key
    is seen (tracked in the `warned` set).
    See §5.8 / Addendum A.3 of the PRD.
    """
    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        raw_key, _, value = line.partition(":")
        raw_key = raw_key.strip()
        if raw_key in _STATUS_FIELD_MAP:
            json_key = _STATUS_FIELD_MAP[raw_key]
            if json_key is None:
                continue  # explicitly ignored
        else:
            if raw_key not in warned:
                warn(f"unknown /proc/pid/status key '{raw_key}' - dropped "
                     f"(add to _STATUS_FIELD_MAP to suppress)")
                warned.add(raw_key)
            continue
        result[json_key] = value.strip()
    return result


def split_sections(block_lines):
    """Map 'stat'/'status' -> (pid, raw text) for the delimited sections of one sample."""
    sections = {}
    current = None
    for line in block_lines[1:]:
        m = _DELIMITER_RE.match(line)
        if m:
            current = m.group("file")
            sections[current] = [int(m.group("pid")), []]
        elif current is not None:
            sections[current][1].append(line)
    return {k: (pid, "\n".join(lines) + "\n") for k, (pid, lines) in sections.items()}


def parse_sample(block_lines, warn, warned):
    timestamp = block_lines[0].strip()
    sections = split_sections(block_lines)
    if "stat" not in sections or "status" not in sections:
        warn(f"sample at {timestamp} lacks a stat or status section; skipping sample")
        return None
    pid = sections["stat"][0]
    try:
        stat = parse_stat(sections["stat"][1])
    except ValueError:
        warn(f"cannot parse stat for sample at {timestamp}; skipping sample")
        return None
    return {
        "timestamp": timestamp,
        "pid": pid,
        "proc_pid_stat": stat,
        "status": parse_status(sections["status"][1], warn, warned),
    }


def parse_log(path, warn=None):
    if warn is None:
        def warn(msg):
            print(f"post-process-proc-log: warning: {msg}", file=sys.stderr)

    with open_collector_file(path) as fh:
        text = fh.read()
    lines = text.splitlines()

    boundaries = [i for i, line in enumerate(lines) if _TIMESTAMP_RE.match(line.strip())]
    if not boundaries:
        print(f"post-process-proc-log: no samples found (no timestamp lines) in {path}",
              file=sys.stderr)
        sys.exit(1)

    warned = set()
    samples = []
    for idx, start in enumerate(boundaries):
        last = idx + 1 == len(boundaries)
        end = len(lines) if last else boundaries[idx + 1]
        if last and not text.endswith("\n"):
            warn(f"last sample at {lines[start].strip()} is cut off; skipping sample")
            continue
        sample = parse_sample(lines[start:end], warn, warned)
        if sample is not None:
            samples.append(sample)

    if not samples:
        print(f"post-process-proc-log: no valid samples parsed from {path}", file=sys.stderr)
        sys.exit(1)
    return samples


def main():
    args = parse_args()
    for sample in parse_log(args.input):
        print(json.dumps(sample))


if __name__ == "__main__":
    main()
