#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Parse DaCapo benchmark stdout log and output a JSON representation matching the
dacapo_result schema to stdout.

Usage:
    parse_dacapo_results.py [-h | --help] <stdout.log>
"""

import argparse
import json
import math
import os
import re
import statistics
import sys


# Pattern for timestamp lines emitted by the JBR dacapo-timestamp-extension:
# >>> Warmup N started at <ISO8601>.
# >>> Final started at <ISO8601>.
TIMESTAMP_WARMUP_RE = re.compile(
    r"^>>>\s+Warmup\s+(\d+)\s+started at\s+(\S+)\.\s*$"
)
TIMESTAMP_FINAL_RE = re.compile(
    r"^>>>\s+Final\s+started at\s+(\S+)\.\s*$"
)

# Pattern for warmup completion:
# ===== DaCapo 23.11-MR2-chopin <bench> completed warmup <N> in <time> msec =====
WARMUP_RE = re.compile(
    r"^=====\s+DaCapo\s+\S+\s+(\S+)\s+completed warmup\s+(\d+)\s+in\s+(\d+)\s+msec\s+=====\s*$"
)

# Pattern for final run completion:
# ===== DaCapo 23.11-MR2-chopin <bench> PASSED in <time> msec =====
FINAL_PASSED_RE = re.compile(
    r"^=====\s+DaCapo\s+\S+\s+(\S+)\s+PASSED\s+in\s+(\d+)\s+msec\s+=====\s*$"
)

# Pattern for tail latency lines:
# ===== DaCapo tail latency, <variant>: 50% <p50> usec, 90% <p90> usec, 99% <p99> usec, 99.9% <p999> usec, 99.99% <p9999> usec, max <max> usec, measured over <num_events> events =====
LATENCY_RE = re.compile(
    r"^=====\s+DaCapo tail latency,\s+(simple|metered 100ms smoothing|metered full smoothing):\s+"
    r"50%\s+(\d+)\s+usec,\s+"
    r"90%\s+(\d+)\s+usec,\s+"
    r"99%\s+(\d+)\s+usec,\s+"
    r"99\.9%\s+(\d+)\s+usec,\s+"
    r"99\.99%\s+(\d+)\s+usec,\s+"
    r"max\s+(\d+)\s+usec,\s+"
    r"measured over\s+(\d+)\s+events\s+=====\s*$"
)

LATENCY_KEY_MAP = {
    "simple": "simple",
    "metered 100ms smoothing": "smooth_100ms",
    "metered full smoothing": "smooth_full",
}

# Staggered stability tail: (minimum iteration count, tail length), checked
# in order. Runs with fewer iterations than the last entry get no metrics.
STABILITY_TAILS = ((40, 10), (30, 7), (20, 5))


def _stability_tail_len(num_iterations: int) -> int:
    """Number of trailing iterations to compute stability metrics over, or 0."""
    for min_iterations, tail_len in STABILITY_TAILS:
        if num_iterations >= min_iterations:
            return tail_len
    return 0


def parse_dacapo_output(content: str) -> dict:
    """
    Parse the lines of DaCapo standard output into a dacapo_result dict.
    Raises ValueError on parsing/validation failure.
    """
    iterations = []
    current_iter = None
    passed_found = False
    result_ms = None
    pending_timestamp = None  # timestamp string from the extension, awaiting its iteration

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        ts_warmup_m = TIMESTAMP_WARMUP_RE.match(line)
        if ts_warmup_m:
            pending_timestamp = ts_warmup_m.group(2)
            continue

        ts_final_m = TIMESTAMP_FINAL_RE.match(line)
        if ts_final_m:
            pending_timestamp = ts_final_m.group(1)
            continue

        warmup_m = WARMUP_RE.match(line)
        if warmup_m:
            bench, iter_n_str, time_ms_str = warmup_m.groups()
            n = int(iter_n_str)
            time_ms = int(time_ms_str)
            current_iter = {
                "n": n,
                "warmup": True,
                "time_ms": time_ms,
            }
            if pending_timestamp is not None:
                current_iter["start_ts"] = pending_timestamp
                pending_timestamp = None
            iterations.append(current_iter)
            continue

        passed_m = FINAL_PASSED_RE.match(line)
        if passed_m:
            bench, time_ms_str = passed_m.groups()
            time_ms = int(time_ms_str)
            n = len(iterations) + 1
            current_iter = {
                "n": n,
                "warmup": False,
                "time_ms": time_ms,
            }
            if pending_timestamp is not None:
                current_iter["start_ts"] = pending_timestamp
                pending_timestamp = None
            iterations.append(current_iter)
            result_ms = time_ms
            passed_found = True
            continue

        latency_m = LATENCY_RE.match(line)
        if latency_m:
            variant_raw, p50, p90, p99, p999, p9999, max_val, num_events = latency_m.groups()
            if current_iter is None:
                raise ValueError("Found latency line before any completed iteration header")

            var_key = LATENCY_KEY_MAP.get(variant_raw)
            if not var_key:
                raise ValueError(f"Unknown latency variant: {variant_raw}")

            if "latency_usec" not in current_iter:
                current_iter["latency_usec"] = {}

            current_iter["latency_usec"][var_key] = {
                "p50": int(p50),
                "p90": int(p90),
                "p99": int(p99),
                "p999": int(p999),
                "p9999": int(p9999),
                "max": int(max_val),
                "num_events": int(num_events),
            }

    if not passed_found or not iterations:
        raise ValueError("No successful DaCapo PASSED line found in output")

    # Validate iteration numbering consistency: 1..N
    for idx, it in enumerate(iterations, start=1):
        if it["n"] != idx:
            raise ValueError(f"Iteration sequence mismatch: expected {idx}, got {it['n']}")

    result = {
        "iterations": iterations,
        "result_ms": result_ms,
    }

    # Start timestamps of the first and final iteration, copied verbatim from
    # the iterations (absent if the timestamp extension didn't emit them).
    if "start_ts" in iterations[0]:
        result["first_iteration_ts"] = iterations[0]["start_ts"]
    if "start_ts" in iterations[-1]:
        result["final_iteration_ts"] = iterations[-1]["start_ts"]

    # Stability metrics over the tail of the run (including the final one),
    # only meaningful once there's enough of a tail to sample from.
    tail_len = _stability_tail_len(len(iterations))
    if tail_len:
        tail_ms = [it["time_ms"] for it in iterations[-tail_len:]]
        stddev_ms = statistics.stdev(tail_ms)
        result["stddev_ms"] = round(stddev_ms, 2)
        stderr_ms = stddev_ms / math.sqrt(len(tail_ms))
        result["stderr_ms"] = round(stderr_ms, 2)
        mean_ms = statistics.mean(tail_ms)
        if mean_ms > 0:
            # Relative standard error, in percent of the mean.
            result["stderr_pct"] = round(stderr_ms / mean_ms * 100, 2)

    # Latency summary (usec) from the final iteration, for the simple and the
    # 100ms-smoothed variant; p100 is the max. p99 additionally gets its
    # standard error over the same tail, same rule as stderr_ms.
    for variant, suffix in (("simple", ""), ("smooth_100ms", "_smooth")):
        final_lat = iterations[-1].get("latency_usec", {}).get(variant)
        if final_lat is None:
            continue
        result[f"p50{suffix}"] = final_lat["p50"]
        result[f"p99{suffix}"] = final_lat["p99"]
        result[f"p100{suffix}"] = final_lat["max"]
        tail = iterations[-tail_len:] if tail_len else []
        if tail and all(
                variant in it.get("latency_usec", {}) for it in tail):
            tail_p99 = [it["latency_usec"][variant]["p99"] for it in tail]
            stderr_p99 = statistics.stdev(tail_p99) / math.sqrt(len(tail_p99))
            result[f"p99{suffix}_stderr"] = round(stderr_p99, 2)
            mean_p99 = statistics.mean(tail_p99)
            if mean_p99 > 0:
                result[f"p99{suffix}_stderr_pct"] = round(stderr_p99 / mean_p99 * 100, 2)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Parse DaCapo benchmark stdout log and print JSON dacapo_result."
    )
    parser.add_argument("logfile", help="Path to DaCapo stdout log file")
    args = parser.parse_args()

    if not os.path.exists(args.logfile):
        sys.stderr.write(f"Error: file not found: {args.logfile}\n")
        sys.exit(1)

    try:
        with open(args.logfile, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        sys.stderr.write(f"Error reading file '{args.logfile}': {e}\n")
        sys.exit(1)

    try:
        result = parse_dacapo_output(content)
    except Exception as e:
        sys.stderr.write(f"Error parsing DaCapo output: {e}\n")
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
