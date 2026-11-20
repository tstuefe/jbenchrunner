#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/post-process-thread-print-log.py
# Post-processes a thread-print.log file (produced by jcmd-logger.sh running "Thread.print" on an
# interval) into JSON Lines, one record per sample with per-thread data, thread counts and CPU
# usage per thread category (spec-post-process.md §3c).
#
# Each sample in thread-print.log looks like (HotSpot):
#
#   <ISO-8601 timestamp>
#   <pid>:
#   <jcmd local time>
#   Full thread dump OpenJDK 64-Bit Server VM (...)
#   ...
#   "main" #3 [1376533] prio=5 os_prio=0 cpu=148.76ms elapsed=0.35s tid=0x... nid=1376533 runnable  [0x...]
#      java.lang.Thread.State: RUNNABLE
#   	at java.util.ArrayList.isEmpty(...)
#   ...
#   "GC Thread#0" os_prio=0 cpu=91.34ms elapsed=1.48s tid=0x... nid=1376547 runnable
#   ...
#   JNI global refs: 9, weak refs: 0
#
# Per thread: "name", "jlt_state" (the java.lang.Thread.State enum value, e.g. "WAITING" for
# "WAITING (on object monitor)"; null for native VM threads), "cpu_since_start_ms" (the cpu=
# value; null if absent), "elapsed_s" (the elapsed= value; null if absent), "cpu_pct" (CPU
# usage since the previous sample, see below), "os_tid" (the nid= value, decimal or 0x-hex;
# null if absent).
#
# Each thread belongs to exactly one category; first matching rule wins:
#   name starts with "C2 CompilerThread"                          -> c2
#   name starts with "C1 CompilerThread"                          -> c1
#   no jlt_state (native VM thread) and name starts with "GC Thread", "G1 ", "Shenandoah " or
#   "Z" + uppercase letter (ZGC: ZDirector, ZWorkerYoung#0, ...)  -> gc
#   jlt_state, at least one "at ..." frame, and name is not one of the JDK-internal
#   "Reference Handler", "Finalizer", "Common-Cleaner"            -> mutator
#   anything else                                                 -> other
# num_threads is the sum of the categories' num_threads.
#
# CPU usage (spec-post-process.md §3c.1), IRIX notation (100 = one core): a thread is matched
# to the previous sample by os_tid (a decreasing elapsed_s means a reused tid, i.e. a new
# thread). "interval_secs" is the median elapsed_s delta of the matched threads; a thread's
# cpu_pct is its cpu_since_start_ms delta (all of it for a new thread) over that interval; a
# category's cpu_pct is the sum over its threads. With --proclog, the process CPU time from
# proclog.jsonl (utime + stime, interpolated to the sample timestamps) minus the sum of all
# categories is the CPU time of threads no dump saw (ended between dumps, or short-lived); it
# is recorded as "unaccounted" (only present with --proclog). The first sample has null CPU
# values.
#
# Record: {"timestamp": ..., "interval_secs": ..., "num_threads": N,
#          "c2": {"num_threads": N, "cpu_pct": ...}, "c1": {...}, "gc": {...},
#          "mutator": {...}, "other": {...}, "unaccounted": {"cpu_pct": ...},
#          "threads": [{"name": ..., "jlt_state": ..., "cpu_since_start_ms": ...,
#                       "elapsed_s": ..., "cpu_pct": ..., "os_tid": ...}, ...]}
#
# Errors: a sample that is not a HotSpot thread dump (no "Full thread dump" line, e.g. OpenJ9 or
# a failed jcmd call), or one cut off before its "JNI global refs:" trailer (JVM killed mid-dump,
# compressor killed) is warned about and skipped. No valid sample at all is a file-level error
# (exit 1).
#
# Usage: post-process-thread-print-log.py [--proclog <proclog.jsonl>] <input-thread-print-log>
# The input may be zstd-compressed (thread-print.log.zst, see collectors.compression).

import argparse
import bisect
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common.collector_files import open_collector_file  # noqa: E402
from common import collector_timestamp  # noqa: E402
from common.collector_timestamp import TIMESTAMP_RE as _TIMESTAMP_RE  # noqa: E402


_DUMP_START_RE = re.compile(r"^Full thread dump ")
# The trailer after the last thread; its presence proves the sample was written completely.
_DUMP_END_RE = re.compile(r"^JNI global refs:")
# Greedy: the name runs up to the last quote (the header fields after it contain none).
_THREAD_HEADER_RE = re.compile(r'^"(?P<name>.*)"(?P<rest>\s.*)?$')
_CPU_RE = re.compile(r"\scpu=(?P<cpu>\d+(?:\.\d+)?)ms\b")
_ELAPSED_RE = re.compile(r"\selapsed=(?P<elapsed>\d+(?:\.\d+)?)s\b")
_NID_RE = re.compile(r"\snid=(?P<nid>0x[0-9a-fA-F]+|\d+)\b")
_STATE_RE = re.compile(r"^\s+java\.lang\.Thread\.State:\s+(?P<state>\S+)")
_FRAME_RE = re.compile(r"^\s+at\s")

_GC_NAME_RE = re.compile(r"^(GC Thread|G1 |Shenandoah |Z[A-Z])")
_NON_MUTATOR_NAMES = frozenset({"Reference Handler", "Finalizer", "Common-Cleaner"})

CATEGORIES = ("c2", "c1", "gc", "mutator", "other")

# proclog.jsonl utime/stime are in clock ticks; 100/s is the Linux default (USER_HZ). The
# benchmark host's value is not recorded.
_CLOCK_TICKS_PER_SEC = 100


def parse_args():
    parser = argparse.ArgumentParser(
        description="Post-process a thread-print.log (jcmd Thread.print) into JSON Lines."
    )
    parser.add_argument("--proclog", metavar="PROCLOG_JSONL",
                        help="proclog.jsonl of the same run; its process CPU time minus the "
                             "threads' is recorded as \"unaccounted\"")
    parser.add_argument("input", metavar="INPUT", help="Input thread-print log file")
    return parser.parse_args()


def category_for(name, jlt_state, has_frames):
    """Return the category a thread is booked under."""
    if name.startswith("C2 CompilerThread"):
        return "c2"
    if name.startswith("C1 CompilerThread"):
        return "c1"
    if jlt_state is None and _GC_NAME_RE.match(name):
        return "gc"
    if jlt_state is not None and has_frames and name not in _NON_MUTATOR_NAMES:
        return "mutator"
    return "other"


def _parse_header(match):
    rest = match.group("rest") or ""
    cpu = _CPU_RE.search(rest)
    elapsed = _ELAPSED_RE.search(rest)
    nid = _NID_RE.search(rest)
    return {
        "name": match.group("name"),
        "jlt_state": None,
        "cpu_since_start_ms": float(cpu.group("cpu")) if cpu else None,
        "elapsed_s": float(elapsed.group("elapsed")) if elapsed else None,
        "cpu_pct": None,
        "os_tid": int(nid.group("nid"), 0) if nid else None,
    }


def scan_dump(lines):
    """Scan one sample's lines; return a list of (thread dict, has_frames) or None.

    None means the sample is not a HotSpot thread dump (no "Full thread dump" line) or is
    incomplete: no "JNI global refs:" trailer (cut off).
    """
    threads = []
    current = None
    started = False
    for line in lines:
        line = line.rstrip("\n")
        if not started:
            started = bool(_DUMP_START_RE.match(line))
            continue
        if _DUMP_END_RE.match(line):
            return threads
        header = _THREAD_HEADER_RE.match(line)
        if header:
            current = [_parse_header(header), False]
            threads.append(current)
            continue
        if current is None:
            continue
        state = _STATE_RE.match(line)
        if state:
            current[0]["jlt_state"] = state.group("state")
        elif _FRAME_RE.match(line):
            current[1] = True
    return None


def parse_sample(block_lines, warn):
    """Return (record, categories) for one sample, or None if it is skipped.

    *categories* holds each thread's category, parallel to record["threads"]. CPU values
    are left null; add_cpu() fills them in.
    """
    timestamp = block_lines[0].strip()
    threads = scan_dump(block_lines[1:])
    if threads is None:
        warn(f"no complete HotSpot thread dump in sample at {timestamp} "
             "(truncated, jcmd failed, or non-HotSpot format?); skipping sample")
        return None
    categories = [category_for(thread["name"], thread["jlt_state"], has_frames)
                  for thread, has_frames in threads]
    record = {"timestamp": timestamp, "interval_secs": None, "num_threads": len(threads)}
    for category in CATEGORIES:
        record[category] = {"num_threads": categories.count(category), "cpu_pct": None}
    record["threads"] = [thread for thread, _has_frames in threads]
    return record, categories


def _cpu_usable(thread):
    return (thread["os_tid"] is not None and thread["cpu_since_start_ms"] is not None
            and thread["elapsed_s"] is not None)


def load_process_cpu(path):
    """Return a function mapping epoch seconds to the process's cumulative CPU seconds,
    linearly interpolated from proclog.jsonl, or None outside the covered time span.
    Returns None if the file yields fewer than two usable samples."""
    points = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
                t = collector_timestamp.parse(obj["timestamp"]).timestamp()
                stat = obj["proc_pid_stat"]
                cpu = (int(stat["utime"]) + int(stat["stime"])) / _CLOCK_TICKS_PER_SEC
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            points.append((t, cpu))
    points.sort()
    if len(points) < 2:
        return None
    times = [t for t, _cpu in points]

    def cpu_at(t):
        if t < times[0] or t > times[-1]:
            return None
        i = bisect.bisect_left(times, t)
        if times[i] == t:
            return points[i][1]
        (t0, c0), (t1, c1) = points[i - 1], points[i]
        return c0 + (c1 - c0) * (t - t0) / (t1 - t0)

    return cpu_at


def _unaccounted_pct(prev_record, record, threads_pct, process_cpu):
    """Process CPU % over the two samples' timestamp span minus *threads_pct*, or None."""
    t0 = collector_timestamp.parse(prev_record["timestamp"]).timestamp()
    t1 = collector_timestamp.parse(record["timestamp"]).timestamp()
    c0, c1 = process_cpu(t0), process_cpu(t1)
    if c0 is None or c1 is None or t1 <= t0:
        return None
    return (c1 - c0) / (t1 - t0) * 100.0 - threads_pct


def add_interval_cpu(prev_record, record, categories, process_cpu=None):
    """Fill in *record*'s CPU values for the interval since *prev_record*."""
    prev_by_tid = {t["os_tid"]: t for t in prev_record["threads"] if _cpu_usable(t)}

    def match(thread):
        prev = prev_by_tid.get(thread["os_tid"])
        return prev if prev is not None and thread["elapsed_s"] >= prev["elapsed_s"] else None

    deltas = [t["elapsed_s"] - match(t)["elapsed_s"]
              for t in record["threads"] if _cpu_usable(t) and match(t) is not None]
    interval = statistics.median(deltas) if deltas else 0.0
    if interval <= 0:
        return
    record["interval_secs"] = round(interval, 3)

    category_pct = dict.fromkeys(CATEGORIES, 0.0)
    for thread, category in zip(record["threads"], categories):
        if not _cpu_usable(thread):
            continue
        prev = match(thread)
        cpu_ms = thread["cpu_since_start_ms"] - (prev["cpu_since_start_ms"] if prev else 0.0)
        pct = cpu_ms / 10.0 / interval  # ms / (1000 * s) * 100
        thread["cpu_pct"] = round(pct, 1)
        category_pct[category] += pct

    for category in CATEGORIES:
        record[category]["cpu_pct"] = round(category_pct[category], 1)

    if process_cpu is not None:
        unaccounted = _unaccounted_pct(prev_record, record, sum(category_pct.values()),
                                       process_cpu)
        if unaccounted is not None:
            record["unaccounted"]["cpu_pct"] = round(unaccounted, 1)


def add_cpu(samples, process_cpu=None):
    """Fill in the CPU values of all (record, categories) *samples*, in order."""
    for (prev_record, _prev_categories), (record, categories) in zip(samples, samples[1:]):
        add_interval_cpu(prev_record, record, categories, process_cpu)


def parse_log(path, warn=None, proclog=None):
    if warn is None:
        def warn(msg):
            print(f"post-process-thread-print-log: warning: {msg}", file=sys.stderr)

    with open_collector_file(path) as fh:
        lines = fh.read().splitlines()

    boundaries = [i for i, line in enumerate(lines) if _TIMESTAMP_RE.match(line.strip())]
    if not boundaries:
        print(f"post-process-thread-print-log: no samples found (no timestamp lines) in {path}",
              file=sys.stderr)
        sys.exit(1)

    samples = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(lines)
        sample = parse_sample(lines[start:end], warn)
        if sample is not None:
            samples.append(sample)

    if not samples:
        print(f"post-process-thread-print-log: no valid samples parsed from {path}",
              file=sys.stderr)
        sys.exit(1)

    process_cpu = None
    if proclog is not None:
        process_cpu = load_process_cpu(proclog)
        if process_cpu is None:
            warn(f"no usable samples in {proclog}; no 'unaccounted' CPU recorded")
        else:
            for record, _categories in samples:
                # Keep "threads" last: re-insert it after the new key.
                threads = record.pop("threads")
                record["unaccounted"] = {"cpu_pct": None}
                record["threads"] = threads
    add_cpu(samples, process_cpu)
    return [record for record, _categories in samples]


def main():
    args = parse_args()
    for sample in parse_log(args.input, proclog=args.proclog):
        print(json.dumps(sample))


if __name__ == "__main__":
    main()
