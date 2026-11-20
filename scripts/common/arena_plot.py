#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Two-run comparison plot of the combined arena memory manager series, from
# each run's arena-report/combined.json (written by bin/tools/arena-log-parser.py
# at post-process time). Used by scripts/phase-analyze/listing.py for
# `jbr-ls --report` with --compare on the arena columns (arumax, ardavg, ...).
# Not invoked directly.

import json
import os
import sys

from gnuplot_utils import gnuplot_quote, temp_dat_files, run_gnuplot

# (combined.json sample key, panel title), top to bottom.
PANELS = [
    ("in_use_peak_bytes", "Arena in use (last tick peak)"),
    ("dirty_bytes", "Arena dirty"),
    ("cached_bytes", "Arena cached"),
]

MIB = 1024 * 1024

_PANEL_WIDTH = 1400
_PANEL_HEIGHT = 400


def parse(result_dir):
    """
    Read result_dir's arena-report/combined.json and return its samples,
    sorted by uptime; samples without an uptime are dropped. None if the
    file is missing or unreadable.
    """
    path = os.path.join(result_dir, "arena-report", "combined.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            samples = json.load(f)["samples"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Warning: cannot read {path}: {e}", file=sys.stderr)
        return None
    samples = [s for s in samples if isinstance(s.get("uptime_s"), (int, float))]
    return sorted(samples, key=lambda s: s["uptime_s"])


def rows(samples, key):
    """Rows (uptime_s, MiB) of the samples that have key."""
    return [[s["uptime_s"], s[key] / MIB] for s in samples if key in s]


def build_comparison_gnuplot_script(panels, label_baseline, label_row, out_svg, xmax):
    """
    One panel per entry of panels, a list of (title, dat_baseline, dat_row,
    ymax); dat_baseline/dat_row may be None if that run lacks the series.
    All panels share the x-range [0, xmax]; each has its own y-range
    [0, ymax], shared by both runs.
    """
    parts = []
    for title, dat_baseline, dat_row, ymax in panels:
        plots = []
        if dat_baseline is not None:
            plots.append(f"{gnuplot_quote(dat_baseline)} using 1:2 with lines lw 2 "
                         f"lc rgb '#4e79a7' title {gnuplot_quote(label_baseline)}")
        if dat_row is not None:
            plots.append(f"{gnuplot_quote(dat_row)} using 1:2 with lines lw 2 "
                         f"lc rgb '#e15759' title {gnuplot_quote(label_row)}")
        parts.append(f"""\
set title {gnuplot_quote(title)} noenhanced
set yrange [0:{ymax}]
plot {", ".join(plots)}
""")
    return f"""\
set terminal svg size {_PANEL_WIDTH},{len(panels) * _PANEL_HEIGHT} enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

set xlabel 'Uptime (s)'
set ylabel 'Memory (MiB)'
set grid
set lmargin 12
set rmargin 4
set xrange [0:{xmax}]
set key outside right top

set multiplot layout {len(panels)},1
{chr(10).join(parts)}
unset multiplot
"""


def build_comparison_svg(result_dir_a, result_dir_b, out_svg):
    """
    Build an SVG at out_svg comparing result_dir_b's (the baseline's) and
    result_dir_a's (the row's) combined arena in-use peak, dirty and cached
    memory over JVM uptime, as line plots in one panel per series, sharing
    one x-range and, per panel, one y-range sized to fit both runs. A
    series missing from both runs (no "last tick peak" in the logs) gets no
    panel; missing from one run, that run has no line in it.

    Returns True on success. Returns False, without writing out_svg, if
    either directory lacks a usable arena-report/combined.json, or if
    gnuplot fails.
    """
    samples_a = parse(result_dir_a)
    samples_b = parse(result_dir_b)
    if not samples_a or not samples_b:
        return False

    label_a = f"Row: {os.path.basename(os.path.normpath(result_dir_a))}"
    label_b = f"Baseline: {os.path.basename(os.path.normpath(result_dir_b))}"
    xmax = max(1, samples_a[-1]["uptime_s"], samples_b[-1]["uptime_s"])

    with temp_dat_files() as make_dat:
        panels = []
        for key, title in PANELS:
            rows_a = rows(samples_a, key)
            rows_b = rows(samples_b, key)
            if not rows_a and not rows_b:
                continue
            ymax = max([1.0] + [r[1] for r in rows_a + rows_b]) * 1.05
            panels.append((title, make_dat(rows_b) if rows_b else None,
                           make_dat(rows_a) if rows_a else None, ymax))
        if not panels:
            return False
        return run_gnuplot(build_comparison_gnuplot_script(panels, label_b, label_a, out_svg, xmax))
