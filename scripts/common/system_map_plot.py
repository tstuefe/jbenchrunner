#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Shared RSS-breakdown parsing/plotting core for system.map.jsonl, used by
# both scripts/phase-post-process/plot-system-map.py (per-run plots
# generated at post-process time) and scripts/phase-analyze/listing.py
# (on-the-fly two-run comparison plots for `jbr-ls --report` with
# --compare). Not invoked directly.

import bisect
import json
import os
import re
import sys
from datetime import datetime

from gnuplot_utils import gnuplot_quote, gnuplot_dquote, temp_dat_files, run_gnuplot, plot_title

_NON_CATEGORY_KEYS = {"timestamp", "pid", "total"}

_MB = 1024 * 1024

# Sentinel category key marking the "Other/Combined" catch-all slot: the sum
# of every category not otherwise listed in SERIES (including "combined",
# "stacks", "intern", "poll", any "mt*" NMT category except "chunk"/
# "chunk_*", which is folded into "arena" instead - see _is_arena_key).
OTHER = object()

# (series title, category key, color), in stack order (bottom to top). The
# OTHER slot is where the catch-all is stacked, not necessarily last.
SERIES = [
    ("Shared Lib Text", "sharedlib_text", "#edc948"),
    ("Shared Lib Data", "sharedlib_data", "#17becf"),
    ("Java Heap", "javaheap", "#4e79a7"),
    ("Code", "code", "#e15759"),
    ("Class Space", "class", "#f28e2b"),
    ("CDS", "cds", "#b07aa1"),
    ("Metaspace", "meta", "#59a14f"),
    ("GC", "gc", "#76b7b2"),
    ("Arenas", "arena", "#ff9da7"),
    ("Other/Combined", OTHER, "#7f7f7f"),
    ("JVM mallocs (inferred)", "jvm_malloc", "#d37295"),
    ("UNKNOWN", "unknown", "#9c755f"),
]

# Max distance between a system.map sample and the nmt.jsonl sample matched
# to it. Further away, the nmt sample is considered stale and ignored.
_MAX_NMT_GAP_SECONDS = 5.0

# "mtChunk" / "mtChunkSomething" in System.map are post-processed (see
# post-process-system-map-log.py) into "chunk" / "chunk_something"; both
# are folded into the "arena" series rather than "Other/Combined".
_ARENA_CHUNK_RE = re.compile(r"^chunk(_.*)?$")


def _is_arena_key(key):
    return key == "arena" or bool(_ARENA_CHUNK_RE.match(key))


OFFHEAP_SERIES = [s for s in SERIES if s[1] != "javaheap"]

_LITERAL_EXPLICIT_KEYS = {key for _, key, _ in SERIES if key is not OTHER}


def _is_explicit_key(key):
    return key in _LITERAL_EXPLICIT_KEYS or _is_arena_key(key)


def category_rss_mb(sample, key):
    if key is OTHER:
        return sum(
            value["rss"] / _MB
            for k, value in sample.items()
            if k not in _NON_CATEGORY_KEYS and not _is_explicit_key(k)
        )
    if key == "arena":
        return sum(
            value["rss"] / _MB
            for k, value in sample.items()
            if _is_arena_key(k)
        )
    entry = sample.get(key)
    return entry["rss"] / _MB if entry else 0.0


def load_nmt_malloc(nmt_path):
    """
    Read nmt.jsonl into a timestamp-sorted list of (datetime, total_malloc_mb).
    Returns [] if nmt_path is None or missing. Malformed lines are skipped.
    """
    if nmt_path is None or not os.path.isfile(nmt_path):
        return []
    samples = []
    with open(nmt_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
                samples.append((datetime.fromisoformat(sample["timestamp"]),
                                float(sample["total_malloc"])))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                continue
    samples.sort(key=lambda s: s[0])
    return samples


def nearest_nmt_malloc(nmt_samples, ts):
    """
    total_malloc (MB) of the nmt sample closest to ts (searching both
    directions; ties go to the earlier sample), or None if there is none
    within _MAX_NMT_GAP_SECONDS.
    """
    if not nmt_samples:
        return None
    i = bisect.bisect_left(nmt_samples, ts, key=lambda s: s[0])
    candidates = nmt_samples[max(i - 1, 0):i + 1]
    best = min(candidates, key=lambda s: abs((s[0] - ts).total_seconds()))
    if abs((best[0] - ts).total_seconds()) > _MAX_NMT_GAP_SECONDS:
        return None
    return best[1]


def parse(jsonl_path, nmt_path=None):
    """
    Parse system.map.jsonl into (elapsed_seconds, [series_values...]) points,
    one RSS value (MB) per entry in SERIES, in order (the OTHER slot holds
    the sum of every category not covered by an explicit entry). Elapsed is
    relative to the first sample's timestamp. Malformed lines are skipped
    with a warning.

    If nmt_path is given, each sample is matched to the nearest nmt.jsonl
    sample by timestamp; its total_malloc becomes the JVM_MALLOC value and is
    subtracted from UNKNOWN (clamped at zero).
    """
    nmt_samples = load_nmt_malloc(nmt_path)
    idx_jvm = [key for _, key, _ in SERIES].index("jvm_malloc")
    idx_unknown = [key for _, key, _ in SERIES].index("unknown")
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
                values = [category_rss_mb(sample, key) for _, key, _ in SERIES]
            except (json.JSONDecodeError, KeyError, ValueError, TypeError,
                    AttributeError):
                print(f"Warning: skipping malformed line {lineno} in {jsonl_path}",
                      file=sys.stderr)
                continue

            malloc = nearest_nmt_malloc(nmt_samples, ts)
            if malloc is not None:
                values[idx_jvm] = malloc
                values[idx_unknown] = max(0.0, values[idx_unknown] - malloc)

            if first_ts is None:
                first_ts = ts
            points.append(((ts - first_ts).total_seconds(), values))

    points.sort(key=lambda p: p[0])
    return points


def build_rows(points, skip_idx=None):
    """
    Build cumulative-sum rows (elapsed, 0, cum1, cum2, ..., cumN) from
    parse()'s output, suitable for gnuplot's `filledcurves` (see
    _plot_items). skip_idx drops one series column from the running sum
    (used to leave Java Heap out of an offheap plot).
    """
    rows = []
    for elapsed, values in points:
        row = [elapsed, 0.0]
        running = 0.0
        for i, v in enumerate(values):
            if i == skip_idx:
                continue
            running += v
            row.append(running)
        rows.append(row)
    return rows


def _plot_items(dat_path, series):
    items = []
    for i, (name, _key, color) in enumerate(series):
        lo_col = i + 2   # cumulative sum before this series
        hi_col = i + 3   # cumulative sum including this series
        items.append(
            f"{gnuplot_quote(dat_path)} using 1:{hi_col}:{lo_col} "
            f"with filledcurves lc rgb {gnuplot_quote(color)} title {gnuplot_quote(name)}"
        )
    return "plot " + ", \\\n     ".join(items)


def build_gnuplot_script(dat_path, out_svg, title, series):
    """Single-panel gnuplot script (one run's RSS breakdown, auto-ranged)."""
    script = f"""\
set terminal svg size 1400,700 enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

set title {gnuplot_dquote(title)} noenhanced
set xlabel 'Elapsed (s)'
set ylabel 'RSS (MB)'
set yrange [0:*]
set grid
set key outside right top
set style fill solid 0.85 noborder

{_plot_items(dat_path, series)}
"""
    return script


def _total_rows(rows):
    """[[elapsed, total_rss], ...] from build_rows()'s cumulative rows (each
    row's last column is the running total across all series, i.e. that
    sample's total RSS)."""
    return [[row[0], row[-1]] for row in rows]


# Axis label / tic font, a bit larger than the terminal's base 10pt, for
# readability in the (taller, 3-panel) comparison plot.
_AXIS_FONT = "Sans,12"
_TIC_FONT = "Sans,11"

# Left/right plot-area edges, as a fraction of the 1400px-wide canvas, fixed
# identically across all three panels (see build_comparison_gnuplot_script).
# The right edge is sized to fit the widest "key outside right" legend
# entry across every panel: the RSS breakdown's longest series name, "JVM
# mallocs (inferred)".
_LMARGIN_SCREEN = 0.045
_RMARGIN_SCREEN = 0.79


def build_comparison_gnuplot_script(dat_baseline, title_baseline, label_baseline,
                                     dat_row, title_row, label_row,
                                     dat_line_baseline, dat_line_row,
                                     out_svg, xmax, ymax, series=SERIES):
    """Three-panel (stacked) gnuplot script comparing a run against its
    baseline: a top panel plotting both runs' total RSS over time as lines,
    then the baseline's stacked RSS breakdown, then the row's own — in that
    order top to bottom. All three panels share one x-range [0, xmax] and
    one y-range [0, ymax] so they are directly comparable."""
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
set ylabel 'RSS (MB)' font {gnuplot_quote(_AXIS_FONT)}
set xtics font {gnuplot_quote(_TIC_FONT)}
set ytics font {gnuplot_quote(_TIC_FONT)}
set xrange [0:{xmax}]
set yrange [0:{ymax}]
set grid
set key outside right top
set style fill solid 0.85 noborder

# Pin the plot area's left/right edges to the same screen position for all
# three panels, rather than letting gnuplot auto-size them: the right
# margin gnuplot reserves for "key outside right" is auto-sized from each
# panel's own key text, and the 12-entry breakdown legend is much wider
# than the top panel's 2-entry one, so without pinning both margins
# explicitly the panels' plot areas end up horizontally misaligned.
set lmargin at screen {_LMARGIN_SCREEN}
set rmargin at screen {_RMARGIN_SCREEN}

set multiplot layout 3,1

set title "Total RSS" noenhanced
{line_plot}

set title {gnuplot_dquote(title_baseline)} noenhanced
{_plot_items(dat_baseline, series)}

set title {gnuplot_dquote(title_row)} noenhanced
{_plot_items(dat_row, series)}

unset multiplot
"""
    return script


def build_comparison_svg(result_dir_a, result_dir_b, out_svg):
    """
    Build a stacked three-panel comparison SVG at out_svg: result_dir_b (the
    baseline) and result_dir_a's (the row's) RSS breakdown over time —
    baseline first, then the row — preceded by a top panel plotting both
    runs' total RSS as line plots. All three panels share one x-range and
    one y-range sized to fit both runs (see build_comparison_gnuplot_script).

    Returns True on success. Returns False, without writing out_svg, if
    either directory lacks a system.map.jsonl (or it has no usable data),
    or if gnuplot fails.
    """
    jsonl_a = os.path.join(result_dir_a, "system.map.jsonl")
    jsonl_b = os.path.join(result_dir_b, "system.map.jsonl")
    if not os.path.isfile(jsonl_a) or not os.path.isfile(jsonl_b):
        return False

    points_a = parse(jsonl_a, os.path.join(result_dir_a, "nmt.jsonl"))
    points_b = parse(jsonl_b, os.path.join(result_dir_b, "nmt.jsonl"))
    if not points_a or not points_b:
        return False

    rows_a = build_rows(points_a)
    rows_b = build_rows(points_b)

    xmax = max(rows_a[-1][0], rows_b[-1][0])
    ymax = max(max(row[-1] for row in rows_a), max(row[-1] for row in rows_b))
    if ymax <= 0:
        return False

    title_a = plot_title("RSS Usage", result_dir_a)
    title_b = plot_title("RSS Usage", result_dir_b)
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
