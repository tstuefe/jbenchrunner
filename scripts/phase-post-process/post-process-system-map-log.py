#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/post-process-system-map-log.py
# Post-processes a system.map.log file (produced by jcmd-logger.sh running "System.map" on an
# interval) into JSON Lines, one record per sample with vsize/rss summed per "info" category.
#
# Each sample in system.map.log looks like:
#
#   <ISO-8601 timestamp>
#   <pid>:
#   Memory mappings:
#   ...
#   from   to   vsize prot   rss   hugetlb pgsz notes   info   file
#   ==========================================================
#   <range> <vsize> <prot> <rss> <hugetlb> <pgsz> <notes> <info> <file>
#   ...
#   (blank line)
#   Number of mappings: N
#                vsize: NNN (...)
#   ...
#
# Each table row has 9 whitespace-separated fields (the file field may contain spaces).
# vsize (field 2) and rss (field 4) are summed per category, chosen from the info field
# (field 8); first matching rule wins:
#   "-"                 -> "unknown", or, if the file is a shared library (*.so, *.so.N...):
#                          "sharedlib_text" (prot r-xp) / "sharedlib_data"
#   contains ","        -> "combined"
#   starts with "STACK-" -> "stacks"
#   anything else       -> a leading "mt" is stripped when followed by an uppercase letter
#                          (e.g. "mtChunkMmap"), camelCase boundaries get "_" inserted, then
#                          lowercased with non-alphanumeric runs collapsed to "_"
#                          (e.g. "mtChunkMmap" -> "chunk_mmap", "CODE" -> "code")
#
# Record: {"timestamp": ..., "pid": ..., "total": {"vsize": bytes, "rss": bytes},
#          "<category>": {"vsize": bytes, "rss": bytes}, ...}
# "total" sums vsize/rss across all categories in the sample.
#
# Errors: a sample without a table, or one cut off before its summary section (JVM killed
# mid-dump, compressor killed) is warned about and skipped; an unparseable row is warned about
# and skipped. No valid sample at all is a file-level error (exit 1).
#
# Usage: post-process-system-map-log.py <input-system-map-log>
# The input may be zstd-compressed (system.map.log.zst, see collectors.compression).

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common.collector_files import open_collector_file  # noqa: E402
from common.collector_timestamp import TIMESTAMP_RE as _TIMESTAMP_RE  # noqa: E402


_PID_RE = re.compile(r"^(?P<pid>\d+):\s*$")
_SEPARATOR_RE = re.compile(r"^=+\s*$")
# The summary after the table; its presence proves the sample was written completely.
_SUMMARY_RE = re.compile(r"^\s*(Number of mappings:|vsize:)")

# Data row: range, vsize, prot, rss, hugetlb, pgsz, notes, info, file (file may contain spaces).
_NUM_FIELDS = 9
_VSIZE, _PROT, _RSS, _INFO, _FILE = 1, 2, 3, 7, 8

_SHARED_LIB_RE = re.compile(r"\.so(\.\d+)*$")

_MT_PREFIX_RE = re.compile(r"^mt(?=[A-Z])")
_CAMEL_BOUNDARY1_RE = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_BOUNDARY2_RE = re.compile(r"([a-z0-9])([A-Z])")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Post-process a system.map.log (jcmd System.map) into JSON Lines."
    )
    parser.add_argument("input", metavar="INPUT", help="Input system.map log file")
    return parser.parse_args()


def snake_case(name):
    """Convert a category name to snake_case: strip a leading "mt" when it is followed by
    an uppercase letter (e.g. "mtChunkMmap"), split camelCase boundaries, then lowercase."""
    name = _MT_PREFIX_RE.sub("", name)
    name = _CAMEL_BOUNDARY1_RE.sub(r"\1_\2", name)
    name = _CAMEL_BOUNDARY2_RE.sub(r"\1_\2", name)
    return name.lower()


def category_for(info):
    """Map a raw info column value to the category it is booked under."""
    if info == "-":
        return "unknown"
    if "," in info:
        return "combined"
    if info.startswith("STACK-"):
        return "stacks"
    return re.sub(r"[^a-z0-9]+", "_", snake_case(info)).strip("_") or "unknown"


def shared_library_category(fields):
    """Return the artificial shared-library category for an info-less row, or None."""
    if len(fields) <= _FILE or not _SHARED_LIB_RE.search(fields[_FILE].strip()):
        return None
    return "sharedlib_text" if fields[_PROT] == "r-xp" else "sharedlib_data"


def scan_table(lines, warn=None):
    """Scan one sample's lines; return {category: {"vsize": int, "rss": int}} or None.

    None means the sample has no table, or is incomplete: no summary section after the
    table (cut off). Malformed rows are reported through *warn* and skipped.
    """
    totals = {}
    state = "header"   # header -> table -> after_table
    for line in lines:
        line = line.rstrip("\n")
        if state == "header":
            if _SEPARATOR_RE.match(line):
                state = "table"
        elif state == "table":
            if not line.strip():
                state = "after_table"
                continue
            fields = line.split(None, _NUM_FIELDS - 1)
            try:
                if len(fields) < _NUM_FIELDS - 1:
                    raise ValueError("too few fields")
                vsize = int(fields[_VSIZE])
                rss = int(fields[_RSS])
            except ValueError:
                if warn:
                    warn(f"unparseable System.map row skipped: {line!r}")
                continue
            category = shared_library_category(fields) if fields[_INFO] == "-" else None
            entry = totals.setdefault(category or category_for(fields[_INFO]),
                                      {"vsize": 0, "rss": 0})
            entry["vsize"] += vsize
            entry["rss"] += rss
        elif _SUMMARY_RE.match(line):
            return totals
    return None


def parse_sample(block_lines, warn):
    timestamp = block_lines[0].strip()
    pid_match = _PID_RE.match(block_lines[1].strip()) if len(block_lines) > 1 else None
    if not pid_match:
        warn(f"cannot parse pid line for sample at {timestamp}; skipping sample")
        return None
    totals = scan_table(block_lines[2:], lambda m: warn(f"{m} (sample at {timestamp})"))
    if totals is None:
        warn(f"no complete System.map table in sample at {timestamp} "
             "(truncated or jcmd failed?); skipping sample")
        return None
    total = {"vsize": sum(e["vsize"] for e in totals.values()),
              "rss": sum(e["rss"] for e in totals.values())}
    record = {"timestamp": timestamp, "pid": int(pid_match.group("pid")), "total": total}
    record.update(totals)
    return record


def parse_log(path, warn=None):
    if warn is None:
        def warn(msg):
            print(f"post-process-system-map-log: warning: {msg}", file=sys.stderr)

    with open_collector_file(path) as fh:
        lines = fh.read().splitlines()

    boundaries = [i for i, line in enumerate(lines) if _TIMESTAMP_RE.match(line.strip())]
    if not boundaries:
        print(f"post-process-system-map-log: no samples found (no timestamp lines) in {path}",
              file=sys.stderr)
        sys.exit(1)

    samples = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(lines)
        sample = parse_sample(lines[start:end], warn)
        if sample is not None:
            samples.append(sample)

    if not samples:
        print(f"post-process-system-map-log: no valid samples parsed from {path}", file=sys.stderr)
        sys.exit(1)
    return samples


def main():
    args = parse_args()
    for sample in parse_log(args.input):
        print(json.dumps(sample))


if __name__ == "__main__":
    main()
