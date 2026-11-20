#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Shared page fault parsing/plotting core for proclog.jsonl, used by both
# scripts/phase-post-process/plot-pagefaults.py (per-run fault rate plot
# generated at post-process time) and scripts/phase-analyze/listing.py
# (on-the-fly two-run comparison plots of the cumulative fault counts for
# `jbr-ls --report` with --compare on minflt/majflt). Not invoked directly.

import json
import os
import sys
from datetime import datetime

from gnuplot_utils import gnuplot_quote, gnuplot_dquote, temp_dat_files, run_gnuplot, plot_title


# (panel title, y axis label for the rate plot, color), top to bottom; the
# values in parse()'s points are in the same order.
PANELS = [
    ("Minor page faults", "Minor faults/s", "#4e79a7"),
    ("Major page faults", "Major faults/s", "#e15759"),
]

_PANEL_WIDTH = 1400
_PANEL_HEIGHT = 450


def parse(jsonl_path):
    """
    Parse proclog.jsonl into (elapsed_seconds, [minflt, majflt]) points,
    the counts being cumulative since process start (proc_pid_stat), and
    elapsed relative to the first sample's timestamp. Malformed lines are
    skipped with a warning.
    """
    points = []
    first_ts = None

    with open(jsonl_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = datetime.fromisoformat(obj["timestamp"])
                stat = obj["proc_pid_stat"]
                values = [int(stat["minflt"]), int(stat["majflt"])]
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                print(f"Warning: skipping malformed line {lineno} in {jsonl_path}",
                      file=sys.stderr)
                continue

            if first_ts is None:
                first_ts = ts
            points.append(((ts - first_ts).total_seconds(), values))

    points.sort(key=lambda p: p[0])
    return points


def rate_rows(points):
    """
    Fault rate rows (elapsed, minflt/s, majflt/s) from parse()'s points:
    each interval between two samples yields two rows with the same rates,
    at the interval's start and end, so gnuplot draws a step over the
    interval. Faults before the first sample have no interval and are not
    drawn (they are in the cumulative counts). Intervals of zero length
    are skipped.
    """
    rows = []
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        dt = t1 - t0
        if dt <= 0:
            continue
        rates = [(b - a) / dt for a, b in zip(v0, v1)]
        rows.append([t0] + rates)
        rows.append([t1] + rates)
    return rows


def cumulative_rows(points):
    """Rows (elapsed, minflt, majflt) of the cumulative counts."""
    return [[t] + values for t, values in points]


def _panel_settings():
    return """\
set xlabel 'Elapsed (s)'
set grid
set lmargin 12
set rmargin 4"""


def build_rate_script(dat_path, out_svg, title):
    """Two-panel gnuplot script of one run's minor (top) and major (bottom)
    fault rates over time, sharing the x-axis. dat_path holds rate_rows()
    output."""
    panels = []
    for i, (name, ylabel, color) in enumerate(PANELS):
        panel_title = title if i == 0 else name
        panels.append(f"""\
set title {gnuplot_dquote(panel_title)} noenhanced
set ylabel {gnuplot_quote(ylabel)}
plot {gnuplot_quote(dat_path)} using 1:{i + 2} with lines lw 1.5 lc rgb {gnuplot_quote(color)}
""")
    return f"""\
set terminal svg size {_PANEL_WIDTH},{2 * _PANEL_HEIGHT + 60} enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

{_panel_settings()}
set yrange [0:*]
set key off

set multiplot layout 2,1
{chr(10).join(panels)}
unset multiplot
"""


def build_rate_svg(result_dir, out_svg):
    """
    Build the per-run fault rate plot at out_svg from result_dir's
    proclog.jsonl. Returns True on success; False, without writing out_svg,
    if proclog.jsonl is missing or has fewer than two usable samples, or if
    gnuplot fails.
    """
    jsonl_path = os.path.join(result_dir, "proclog.jsonl")
    if not os.path.isfile(jsonl_path):
        return False
    rows = rate_rows(parse(jsonl_path))
    if not rows:
        return False
    title = plot_title(PANELS[0][0], result_dir)
    with temp_dat_files() as make_dat:
        return run_gnuplot(build_rate_script(make_dat(rows), out_svg, title))


def build_comparison_gnuplot_script(dat_baseline, label_baseline, dat_row, label_row,
                                     out_svg, xmax, ymaxes):
    """Two-panel gnuplot script comparing two runs' cumulative minor (top)
    and major (bottom) fault counts as line plots. Both panels share one
    x-range [0, xmax]; each has its own y-range [0, ymaxes[i]], shared by
    both runs."""
    panels = []
    for i, (name, _ylabel, _color) in enumerate(PANELS):
        panels.append(f"""\
set title {gnuplot_quote(name + " (cumulative)")} noenhanced
set yrange [0:{ymaxes[i]}]
plot {gnuplot_quote(dat_baseline)} using 1:{i + 2} with lines lw 2 lc rgb '#4e79a7' title {gnuplot_quote(label_baseline)}, \\
     {gnuplot_quote(dat_row)} using 1:{i + 2} with lines lw 2 lc rgb '#e15759' title {gnuplot_quote(label_row)}
""")
    return f"""\
set terminal svg size {_PANEL_WIDTH},{2 * _PANEL_HEIGHT} enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

{_panel_settings()}
set ylabel 'Faults'
set xrange [0:{xmax}]
set key outside right top

set multiplot layout 2,1
{chr(10).join(panels)}
unset multiplot
"""


def build_comparison_svg(result_dir_a, result_dir_b, out_svg):
    """
    Build a two-panel SVG at out_svg comparing result_dir_b's (the
    baseline's) and result_dir_a's (the row's) cumulative minor and major
    fault counts over time from proclog.jsonl, sharing one x-range and, per
    panel, one y-range sized to fit both runs.

    Returns True on success. Returns False, without writing out_svg, if
    either directory lacks a proclog.jsonl (or it has no usable fault
    data), or if gnuplot fails.
    """
    jsonl_a = os.path.join(result_dir_a, "proclog.jsonl")
    jsonl_b = os.path.join(result_dir_b, "proclog.jsonl")
    if not os.path.isfile(jsonl_a) or not os.path.isfile(jsonl_b):
        return False

    points_a = parse(jsonl_a)
    points_b = parse(jsonl_b)
    if not points_a or not points_b:
        return False

    xmax = max(points_a[-1][0], points_b[-1][0])
    ymaxes = [max(1, max(v[i] for _, v in points_a), max(v[i] for _, v in points_b)) * 1.05
              for i in range(len(PANELS))]

    label_a = f"Row: {os.path.basename(os.path.normpath(result_dir_a))}"
    label_b = f"Baseline: {os.path.basename(os.path.normpath(result_dir_b))}"

    with temp_dat_files() as make_dat:
        dat_a = make_dat(cumulative_rows(points_a))
        dat_b = make_dat(cumulative_rows(points_b))
        gp = build_comparison_gnuplot_script(
            dat_b, label_b, dat_a, label_a, out_svg, max(xmax, 1), ymaxes,
        )
        return run_gnuplot(gp)
