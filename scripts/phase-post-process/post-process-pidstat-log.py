#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/post-process-pidstat-log.py
# Post-processes a pidstat-logger.sh output file into JSON Lines.
# Reads the start timestamp and monitored PID from the log header, then parses
# 'pidstat -h -H -rdu' sample lines into JSON sample objects written one per
# line to stdout.
#
# Usage: post-process-pidstat-log.py <input-pidstat-log>

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common import collector_timestamp  # noqa: E402

# Maps raw pidstat column headers (as printed under -h) to compliant JSON
# field names. Rules: A-Za-z0-9_ only, snake_case, "pct" for %, "per_sec" for /s.
# UID, PID, and Command are intentionally absent: PID duplicates the top-level
# "pid" field, and UID/Command carry no benchmarking value here.
_FIELD_MAP = {
    "%usr":      "pct_usr",
    "%system":   "pct_system",
    "%guest":    "pct_guest",
    "%wait":     "pct_wait",
    "%CPU":      "pct_cpu",
    "CPU":       "cpu",
    "minflt/s":  "minflt_per_sec",
    "majflt/s":  "majflt_per_sec",
    "VSZ":       "vsz",
    "RSS":       "rss",
    "%MEM":      "pct_mem",
    "kB_rd/s":   "kb_rd_per_sec",
    "kB_wr/s":   "kb_wr_per_sec",
    "kB_ccwr/s": "kb_ccwr_per_sec",
    "iodelay":   "iodelay",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Post-process a pidstat-logger.sh log into JSON Lines."
    )
    parser.add_argument(
        "input",
        metavar="INPUT",
        help="Input pidstat log file",
    )
    return parser.parse_args()


def parse_log(path: str):
    with open(path, "r") as fh:
        lines = fh.readlines()

    if not lines:
        print(f"post-process-pidstat-log: empty input file: {path}", file=sys.stderr)
        sys.exit(1)

    # Line 1: start datetime. Each sample below carries its own absolute epoch
    # timestamp (pidstat's -H option), so this is only validated for a
    # well-formed header, not used to compute sample timestamps.
    start_str = lines[0].strip()
    try:
        collector_timestamp.parse(start_str)
    except ValueError:
        print(
            f"post-process-pidstat-log: cannot parse start datetime: {start_str!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Line 2: "Monitoring PID <pid>"
    pid_line = lines[1].strip() if len(lines) > 1 else ""
    pid_match = re.match(r"Monitoring PID (\d+)", pid_line)
    if not pid_match:
        print(
            f"post-process-pidstat-log: cannot parse PID line: {pid_line!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    pid = int(pid_match.group(1))

    # Remaining lines: a "Linux ..." sysinfo banner, a blank line, a single
    # "#"-prefixed header line (pidstat -h prints exactly one, up front), then
    # one data row per sample, and optionally a trailing "Average:" summary row.
    header = None
    samples = []

    for line in lines[2:]:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            header = stripped.lstrip("#").split()
            continue

        if header is None:
            # Sysinfo banner or other line seen before any header — skip.
            continue

        parts = stripped.split()
        if len(parts) != len(header):
            # Unrecognised/truncated line — skip.
            continue

        row = dict(zip(header, parts))

        try:
            epoch_seconds = int(row["Time"])
        except (KeyError, ValueError):
            # Not a data row (e.g. the trailing "Average:" summary) — skip.
            continue

        sample = {
            "timestamp": datetime.fromtimestamp(epoch_seconds).astimezone().isoformat(timespec="milliseconds"),
            "pid": pid,
        }
        for raw_field, json_field in _FIELD_MAP.items():
            if raw_field in row:
                sample[json_field] = row[raw_field]

        samples.append(sample)

    return samples


def main():
    args = parse_args()
    for sample in parse_log(args.input):
        print(json.dumps(sample))


if __name__ == "__main__":
    main()
