# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
listing.py — Core table-rendering logic for jbr-ls.

Provides column definitions, cell extraction, jvmargs sanitization,
column-group expansion, auto-omit, sort, and table formatting for the
jbr-ls run-listing tool (spec §1.2).

Public API
----------
resolve_columns(o_arg, O_arg)          -> list[str]
resolve_sort_cols(sort_col_arg)        -> list[str]
resolve_group_cols(group_arg, col_list) -> list[str] or None
resolve_compare_cols(compare_arg, col_list) -> dict[str, float|None] or None
extract_cell(col, merged)              -> str
sanitize_jvmargs(flags)               -> str
format_run_table(rows, col_list, sort_col, sort_asc, no_autoomit,
                 results_dir, report_dir, group_cols, baseline_runid,
                 compare_cols, markdown, html, color,
                 relative_links, no_header, compare_thresholds) -> str
"""

import math
import os
from html import escape as html_escape
from urllib.parse import quote as url_quote
from pathlib import Path

from term_link import terminal_link
from term_color import colorize
from system_map_plot import build_comparison_svg as build_system_map_comparison_svg
from pidstat_plot import build_comparison_svg as build_pidstat_comparison_svg
from pidstat_plot import build_cpu_comparison_svg
from nmt_plot import build_comparison_svg as build_nmt_comparison_svg
from pagefaults_plot import build_comparison_svg as build_page_faults_comparison_svg
from arena_plot import build_comparison_svg as build_arena_comparison_svg

# ---------------------------------------------------------------------------
# Column groups (spec §1.2.5)
# ---------------------------------------------------------------------------

GROUPS = {
    "DEFAULT":  ["runid", "time", "name", "tags"],
    "JVM":      ["jdkname", "jdktype", "jdkmajor", "jdkversion"],
    "RESULTS":  ["duration", "result_ms", "stderr_pct", "startup_ms", "maxjops", "critjops", "hbirmax", "hbirset"],
    "LATENCY":  ["p50", "p99", "p100", "p99_stderr_pct"],
    "LATENCYSMOOTH": ["p50_smooth", "p99_smooth", "p100_smooth", "p99_smooth_stderr_pct"],
    "HOSTINFO": ["hostname", "arch", "os"],
    "FLAGS":    ["heapsz", "heapszf", "gc", "jvmargs"],
    "MEM":      ["rssmax", "rssavg", "mallocmax", "mallocavg"],
    "FAULTS":   ["minflt", "majflt"],
    "ARENA":    ["ardmax", "ardavg"],
    "ARENAALL": ["arumax", "aruavg", "ardmax", "ardavg", "arcmax", "arcavg"],
    "TEMP":     ["tempmax", "tempin", "tempout"],
    "CPU":      ["cpu_usr_avg", "cpu_sys_avg", "cpu_avg"],
    "CPUALL":   ["cpu_usr_avg", "cpu_sys_avg", "cpu_guest_avg",
                 "cpu_wait_avg", "cpu_avg",
                 "cpu_usr_max", "cpu_sys_max", "cpu_guest_max",
                 "cpu_wait_max", "cpu_max"],
    "CPUMAX":   ["cpu_usr_max", "cpu_sys_max", "cpu_guest_max",
                 "cpu_wait_max", "cpu_max"],
    "THREADS":  ["osthreads_max", "osthreads_avg",
                 "c2_max", "c2_avg", "c1_max", "c1_avg",
                 "gc_max", "gc_avg", "mutator_max", "mutator_avg",
                 "other_max", "other_avg"],
    "THREADCPU": ["c2_cpu_avg", "c1_cpu_avg", "gc_cpu_avg", "mutator_cpu_avg", "other_cpu_avg",
                  "unaccounted_cpu_avg",
                  "c2_cpu_max", "c1_cpu_max", "gc_cpu_max", "mutator_cpu_max", "other_cpu_max",
                  "unaccounted_cpu_max"],
}

# Variable-width columns; moved to the end of the column list (except
# with -O) so they do not push the other columns to the right
# (spec §1.2.2).
TRAILING_COLS = ("collectors", "tags", "jvmargs")

# Per-category thread counts and CPU usage from thread dumps (see
# aggregators.parse_thread_categories), keyed by column name, valued by
# (category, key under merged["threads"][category]).
_THREAD_CATEGORY_STATS = {
    "max": "num_threads_max", "avg": "num_threads_avg",
    "cpu_avg": "cpu_pct_avg", "cpu_max": "cpu_pct_max",
}
_THREAD_CATEGORY_FIELDS = {
    f"{category}_{stat}": (category, key)
    for category in ("c2", "c1", "gc", "mutator", "other")
    for stat, key in _THREAD_CATEGORY_STATS.items()
}
# CPU time no dumped thread accounts for: CPU columns only.
_THREAD_CATEGORY_FIELDS.update({
    "unaccounted_cpu_avg": ("unaccounted", "cpu_pct_avg"),
    "unaccounted_cpu_max": ("unaccounted", "cpu_pct_max"),
})

# pidstat CPU percentage metrics/stats (see aggregators.parse_cpu), keyed by
# column name, valued by the result.json key under merged["cpu"]. E.g.
# column "cpu_usr_max" reads "pct_usr_max", "cpu_sys_max" reads
# "pct_system_max"; the total ("pct_cpu_*") is plain "cpu_max" etc.
_CPU_METRICS = {"usr": "usr", "sys": "system", "guest": "guest", "wait": "wait", "": "cpu"}
_CPU_STATS = ("max", "avg")
_CPU_FIELDS = {
    (f"cpu_{metric}_{stat}" if metric else f"cpu_{stat}"): f"pct_{json_metric}_{stat}"
    for metric, json_metric in _CPU_METRICS.items() for stat in _CPU_STATS
}

# Arena memory manager columns (combined over all block sizes, MB; see
# aggregators.parse_arena_summary), keyed by column name, valued by the
# result.json key under merged["arena"].
_ARENA_FIELDS = {
    "arumax": "used_mb_max", "aruavg": "used_mb_avg",
    "ardmax": "dirty_mb_max", "ardavg": "dirty_mb_avg",
    "arcmax": "cached_mb_max", "arcavg": "cached_mb_avg",
}

# DaCapo tail latency summary columns (usec; see parse_dacapo_results);
# column name == key under merged["dacapo_result"]. Never auto-omitted, so
# that "-o LATENCY" always shows its columns.
_LATENCY_PCTL_FIELDS = ("p50", "p99", "p100", "p50_smooth", "p99_smooth", "p100_smooth")
_LATENCY_FIELDS = (*_LATENCY_PCTL_FIELDS,
                   "p99_stderr", "p99_stderr_pct", "p99_smooth_stderr", "p99_smooth_stderr_pct")

# Columns that may be auto-omitted when all values are "-" (spec §1.2.6).
AUTO_OMIT_CANDIDATES = {"result_ms", "stderr_ms", "stderr_pct", "startup_ms", "maxjops", "critjops", "hbirmax", "hbirset",
                        "heapszf", *_THREAD_CATEGORY_FIELDS}

# All known column names (used for validation).
ALL_COLUMNS = [
    "runid", "name", "benchmark", "sub", "datasize", "time", "duration", "collint_secs", "collectors",
    "tags",
    "batchid", "repeat_no",
    "jdkname", "jdkmajor", "jdktype", "jdkversion", "hostname", "arch", "os",
    "result_ms", "stderr_ms", "stderr_pct", "startup_ms", "maxjops", "critjops", "hbirmax", "hbirset",
    *_LATENCY_FIELDS,
    "heapsz", "heapszf", "gc", "jvmargs",
    "rssmax", "rssavg",
    "mallocmax", "mallocavg",
    "minflt", "majflt",
    *_ARENA_FIELDS,
    "tempmax", "tempin", "tempout",
    *_CPU_FIELDS,
    "osthreads_max", "osthreads_avg",
    *_THREAD_CATEGORY_FIELDS,
]

# Columns treated as numeric for sort purposes (spec §1.2.7).
_NUMERIC_SORT_COLS = {"jdkmajor", "duration", "collint_secs", "repeat_no", "heapsz", "heapszf", "result_ms", "stderr_ms",
                      "stderr_pct", "startup_ms", "maxjops", "critjops", "hbirmax", "hbirset",
                      *_LATENCY_FIELDS,
                      "rssmax", "rssavg",
                      "mallocmax", "mallocavg",
                      "minflt", "majflt",
                      *_ARENA_FIELDS,
                      "tempmax", "tempin", "tempout",
                      *_CPU_FIELDS,
                      "osthreads_max", "osthreads_avg",
                      *_THREAD_CATEGORY_FIELDS}

# --compare columns whose delta gets colored green/red under --color, and
# whether a larger value is the "good" direction for that column (critjops/
# maxjops are throughput -- higher is better; result_ms/startup_ms are elapsed time,
# the p50/p99/p100 latency percentiles, the rss*/malloc* memory-footprint columns
# and the page fault counts are all lower-is-better).
# Columns not listed here (nor in _COMPARE_COLOR_HEAT) are never colored,
# even when named in --compare.
_COMPARE_COLOR_HIGHER_IS_BETTER = {
    "critjops": True,
    "maxjops": True,
    "result_ms": False,
    "startup_ms": False,
    **{col: False for col in _LATENCY_PCTL_FIELDS},
    "rssmax": False,
    "rssavg": False,
    "mallocmax": False,
    "mallocavg": False,
    "minflt": False,
    "majflt": False,
    **{col: False for col in _ARENA_FIELDS},
}

# --compare columns whose delta gets colored by "heat" under --color: orange
# for an increase ("hotter"), blue for a decrease ("cooler"). Deliberately
# value-neutral -- more CPU usage is neither good nor bad per se.
_COMPARE_COLOR_HEAT = set(_CPU_FIELDS)

# ---------------------------------------------------------------------------
# jvmargs sanitization (spec §1.2.4)
# ---------------------------------------------------------------------------

from jvm_flag_filter import filter_jvm_flags


def sanitize_jvmargs(flags: list) -> str:
    """Strip harness-injected flags and join the remainder with a single space.

    Strips flags per the shared filter list (see jvm_flag_filter.py). The
    remaining flags are joined with a single space. No truncation is applied.

    Parameters
    ----------
    flags : list[str]
        Raw ``jvmargs`` list from the merged run dict.

    Returns
    -------
    str
        Space-joined sanitized flag string (may be empty).
    """
    return " ".join(filter_jvm_flags(flags))


# ---------------------------------------------------------------------------
# Cell extraction (spec §1.2.3)
# ---------------------------------------------------------------------------

def extract_cell(col: str, merged: dict) -> str:
    """Return the display string for *col* from the merged run dict.

    Returns ``"-"`` as a sentinel when a value is missing or not applicable
    for the current benchmark type.  Applies sanitization according to the
    column rules in spec §1.2.3.

    Parameters
    ----------
    col : str
        Column name (see ALL_COLUMNS).
    merged : dict
        Merged run dict produced by ``run_loader.load_run``.

    Returns
    -------
    str
        Display string for the cell (never None).
    """
    if col == "runid":
        val = merged.get("runid")
        if val is None:
            return "-"
        return str(val)

    if col == "name":
        val = merged.get("name")
        return str(val) if val is not None else "-"

    if col == "benchmark":
        val = merged.get("benchmark")
        return str(val) if val is not None else "-"

    if col == "sub":
        dacapo = merged.get("dacapo")
        if dacapo is None:
            return "-"
        val = dacapo.get("sub_benchmark")
        return str(val) if val is not None else "-"

    if col == "datasize":
        dacapo = merged.get("dacapo")
        if dacapo is None:
            return "-"
        val = dacapo.get("datasize")
        return str(val) if val is not None else "-"

    if col == "time":
        val = merged.get("time")
        if val is None:
            return "-"
        s = str(val)
        # Keep only up to second precision: first 19 chars (YYYY-MM-DDTHH:MM:SS)
        # Strip any timezone suffix (Z, +HH:MM, .fractional, etc.)
        s = s[:19]
        return s

    if col == "duration":
        val = merged.get("duration_secs")
        return str(val) if val is not None else "-"

    if col == "collint_secs":
        val = (merged.get("collectors") or {}).get("interval_secs")
        return str(val) if val is not None else "-"

    if col == "collectors":
        val = (merged.get("collectors") or {}).get("enabled")
        return ",".join(val) if val is not None else "-"

    if col == "tags":
        if "tags" not in merged:
            return "-"
        tags = merged["tags"]
        if not tags:
            return ""
        return ",".join(str(t) for t in tags)

    if col == "batchid":
        val = merged.get("batchid")
        return str(val) if val is not None else "-"

    if col == "jdkname":
        jdk = merged.get("jdk", {})
        val = jdk.get("name") if jdk else None
        return str(val) if val is not None else "-"

    if col == "jdkmajor":
        jdk = merged.get("jdk", {})
        val = jdk.get("major") if jdk else None
        return str(val) if val is not None else "-"

    if col == "jdktype":
        jdk = merged.get("jdk", {})
        val = jdk.get("type") if jdk else None
        return str(val) if val is not None else "-"

    if col == "jdkversion":
        jdk = merged.get("jdk", {})
        val = jdk.get("version") if jdk else None
        return str(val) if val is not None else "-"

    if col == "hostname":
        val = merged.get("hostname")
        return str(val) if val is not None else "-"

    if col == "arch":
        val = merged.get("arch")
        return str(val) if val is not None else "-"

    if col == "os":
        val = merged.get("os")
        return str(val) if val is not None else "-"

    if col == "result_ms":
        dr = merged.get("dacapo_result")
        if dr is None:
            return "-"
        val = dr.get("result_ms")
        return str(val) if val is not None else "-"

    if col == "stderr_ms":
        dr = merged.get("dacapo_result")
        if dr is None:
            return "-"
        val = dr.get("stderr_ms")
        return str(val) if val is not None else "-"

    if col == "stderr_pct":
        dr = merged.get("dacapo_result")
        if dr is None:
            return "-"
        val = dr.get("stderr_pct")
        return str(val) if val is not None else "-"

    if col in _LATENCY_FIELDS:
        dr = merged.get("dacapo_result")
        if dr is None:
            return "-"
        val = dr.get(col)
        return str(val) if val is not None else "-"

    if col == "startup_ms":
        dr = merged.get("dacapo_result")
        if dr is None:
            return "-"
        val = dr.get("startup_ms")
        return str(val) if val is not None else "-"

    if col == "maxjops":
        sr = merged.get("specjbb_result")
        if sr is None:
            return "-"
        val = sr.get("maxjops")
        return str(val) if val is not None else "-"

    if col == "critjops":
        sr = merged.get("specjbb_result")
        if sr is None:
            return "-"
        val = sr.get("critjops")
        return str(val) if val is not None else "-"

    if col == "hbirmax":
        sr = merged.get("specjbb_result")
        if sr is None:
            return "-"
        val = sr.get("hbirmax")
        return str(val) if val is not None else "-"

    if col == "hbirset":
        sr = merged.get("specjbb_result")
        if sr is None:
            return "-"
        val = sr.get("hbirset")
        return str(val) if val is not None else "-"

    if col == "heapsz":
        val = merged.get("heap", {}).get("heapsize")
        return str(val) if val is not None else "-"

    if col == "gc":
        val = merged.get("heap", {}).get("gc")
        return str(val) if val is not None else "-"

    if col == "repeat_no":
        val = merged.get("repeat_no")
        return str(val) if val is not None else "-"

    if col == "heapszf":
        val = merged.get("heapsize_factor")
        return str(val) if val is not None else "-"

    if col == "jvmargs":
        if "jvmargs" not in merged:
            return "-"
        flags = merged["jvmargs"]
        return sanitize_jvmargs(flags)

    if col == "rssmax":
        mem = merged.get("memory")
        if mem is None:
            return "-"
        val = mem.get("rssmax")
        return str(val) if val is not None else "-"

    if col == "rssavg":
        mem = merged.get("memory")
        if mem is None:
            return "-"
        val = mem.get("rssavg")
        return str(val) if val is not None else "-"

    if col == "mallocmax":
        malloc = merged.get("malloc")
        if malloc is None:
            return "-"
        val = malloc.get("mallocmax")
        return str(val) if val is not None else "-"

    if col == "mallocavg":
        malloc = merged.get("malloc")
        if malloc is None:
            return "-"
        val = malloc.get("mallocavg")
        return str(val) if val is not None else "-"

    if col in ("minflt", "majflt"):
        val = (merged.get("page_faults") or {}).get(col)
        return str(val) if val is not None else "-"

    if col in _ARENA_FIELDS:
        val = (merged.get("arena") or {}).get(_ARENA_FIELDS[col])
        return str(val) if val is not None else "-"

    if col == "osthreads_max":
        threads = merged.get("threads")
        if threads is None:
            return "-"
        val = threads.get("osthreads_max")
        return str(val) if val is not None else "-"

    if col == "osthreads_avg":
        threads = merged.get("threads")
        if threads is None:
            return "-"
        val = threads.get("osthreads_avg")
        return str(val) if val is not None else "-"

    if col in _THREAD_CATEGORY_FIELDS:
        category, key = _THREAD_CATEGORY_FIELDS[col]
        val = ((merged.get("threads") or {}).get(category) or {}).get(key)
        return str(val) if val is not None else "-"

    if col == "tempmax":
        temps = merged.get("temperatures")
        if temps is None:
            return "-"
        val = temps.get("tempmax")
        return str(val) if val is not None else "-"

    if col == "tempin":
        temps = merged.get("temperatures")
        if temps is None:
            return "-"
        val = temps.get("tempin")
        return str(val) if val is not None else "-"

    if col == "tempout":
        temps = merged.get("temperatures")
        if temps is None:
            return "-"
        val = temps.get("tempout")
        return str(val) if val is not None else "-"

    if col in _CPU_FIELDS:
        cpu = merged.get("cpu")
        if cpu is None:
            return "-"
        val = cpu.get(_CPU_FIELDS[col])
        return str(val) if val is not None else "-"

    # Unknown column — return sentinel.
    return "-"


# ---------------------------------------------------------------------------
# Column resolution (spec §1.2.2)
# ---------------------------------------------------------------------------

def _expand_cols(token_str: str) -> list:
    """Expand a comma-separated token string into a flat column list.

    Each token is either a group name (uppercase key in GROUPS) or a bare
    column name.  Group names expand inline to their member columns.
    """
    cols = []
    for token in token_str.split(","):
        token = token.strip()
        if not token:
            continue
        if token in GROUPS:
            cols.extend(GROUPS[token])
        else:
            cols.append(token)
    return cols


def resolve_columns(o_arg, O_arg) -> list:
    """Return the ordered, deduplicated list of columns to display.

    Implements the ``-o`` / ``-O`` logic from spec §1.2.2:

    - No ``-o`` and no ``-O`` → DEFAULT group only.
    - ``-o <cols>`` → DEFAULT + expand(cols), deduplicated.
    - ``-O <cols>`` → expand(cols) only, no DEFAULT prepended.

    Except with ``-O``, TRAILING_COLS are moved to the end, keeping the
    relative order of all other columns.

    Parameters
    ----------
    o_arg : str or None
        Value of ``-o`` flag (comma-separated column/group tokens).
    O_arg : str or None
        Value of ``-O`` flag (comma-separated column/group tokens).

    Returns
    -------
    list[str]
        Ordered column list, no duplicates.
    """
    if O_arg is not None:
        raw = _expand_cols(O_arg)
    elif o_arg is not None:
        raw = GROUPS["DEFAULT"] + _expand_cols(o_arg)
    else:
        raw = list(GROUPS["DEFAULT"])

    # Deduplicate, preserving order of first appearance.
    seen = set()
    result = []
    for col in raw:
        if col not in seen:
            result.append(col)
            seen.add(col)
    if O_arg is None:
        result = ([c for c in result if c not in TRAILING_COLS]
                  + [c for c in result if c in TRAILING_COLS])
    return result


# ---------------------------------------------------------------------------
# Sort key helper (spec §1.2.7)
# ---------------------------------------------------------------------------

def _sort_key(col: str):
    """Return a key function for sorting rows by *col*."""
    def key(merged):
        cell = extract_cell(col, merged)
        if col in _NUMERIC_SORT_COLS:
            if cell == "-":
                # Treat missing as smallest value for numeric sort.
                return (0, float("-inf"))
            try:
                return (1, float(cell))
            except (ValueError, TypeError):
                pass
        # Fallback: lexicographic
        return (1, cell)
    return key


def _parse_sort_cols(sort_col_arg) -> list:
    """Split a ``-s`` argument into an ordered list of column names.

    Accepts either a comma-separated string or an already-split
    list/tuple. Blank tokens are dropped; an empty result falls back to
    ``["time"]``.
    """
    if isinstance(sort_col_arg, (list, tuple)):
        cols = [c.strip() for c in sort_col_arg if c.strip()]
    else:
        cols = [c.strip() for c in sort_col_arg.split(",") if c.strip()]
    return cols or ["time"]


def resolve_sort_cols(sort_col_arg) -> list:
    """Parse and validate a ``-s`` argument into an ordered column list.

    Parameters
    ----------
    sort_col_arg : str or list[str]
        Comma-separated column names (or an already-split list), as
        given to ``-s``.

    Returns
    -------
    list[str]
        Ordered, non-empty list of column names to sort by.

    Raises
    ------
    ValueError
        If any column name is not a recognized column (see
        ``ALL_COLUMNS``).
    """
    cols = _parse_sort_cols(sort_col_arg)
    for col in cols:
        if col not in ALL_COLUMNS:
            raise ValueError(f"unknown sort column: {col!r}")
    return cols


def resolve_group_cols(group_arg, col_list) -> list:
    """Parse and validate a ``-g``/``--group`` argument.

    Parameters
    ----------
    group_arg : str or None
        Comma-separated column names, as given to ``-g``/``--group``.
        ``None`` (flag not given) returns ``None``.
    col_list : list[str]
        The resolved, displayed column list (see ``resolve_columns``).
        Each group column must be a member of this list, since the
        group banner is derived from displayed values.

    Returns
    -------
    list[str] or None
        Ordered, non-empty list of column names to group by, or
        ``None`` if *group_arg* is ``None`` (or empty).

    Raises
    ------
    ValueError
        If any column name is not a recognized column, or is not part
        of the displayed columns.
    """
    if group_arg is None:
        return None
    cols = [c.strip() for c in group_arg.split(",") if c.strip()]
    if not cols:
        return None
    for col in cols:
        if col not in ALL_COLUMNS:
            raise ValueError(f"unknown group column: {col!r}")
        if col not in col_list:
            raise ValueError(
                f"group column {col!r} is not among the displayed columns "
                f"(add it via -o/-O)"
            )
    return cols


def resolve_compare_cols(compare_arg, col_list) -> dict:
    """Parse and validate a ``--compare`` argument.

    Parameters
    ----------
    compare_arg : str or None
        Comma-separated ``<col>`` or ``<col>=<n>`` items, as given to
        ``--compare``. ``<n>`` is a percent threshold (a number > 0):
        that column's delta is only shown when the percent delta is at
        least +-n (see ``format_run_table``'s ``compare_thresholds``).
        ``None`` (flag not given) returns ``None``.
    col_list : list[str]
        The resolved, displayed column list (see ``resolve_columns``).
        Each compare column must be a member of this list.

    Returns
    -------
    dict[str, float or None] or None
        Ordered, non-empty mapping of column names to compare against the
        baseline to their threshold (``None`` when no ``=<n>`` given), or
        ``None`` if *compare_arg* is ``None`` (or empty).

    Raises
    ------
    ValueError
        If any column name is not a recognized column, is not numeric,
        or is not part of the displayed columns; if a threshold is not a
        number > 0; or if a column is given twice with different
        thresholds (a mention without ``=<n>`` never conflicts).
    """
    if compare_arg is None:
        return None
    items = [c.strip() for c in compare_arg.split(",") if c.strip()]
    if not items:
        return None
    cols = {}
    for item in items:
        col, sep, n_text = item.partition("=")
        col = col.strip()
        threshold = None
        if sep:
            try:
                threshold = float(n_text)
            except ValueError:
                threshold = None
            if threshold is None or not (threshold > 0 and math.isfinite(threshold)):
                raise ValueError(
                    f"compare threshold for {col!r} must be a number > 0: "
                    f"{n_text.strip()!r}"
                )
        if col in cols:
            if threshold is None:
                continue
            if cols[col] is not None and cols[col] != threshold:
                raise ValueError(
                    f"compare column {col!r} given with different thresholds"
                )
        cols[col] = threshold
    for col in cols:
        if col not in ALL_COLUMNS:
            raise ValueError(f"unknown compare column: {col!r}")
        if col not in _NUMERIC_SORT_COLS:
            raise ValueError(f"compare column {col!r} is not numeric")
        if col not in col_list:
            raise ValueError(
                f"compare column {col!r} is not among the displayed columns "
                f"(add it via -o/-O)"
            )
    return cols


def _multi_sort_key(cols: list):
    """Return a key function sorting rows by *cols*, in order.

    Later columns break ties among rows that compare equal on the
    earlier ones. A single global sort direction (``sort_asc`` /
    ``reverse=``) applies to all columns uniformly.
    """
    key_fns = [_sort_key(col) for col in cols]

    def key(merged):
        return tuple(fn(merged) for fn in key_fns)

    return key


# ---------------------------------------------------------------------------
# Table formatter (spec §1.2, §1.2.10)
# ---------------------------------------------------------------------------

def _md_escape(text: str) -> str:
    """Escape literal pipe characters so cell text can't break table syntax."""
    return text.replace("|", "\\|")


def _format_markdown_table(cell_matrix, header_names, group_col_indices, group_banner_at) -> str:
    """Render the resolved cell matrix as a single markdown table.

    When ``group_col_indices`` is non-empty, each group starts with a banner
    row that holds the group's values, in bold, in the group columns' own
    positions and leaves every other cell empty, followed (except for the
    first group, which sits right under the table's own header) by a
    repeated header row with the column names in bold. This keeps all
    groups in one table, so the rendered columns line up across groups.
    """
    header_row = "| " + " | ".join(_md_escape(h) for h in header_names) + " |"
    separator_row = "| " + " | ".join("---" for _ in header_names) + " |"

    def data_row(row_cells):
        return "| " + " | ".join(_md_escape(c) for c in row_cells) + " |"

    def banner_row(row_cells):
        cells = [
            f"**{_md_escape(c)}**" if ci in group_col_indices else ""
            for ci, c in enumerate(row_cells)
        ]
        return "| " + " | ".join(cells) + " |"

    repeated_header_row = "| " + " | ".join(f"**{_md_escape(h)}**" for h in header_names) + " |"

    lines = [header_row, separator_row]
    for ri, row_cells in enumerate(cell_matrix):
        if group_col_indices and ri in group_banner_at:
            lines.append(banner_row(row_cells))
            if ri != 0:
                lines.append(repeated_header_row)
        lines.append(data_row(row_cells))

    return "\n".join(lines)


_HTML_STYLE = """\
table { border-collapse: collapse; margin: 0 0 1.5em 0; font-family: monospace; font-size: 13px; }
th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; white-space: nowrap; }
th { background: #f0f0f0; }
tr:nth-child(even) td { background: #fafafa; }
tr.group th { font-family: sans-serif; background: #dde3ea; border-top: 3px solid #999; padding-top: 8px; }
.delta-good { color: #2e7d32; font-weight: bold; }
.delta-bad { color: #c62828; font-weight: bold; }
.delta-hot { color: #e65100; font-weight: bold; }
.delta-cold { color: #1565c0; font-weight: bold; }
"""


def _file_uri(path: str) -> str:
    """Return a percent-encoded ``file://`` URI for *path*.

    Falls back to plain ``file://`` concatenation if *path* isn't absolute
    (``Path.as_uri`` requires an absolute path).
    """
    try:
        return Path(path).as_uri()
    except ValueError:
        return "file://" + path


def _html_href(path: str, link_base: str) -> str:
    """Return the href for *path* in HTML output.

    When *link_base* is given, a percent-encoded path relative to it (the
    directory holding the generated ``index.html``); otherwise an absolute
    ``file://`` URI (see ``_file_uri``).
    """
    if link_base is not None:
        return url_quote(os.path.relpath(path, link_base))
    return _file_uri(path)


_RSS_COLUMNS = {"rssmax", "rssavg"}
_MALLOC_COLUMNS = {"mallocmax", "mallocavg"}
# Only the total %CPU columns (not the usr/sys/guest/wait breakdown) link to
# the per-run and comparison CPU plots.
_CPU_TOTAL_COLUMNS = {"cpu_max", "cpu_avg"}
_PAGE_FAULT_COLUMNS = {"minflt", "majflt"}

# Per-run plot files a displayed RSS/malloc/CPU/page fault cell links to in HTML output,
# in order of preference: the first one present in the run's directory is
# used (system_map.svg falls back to pidstat-rss.svg when System.map
# tracking wasn't enabled for the run).
_PER_RUN_SVGS = {
    **{col: ("system_map.svg", "pidstat-rss.svg") for col in _RSS_COLUMNS},
    **{col: ("nmt-malloc.svg",) for col in _MALLOC_COLUMNS},
    **{col: ("pidstat-cpu.svg",) for col in _CPU_TOTAL_COLUMNS},
    **{col: ("pagefaults.svg",) for col in _PAGE_FAULT_COLUMNS},
    "ardavg": (os.path.join("arena-report", "combined-all.svg"),
               os.path.join("arena-report", "combined-bytes.svg"),
               os.path.join("arena-report", "combined.svg")),
}

# Bar chart emoji prepended (as a link to that run's per-run plot, see
# _PER_RUN_SVGS) to every displayed RSS/malloc/CPU value in HTML output, when
# that run has one.
_BAR_CHART_EMOJI = "\U0001F4CA"


def _format_html_table(cell_matrix, header_names, group_col_indices, group_banner_at, color_spans,
                        results_dir, runid_idx, plot_svgs, compare_svg_hrefs,
                        link_base=None) -> str:
    """Render the resolved cell matrix as a standalone HTML document.

    All rows go into a single ``<table>``, so columns line up across
    groups. When ``group_col_indices`` is non-empty, there is no
    ``<thead>``; instead each group gets its own ``<tbody>``, starting with
    a ``<tr class="group">`` banner row whose single cell spans all
    columns, followed by a header row with the column names. ``color_spans`` maps
    ``(row_index, col_index) -> (delta_str, tone)`` for cells whose
    ``--compare`` delta substring should be wrapped in a ``delta-<tone>``
    span, tone being ``good``/``bad``/``hot``/``cold`` (empty when
    ``--color`` wasn't given).
    When ``results_dir`` is not ``None`` and ``runid_idx`` names the ``runid``
    column's position in each row, that column's cells (other than ``"-"``)
    are wrapped in an ``<a href="file://...">`` link to the run's result
    directory. When, additionally, ``plot_svgs`` maps the position of a
    displayed RSS/malloc/CPU column to its candidate per-run plot file names
    (see ``_PER_RUN_SVGS``) and that row's run directory has one of them,
    a bar-chart emoji linking to the first one present is prepended before
    the cell's value. ``compare_svg_hrefs`` maps
    ``(row_index, col_index) -> (delta_str, href)`` for compared RSS/malloc/CPU cells
    that got a comparison plot (see ``format_run_table``'s ``report_dir``);
    a second bar-chart emoji linking to it is inserted right after the
    opening ``(`` of that cell's ``(<delta>)`` text, before the delta value
    (inside any ``--color`` span, which wraps the same substring).
    When ``link_base`` is given, the run directory and per-run SVG links
    are relative to it instead of absolute ``file://`` URIs (links in
    ``compare_svg_hrefs`` are used as given).
    """
    def cell_html(ri, ci, text):
        escaped = html_escape(text)
        span = color_spans.get((ri, ci))
        if span is not None:
            delta_str, tone = span
            cls = f"delta-{tone}"
            escaped_delta = html_escape(delta_str)
            escaped = escaped.replace(
                escaped_delta, f'<span class="{cls}">{escaped_delta}</span>', 1
            )
        svg_span = compare_svg_hrefs.get((ri, ci))
        if svg_span is not None:
            delta_str, href = svg_span
            escaped_delta = html_escape(delta_str)
            icon_link = f'<a href="{html_escape(href)}">{_BAR_CHART_EMOJI}</a>'
            escaped = escaped.replace(
                escaped_delta, escaped_delta.replace("(", f"({icon_link}", 1), 1
            )
        if results_dir is not None and ci == runid_idx and text != "-":
            href = html_escape(_html_href(os.path.join(results_dir, text), link_base))
            escaped = f'<a href="{href}">{escaped}</a>'
        if results_dir is not None and runid_idx is not None and ci in plot_svgs and text != "-":
            run_id = cell_matrix[ri][runid_idx]
            if run_id != "-":
                for svg_name in plot_svgs[ci]:
                    svg_path = os.path.join(results_dir, run_id, svg_name)
                    if os.path.isfile(svg_path):
                        href = html_escape(_html_href(svg_path, link_base))
                        escaped = f'<a href="{href}">{_BAR_CHART_EMOJI}</a> {escaped}'
                        break
        return f"<td>{escaped}</td>"

    def row_html(ri, row_cells):
        return "<tr>" + "".join(cell_html(ri, ci, c) for ci, c in enumerate(row_cells)) + "</tr>"

    header_row = "<tr>" + "".join(f"<th>{html_escape(h)}</th>" for h in header_names) + "</tr>"
    if group_col_indices:
        body = ["<table>"]
        tbody_open = False
        for ri, row_cells in enumerate(cell_matrix):
            if ri in group_banner_at:
                if tbody_open:
                    body.append("</tbody>")
                body.append("<tbody>")
                body.append(
                    f'<tr class="group"><th colspan="{len(header_names)}">'
                    f"{html_escape(group_banner_at[ri])}</th></tr>"
                )
                body.append(header_row)
                tbody_open = True
            body.append(row_html(ri, row_cells))
        if tbody_open:
            body.append("</tbody>")
    else:
        body = [f"<table>\n<thead>\n{header_row}\n</thead>", "<tbody>"]
        for ri, row_cells in enumerate(cell_matrix):
            body.append(row_html(ri, row_cells))
        body.append("</tbody>")
    body.append("</table>")

    return "\n".join([
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        "<title>jbr-ls</title>",
        f"<style>\n{_HTML_STYLE}</style>",
        "</head>",
        "<body>",
        *body,
        "</body>",
        "</html>",
    ])


def format_run_table(
    rows: list,
    col_list: list,
    sort_col: str = "time",
    sort_asc: bool = True,
    no_autoomit: bool = False,
    results_dir: str = None,
    report_dir: str = None,
    group_cols: list = None,
    baseline_runid: str = None,
    compare_cols: list = None,
    markdown: bool = False,
    html: bool = False,
    color: bool = False,
    relative_links: bool = False,
    no_header: bool = False,
    compare_thresholds: dict = None,
) -> str:
    """Format a list of merged run dicts as an auto-sized table string.

    Parameters
    ----------
    rows : list[dict]
        Each element is a merged run dict (output of ``run_loader.load_run``).
    col_list : list[str]
        Ordered list of column names to display.
    sort_col : str
        Column(s) to sort by, comma-separated (default ``"time"``).
        Later columns break ties among rows that compare equal on the
        earlier ones.
    sort_asc : bool
        When True (default), sort ascending; otherwise descending.
        Applies uniformly to all columns in ``sort_col``.
    no_autoomit : bool
        When True, keep columns even when every value is ``"-"``.
    results_dir : str or None
        Absolute path to the results/ directory. When given and the
        ``runid`` column is present, each RUNID is wrapped as a terminal
        hyperlink to its run directory (``file://<results_dir>/<run_id>``)
        when the terminal supports it. When ``None``, RUNIDs are printed
        as plain text. In ``html`` mode, this instead makes each RUNID an
        ``<a href="file://...">`` link to its run directory (see ``html``).
    report_dir : str or None
        Absolute path to the report directory being written (``--report``'s
        ``reports/<name>``), distinct from ``results_dir`` (which may itself
        be a copy of ``results_dir`` under ``report_dir`` when
        ``--self-contained`` was given). Only used in ``html`` mode, together
        with ``compare_cols``: each compared RSS/malloc/CPU/page fault cell whose delta is
        computable gets a new comparison plot written directly into
        ``report_dir`` (never into ``results_dir``, which may be read-only
        or shared), comparing that row's run against its baseline's, and a
        bar-chart emoji linking to it (relative to ``report_dir``) is
        inserted inside the cell's
        ``(<delta>)`` text, before the delta value. Prefers a
        ``system_map_compare_<row_runid>_<baseline_runid>.svg`` (category
        breakdown) when both runs have a ``system.map.jsonl``; otherwise
        falls back to a plainer ``pidstat_compare_<row_runid>_<baseline_runid>.svg``
        (RSS line plot only) when both instead have a ``pidstat.jsonl``.
        For malloc columns, it's a ``nmt_compare_<row_runid>_<baseline_runid>.svg``
        (NMT malloc category breakdown) when both runs have an ``nmt.jsonl``.
        For the total CPU columns (``cpu_max``/``cpu_avg``), it's a ``cpu_compare_<row_runid>_<baseline_runid>.svg``
        (stacked CPU load breakdown) when both runs have a ``pidstat.jsonl``.
        For the page fault columns (``minflt``/``majflt``), it's a
        ``faults_compare_<row_runid>_<baseline_runid>.svg`` (cumulative minor
        and major faults) when both runs have a ``proclog.jsonl``.
        Built once per (row, baseline) pair and reused across that row's
        compared RSS (respectively malloc, CPU, page fault) columns. No file is written and no emoji is added for
        a cell whose run and baseline don't share a usable data source (or
        when ``report_dir`` is ``None``, or ``runid`` isn't displayed).
    group_cols : list[str] or None
        When given, a "<col>: <value>, ..." banner followed by a fresh
        header/separator is printed before the first row and before any
        row whose values in these columns differ from the previous row's
        (see ``resolve_group_cols``). Columns named here are exempt from
        auto-omit. Mutually exclusive with ``baseline_runid`` (enforced by
        the caller); freely combinable with ``compare_cols``, in which case
        each group gets its own baseline (see ``compare_cols``).
    baseline_runid : str or None
        When given, identifies the row (by ``runid``) to use as the
        baseline for ``compare_cols``. The row is left in its normal sort
        position — it is no longer pulled to the front. The caller must
        have already verified a matching row exists, and must not combine
        this with ``group_cols``. When ``None``, the baseline is instead
        the first row in the (post-sort) table — or, with ``group_cols``,
        the first row of each group.
    compare_cols : list[str] or None
        When given, each non-baseline row's cell in the named column is
        rewritten as ``"<value> (<delta>)"``, showing that row's value as a
        percent delta from its baseline row's value in the same column,
        e.g. ``"4652 (+6.9%)"``. Without ``group_cols``, there is a single
        baseline for the whole table (``baseline_runid``'s row, or row 0 if
        omitted). With ``group_cols``, each group's own first row is its
        baseline, independent of ``baseline_runid`` (which is disallowed
        together with ``group_cols``). The baseline row's cells, and any
        cell whose delta isn't computable (missing/non-numeric value, zero
        baseline, or cell is ``"-"``), are left as the plain value with no
        suffix (see ``resolve_compare_cols``).
    compare_thresholds : dict[str, float] or None
        Per compared column, a percent threshold (> 0): a cell of that
        column whose percent delta, rounded to the one decimal place it is
        displayed with, has an absolute value below the threshold is left
        as is: no delta text, no ``--color`` span, no comparison-plot
        emoji. Columns not in the dict have no threshold.
    markdown : bool
        When True, render as a single markdown table (with ``group_cols``,
        each group starts with a banner row holding the bold group values in
        the group columns, other cells empty) instead of a fixed-width plain-text table. ``runid`` is printed as
        plain text (no terminal hyperlink) in this mode, and literal ``|``
        characters in cell values are escaped. Mutually exclusive with
        ``html`` (enforced by the caller).
    html : bool
        When True, render a standalone HTML document with a single
        ``<table>`` (with ``group_cols``, one ``<tbody>`` per group, each
        starting with a full-width banner row) instead of a fixed-width plain-text table. Cell text is
        HTML-escaped. When ``results_dir`` is given and the ``runid``
        column is present, each RUNID cell (other than ``"-"``) is wrapped
        in an ``<a href="file://<results_dir>/<run_id>">`` link to its run
        directory; otherwise ``runid`` is printed as plain text. Under the
        same condition, each displayed RSS cell (``rssmax``/``rssavg``,
        other than ``"-"``) whose run directory contains a
        ``system_map.svg`` (or, lacking that, a ``pidstat-rss.svg``) gets a
        leading bar-chart emoji linking to it; likewise each malloc cell
        (``mallocmax``/``mallocavg``) for an
        ``nmt-malloc.svg``, each total CPU cell (``cpu_max``/``cpu_avg``)
        for a
        ``pidstat-cpu.svg``, and each page fault cell (``minflt``/``majflt``)
        for a ``pagefaults.svg``.
        Mutually exclusive with ``markdown`` (enforced by the caller).
    color : bool
        When True, colors the ``(<delta>)`` part of a ``compare_cols`` cell
        green when that delta is in the "good" direction for the column, or
        red when it's in the "bad" direction; for a ``_COMPARE_COLOR_HEAT``
        (CPU) column, orange for an increase and blue for a decrease
        (value-neutral "heat"). Never for a zero delta or a column outside
        both sets. In ``html`` mode this wraps the delta substring in a
        ``delta-good``/``delta-bad``/``delta-hot``/``delta-cold``
        ``<span>`` instead of an ANSI code. No effect without
        ``compare_cols``, in ``markdown`` mode, or when the output stream
        doesn't support color (see ``term_color.supports_color``).
    relative_links : bool
        Only used in ``html`` mode with ``report_dir`` given (``--report``
        with ``--self-contained``, where ``results_dir`` is ``report_dir``).
        When True, RUNID links and per-run RSS/malloc SVG links are made relative
        to ``report_dir`` (where ``index.html`` is written) instead of
        absolute ``file://`` URIs. Comparison plot links (see
        ``report_dir``) are always relative to ``report_dir``.
    no_header : bool
        When True, omit the header and separator lines, printing only the
        data rows. Plain-text mode only; mutually exclusive with
        ``group_cols``, ``markdown`` and ``html`` (enforced by the caller).
        Column widths still account for the header names.

    Returns
    -------
    str
        Formatted table string (no trailing newline).
    """
    # Sort rows (stable sort), by one or more comma-separated columns.
    key_fn = _multi_sort_key(_parse_sort_cols(sort_col))
    sorted_rows = sorted(rows, key=key_fn, reverse=not sort_asc)

    # Build cell matrix: list of lists, one inner list per row.
    cell_matrix = []
    for merged in sorted_rows:
        cell_matrix.append([extract_cell(col, merged) for col in col_list])

    group_cols = group_cols or []

    # Auto-omit: drop columns where every row's value is "-" (spec §1.2.6).
    # Group columns are exempt, since the group banner needs them to stay
    # visible.
    if not no_autoomit:
        keep_indices = []
        for ci, col in enumerate(col_list):
            if col in AUTO_OMIT_CANDIDATES and col not in group_cols:
                all_dash = all(
                    (cell_matrix[ri][ci] == "-")
                    for ri in range(len(cell_matrix))
                )
                if all_dash:
                    continue
            keep_indices.append(ci)
        col_list = [col_list[i] for i in keep_indices]
        cell_matrix = [[row[i] for i in keep_indices] for row in cell_matrix]

    if not col_list:
        return ""

    # Column header text is the column's display name (may differ from its
    # internal name).
    header_names = [col for col in col_list]

    # Compute group banners from the raw (unwrapped) cell text, before the
    # runid column is wrapped in a hyperlink below and before --compare
    # rewrites any cells — so banners never contain escape bytes or deltas,
    # and so the banner-start indices below double as each group's baseline
    # row index for --compare.
    group_col_indices = [col_list.index(c) for c in group_cols]
    group_banner_at = {}
    if group_col_indices:
        prev_key = None
        for ri, row_cells in enumerate(cell_matrix):
            key = tuple(row_cells[ci] for ci in group_col_indices)
            if ri == 0 or key != prev_key:
                group_banner_at[ri] = ", ".join(
                    f"{c}: {row_cells[ci]}"
                    for c, ci in zip(group_cols, group_col_indices)
                )
            prev_key = key

    # Compare flags: each row's value in *col* compared against its baseline
    # row's value, as a percent delta ("(+6.0%)"). Not computable for the
    # baseline row itself, or when either value is missing/non-numeric, the
    # cell is "-", or the baseline is zero.
    #
    # Without -g there is one baseline for the whole table: the row named
    # by --baseline, or row 0 (post-sort) if --baseline was omitted. With
    # -g (--baseline is disallowed there, enforced by the caller) each
    # group has its own baseline: that group's first row.
    # (ri, ci) -> (delta_str, tone) for cells --color should tint; filled
    # below, applied after column widths are computed (see there for why).
    color_spans = {}

    # (ri, ci) -> (delta_str, baseline_ri, kind) for compared RSS/malloc/CPU/page
    # fault cells with a computable delta, kind being "rss", "malloc", "cpu" or
    # "faults"; used
    # below (html + report_dir only) to build a per-(kind, row, baseline)
    # comparison plot and link a bar-chart emoji to it from inside the
    # cell's "(<delta>)" text.
    plot_compare_targets = {}

    compare_cols = compare_cols or []
    if compare_cols:
        def _delta_pct(cell, baseline_val):
            if baseline_val in (None, 0) or cell == "-":
                return None
            try:
                return (float(cell) / baseline_val * 100) - 100
            except (ValueError, TypeError):
                return None

        def _apply_compare(baseline_ri, row_indices):
            baseline_cells = cell_matrix[baseline_ri]
            for ci, col in enumerate(col_list):
                if col not in compare_cols:
                    continue
                try:
                    baseline_val = float(baseline_cells[ci])
                except (ValueError, TypeError):
                    baseline_val = None
                higher_is_better = _COMPARE_COLOR_HIGHER_IS_BETTER.get(col)
                threshold = (compare_thresholds or {}).get(col)
                for ri in row_indices:
                    if ri == baseline_ri:
                        continue
                    cell = cell_matrix[ri][ci]
                    delta = _delta_pct(cell, baseline_val)
                    if delta is None:
                        continue
                    # Compare the displayed (rounded) value, so a delta
                    # shown as "+5.0%" is never hidden by threshold 5.
                    if threshold is not None and abs(round(delta, 1)) < threshold:
                        continue
                    delta_str = f"({delta:+.1f}%)"
                    cell_matrix[ri][ci] = f"{cell} {delta_str}"
                    if col in _RSS_COLUMNS:
                        plot_compare_targets[(ri, ci)] = (delta_str, baseline_ri, "rss")
                    elif col in _MALLOC_COLUMNS:
                        plot_compare_targets[(ri, ci)] = (delta_str, baseline_ri, "malloc")
                    elif col in _CPU_TOTAL_COLUMNS:
                        plot_compare_targets[(ri, ci)] = (delta_str, baseline_ri, "cpu")
                    elif col in _PAGE_FAULT_COLUMNS:
                        plot_compare_targets[(ri, ci)] = (delta_str, baseline_ri, "faults")
                    elif col in _ARENA_FIELDS:
                        plot_compare_targets[(ri, ci)] = (delta_str, baseline_ri, "arena")
                    if color and delta != 0:
                        if higher_is_better is not None:
                            good = (delta > 0) == higher_is_better
                            color_spans[(ri, ci)] = (delta_str, "good" if good else "bad")
                        elif col in _COMPARE_COLOR_HEAT:
                            color_spans[(ri, ci)] = (delta_str, "hot" if delta > 0 else "cold")

        if group_col_indices:
            starts = sorted(group_banner_at.keys())
            for i, start in enumerate(starts):
                end = starts[i + 1] if i + 1 < len(starts) else len(cell_matrix)
                _apply_compare(start, range(start, end))
        else:
            baseline_idx = 0
            if baseline_runid is not None:
                for i, merged in enumerate(sorted_rows):
                    if extract_cell("runid", merged) == baseline_runid:
                        baseline_idx = i
                        break
            _apply_compare(baseline_idx, range(len(cell_matrix)))

    # Build one comparison plot per (kind, row, baseline) referenced by
    # plot_compare_targets, and record its link for each such cell. Only
    # possible in html mode with a report directory to write into and a
    # displayed runid column to resolve run directories from; silently
    # produces no files/links otherwise (see report_dir's docstring).
    #
    # For RSS, prefers the richer system_map-based comparison (category
    # breakdown); when either run lacks system.map.jsonl, falls back to a
    # plain RSS line-plot comparison from pidstat.jsonl instead (no
    # breakdown to offer there), named distinctly (pidstat_compare_... vs
    # system_map_compare_...) so it's clear which data source it came from.
    # Never mixes the two: a pair only gets a plot when the SAME source is
    # usable for both sides. For malloc, the only source is nmt.jsonl
    # (nmt_compare_...); for CPU, it's pidstat.jsonl (cpu_compare_...); for
    # page faults, proclog.jsonl (faults_compare_...); for the arena columns,
    # arena-report/combined.json (arena_compare_...).
    compare_builders = {
        "rss": [("system_map_compare", build_system_map_comparison_svg),
                ("pidstat_compare", build_pidstat_comparison_svg)],
        "malloc": [("nmt_compare", build_nmt_comparison_svg)],
        "cpu": [("cpu_compare", build_cpu_comparison_svg)],
        "faults": [("faults_compare", build_page_faults_comparison_svg)],
        "arena": [("arena_compare", build_arena_comparison_svg)],
    }
    compare_svg_hrefs = {}
    if html and report_dir is not None and results_dir is not None and plot_compare_targets:
        runid_idx_for_compare = col_list.index("runid") if "runid" in col_list else None
        if runid_idx_for_compare is not None:
            built = {}
            for (ri, ci), (delta_str, baseline_ri, kind) in plot_compare_targets.items():
                row_run_id = cell_matrix[ri][runid_idx_for_compare]
                baseline_run_id = cell_matrix[baseline_ri][runid_idx_for_compare]
                if row_run_id == "-" or baseline_run_id == "-":
                    continue
                pair_key = (kind, row_run_id, baseline_run_id)
                if pair_key not in built:
                    row_dir = os.path.join(results_dir, row_run_id)
                    baseline_dir = os.path.join(results_dir, baseline_run_id)
                    built[pair_key] = None
                    for prefix, build_svg in compare_builders[kind]:
                        out_svg = os.path.join(
                            report_dir, f"{prefix}_{row_run_id}_{baseline_run_id}.svg",
                        )
                        if build_svg(row_dir, baseline_dir, out_svg):
                            built[pair_key] = out_svg
                            break
                svg_path = built[pair_key]
                if svg_path is not None:
                    compare_svg_hrefs[(ri, ci)] = (delta_str, _html_href(svg_path, report_dir))

    if markdown:
        return _format_markdown_table(
            cell_matrix, header_names, group_col_indices, group_banner_at
        )

    if html:
        runid_idx = col_list.index("runid") if "runid" in col_list else None
        plot_svgs = {ci: _PER_RUN_SVGS[col] for ci, col in enumerate(col_list)
                     if col in _PER_RUN_SVGS}
        return _format_html_table(
            cell_matrix, header_names, group_col_indices, group_banner_at, color_spans,
            results_dir, runid_idx, plot_svgs, compare_svg_hrefs,
            link_base=report_dir if relative_links else None,
        )

    # Compute column widths: max(header width, max cell width).
    col_widths = [len(name) for name in header_names]
    for row_cells in cell_matrix:
        for ci, cell in enumerate(row_cells):
            col_widths[ci] = max(col_widths[ci], len(cell))

    # Color the --compare delta (not the whole cell) for the cells recorded
    # in color_spans. Pad to column width *before* injecting the ANSI
    # escape codes, so they never affect visual alignment (same trick as
    # the runid link below).
    for (ri, ci), (delta_str, tone) in color_spans.items():
        padded = cell_matrix[ri][ci].ljust(col_widths[ci])
        cell_matrix[ri][ci] = padded.replace(delta_str, colorize(delta_str, tone), 1)

    # Wrap the runid column in a terminal hyperlink to its result directory,
    # if requested. Pad to column width *before* wrapping, so the invisible
    # OSC 8 escape bytes never affect visual alignment.
    if results_dir is not None and "runid" in col_list:
        runid_idx = col_list.index("runid")
        width = col_widths[runid_idx]
        for row_cells in cell_matrix:
            text = row_cells[runid_idx]
            if text != "-":
                target = os.path.join(results_dir, text)
                row_cells[runid_idx] = terminal_link(target, text.ljust(width))

    fmt = "  ".join("{{:<{}}}".format(w) for w in col_widths)
    header_line = fmt.format(*header_names).rstrip()
    separator_line = "  ".join("-" * w for w in col_widths)

    lines = []
    if group_col_indices:
        for ri, row_cells in enumerate(cell_matrix):
            if ri in group_banner_at:
                lines.append(group_banner_at[ri])
                lines.append(header_line)
                lines.append(separator_line)
            lines.append(fmt.format(*row_cells).rstrip())
    else:
        if not no_header:
            lines.append(header_line)
            lines.append(separator_line)
        for row_cells in cell_matrix:
            lines.append(fmt.format(*row_cells).rstrip())

    return "\n".join(lines)
