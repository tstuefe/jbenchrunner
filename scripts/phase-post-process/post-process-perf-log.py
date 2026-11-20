#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/post-process-perf-log.py
# Post-processes a perf-logger.sh output file into JSON Lines.
# Reads the start timestamp and monitored PID from the log header, then parses
# the perf stat interval CSV lines (perf stat -x ';', docs/spec-execute.md §5.2)
# into JSON sample objects written one per line to stdout.
#
# Usage: post-process-perf-log.py <input-perf-log>

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common import collector_timestamp  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(
        description="Post-process a perf-logger.sh log into JSON Lines."
    )
    parser.add_argument(
        "input",
        metavar="INPUT",
        help="Input perf log file",
    )
    return parser.parse_args()


# Data line fields of 'perf stat -x ";" --interval-print' (spec-execute.md §5.2):
#   <time>;<value>;<unit>;<event>;<run time>;<enabled pct>;<metric value>;<metric unit>
# <value> is an integer, a decimal (e.g. task-clock in msec), or one of the
# special values below. Lines with an empty value (metric-only lines, e.g. the
# topdown metrics of newer perf versions) carry no count and are skipped, as
# are the metric columns.
_SPECIAL_VALUES = ("<not counted>", "<not supported>")
_INT_RE = re.compile(r"\d+")


def parse_value(raw: str):
    """Return the count as int or float, None for a special value; raise ValueError otherwise."""
    if raw in _SPECIAL_VALUES:
        return None
    if _INT_RE.fullmatch(raw):
        return int(raw)
    return float(raw)


def compute_timestamp(start_dt: datetime, relative_seconds: float) -> str:
    """Return ISO 8601 string with milliseconds for start_dt + relative_seconds."""
    delta = timedelta(seconds=relative_seconds)
    ts = start_dt + delta
    return ts.isoformat(timespec="milliseconds")


def strip_trailing_slashes(name: str) -> str:
    return name.rstrip("/")


def parse_log(path: str):
    with open(path, "r") as fh:
        lines = fh.readlines()

    if not lines:
        print(f"post-process-perf-log: empty input file: {path}", file=sys.stderr)
        sys.exit(1)

    # Line 1: start datetime
    start_str = lines[0].strip()
    try:
        start_dt = collector_timestamp.parse(start_str)
    except ValueError:
        print(
            f"post-process-perf-log: cannot parse start datetime: {start_str!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Line 2: "Monitoring PID <pid>"
    pid_line = lines[1].strip() if len(lines) > 1 else ""
    pid_match = re.match(r"Monitoring PID (\d+)", pid_line)
    if not pid_match:
        print(
            f"post-process-perf-log: cannot parse PID line: {pid_line!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    pid = int(pid_match.group(1))

    # Remaining lines: header comment(s) then data
    samples = []          # list of completed sample dicts
    current_time = None   # relative time float for current interval
    current_counters = {} # event_key -> counter dict

    def flush():
        nonlocal current_time, current_counters
        if current_time is not None and current_counters:
            samples.append({
                "timestamp": compute_timestamp(start_dt, current_time),
                "pid": pid,
                "counters": current_counters,
            })
        current_time = None
        current_counters = {}

    for line in lines[2:]:
        if line.startswith("#") or line.strip() == "":
            continue

        fields = [f.strip() for f in line.rstrip("\n").split(";")]
        if len(fields) < 4 or not fields[3]:
            # Ignore unrecognised lines (e.g. perf messages)
            continue
        try:
            rel_time = float(fields[0])
            value = parse_value(fields[1])
        except ValueError:
            continue
        raw_event = fields[3]
        unit = fields[2] or None
        raw_pct = fields[5] if len(fields) > 5 else ""

        # When a new time stamp appears, flush the previous interval
        if rel_time != current_time:
            flush()
            current_time = rel_time

        # Multiplex percentage
        pct = float(raw_pct) if raw_pct else 100.0

        # Event key: strip trailing slashes, then replace any character outside
        # A-Za-z0-9_ (colons from modifiers, slashes from PMU group names, hyphens)
        # with underscores.
        event_key = re.sub(r"[^A-Za-z0-9_]", "_", strip_trailing_slashes(raw_event))

        current_counters[event_key] = {
            "value": value,
            "unit": unit,
            "multiplex_pct": pct,
        }

    flush()
    return samples


def main():
    args = parse_args()
    for sample in parse_log(args.input):
        print(json.dumps(sample))


if __name__ == "__main__":
    main()
