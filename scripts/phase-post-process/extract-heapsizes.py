#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Extract heap sizes before/after each GC from a verbose GC log.
# Supports both OpenJ9 (XML) and OpenJDK (text) GC log formats.
# Auto-detects the format from the log content.
#
# Usage: extract-heapsizes.py <gc.log | result-dir>
#
# The log may be zstd-compressed (gc.log.zst); post-processing compresses it
# after use (spec-post-process.md §1).
#
# J9 format: parses <gc-start>/<gc-end>/<mem-info> XML blocks.
# OpenJDK format: parses [timestamp] GC(id) ... Xbefore->Xafter(Xtotal) lines.
#   Supports G1, Parallel, Serial, Shenandoah, ZGC.
#
# Timestamp formats accepted (OpenJDK):
#   [0.257s]                          -- elapsed seconds from JVM start
#   [2025-06-04T14:37:23.236+0300]    -- absolute ISO 8601
#
# Output columns:
#   Elapsed(s)  Type  GCID  GCPhase  Used(MB)
#
# Summary: max and time-weighted average of after-GC heap sizes.

import argparse
import re
import sys
import os
from datetime import datetime

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.collector_files import open_collector_file, resolve_collector_file  # noqa: E402


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def resolve_log(path):
    """
    Resolve a path to a GC log file.
    Returns the log file path, or None if no log found (for directories).
    Exits with error for non-existent paths or files.
    """
    if os.path.isdir(path):
        # Prefer gc-all.log if it exists, otherwise use gc.log; either may be .zst.
        # None means no log found (will be ignored).
        return (resolve_collector_file(path, "gc-all.log")
                or resolve_collector_file(path, "gc.log"))
    if os.path.isfile(path):
        return path
    print(f"Error: {path}: no such file or directory", file=sys.stderr)
    sys.exit(1)


def detect_format(log_path):
    with open_collector_file(log_path) as f:
        for _, line in zip(range(20), f):
            if any(tag in line for tag in ("<verbosegc", "<initialized", "<?xml", "<gc-start")):
                return 'j9'
    return 'openjdk'


# ---------------------------------------------------------------------------
# J9 parser
# ---------------------------------------------------------------------------

def _get_attr(line, name):
    m = re.search(rf'\b{name}="([^"]+)"', line)
    return m.group(1) if m else None


def _j9_die(msg, lineno, line):
    print(f"Error at line {lineno}: {msg}", file=sys.stderr)
    print(f"  Line: {line.rstrip()}", file=sys.stderr)
    sys.exit(1)


def parse_j9(log_path):
    t0             = None
    max_heap_bytes = None
    block          = None
    events         = {}   # contextid -> {start_ts, start_used, end_ts, end_used}

    with open_collector_file(log_path) as f:
        for lineno, line in enumerate(f, 1):

            if "<initialized" in line:
                ts_s = _get_attr(line, "timestamp")
                if ts_s and t0 is None:
                    t0 = datetime.fromisoformat(ts_s)
                continue

            if max_heap_bytes is None and 'name="maxHeapSize"' in line:
                val = _get_attr(line, "value")
                if val:
                    max_heap_bytes = int(val, 16)
                continue

            if "<gc-start" in line and not line.lstrip().startswith("</"):
                if block is not None:
                    _j9_die(f"<gc-start> inside a <{block['kind']}> block", lineno, line)
                ctx_s = _get_attr(line, "contextid")
                ts_s  = _get_attr(line, "timestamp")
                if not ctx_s or not ts_s:
                    _j9_die("missing contextid or timestamp in <gc-start>", lineno, line)
                ts = datetime.fromisoformat(ts_s)
                if t0 is None:
                    t0 = ts
                gc_type_s = _get_attr(line, "type")
                block = {"kind": "start", "contextid": int(ctx_s),
                         "timestamp": ts, "got_mem_info": False,
                         "gc_type": gc_type_s}
                continue

            if "<gc-end" in line and not line.lstrip().startswith("</"):
                if block is not None:
                    _j9_die(f"<gc-end> inside a <{block['kind']}> block", lineno, line)
                ctx_s = _get_attr(line, "contextid")
                ts_s  = _get_attr(line, "timestamp")
                if not ctx_s or not ts_s:
                    _j9_die("missing contextid or timestamp in <gc-end>", lineno, line)
                block = {"kind": "end", "contextid": int(ctx_s),
                         "timestamp": datetime.fromisoformat(ts_s), "got_mem_info": False}
                continue

            if "</gc-start>" in line:
                if block is None or block["kind"] != "start":
                    _j9_die("unexpected </gc-start>", lineno, line)
                if not block["got_mem_info"]:
                    _j9_die(f"</gc-start> for contextid={block['contextid']} without <mem-info>",
                            lineno, line)
                block = None
                continue

            if "</gc-end>" in line:
                if block is None or block["kind"] != "end":
                    _j9_die("unexpected </gc-end>", lineno, line)
                if not block["got_mem_info"]:
                    _j9_die(f"</gc-end> for contextid={block['contextid']} without <mem-info>",
                            lineno, line)
                block = None
                continue

            if "<mem-info" in line:
                if block is None:
                    continue
                free_s  = _get_attr(line, "free")
                total_s = _get_attr(line, "total")
                if not free_s or not total_s:
                    _j9_die("missing free or total in <mem-info>", lineno, line)
                if block["got_mem_info"]:
                    continue
                used = int(total_s) - int(free_s)
                ctx  = block["contextid"]
                ev   = events.setdefault(ctx, {})
                if block["kind"] == "start":
                    ev["start_ts"]   = block["timestamp"]
                    ev["start_used"] = used
                    ev["gc_type"]    = block.get("gc_type")
                else:
                    ev["end_ts"]   = block["timestamp"]
                    ev["end_used"] = used
                block["got_mem_info"] = True
                continue

    if block is not None:
        print(f"Error: truncated log — unclosed <gc-{block['kind']}> "
              f"for contextid={block['contextid']}", file=sys.stderr)
        sys.exit(1)

    raw = []
    for ctx, ev in events.items():
        raw_type = ev.get("gc_type")
        gc_type  = "GLOBAL" if raw_type == "global" else None
        if "start_ts" in ev and "start_used" in ev:
            raw.append((ev["start_ts"], ctx, gc_type, ev["start_used"], False))
        if "end_ts" in ev and "end_used" in ev:
            raw.append((ev["end_ts"], ctx, gc_type, ev["end_used"], True))

    if not raw:
        print("No GC events found.", file=sys.stderr)
        return [], None

    raw.sort(key=lambda p: p[0])
    if t0 is None:
        t0 = raw[0][0]

    points = [
        ((ts - t0).total_seconds(), ctx, gc_type, used / (1024 * 1024), is_after)
        for ts, ctx, gc_type, used, is_after in raw
    ]
    max_heap_mb = max_heap_bytes / (1024 * 1024) if max_heap_bytes is not None else None
    return points, max_heap_mb


# ---------------------------------------------------------------------------
# OpenJDK parser
# ---------------------------------------------------------------------------

_SIZE_RE = re.compile(r'^(\d+(?:\.\d+)?)([KMGkmg])$')

def _size_to_mb(s):
    m = _SIZE_RE.match(s.strip())
    if not m:
        raise ValueError(f"unrecognised size: {s!r}")
    v, unit = float(m.group(1)), m.group(2).upper()
    return v / 1024 if unit == 'K' else v if unit == 'M' else v * 1024


_TS_ELAPSED_RE = re.compile(r'^\[(\d+\.\d+)s\]')
_TS_ABSOLUTE_RE = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+(?:[+-]\d{4}|Z)?)\]')
_GCID_RE     = re.compile(r'\bGC\((\d+)\)')
_HEAP_STD_RE = re.compile(
    r'(\d+(?:\.\d+)?[KMGkmg])\s*->\s*(\d+(?:\.\d+)?[KMGkmg])'
    r'\((\d+(?:\.\d+)?[KMGkmg])\)'
)
_HEAP_ZGC_RE = re.compile(
    r'(\d+(?:\.\d+)?[KMGkmg])\(\d+%\)\s*->\s*(\d+(?:\.\d+)?[KMGkmg])\(\d+%\)'
)


def _parse_ts(line):
    m = _TS_ELAPSED_RE.match(line)
    if m:
        return ('elapsed', float(m.group(1)))
    m = _TS_ABSOLUTE_RE.match(line)
    if m:
        return ('absolute', datetime.fromisoformat(m.group(1)))
    return None


def _to_elapsed(ts, t0):
    kind, val = ts
    if kind == 'elapsed':
        return val
    return (val - t0).total_seconds()


def parse_openjdk(log_path):
    first          = {}
    last           = {}
    raw_timestamps = []
    full_gcids     = set()

    with open_collector_file(log_path) as f:
        for line in f:
            ts = _parse_ts(line)
            if ts is None:
                continue
            raw_timestamps.append(ts)

            m_gcid = _GCID_RE.search(line)
            if not m_gcid:
                continue
            gcid = int(m_gcid.group(1))

            if "Pause Full" in line:
                full_gcids.add(gcid)

            m = _HEAP_ZGC_RE.search(line)
            if m:
                before_mb = _size_to_mb(m.group(1))
                after_mb  = _size_to_mb(m.group(2))
                if gcid not in first:
                    first[gcid] = {'ts': ts, 'before': before_mb}
                last[gcid] = {'ts': ts, 'after': after_mb}
                continue

            m = _HEAP_STD_RE.search(line)
            if m:
                before_mb = _size_to_mb(m.group(1))
                after_mb  = _size_to_mb(m.group(2))
                if gcid not in first:
                    first[gcid] = {'ts': ts, 'before': before_mb}
                last[gcid] = {'ts': ts, 'after': after_mb}

    t0 = None
    for ts in raw_timestamps:
        kind, val = ts
        if kind == 'elapsed':
            t0 = ('elapsed', 0.0)
            break
        if kind == 'absolute':
            if t0 is None:
                t0 = ('absolute', val)
            break

    t0_val = t0[1] if t0 else None

    points = []
    for gcid, fv in first.items():
        if gcid in last:
            gc_type = "FULL" if gcid in full_gcids else None
            points.append((_to_elapsed(fv['ts'], t0_val), gcid, gc_type, fv['before'], False))
            points.append((_to_elapsed(last[gcid]['ts'], t0_val), gcid, gc_type, last[gcid]['after'], True))

    if not points:
        print("No GC events found.", file=sys.stderr)
        return [], None

    points.sort(key=lambda p: p[0])
    return points, None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_results(points, max_heap_mb, out=sys.stdout):
    CW  = (12, 8, 10, 7, 12)
    HDR = ("Elapsed(s)", "Type", "GCID", "GCPhase", "Used(MB)")
    SEP = "  "
    header  = SEP.join(h.rjust(w) for h, w in zip(HDR, CW))
    divider = "-" * len(header)

    print(header, file=out)
    print(divider, file=out)

    for el, gcid, gc_type, used_mb, is_after in points:
        print(SEP.join([
            f"{el:{CW[0]}.3f}",
            f"{(gc_type or ''):>{CW[1]}}",
            f"{gcid:{CW[2]}}",
            f"{'AFTER' if is_after else 'BEFORE':>{CW[3]}}",
            f"{used_mb:{CW[4]}.1f}",
        ]), file=out)

    after_points = sorted(
        [(el, used_mb) for el, _, _t, used_mb, is_after in points if is_after],
        key=lambda p: p[0]
    )

    if not after_points:
        return

    max_after  = max(v for _, v in after_points)
    integral   = sum(
        after_points[i][1] * (after_points[i + 1][0] - after_points[i][0])
        for i in range(len(after_points) - 1)
    )
    total_time = after_points[-1][0] - after_points[0][0] if len(after_points) > 1 else 0.0
    tw_avg_mb  = (integral / total_time) if total_time > 0 else 0.0

    print(divider, file=out)
    W = len(header) - 10
    if max_heap_mb is not None:
        print(f"{'Max heap size (MB):'.ljust(W)} {max_heap_mb:>10.1f}", file=out)
    print(f"{'Max after GC (MB):'.ljust(W)} {max_after:>10.1f}", file=out)
    print(f"{'Time-weighted avg after GC (MB):'.ljust(W)} {tw_avg_mb:>10.1f}", file=out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Extract heap sizes from a verbose GC log (J9 or OpenJDK) and dumps "
                    "them into an output file (by default, ./heapsizes.txt)")
    ap.add_argument("input", metavar="<gc.log|result-dir>", nargs='+',
                    help="one or more GC log files or result directories")
    ap.add_argument("-o", "--output", default="heapsizes.txt",
                    metavar="FILE", help="output file (default: heapsizes.txt)")
    args = ap.parse_args()

    # Process each input
    for input_path in args.input:
        log_path = resolve_log(input_path)
        
        # Skip if no log found (directory without gc.log or gc-all.log)
        if log_path is None:
            print(f"{input_path}: ignored (no gc.log or gc-all.log)")
            continue
            
        fmt = detect_format(log_path)

        if fmt == 'j9':
            points, max_heap_mb = parse_j9(log_path)
        else:
            points, max_heap_mb = parse_openjdk(log_path)

        # Skip if no GC events found
        if not points:
            print(f"{input_path}: ignored (no GC events found)")
            continue

        # Determine output file name
        if len(args.input) == 1:
            # Single input: use specified output or default
            output_file = args.output
        else:
            # Multiple inputs: derive output name from input
            if os.path.isdir(input_path):
                output_file = os.path.join(input_path, "heapsizes.txt")
            else:
                # For direct log files, put output in same directory
                log_dir = os.path.dirname(log_path)
                output_file = os.path.join(log_dir if log_dir else ".", "heapsizes.txt")

        with open(output_file, "w", encoding="utf-8") as out:
            print_results(points, max_heap_mb, out)
        print(f"Written: {output_file}")


if __name__ == "__main__":
    import signal
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except AttributeError:
        pass
    main()
