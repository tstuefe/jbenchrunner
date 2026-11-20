#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Plot stacked per-category thread counts and CPU usage over time from a
# thread-print.jsonl produced by post-process-thread-print-log.py.
#
# Usage: plot-thread-print.py <result-dir> [<result-dir> ...]
#
# For each result directory, reads thread-print.jsonl and produces two SVGs in
# that directory, each with one stacked band per category
# (spec-post-process.md §3c.2):
#   thread-print.svg      thread counts; the top edge is num_threads
#   thread-print-cpu.svg  CPU usage in % (100 = one core), plus on top the
#                         CPU time no dumped thread accounts for (when
#                         thread-print.jsonl has it); each sample's value
#                         covers the interval since the previous sample, so it
#                         is drawn as a step over that interval

import sys
import os
import argparse
import json
from datetime import datetime

_COMMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "common"))
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)

from gnuplot_utils import gnuplot_quote, gnuplot_dquote, plot_title, temp_dat_files, run_gnuplot


# (series title, thread-print.jsonl category, color), stacked bottom to top.
# The steady categories form the base; the volatile ones sit on top so their
# swings don't shift the bands below them.
SERIES = [
    ("GC", "gc", "#e15759"),
    ("Other", "other", "#9c755f"),
    ("C1", "c1", "#59a14f"),
    ("C2", "c2", "#f28e2b"),
    ("Mutator", "mutator", "#4e79a7"),
]

# CPU plot only, on top: CPU time of threads no dump saw ("unaccounted", present
# only when post-processing had proclog.jsonl).
UNACCOUNTED_SERIES = ("Unaccounted/Short-lived", "unaccounted", "#bab0ac")


def resolve_result_dir(path):
    """
    Validate that path is a directory containing thread-print.jsonl.
    Returns the path to thread-print.jsonl, or None if not found.
    Exits with error for non-existent directories.
    """
    if not os.path.exists(path):
        print(f"Error: {path}: no such file or directory", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(path):
        print(f"Error: {path}: not a directory", file=sys.stderr)
        sys.exit(1)

    jsonl_path = os.path.join(path, "thread-print.jsonl")
    if not os.path.isfile(jsonl_path):
        return None

    return jsonl_path


def _read_samples(jsonl_path):
    """
    Read thread-print.jsonl into (datetime, sample) pairs sorted by time.
    Lines that are malformed or lack a category's thread count are skipped
    with a warning.
    """
    samples = []
    with open(jsonl_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
                ts = datetime.fromisoformat(sample["timestamp"])
                for _, category, _ in SERIES:
                    float(sample[category]["num_threads"])
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                print(f"Warning: skipping malformed line {lineno} in {jsonl_path}",
                      file=sys.stderr)
                continue
            samples.append((ts, sample))
    samples.sort(key=lambda s: s[0])
    return samples


def _stack(values):
    """Return [0, cum1, ..., cumN] for the per-series *values*."""
    row = [0.0]
    for v in values:
        row.append(row[-1] + v)
    return row


def count_rows(samples):
    """
    Return (rows, series): cumulative-sum rows (elapsed_seconds, 0, cum1,
    ..., cumN) of the thread counts, one column per SERIES entry, suitable
    for gnuplot's `filledcurves`; the last column is the sample's total.
    Elapsed is relative to the first sample's timestamp.
    """
    if not samples:
        return [], SERIES
    first_ts = samples[0][0]
    return [
        [(ts - first_ts).total_seconds()]
        + _stack([float(sample[category]["num_threads"]) for _, category, _ in SERIES])
        for ts, sample in samples
    ], SERIES


def cpu_rows(samples):
    """
    Return (rows, series): cumulative-sum rows like count_rows(), of the
    categories' cpu_pct, with UNACCOUNTED_SERIES on top if any sample has
    an "unaccounted" object. Each sample with CPU values yields two rows
    with the same values, at the previous sample's time and at its own, so
    gnuplot draws a step over the interval the value covers. Samples without
    thread-category CPU values (the first one) contribute no step. An
    unaccounted value that is null (proclog did not cover the interval) or
    negative (it is approximate) is drawn as 0.
    """
    series = SERIES
    if any("unaccounted" in sample for _, sample in samples):
        series = SERIES + [UNACCOUNTED_SERIES]
    rows = []
    if not samples:
        return rows, series
    first_ts = samples[0][0]
    for (prev_ts, _), (ts, sample) in zip(samples, samples[1:]):
        pcts = [sample[category].get("cpu_pct") for _, category, _ in SERIES]
        if any(not isinstance(p, (int, float)) for p in pcts):
            continue
        if len(series) > len(SERIES):
            pct = (sample.get("unaccounted") or {}).get("cpu_pct")
            pcts.append(pct if isinstance(pct, (int, float)) else 0.0)
        stacked = _stack([max(p, 0.0) for p in pcts])
        rows.append([(prev_ts - first_ts).total_seconds()] + stacked)
        rows.append([(ts - first_ts).total_seconds()] + stacked)
    return rows, series


def build_gnuplot_script(dat_path, out_svg, title, ylabel, series=SERIES):
    # Bands are plotted top to bottom so the key lists them in stacking order.
    plot_items = [
        f"{gnuplot_quote(dat_path)} using 1:{i + 3}:{i + 2} with filledcurves "
        f"lc rgb {gnuplot_quote(color)} title {gnuplot_quote(name)}"
        for i, (name, _, color) in reversed(list(enumerate(series)))
    ]
    # Joined outside the f-string: a backslash in an f-string expression
    # needs Python 3.12, and the minimum is 3.11 (PRD.md D-75).
    plot_cmd = ", \\\n     ".join(plot_items)
    return f"""\
set terminal svg size 1400,700 enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

set title {gnuplot_dquote(title)} noenhanced
set xlabel 'Elapsed (s)'
set ylabel {gnuplot_quote(ylabel)}
set yrange [0:*]
set grid
set key outside right top
set style fill solid 0.85 noborder

plot {plot_cmd}
"""


# (output file, title, y axis label, row builder)
PLOTS = [
    ("thread-print.svg", "Threads (measured by Thread.print)", "Threads", count_rows),
    ("thread-print-cpu.svg", "Thread CPU usage (measured by Thread.print)",
     "CPU % (100 = one core)", cpu_rows),
]


def main():
    ap = argparse.ArgumentParser(
        description="Plot thread counts and CPU usage per category over time from thread-print.jsonl in result directories")
    ap.add_argument("result_dirs", metavar="<result-dir>", nargs='+',
                    help="one or more result directories containing thread-print.jsonl")
    args = ap.parse_args()

    any_failure = False

    for result_dir in args.result_dirs:
        jsonl_path = resolve_result_dir(result_dir)

        if jsonl_path is None:
            print(f"{result_dir}: ignored (no thread-print.jsonl)")
            continue

        samples = _read_samples(jsonl_path)

        if not samples:
            print(f"{result_dir}: ignored (no data in thread-print.jsonl)")
            continue

        for out_name, title_text, ylabel, make_rows in PLOTS:
            rows, series = make_rows(samples)
            if not rows:
                print(f"{result_dir}: {out_name} skipped (no data)")
                continue
            title = plot_title(title_text, result_dir)
            out_svg = os.path.join(result_dir, out_name)

            with temp_dat_files() as make_dat:
                dat_path = make_dat(rows)
                gp = build_gnuplot_script(dat_path, out_svg, title, ylabel, series)
                if not run_gnuplot(gp):
                    any_failure = True
                    continue
                print(f"Written: {out_svg}")

    if any_failure:
        sys.exit(1)


if __name__ == "__main__":
    import signal
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except AttributeError:
        pass
    main()
