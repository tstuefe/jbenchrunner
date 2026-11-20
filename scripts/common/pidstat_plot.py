#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Shared pidstat.jsonl parsing/plotting core, used by both
# scripts/phase-post-process/plot-pidstat.py (per-run RSS/CPU plots
# generated at post-process time) and scripts/phase-analyze/listing.py's
# comparison plots for jbr-ls --report --compare: the stacked CPU load
# comparison, and the fallback RSS comparison used when a run lacks
# system.map.jsonl (see system_map_plot.py for the richer,
# category-breakdown comparison used when system.map data is available).
# Not invoked directly.

import json
import os
import sys
from datetime import datetime

from gnuplot_utils import gnuplot_quote, gnuplot_dquote, temp_dat_files, run_gnuplot, plot_title


def parse(jsonl_path):
    """
    Parse pidstat.jsonl into series for RSS, as (elapsed_seconds, MB), and
    CPU load, as (elapsed_seconds, [value (%) per CPU_SERIES entry, in
    order]), elapsed being relative to the first sample's timestamp.
    Malformed lines are skipped with a warning.

    Returns (rss_points, cpu_points).
    """
    rss_points = []
    cpu_points = []
    first_ts = None

    with open(jsonl_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = datetime.fromisoformat(obj["timestamp"])
                rss_mb = int(obj["rss"]) / 1024.0
                cpu_values = [float(obj[key]) for _, key, _ in CPU_SERIES]
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                print(f"Warning: skipping malformed line {lineno} in {jsonl_path}",
                      file=sys.stderr)
                continue

            if first_ts is None:
                first_ts = ts
            elapsed = (ts - first_ts).total_seconds()
            rss_points.append((elapsed, rss_mb))
            cpu_points.append((elapsed, cpu_values))

    rss_points.sort(key=lambda p: p[0])
    cpu_points.sort(key=lambda p: p[0])
    return rss_points, cpu_points


# (series title, pidstat.jsonl key, color) for the stacked CPU load plot, in
# stack order (bottom to top). pidstat's %CPU is exactly the sum of these,
# so the top of the stack is the total. %wait (runnable but not running) is
# not CPU time and is left out.
CPU_SERIES = [
    ("System", "pct_system", "#e15759"),
    ("User", "pct_usr", "#4e79a7"),
    ("Guest", "pct_guest", "#59a14f"),
]


def build_cpu_rows(cpu_points):
    """Cumulative-sum rows (elapsed, 0, cum1, ..., cumN) from parse()'s
    cpu_points, for the filledcurves stack in build_cpu_stack_script."""
    rows = []
    for elapsed, values in cpu_points:
        row = [elapsed, 0.0]
        running = 0.0
        for v in values:
            running += v
            row.append(running)
        rows.append(row)
    return rows


def _cpu_stack_plot(dat_path):
    """gnuplot `plot` command stacking CPU_SERIES from build_cpu_rows()
    output in dat_path."""
    items = []
    for i, (name, _key, color) in enumerate(CPU_SERIES):
        items.append(
            f"{gnuplot_quote(dat_path)} using 1:{i + 3}:{i + 2} "
            f"with filledcurves lc rgb {gnuplot_quote(color)} title {gnuplot_quote(name)}"
        )
    return "plot " + ", \\\n     ".join(items)


# Canvas width and per-panel height shared by the per-run CPU plot and each
# panel of the three-panel CPU comparison, so a run's stack looks the same
# in both.
_CPU_PANEL_WIDTH = 1400
_CPU_PANEL_HEIGHT = 630


def _cpu_panel_settings():
    """gnuplot settings shared by the per-run CPU plot and the CPU
    comparison's panels (see _CPU_PANEL_HEIGHT)."""
    return f"""\
set xlabel 'Elapsed (s)' font {gnuplot_quote(_AXIS_FONT)}
set ylabel 'CPU load (%)' font {gnuplot_quote(_AXIS_FONT)}
set xtics font {gnuplot_quote(_TIC_FONT)}
set ytics font {gnuplot_quote(_TIC_FONT)}
set grid
set key outside right top
set style fill solid 0.85 noborder
set lmargin at screen {_CPU_LMARGIN_SCREEN}
set rmargin at screen {_CPU_RMARGIN_SCREEN}"""


def build_cpu_stack_script(dat_path, out_svg, title):
    """Single-panel gnuplot script stacking one run's CPU load components
    (CPU_SERIES) over time, auto-ranged. dat_path holds build_cpu_rows()
    output. Styled like one panel of build_cpu_comparison_gnuplot_script."""
    return f"""\
set terminal svg size {_CPU_PANEL_WIDTH},{_CPU_PANEL_HEIGHT} enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

{_cpu_panel_settings()}
set yrange [0:*]

set title {gnuplot_dquote(title)} noenhanced
{_cpu_stack_plot(dat_path)}
"""


def build_single_series_script(dat_path, out_svg, title, ylabel, color):
    """Single-panel, single-series gnuplot script (one run's RSS over time,
    auto-ranged)."""
    return f"""\
set terminal svg size 1400,600 enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

set title {gnuplot_dquote(title)} noenhanced
set xlabel 'Elapsed (s)'
set ylabel {gnuplot_quote(ylabel)}
set yrange [0:*]
set grid
set key off

plot {gnuplot_quote(dat_path)} using 1:2 with lines lw 1.5 lc rgb {gnuplot_quote(color)}
"""


# Axis label / tic font, matching system_map_plot.py's comparison plot so
# the two fallback plots look like one family.
_AXIS_FONT = "Sans,12"
_TIC_FONT = "Sans,11"


def build_comparison_gnuplot_script(dat_baseline, label_baseline, dat_row, label_row,
                                     out_svg, xmax, ymax):
    """Single-panel gnuplot script comparing two runs' process RSS over
    time as line plots, sharing one x-range [0, xmax] and one y-range
    [0, ymax]. Used as the system_map-comparison fallback when a run lacks
    system.map.jsonl but has pidstat.jsonl -- pidstat has no category
    breakdown to stack, so unlike system_map_plot.py's 3-panel comparison,
    this is a single panel."""
    return f"""\
set terminal svg size 1400,700 enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

set title "Process RSS (pidstat; no System.map data for this comparison)" noenhanced
set xlabel 'Elapsed (s)' font {gnuplot_quote(_AXIS_FONT)}
set ylabel 'RSS (MB)' font {gnuplot_quote(_AXIS_FONT)}
set xtics font {gnuplot_quote(_TIC_FONT)}
set ytics font {gnuplot_quote(_TIC_FONT)}
set xrange [0:{xmax}]
set yrange [0:{ymax}]
set grid
set key outside right top

plot {gnuplot_quote(dat_baseline)} using 1:2 with lines lw 2 lc rgb '#4e79a7' title {gnuplot_quote(label_baseline)}, \\
     {gnuplot_quote(dat_row)} using 1:2 with lines lw 2 lc rgb '#e15759' title {gnuplot_quote(label_row)}
"""


def build_comparison_svg(result_dir_a, result_dir_b, out_svg):
    """
    Fallback comparison plot for when system_map data isn't available for
    both runs (see system_map_plot.build_comparison_svg): build a
    single-panel SVG at out_svg plotting result_dir_b's (the baseline's)
    and result_dir_a's (the row's) process RSS over time as two lines,
    baseline first, sharing one x-range and one y-range sized to fit both.

    Returns True on success. Returns False, without writing out_svg, if
    either directory lacks a pidstat.jsonl (or it has no usable RSS data),
    or if gnuplot fails.
    """
    jsonl_a = os.path.join(result_dir_a, "pidstat.jsonl")
    jsonl_b = os.path.join(result_dir_b, "pidstat.jsonl")
    if not os.path.isfile(jsonl_a) or not os.path.isfile(jsonl_b):
        return False

    rss_a, _ = parse(jsonl_a)
    rss_b, _ = parse(jsonl_b)
    if not rss_a or not rss_b:
        return False

    xmax = max(rss_a[-1][0], rss_b[-1][0])
    ymax = max(max(v for _, v in rss_a), max(v for _, v in rss_b))
    if ymax <= 0:
        return False

    label_a = f"Row: {os.path.basename(os.path.normpath(result_dir_a))}"
    label_b = f"Baseline: {os.path.basename(os.path.normpath(result_dir_b))}"

    with temp_dat_files() as make_dat:
        dat_a = make_dat(rss_a)
        dat_b = make_dat(rss_b)
        gp = build_comparison_gnuplot_script(
            dat_b, label_b, dat_a, label_a, out_svg, xmax, ymax * 1.05,
        )
        return run_gnuplot(gp)


# Left/right plot-area edges for the three-panel CPU comparison, pinned
# identically across all panels so their plot areas line up (the "key
# outside right" legends differ in width between the top panel and the
# stacked ones). The right edge leaves room for the top panel's
# "Baseline: <runid>" / "Row: <runid>" legend.
_CPU_LMARGIN_SCREEN = 0.045
_CPU_RMARGIN_SCREEN = 0.72


def build_cpu_comparison_gnuplot_script(dat_baseline, title_baseline, label_baseline,
                                         dat_row, title_row, label_row,
                                         dat_line_baseline, dat_line_row,
                                         out_svg, xmax, ymax):
    """Three-panel (stacked) gnuplot script comparing a run's CPU load
    against its baseline's: a top panel plotting both runs' total CPU load
    over time as lines, then the baseline's stacked CPU load breakdown,
    then the row's own - in that order top to bottom. All three panels
    share one x-range [0, xmax] and one y-range [0, ymax]."""
    line_plot = (
        "plot "
        f"{gnuplot_quote(dat_line_baseline)} using 1:2 with lines lw 2 "
        f"lc rgb '#4e79a7' title {gnuplot_quote(label_baseline)}, \\\n     "
        f"{gnuplot_quote(dat_line_row)} using 1:2 with lines lw 2 "
        f"lc rgb '#e15759' title {gnuplot_quote(label_row)}"
    )
    return f"""\
set terminal svg size {_CPU_PANEL_WIDTH},{3 * _CPU_PANEL_HEIGHT} enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

{_cpu_panel_settings()}
set xrange [0:{xmax}]
set yrange [0:{ymax}]

set multiplot layout 3,1

set title "Total CPU" noenhanced
{line_plot}

set title {gnuplot_dquote(title_baseline)} noenhanced
{_cpu_stack_plot(dat_baseline)}

set title {gnuplot_dquote(title_row)} noenhanced
{_cpu_stack_plot(dat_row)}

unset multiplot
"""


def build_cpu_comparison_svg(result_dir_a, result_dir_b, out_svg):
    """
    Build a stacked three-panel CPU load comparison SVG at out_svg from
    pidstat.jsonl: both runs' total CPU load as lines (top), then
    result_dir_b's (the baseline's) stacked CPU load breakdown, then
    result_dir_a's (the row's). All three panels share one x-range and one
    y-range sized to fit both runs.

    Returns True on success. Returns False, without writing out_svg, if
    either directory lacks a pidstat.jsonl (or it has no usable CPU data),
    or if gnuplot fails.
    """
    jsonl_a = os.path.join(result_dir_a, "pidstat.jsonl")
    jsonl_b = os.path.join(result_dir_b, "pidstat.jsonl")
    if not os.path.isfile(jsonl_a) or not os.path.isfile(jsonl_b):
        return False

    _, cpu_a = parse(jsonl_a)
    _, cpu_b = parse(jsonl_b)
    if not cpu_a or not cpu_b:
        return False

    rows_a = build_cpu_rows(cpu_a)
    rows_b = build_cpu_rows(cpu_b)

    xmax = max(rows_a[-1][0], rows_b[-1][0])
    ymax = max(max(row[-1] for row in rows_a), max(row[-1] for row in rows_b))
    if ymax <= 0:
        return False

    title_a = plot_title("Process CPU Load", result_dir_a)
    title_b = plot_title("Process CPU Load", result_dir_b)
    label_a = f"Row: {os.path.basename(os.path.normpath(result_dir_a))}"
    label_b = f"Baseline: {os.path.basename(os.path.normpath(result_dir_b))}"

    with temp_dat_files() as make_dat:
        dat_a = make_dat(rows_a)
        dat_b = make_dat(rows_b)
        dat_line_a = make_dat([[row[0], row[-1]] for row in rows_a])
        dat_line_b = make_dat([[row[0], row[-1]] for row in rows_b])
        gp = build_cpu_comparison_gnuplot_script(
            dat_b, title_b, label_b,
            dat_a, title_a, label_a,
            dat_line_b, dat_line_a,
            out_svg, xmax, ymax * 1.05,
        )
        return run_gnuplot(gp)
