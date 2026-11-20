#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# arena-log-parser.py
#
# Parse a single arena memory manager log (the periodic "Arena Memory Manager,
# Tick N" statistics blocks) and write a report directory with:
#
#   blocks-<block_size>.json / .svg   per size class time series (in blocks):
#       cached_blocks  - blocks held in all per-CPU ArenaBlockCaches ("count:",
#                        summed over the per-CPU records, or the single
#                        "across all active cores" record of newer logs)
#       in_use_blocks  - blocks really in use by the user, i.e. the slab's
#                        "in-use blocks:" minus cached_blocks
#       in_use_peak_blocks - "blocks in use (last tick peak):", the highest
#                        slab in-use count (incl. cached blocks) during the
#                        preceding tick interval; omitted if not in the log
#       dirty_blocks   - "Dirty granules:" translated to blocks
#                        (granules * granule_size / block_size)
#     plus, per tick (delta of the log's cumulative counters since the
#     previous tick; for the first tick, since startup):
#       cache_alloc_blocks    - "Blocks allocs" (summed over all CPUs)
#       cache_dealloc_blocks  - "Blocks deallocs" (summed over all CPUs)
#       cache_dealloc_ops     - "Bulk deallocs" (summed over all CPUs)
#       slab_alloc_blocks     - "Block units allocated"
#       slab_alloc_ops        - "Alloc ops"
#       slab_dealloc_blocks   - "Block units deallocated"
#       slab_dealloc_ops      - "Dealloc ops"
#       slab_released_blocks  - "Block units released"
#     (the counters depend on JVM options; missing from the log, they are 0)
#   allocs-<block_size>.svg           impulse plots of the per tick deltas, five
#                                     panels: cache blocks and slab blocks
#                                     (alloc up, dealloc down); alloc ops and
#                                     dealloc ops (down), slab drawn over cache;
#                                     cache absorption: cache ops minus slab ops,
#                                     i.e. ops done without slab involvement
#                                     (alloc up, dealloc down; clamped to 0).
#                                     Cache alloc ops are the cache alloc blocks
#                                     (there is no bulk cache alloc)
#   combined.json / .svg              the cached/in-use/in-use peak/dirty series
#                                     summed over all size classes, in bytes
#                                     (plotted in MiB; the in-use peak sum is an
#                                     upper bound, the peaks are not simultaneous),
#                                     plus the alloc/dealloc/released deltas in
#                                     bytes and, as plain counts, the ops deltas
#                                     and cache_alloc_blocks; each sample
#                                     also has by_block_size: block size ->
#                                     {in_use_peak_bytes (if logged),
#                                     dirty_bytes, cached_bytes}
#   combined-bytes-by-block-size.svg  three panels sharing the x axis (in-use
#                                     peak, dirty, cached; MiB), each stacking
#                                     the block sizes, smallest at the bottom;
#                                     a block size missing from a sample counts
#                                     as 0; the in-use peak panel is omitted if
#                                     the log has no "last tick peak"
#   combined-allocs.svg               impulse plot of the combined deltas, as
#                                     allocs-<block_size>.svg (memory in MiB)
#   combined-all.svg                  combined-bytes.svg, combined-bytes-by-block-size.svg
#                                     and combined-allocs.svg stacked top to
#                                     bottom in one file (nested <svg> elements)
#   arena-summary.json                time-weighted averages (in bytes) of the
#                                     in-use peak, dirty and cached series, per
#                                     block size and combined: trapezoidal
#                                     integral over [first tick, last tick]
#                                     divided by that span (same method as
#                                     jbr's aggregators.time_weighted_mean);
#                                     combined also has their maxima and, if
#                                     the log has an "OOMs per block size"
#                                     section, oom_end: block size -> OOM
#                                     count at the last tick
#
# blocks-*.svg / combined-bytes.svg are filled areas, all from 0, back to front:
# dirty, in-use + cached (the last tick peak), cached.
#
# Self-contained on purpose: depends only on the Python standard library (and
# gnuplot for the plots), so it can be copied and used outside jbenchrunner.
# A log ending in .zst is decompressed by running "zstd -dc" (needs zstd).
# If gnuplot is not installed, the JSON files are still written and plotting
# is skipped with a warning.
#
# Usage: arena-log-parser.py [-h] [-o DIR] [--header TEXT] <arena.log[.zst]>
#   Output goes to DIR/arena-report (DIR defaults to the current directory).
#   --header TEXT is written as an extra line below each plot's title.

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

_UPTIME_RE = re.compile(r"^\[(\d+(?:\.\d+)?)s\]")
_TICK_RE = re.compile(r"Arena Memory Manager, Tick (\d+)\s*$")
_SLAB_RE = re.compile(r"\]\s+ArenaSlab (\d+)\s*$")
# Old format: one "ArenaBlockCache <bs>" record per CPU; new format: one
# "ArenaBlockCache <bs> (across all active cores)" summary record.
_CACHE_RE = re.compile(r"\]\s+ArenaBlockCache (\d+)(?: \(across all active cores\))?\s*$")
_PERCORE_RE = re.compile(r"\]\s*PerCore Holder\s*$")
_GRANULE_SIZE_RE = re.compile(r"\]\s+granule_size: (\d+) ")
_IN_USE_RE = re.compile(r"\]\s+in-use blocks: (\d+)\s*$")
_IN_USE_PEAK_RE = re.compile(r"\]\s+blocks in use \(last tick peak\): (\d+)\s*$")
_DIRTY_RE = re.compile(r"\]\s+Dirty granules: (\d+) \((\d+) blocks")
_COUNT_RE = re.compile(r"\]\s+count: (\d+)\s*$")
# Newer logs: "OOMs per block size:" section, one "<block size>: <count>" line
# per block size (cumulative).
_OOMS_RE = re.compile(r"\]\s+OOMs per block size:\s*$")
_OOM_RE = re.compile(r"\]\s+(\d+): (\d+)\s*$")

# Cumulative counters: series key -> regex on the log line.
_SLAB_COUNTERS = {
    "slab_alloc_blocks": re.compile(r"\]\s+Block units allocated: (\d+)\s*$"),
    "slab_alloc_ops": re.compile(r"\]\s+Alloc ops: (\d+)\s*$"),
    "slab_dealloc_blocks": re.compile(r"\]\s+Block units deallocated: (\d+)\s*$"),
    "slab_dealloc_ops": re.compile(r"\]\s+Dealloc ops: (\d+)\s*$"),
    "slab_released_blocks": re.compile(r"\]\s+Block units released: (\d+)\s*$"),
}
# Old format: "Blocks allocs/deallocs", new format: "Block allocs/deallocs".
_CACHE_COUNTERS = {
    "cache_alloc_blocks": re.compile(r"\]\s+Blocks? allocs: (\d+)\s*$"),
    "cache_dealloc_blocks": re.compile(r"\]\s+Blocks? deallocs: (\d+)\s*$"),
    "cache_dealloc_ops": re.compile(r"\]\s+Bulk deallocs: (\d+)\s*$"),
}
COUNTER_KEYS = list(_CACHE_COUNTERS) + list(_SLAB_COUNTERS)
# Counters summed (in bytes) into the combined series; ops are not.
_COMBINED_COUNTER_KEYS = [k for k in COUNTER_KEYS if k.endswith("_blocks")]
# Counters summed as plain counts into the combined series: the ops, plus the
# cache alloc blocks (= cache alloc ops, there is no bulk cache alloc).
_COMBINED_COUNT_KEYS = ["cache_alloc_blocks"] + [k for k in COUNTER_KEYS if k.endswith("_ops")]

MIB = 1024 * 1024


def warn(msg):
    print(f"arena-log-parser.py: warning: {msg}", file=sys.stderr)


def _new_tick(number, uptime):
    # slabs: block_size -> {granule_size, slab_in_use, slab_in_use_peak (if logged),
    #                       dirty_granules, dirty_blocks_logged,
    #                       <_SLAB_COUNTERS keys> (cumulative)}
    # cached: block_size -> summed "count:" over all CPUs
    # cache_counters: block_size -> {<_CACHE_COUNTERS keys>: cumulative, summed over all CPUs}
    # ooms: block_size -> cumulative OOM count (empty if not logged)
    return {"tick": number, "uptime_s": uptime, "slabs": {}, "cached": {},
            "cache_counters": {}, "num_cache_records": 0, "ooms": {}}


def parse_log(lines):
    """
    Parse arena log lines into a list of raw tick records (see _new_tick).

    A truncated final tick (fewer slab/cache records than the ticks before
    it) is dropped with a warning.
    """
    ticks = []
    cur = None
    slab = None          # dict of the ArenaSlab section being read
    cache_size = None    # block size of the ArenaBlockCache section being read
    in_ooms = False      # reading the "OOMs per block size" section

    for line in lines:
        m = _TICK_RE.search(line)
        if m:
            um = _UPTIME_RE.match(line)
            cur = _new_tick(int(m.group(1)), float(um.group(1)) if um else None)
            ticks.append(cur)
            slab = None
            cache_size = None
            in_ooms = False
            continue
        if cur is None:
            continue

        m = _SLAB_RE.search(line)
        if m:
            slab = cur["slabs"].setdefault(int(m.group(1)), {})
            cache_size = None
            in_ooms = False
            continue
        m = _CACHE_RE.search(line)
        if m:
            cache_size = int(m.group(1))
            slab = None
            in_ooms = False
            continue
        if _PERCORE_RE.search(line):
            slab = None
            cache_size = None
            in_ooms = False
            continue
        if _OOMS_RE.search(line):
            slab = None
            cache_size = None
            in_ooms = True
            continue

        if in_ooms:
            m = _OOM_RE.search(line)
            if m:
                cur["ooms"][int(m.group(1))] = int(m.group(2))
            else:
                in_ooms = False
        elif slab is not None:
            m = _GRANULE_SIZE_RE.search(line)
            if m:
                slab["granule_size"] = int(m.group(1))
                continue
            m = _IN_USE_RE.search(line)
            if m:
                slab["slab_in_use"] = int(m.group(1))
                continue
            m = _IN_USE_PEAK_RE.search(line)
            if m:
                slab["slab_in_use_peak"] = int(m.group(1))
                continue
            m = _DIRTY_RE.search(line)
            if m:
                slab["dirty_granules"] = int(m.group(1))
                slab["dirty_blocks_logged"] = int(m.group(2))
                continue
            for key, rx in _SLAB_COUNTERS.items():
                m = rx.search(line)
                if m:
                    slab[key] = int(m.group(1))
                    break
        elif cache_size is not None:
            m = _COUNT_RE.search(line)
            if m:
                cur["cached"][cache_size] = cur["cached"].get(cache_size, 0) + int(m.group(1))
                cur["num_cache_records"] += 1
                continue
            for key, rx in _CACHE_COUNTERS.items():
                m = rx.search(line)
                if m:
                    cc = cur["cache_counters"].setdefault(cache_size, {})
                    cc[key] = cc.get(key, 0) + int(m.group(1))
                    break

    def size_of(t):
        complete_slabs = sum(1 for s in t["slabs"].values()
                             if {"granule_size", "slab_in_use", "dirty_granules"} <= s.keys())
        return (complete_slabs, t["num_cache_records"])

    if len(ticks) >= 2:
        last, prev = size_of(ticks[-1]), size_of(ticks[-2])
        if last[0] < prev[0] or last[1] < prev[1]:
            warn(f"dropping incomplete final tick {ticks[-1]['tick']}")
            ticks.pop()
    return ticks


def build_series(ticks):
    """
    Turn raw tick records into per block size series and a combined series.

    Returns (per_size, combined):
      per_size: block_size -> {"block_size", "granule_size", "unit", "samples": [...]}
      combined: {"unit", "block_sizes", "samples": [...]}
    """
    block_sizes = sorted({bs for t in ticks for bs in t["slabs"]})
    cache_only = sorted({bs for t in ticks for bs in t["cached"]} - set(block_sizes))
    if cache_only:
        warn(f"caches without a slab are ignored: {cache_only}")

    per_size = {}
    for bs in block_sizes:
        granule = next(t["slabs"][bs]["granule_size"] for t in ticks
                       if "granule_size" in t["slabs"].get(bs, {}))
        per_size[bs] = {"block_size": bs, "granule_size": granule,
                        "unit": "blocks", "samples": []}

    combined = {"unit": "bytes", "block_sizes": block_sizes, "samples": []}
    num_negative = 0
    prev_counters = {bs: {k: 0 for k in COUNTER_KEYS} for bs in block_sizes}

    for t in ticks:
        tot = {"cached_bytes": 0, "in_use_bytes": 0, "in_use_peak_bytes": 0, "dirty_bytes": 0}
        tot.update({k[:-len("_blocks")] + "_bytes": 0 for k in _COMBINED_COUNTER_KEYS})
        tot.update({k: 0 for k in _COMBINED_COUNT_KEYS})
        peak_complete = True
        by_bs = {}
        for bs in block_sizes:
            s = t["slabs"].get(bs)
            if not s or not {"granule_size", "slab_in_use", "dirty_granules"} <= s.keys():
                continue
            cached = t["cached"].get(bs, 0)
            in_use = s["slab_in_use"] - cached
            if in_use < 0:
                num_negative += 1
            dirty = s["dirty_granules"] * s["granule_size"] // bs
            if dirty != s.get("dirty_blocks_logged", dirty):
                warn(f"tick {t['tick']}, block size {bs}: computed {dirty} dirty blocks, "
                     f"log says {s['dirty_blocks_logged']}")
            # Counters are cumulative; a counter missing from the log keeps its
            # previous value (delta 0).
            cumulative = {**prev_counters[bs], **{k: v for k, v in s.items() if k in _SLAB_COUNTERS},
                          **t["cache_counters"].get(bs, {})}
            deltas = {k: cumulative[k] - prev_counters[bs][k] for k in COUNTER_KEYS}
            prev_counters[bs] = cumulative
            peak = {"in_use_peak_blocks": s["slab_in_use_peak"]} if "slab_in_use_peak" in s else {}
            per_size[bs]["samples"].append({
                "tick": t["tick"], "uptime_s": t["uptime_s"],
                "cached_blocks": cached, "in_use_blocks": in_use, **peak, "dirty_blocks": dirty,
                **deltas,
            })
            tot["cached_bytes"] += cached * bs
            tot["in_use_bytes"] += in_use * bs
            if peak:
                tot["in_use_peak_bytes"] += peak["in_use_peak_blocks"] * bs
            else:
                peak_complete = False
            tot["dirty_bytes"] += dirty * bs
            by_bs[str(bs)] = {
                **({"in_use_peak_bytes": peak["in_use_peak_blocks"] * bs} if peak else {}),
                "dirty_bytes": dirty * bs, "cached_bytes": cached * bs}
            for k in _COMBINED_COUNTER_KEYS:
                tot[k[:-len("_blocks")] + "_bytes"] += deltas[k] * bs
            for k in _COMBINED_COUNT_KEYS:
                tot[k] += deltas[k]
        if not peak_complete:
            del tot["in_use_peak_bytes"]
        combined["samples"].append({"tick": t["tick"], "uptime_s": t["uptime_s"], **tot,
                                    "by_block_size": by_bs})

    if num_negative:
        warn(f"{num_negative} samples with cached > slab in-use blocks (negative in_use_blocks)")
    return per_size, combined


def _time_weighted_mean(points):
    """
    Time-weighted mean of (x, value) points: trapezoidal integral divided by
    the span from first to last x. Copy of jbr's
    scripts/phase-post-process/aggregators.time_weighted_mean (this tool is
    self-contained). None if there are no points; the plain mean if there is
    no elapsed time.
    """
    if not points:
        return None
    ordered = sorted(points, key=lambda p: p[0])
    duration = ordered[-1][0] - ordered[0][0]
    if duration <= 0:
        return sum(v for _, v in ordered) / len(ordered)
    area = sum((x1 - x0) * (v0 + v1) / 2.0
               for (x0, v0), (x1, v1) in zip(ordered, ordered[1:]))
    return area / duration


_SUMMARY_KEYS = ("in_use_peak", "dirty", "cached")


def build_summary(per_size, combined, ticks=()):
    """
    Time-weighted averages in bytes of the in-use peak, dirty and cached
    series, combined and per block size. A series missing from all samples
    (no "last tick peak" in the log) has no average. The combined section
    also has the maxima of these series and, if the log has an OOMs section,
    oom_end: block size -> OOM count at the last tick that logged one.
    """
    def averages(samples, suffix, scale):
        out = {}
        for k in _SUMMARY_KEYS:
            avg = _time_weighted_mean([(_x(s), s[k + suffix] * scale)
                                       for s in samples if k + suffix in s])
            if avg is not None:
                out[f"{k}_bytes_avg"] = round(avg)
        return out

    combined_summary = averages(combined["samples"], "_bytes", 1)
    for k in _SUMMARY_KEYS:
        values = [s[k + "_bytes"] for s in combined["samples"] if k + "_bytes" in s]
        if values:
            combined_summary[f"{k}_bytes_max"] = max(values)
    oom_ticks = [t for t in ticks if t.get("ooms")]
    if oom_ticks:
        combined_summary["oom_end"] = {str(bs): n for bs, n in sorted(oom_ticks[-1]["ooms"].items())}

    return {
        "unit": "bytes",
        "combined": combined_summary,
        "block_sizes": [{"block_size": bs, **averages(d["samples"], "_blocks", bs)}
                        for bs, d in per_size.items()],
    }


def _gnuplot_quote(s):
    # Same as _gnuplot_dquote: a single-quoted gnuplot '...' string can't hold
    # a newline (gnuplot ends the command there, so the rest of the value
    # would run as a new command), and it has no escape for one.
    return _gnuplot_dquote(s)


def _gnuplot_dquote(s):
    # Double-quoted gnuplot string (needed for "\n" line breaks): escape
    # backslashes, double quotes and backquotes (gnuplot runs `...` inside a
    # double-quoted string as a shell command), turn real newlines into "\n".
    return '"' + (str(s).replace("\\", "\\\\").replace('"', '\\"')
                  .replace("`", "\\`").replace("\n", "\\n")) + '"'


def _title(header, title):
    """Plot title: the generated title line, then the optional header line."""
    return f"{title}\n{header}" if header else title


def _x(sample):
    return sample["uptime_s"] if sample["uptime_s"] is not None else sample["tick"]


# Left/right margins (character widths) of all plots, so that plots of the
# same samples (e.g. the three combined plots stacked in combined-all.svg)
# have the same plot area width; the x range is gnuplot's autoscaled one
# everywhere, so their x axes line up. 14 leaves room for the stacked plots'
# outside key.
_X_MARGINS = "set lmargin 12\nset rmargin 14"


def build_plot_script(samples, keys, out_svg, dat_path, title, ylabel, scale=1):
    """
    Write a gnuplot data file for samples and return the gnuplot script plotting it.

    keys are the (cached, in-use peak, dirty) sample keys; a key missing from a
    sample is written as NaN (not plotted). Plotted as filled areas from 0,
    back to front: dirty, in-use + cached (the peak), cached.
    """
    with open(dat_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(" ".join([f"{_x(s)}"] + [f"{s[k] / scale}" if k in s else "NaN"
                                             for k in keys]) + "\n")
    has_uptime = all(s["uptime_s"] is not None for s in samples)
    dat = _gnuplot_quote(dat_path)
    # columns: 1 x, 2 cached, 3 in-use peak (incl. cached), 4 dirty
    layers = [
        ("4", "#c8c8c8", "dirty"),
        ("3", "#e6820e", "in-use + cached"),
        ("2", "#9467bd", "cached"),
    ]
    plots = ", \\\n     ".join(
        f"{dat} using 1:{col} with filledcurves x1 fs solid 1.0 noborder "
        f"lc rgb '{color}' title {_gnuplot_quote(lbl)}"
        for col, color, lbl in layers)
    return f"""\
set terminal svg size 1400,600 enhanced font 'Sans,10' background 'white'
set output {_gnuplot_quote(out_svg)}
set title {_gnuplot_dquote(title)} noenhanced
set xlabel {_gnuplot_quote('Uptime (s)' if has_uptime else 'Tick')}
set ylabel {_gnuplot_quote(ylabel)}
set yrange [0:*]
{_X_MARGINS}
set grid front
set key top left noenhanced
plot {plots}
"""


# Cache alloc ops are the cache alloc blocks: there is no bulk cache alloc.
IMPULSE_OPS_KEYS = ["cache_alloc_blocks", "cache_dealloc_ops", "slab_alloc_ops", "slab_dealloc_ops"]


def build_impulse_script(samples, keys, out_svg, dat_path, title, ylabel, scale=1,
                         ops_keys=IMPULSE_OPS_KEYS, amount="Number of blocks"):
    """
    Write a gnuplot data file for samples and return a gnuplot script plotting
    per tick deltas as impulses in five panels sharing the x axis:
      - cache blocks, slab blocks: alloc up, dealloc down
      - alloc ops: cache, slab drawn over it (both up)
      - dealloc ops: cache, slab drawn over it (both down, x axis at the top)
      - cache absorption: cache ops minus slab ops (alloc up, dealloc down),
        clamped to 0

    keys are the (cache alloc, cache dealloc, slab alloc, slab dealloc) sample
    keys of the blocks panels (divided by scale, y axis ylabel, panel titles
    starting with amount); ops_keys the same for the ops panels (unscaled).
    """
    with open(dat_path, "w", encoding="utf-8") as f:
        for s in samples:
            ca, cd, sa, sd = (s[k] for k in ops_keys)
            f.write(" ".join([f"{_x(s)}"] + [f"{s[k] / scale}" for k in keys]
                             + [f"{v}" for v in (ca, cd, sa, sd)]
                             + [f"{max(0, ca - sa)}", f"{max(0, cd - sd)}"]) + "\n")
    has_uptime = all(s["uptime_s"] is not None for s in samples)
    dat = _gnuplot_quote(dat_path)

    # columns: 1 x, 2-5 blocks (cache alloc, cache dealloc, slab alloc, slab
    # dealloc), 6-9 ops (same order), 10/11 cache absorption alloc/dealloc
    def panel(name, ylab, yrange, key, series, xlabel=""):
        """series: (using expression, color, title), drawn in that order."""
        plots = ", \\\n     ".join(
            f"{dat} using 1:{expr} with impulses lw 2 lc rgb '{color}' title {_gnuplot_quote(t)}"
            for expr, color, t in series)
        return f"""\
set title {_gnuplot_quote(name)} noenhanced
set xlabel {_gnuplot_quote(xlabel)}
set ylabel {_gnuplot_quote(ylab)}
set yrange [{yrange}]
set key {key} right noenhanced
plot {plots}
"""

    alloc_color, dealloc_color = "#e6820e", "#9467bd"
    cache_color, slab_color = "#6baed6", "#08519c"
    absorb_alloc_color, absorb_dealloc_color = "#74c476", "#006d2c"
    xlabel = "Uptime (s)" if has_uptime else "Tick"
    panels = (
        panel(f"{amount} allocated/deallocated, Cache", ylabel, "*:*", "top",
              [("2", alloc_color, "alloc"), ("(-$3)", dealloc_color, "dealloc")])
        + panel(f"{amount} allocated/deallocated, Slab", ylabel, "*:*", "top",
                [("4", alloc_color, "alloc"), ("(-$5)", dealloc_color, "dealloc")])
        + panel("Number of alloc ops, Cache vs. Slab", "Ops", "0:*", "top",
                [("6", cache_color, "cache"), ("8", slab_color, "slab")])
        + panel("Number of dealloc ops, Cache vs. Slab", "Ops", "*:0", "bottom",
                [("(-$7)", cache_color, "cache"), ("(-$9)", slab_color, "slab")])
        + panel("Cache absorption: ops without slab involvement (cache ops - slab ops)",
                "Ops", "*:*", "top",
                [("10", absorb_alloc_color, "alloc"),
                 ("(-$11)", absorb_dealloc_color, "dealloc")], xlabel))

    return f"""\
set terminal svg size 1400,1850 enhanced font 'Sans,10' background 'white'
set output {_gnuplot_quote(out_svg)}
set multiplot layout 5,1 title {_gnuplot_dquote(title)} noenhanced
{_X_MARGINS}
set grid
set xzeroaxis lt -1
{panels}unset multiplot
"""


# One color per block size (stacked plots), smallest block size first.
_STACK_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                 "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

_STACK_PANELS = [("in_use_peak_bytes", "in-use + cached (last tick peak)"),
                 ("dirty_bytes", "dirty"),
                 ("cached_bytes", "cached")]


def build_stacked_script(samples, block_sizes, out_svg, dat_path, title, scale=MIB):
    """
    Write a gnuplot data file for the combined samples and return a gnuplot
    script with one panel per _STACK_PANELS metric (shared x axis), each
    stacking the per block size values (sample["by_block_size"]), smallest
    block size at the bottom. A block size missing from a sample counts as 0;
    a metric missing from all samples gets no panel.
    """
    panels = [(k, name) for k, name in _STACK_PANELS
              if any(k in v for s in samples for v in s["by_block_size"].values())]
    n = len(block_sizes)
    with open(dat_path, "w", encoding="utf-8") as f:
        for s in samples:
            row = [f"{_x(s)}"]
            for k, _ in panels:
                total = 0
                for bs in block_sizes:
                    total += s["by_block_size"].get(str(bs), {}).get(k, 0)
                    row.append(f"{total / scale}")
            f.write(" ".join(row) + "\n")
    has_uptime = all(s["uptime_s"] is not None for s in samples)
    dat = _gnuplot_quote(dat_path)

    def panel(p, name, xlabel):
        # columns of panel p: 2 + p*n + i is the stack top of block size i
        bands = []
        for i, bs in enumerate(block_sizes):
            lower = "(0)" if i == 0 else str(1 + p * n + i)
            bands.append(f"{dat} using 1:{lower}:{2 + p * n + i} with filledcurves "
                         f"fs solid 1.0 noborder lc rgb '{_STACK_COLORS[i % len(_STACK_COLORS)]}' "
                         f"title {_gnuplot_quote(bs)}")
        plots = ", \\\n     ".join(bands)
        return f"""\
set title {_gnuplot_quote(name)} noenhanced
set xlabel {_gnuplot_quote(xlabel)}
plot {plots}
"""

    xlabel = "Uptime (s)" if has_uptime else "Tick"
    body = "".join(panel(p, name, xlabel if p == len(panels) - 1 else "")
                   for p, (_, name) in enumerate(panels))
    return f"""\
set terminal svg size 1400,{300 * len(panels) + 100} enhanced font 'Sans,10' background 'white'
set output {_gnuplot_quote(out_svg)}
set multiplot layout {len(panels)},1 title {_gnuplot_dquote(title)} noenhanced
{_X_MARGINS}
set ylabel 'Memory (MiB)'
set yrange [0:*]
set grid front
set key outside right top title 'block size' noenhanced
{body}unset multiplot
"""


def write_report(per_size, combined, report_dir, log_name, plot=True, header=None, ticks=()):
    os.makedirs(report_dir, exist_ok=True)
    for bs, data in per_size.items():
        with open(os.path.join(report_dir, f"blocks-{bs}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    with open(os.path.join(report_dir, "combined.json"), "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)
        f.write("\n")
    with open(os.path.join(report_dir, "arena-summary.json"), "w", encoding="utf-8") as f:
        json.dump(build_summary(per_size, combined, ticks), f, indent=2)
        f.write("\n")

    if not plot:
        return
    if shutil.which("gnuplot") is None:
        warn("gnuplot not found, skipping plots")
        return

    # combined-bytes.svg was called combined.svg before; drop a stale copy
    # left by an earlier run on the same report directory.
    legacy = os.path.join(report_dir, "combined.svg")
    if os.path.exists(legacy):
        os.remove(legacy)

    with tempfile.TemporaryDirectory() as tmp:
        jobs = []
        for bs, data in per_size.items():
            jobs.append(build_plot_script(
                data["samples"], ["cached_blocks", "in_use_peak_blocks", "dirty_blocks"],
                os.path.join(report_dir, f"blocks-{bs}.svg"), os.path.join(tmp, f"{bs}.dat"),
                _title(header, f"{log_name}: block size {bs} (granule {data['granule_size']})"),
                "Blocks"))
            jobs.append(build_impulse_script(
                data["samples"],
                ["cache_alloc_blocks", "cache_dealloc_blocks",
                 "slab_alloc_blocks", "slab_dealloc_blocks"],
                os.path.join(report_dir, f"allocs-{bs}.svg"),
                os.path.join(tmp, f"allocs-{bs}.dat"),
                _title(header, f"{log_name}: block size {bs}, allocs/deallocs per tick"),
                "Blocks"))
        jobs.append(build_plot_script(
            combined["samples"], ["cached_bytes", "in_use_peak_bytes", "dirty_bytes"],
            os.path.join(report_dir, "combined-bytes.svg"), os.path.join(tmp, "combined.dat"),
            _title(header, f"{log_name}: all block sizes combined"), "Memory (MiB)", scale=MIB))
        jobs.append(build_stacked_script(
            combined["samples"], combined["block_sizes"],
            os.path.join(report_dir, "combined-bytes-by-block-size.svg"),
            os.path.join(tmp, "combined-bytes-by-block-size.dat"),
            _title(header, f"{log_name}: all block sizes combined, stacked by block size")))
        jobs.append(build_impulse_script(
            combined["samples"],
            ["cache_alloc_bytes", "cache_dealloc_bytes", "slab_alloc_bytes", "slab_dealloc_bytes"],
            os.path.join(report_dir, "combined-allocs.svg"),
            os.path.join(tmp, "combined-allocs.dat"),
            _title(header, f"{log_name}: all block sizes combined, allocs/deallocs per tick"),
            "MiB", scale=MIB, amount="Memory"))
        for script in jobs:
            proc = subprocess.run(["gnuplot"], input=script, text=True, capture_output=True)
            if proc.returncode != 0:
                warn(f"gnuplot failed (exit {proc.returncode}): {proc.stderr.strip()}")
    stack_svgs([os.path.join(report_dir, n) for n in COMBINED_ALL_PARTS],
               os.path.join(report_dir, "combined-all.svg"))


# Plots stacked top to bottom into combined-all.svg.
COMBINED_ALL_PARTS = ("combined-bytes.svg", "combined-bytes-by-block-size.svg", "combined-allocs.svg")

_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.S)
_SVG_DIM_RE = r'\b{}="(\d+(?:\.\d+)?)'
_SVG_PROLOG_RE = re.compile(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>", re.S)


def stack_svgs(paths, out_svg):
    """
    Write out_svg: the SVG files in paths (those that exist) embedded as
    nested <svg> elements, one below the other, each at its own size. Files
    without a readable width/height on their root element are skipped with
    a warning. Nothing is written if no file is usable. Returns True if
    out_svg was written.
    """
    parts = []
    y = 0
    width = 0
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = _SVG_PROLOG_RE.sub("", f.read()).strip()
        m = _SVG_OPEN_RE.search(text)
        w = m and re.search(_SVG_DIM_RE.format("width"), m.group(0))
        h = m and re.search(_SVG_DIM_RE.format("height"), m.group(0))
        if not (w and h):
            warn(f"{path}: no width/height on the svg element, not stacked")
            continue
        opening = m.group(0)[:-1].rstrip("/").rstrip() + f' x="0" y="{y:g}">'
        parts.append(text[:m.start()] + opening + text[m.end():])
        y += float(h.group(1))
        width = max(width, float(w.group(1)))
    if not parts:
        return False
    with open(out_svg, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8" standalone="no"?>\n')
        f.write(f'<svg width="{width:g}" height="{y:g}" viewBox="0 0 {width:g} {y:g}" '
                'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">\n')
        f.write("\n".join(parts))
        f.write("\n</svg>\n")
    return True


def read_log(path):
    """
    Parse the log at path; a path ending in .zst is decompressed via "zstd -dc".
    A zstd failure (e.g. a truncated final frame) only warns: what was decoded
    up to then is used (and a cut-off final tick dropped by parse_log).
    """
    if not path.endswith(".zst"):
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_log(f)
    proc = subprocess.Popen(["zstd", "-dc", "--", path], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace")
    ticks = parse_log(proc.stdout)
    _, err = proc.communicate()
    if proc.returncode != 0:
        warn(f"zstd failed on {path} (exit {proc.returncode}): {err.strip()}")
    return ticks


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Parse an arena memory manager log into per block size and combined "
                    "time series (JSON) and plots (SVG, needs gnuplot).")
    ap.add_argument("log", help="arena log file (.zst: zstd-compressed)")
    ap.add_argument("-o", "--output", default=".",
                    help="parent directory of the arena-report directory (default: current dir)")
    ap.add_argument("--header",
                    help="extra line written below the generated title line of each plot")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.log):
        print(f"Error: {args.log}: no such file", file=sys.stderr)
        return 1
    try:
        ticks = read_log(args.log)
    except FileNotFoundError:
        print(f"Error: {args.log}: cannot decompress, 'zstd' not found on PATH", file=sys.stderr)
        return 1
    if not ticks:
        print(f"Error: {args.log}: no arena statistics ticks found", file=sys.stderr)
        return 1

    per_size, combined = build_series(ticks)
    report_dir = os.path.join(args.output, "arena-report")
    log_name = os.path.basename(args.log)
    if log_name.endswith(".zst"):
        log_name = log_name[:-len(".zst")]
    write_report(per_size, combined, report_dir, log_name, header=args.header, ticks=ticks)
    print(f"{len(ticks)} ticks, {len(per_size)} block sizes -> {report_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
