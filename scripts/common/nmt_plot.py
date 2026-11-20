#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Shared NMT malloc-breakdown parsing/plotting core for nmt.jsonl, used by
# both scripts/phase-post-process/plot-nmt.py (per-run plots generated at
# post-process time) and scripts/phase-analyze/listing.py (on-the-fly
# two-run comparison plots for `jbr-ls --report` with --compare on a
# malloc_* column). Not invoked directly.

import json
import os
import sys
from datetime import datetime

from gnuplot_utils import gnuplot_quote, gnuplot_dquote, temp_dat_files, run_gnuplot, plot_title


_NON_CATEGORY_KEYS = {
    "timestamp", "pid",
    "total", "total_malloc", "total_malloc_peak", "total_mmap_committed",
}

# (series title, category keys to sum, color). "gc" and "gccardset" are
# combined into one "GC" series (GCCardSet only exists on newer JDKs). NMT's
# own "Other" category is memory allocated via Unsafe.allocateMemory, not a
# leftover bucket, so it gets its own explicit "Unsafe" series.
SERIES = [
    ("Thread", ["thread"], "#4e79a7"),
    ("Code", ["code"], "#f28e2b"),
    ("GC", ["gc", "gccardset"], "#e15759"),
    ("Internal", ["internal"], "#76b7b2"),
    ("Symbol", ["symbol"], "#59a14f"),
    ("Arena Chunks", ["arena_chunk"], "#edc948"),
    ("Object Monitors", ["object_monitors"], "#b07aa1"),
    ("Unsafe", ["other"], "#ff9da7"),
]
_OTHERS_COLOR = "#9c755f"

_EXPLICIT_KEYS = {key for _, keys, _ in SERIES for key in keys}


def category_malloc(sample, key):
    entry = sample.get(key)
    return entry["malloc"] if entry else 0.0


def parse(jsonl_path):
    """
    Parse nmt.jsonl into (elapsed_seconds, [series_values...]) points, one
    value per entry in SERIES plus a trailing "Others" aggregate (the sum
    of every category not covered by SERIES). Elapsed is relative to the
    first sample's timestamp. Malformed lines are skipped with a warning.
    """
    points = []
    first_ts = None

    with open(jsonl_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
                ts = datetime.fromisoformat(sample["timestamp"])
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                print(f"Warning: skipping malformed line {lineno} in {jsonl_path}",
                      file=sys.stderr)
                continue

            if first_ts is None:
                first_ts = ts
            elapsed = (ts - first_ts).total_seconds()

            values = [sum(category_malloc(sample, key) for key in keys)
                      for _, keys, _ in SERIES]

            others = sum(
                value["malloc"]
                for key, value in sample.items()
                if key not in _NON_CATEGORY_KEYS
                and key not in _EXPLICIT_KEYS
            )
            values.append(others)

            points.append((elapsed, values))

    points.sort(key=lambda p: p[0])
    return points


def build_rows(points):
    """
    Build cumulative-sum rows (elapsed, 0, cum1, cum2, ..., cumN) from
    parse()'s output, suitable for gnuplot's `filledcurves` (see
    _plot_items). Each row's last column is that sample's total malloc.
    """
    rows = []
    for elapsed, values in points:
        row = [elapsed, 0.0]
        running = 0.0
        for v in values:
            running += v
            row.append(running)
        rows.append(row)
    return rows


def _plot_items(dat_path):
    names = [name for name, _, _ in SERIES] + ["Others"]
    colors = [color for _, _, color in SERIES] + [_OTHERS_COLOR]

    plot_items = []
    for i, (name, color) in enumerate(zip(names, colors)):
        lo_col = i + 2   # cumulative sum before this series
        hi_col = i + 3   # cumulative sum including this series
        plot_items.append(
            f"{gnuplot_quote(dat_path)} using 1:{hi_col}:{lo_col} "
            f"with filledcurves lc rgb {gnuplot_quote(color)} title {gnuplot_quote(name)}"
        )
    return "plot " + ", \\\n     ".join(plot_items)


def build_gnuplot_script(dat_path, out_svg, title):
    """Single-panel gnuplot script (one run's malloc breakdown, auto-ranged)."""
    script = f"""\
set terminal svg size 1400,700 enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

set title {gnuplot_dquote(title)} noenhanced
set xlabel 'Elapsed (s)'
set ylabel 'Malloc (MB)'
set yrange [0:*]
set grid
set key outside right top
set style fill solid 0.85 noborder

{_plot_items(dat_path)}
"""
    return script


def _total_rows(rows):
    """[[elapsed, total_malloc], ...] from build_rows()'s cumulative rows."""
    return [[row[0], row[-1]] for row in rows]


# Axis label / tic font and pinned plot-area edges, matching
# system_map_plot.py's comparison plot so the two look like one family.
_AXIS_FONT = "Sans,12"
_TIC_FONT = "Sans,11"
_LMARGIN_SCREEN = 0.045
_RMARGIN_SCREEN = 0.79


def build_comparison_gnuplot_script(dat_baseline, title_baseline, label_baseline,
                                     dat_row, title_row, label_row,
                                     dat_line_baseline, dat_line_row,
                                     out_svg, xmax, ymax):
    """Three-panel (stacked) gnuplot script comparing a run's NMT malloc
    usage against its baseline: a top panel plotting both runs' total
    malloc over time as lines, then the baseline's stacked malloc
    breakdown, then the row's own — in that order top to bottom. All three
    panels share one x-range [0, xmax] and one y-range [0, ymax] so they
    are directly comparable."""
    line_plot = (
        "plot "
        f"{gnuplot_quote(dat_line_baseline)} using 1:2 with lines lw 2 "
        f"lc rgb '#4e79a7' title {gnuplot_quote(label_baseline)}, \\\n     "
        f"{gnuplot_quote(dat_line_row)} using 1:2 with lines lw 2 "
        f"lc rgb '#e15759' title {gnuplot_quote(label_row)}"
    )
    script = f"""\
set terminal svg size 1400,1900 enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

set xlabel 'Elapsed (s)' font {gnuplot_quote(_AXIS_FONT)}
set ylabel 'Malloc (MB)' font {gnuplot_quote(_AXIS_FONT)}
set xtics font {gnuplot_quote(_TIC_FONT)}
set ytics font {gnuplot_quote(_TIC_FONT)}
set xrange [0:{xmax}]
set yrange [0:{ymax}]
set grid
set key outside right top
set style fill solid 0.85 noborder

# Pin the plot area's left/right edges identically for all three panels
# (see system_map_plot.build_comparison_gnuplot_script for why).
set lmargin at screen {_LMARGIN_SCREEN}
set rmargin at screen {_RMARGIN_SCREEN}

set multiplot layout 3,1

set title "Total Malloc (NMT)" noenhanced
{line_plot}

set title {gnuplot_dquote(title_baseline)} noenhanced
{_plot_items(dat_baseline)}

set title {gnuplot_dquote(title_row)} noenhanced
{_plot_items(dat_row)}

unset multiplot
"""
    return script


def build_comparison_svg(result_dir_a, result_dir_b, out_svg):
    """
    Build a stacked three-panel comparison SVG at out_svg: result_dir_b's
    (the baseline's) and result_dir_a's (the row's) NMT malloc breakdown
    over time — baseline first, then the row — preceded by a top panel
    plotting both runs' total malloc as line plots. All three panels share
    one x-range and one y-range sized to fit both runs.

    Returns True on success. Returns False, without writing out_svg, if
    either directory lacks an nmt.jsonl (or it has no usable data), or if
    gnuplot fails.
    """
    jsonl_a = os.path.join(result_dir_a, "nmt.jsonl")
    jsonl_b = os.path.join(result_dir_b, "nmt.jsonl")
    if not os.path.isfile(jsonl_a) or not os.path.isfile(jsonl_b):
        return False

    points_a = parse(jsonl_a)
    points_b = parse(jsonl_b)
    if not points_a or not points_b:
        return False

    rows_a = build_rows(points_a)
    rows_b = build_rows(points_b)

    xmax = max(rows_a[-1][0], rows_b[-1][0])
    ymax = max(max(row[-1] for row in rows_a), max(row[-1] for row in rows_b))
    if ymax <= 0:
        return False

    title_a = plot_title("Hotspot Malloc Usage (measured by NMT)", result_dir_a)
    title_b = plot_title("Hotspot Malloc Usage (measured by NMT)", result_dir_b)
    label_a = f"Row: {os.path.basename(os.path.normpath(result_dir_a))}"
    label_b = f"Baseline: {os.path.basename(os.path.normpath(result_dir_b))}"

    with temp_dat_files() as make_dat:
        dat_a = make_dat(rows_a)
        dat_b = make_dat(rows_b)
        dat_line_a = make_dat(_total_rows(rows_a))
        dat_line_b = make_dat(_total_rows(rows_b))
        gp = build_comparison_gnuplot_script(
            dat_b, title_b, label_b,
            dat_a, title_a, label_a,
            dat_line_b, dat_line_a,
            out_svg, xmax, ymax * 1.05,
        )
        return run_gnuplot(gp)
