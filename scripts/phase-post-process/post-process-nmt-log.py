#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/post-process-nmt-log.py
# Post-processes an nmt.log file (produced by jcmd-logger.sh running
# "VM.native_memory summary" on an interval) into JSON Lines.
#
# Each sample in nmt.log looks like:
#
#   <ISO-8601 timestamp>
#   <pid>:
#
#   Native Memory Tracking:
#   ...
#   Total: reserved=NNNNKB, committed=NNNNKB
#          malloc: NNNNKB #NNNN, peak=NNNNKB #NNNN
#          mmap:   reserved=NNNNKB, committed=NNNNKB
#
#   -                 Java Heap (reserved=NNNNKB, committed=NNNNKB)
#                              (mmap: reserved=NNNNKB, committed=NNNNKB, at peak)
#   ...
#
# Categories are resolved dynamically (they vary across JDK releases) and
# their names are sanitized to lowercase with non-alphanumeric runs collapsed
# to a single underscore. Arena allocations (arena=.../arena peak=...) are
# deliberately ignored: the "Arena Chunk" category already aggregates them.
#
# All size fields (top-level totals and per-category mmap_committed/malloc/
# malloc_peak) are converted from the source KB to MB, as a float rounded to
# two decimal places.
#
# Errors are handled at three levels:
#   - file-level: no timestamp lines found at all (or no sample survives
#     parsing) -> error, exit 1.
#   - sample-level: a sample's pid line or Total: block cannot be parsed
#     (e.g. truncated because the JVM was killed mid-dump) -> warn, skip
#     that sample, keep going.
#   - category-level: a single category's body cannot be parsed -> warn,
#     omit that category from that sample, keep the rest of the sample.
#
# Usage: post-process-nmt-log.py <input-nmt-log>
# The input may be zstd-compressed (nmt.log.zst, see collectors.compression).

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common.collector_files import open_collector_file  # noqa: E402
from common.collector_timestamp import TIMESTAMP_RE as _TIMESTAMP_RE  # noqa: E402


_PID_RE = re.compile(r"^(?P<pid>\d+):\s*$")

_TOTAL_RE = re.compile(
    r"Total:\s*reserved=\d+KB,\s*committed=(?P<total>\d+)KB\s*\n"
    r"\s*malloc:\s*(?P<malloc>\d+)KB\s*#\d+,\s*peak=(?P<malloc_peak>\d+)KB\s*#\d+\s*\n"
    r"\s*mmap:\s*reserved=\d+KB,\s*committed=(?P<mmap_committed>\d+)KB"
)

_CATEGORY_HEADER_RE = re.compile(
    r"^-\s*(?P<name>.+?)\s+\(reserved=\d+KB,\s*committed=\d+KB.*\)\s*$",
    re.MULTILINE,
)

_MMAP_RE = re.compile(r"\(mmap:\s*reserved=\d+KB,\s*committed=(?P<committed>\d+)KB")

# tag=<name> can itself contain spaces (e.g. "tag=Native Memory Tracking",
# "tag=Arena Chunk"), and the trailing "#<count>" is omitted entirely when
# the count is zero (e.g. "(malloc=0KB tag=Logging) (peak=1KB #1)") — so both
# the tag/malloc and the peak "#<count>" are optional here.
_MALLOC_RE = re.compile(
    r"\(malloc=(?P<malloc>\d+)KB\s+tag=.+?(?:\s+#\d+)?\)\s*"
    r"(?:\(peak=(?P<peak>\d+)KB(?:\s+#\d+)?\)|\(at peak\))?"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Post-process an nmt.log (jcmd VM.native_memory summary) into JSON Lines."
    )
    parser.add_argument(
        "input",
        metavar="INPUT",
        help="Input nmt log file",
    )
    return parser.parse_args()


def kb_to_mb(kb: int) -> float:
    """Convert a KB integer to a MB float rounded to two decimal places."""
    return round(kb / 1024.0, 2)


def sanitize_category_name(name: str) -> str:
    """Lowercase a category name and collapse non-alphanumeric runs to '_'."""
    name = re.sub(r"[^a-z0-9]+", "_", name.strip().lower())
    return name.strip("_")


def parse_sample(block_lines, warn):
    timestamp = block_lines[0].strip()

    pid_line = block_lines[1].strip() if len(block_lines) > 1 else ""
    pid_match = _PID_RE.match(pid_line)
    if not pid_match:
        warn(f"cannot parse pid line for sample at {timestamp}: {pid_line!r}; skipping sample")
        return None
    pid = int(pid_match.group("pid"))

    block_text = "\n".join(block_lines)

    total_match = _TOTAL_RE.search(block_text)
    if not total_match:
        warn(f"cannot parse Total: block for sample at {timestamp}; skipping sample")
        return None

    sample = {
        "timestamp": timestamp,
        "pid": pid,
        "total": kb_to_mb(int(total_match.group("total"))),
        "total_malloc": kb_to_mb(int(total_match.group("malloc"))),
        "total_malloc_peak": kb_to_mb(int(total_match.group("malloc_peak"))),
        "total_mmap_committed": kb_to_mb(int(total_match.group("mmap_committed"))),
    }

    headers = list(_CATEGORY_HEADER_RE.finditer(block_text))
    if not headers:
        warn(f"no categories parsed for sample at {timestamp}")

    for i, header in enumerate(headers):
        name = header.group("name")
        body_start = header.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(block_text)
        body = block_text[body_start:body_end]

        try:
            mmap_committed = 0
            mmap_match = _MMAP_RE.search(body)
            if mmap_match:
                mmap_committed = int(mmap_match.group("committed"))

            malloc = 0
            malloc_peak = 0
            malloc_match = _MALLOC_RE.search(body)
            if malloc_match:
                malloc = int(malloc_match.group("malloc"))
                peak = malloc_match.group("peak")
                malloc_peak = int(peak) if peak else malloc

            key = sanitize_category_name(name)
            if not key:
                raise ValueError(f"category name sanitizes to empty string: {name!r}")

            sample[key] = {
                "mmap_committed": kb_to_mb(mmap_committed),
                "malloc": kb_to_mb(malloc),
                "malloc_peak": kb_to_mb(malloc_peak),
            }
        except Exception as e:
            warn(f"failed to parse category {name!r} in sample at {timestamp}: {e}; skipping category")
            continue

    return sample


def parse_log(path: str, warn=None):
    if warn is None:
        def warn(msg):
            print(f"post-process-nmt-log: warning: {msg}", file=sys.stderr)

    with open_collector_file(path) as fh:
        lines = fh.read().splitlines()

    boundaries = [i for i, line in enumerate(lines) if _TIMESTAMP_RE.match(line.strip())]
    if not boundaries:
        print(f"post-process-nmt-log: no samples found (no timestamp lines) in {path}", file=sys.stderr)
        sys.exit(1)

    samples = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(lines)
        sample = parse_sample(lines[start:end], warn)
        if sample is not None:
            samples.append(sample)

    if not samples:
        print(f"post-process-nmt-log: no valid samples parsed from {path}", file=sys.stderr)
        sys.exit(1)

    return samples


def main():
    args = parse_args()
    for sample in parse_log(args.input):
        print(json.dumps(sample))


if __name__ == "__main__":
    main()
