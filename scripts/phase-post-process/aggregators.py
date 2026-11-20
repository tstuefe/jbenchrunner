#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Metric aggregators for post-processing benchmark run artefacts.

Functions:
    parse_temperatures(log_path)     -> dict | None
    parse_memory(log_path)           -> dict | None
    parse_malloc(log_path)           -> dict | None
    parse_cpu(log_path)              -> dict | None
    parse_threads(log_path)          -> dict | None
    parse_page_faults(log_path)      -> dict | None
    parse_thread_categories(log_path) -> dict | None
    time_weighted_mean(samples)      -> float | None
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common import collector_timestamp  # noqa: E402


def _parse_timestamp_seconds(raw) -> float | None:
    """Parse a collector timestamp (e.g. "2026-09-13T11:32:30.123+02:00") into epoch
    seconds, or None if invalid."""
    try:
        return collector_timestamp.parse(raw).timestamp()
    except ValueError:
        return None


def time_weighted_mean(samples: list[tuple[float, float]]) -> float | None:
    """
    Compute the time-weighted mean of a (timestamp, value) time series.

    Integrates the series with the trapezoidal rule and normalizes by the
    total elapsed time (last timestamp minus first), so the result is in
    the same unit as the input values and comparable across runs of
    different lengths.

    samples: list of (timestamp_seconds, value) pairs; need not be sorted.

    Returns None if samples is empty. A single sample, or samples that all
    share the same timestamp, return the plain mean of the values (there is
    no elapsed time to weight by).
    """
    if not samples:
        return None

    ordered = sorted(samples, key=lambda s: s[0])
    total_duration = ordered[-1][0] - ordered[0][0]
    if total_duration <= 0:
        return sum(v for _, v in ordered) / len(ordered)

    area = 0.0
    for (t0, v0), (t1, v1) in zip(ordered, ordered[1:]):
        area += (t1 - t0) * (v0 + v1) / 2.0

    return area / total_duration


def parse_temperatures(log_path: str) -> dict | None:
    """
    Parse temps.jsonl and return temperature statistics.

    Each line must be a JSON object containing a "temperature" key whose
    value is an integer or a string representing an integer (milligrade).

    Returns {"tempin": int, "tempout": int, "tempmax": int}, or None on any
    error (missing file, empty file, no valid samples, parse failure).
    """
    if not os.path.exists(log_path):
        sys.stderr.write(f"Warning: temperature log not found: {log_path}\n")
        return None

    samples = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    samples.append(int(obj["temperature"]))
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    sys.stderr.write(
                        f"Warning: skipping malformed line {lineno} in {log_path}\n"
                    )
    except OSError as e:
        sys.stderr.write(f"Warning: cannot read temperature log {log_path}: {e}\n")
        return None

    if not samples:
        sys.stderr.write(
            f"Warning: no valid temperature samples in {log_path}\n"
        )
        return None

    return {
        "tempin": samples[0],
        "tempout": samples[-1],
        "tempmax": max(samples),
    }


def parse_memory(log_path: str) -> dict | None:
    """
    Parse proclog.jsonl and return RSS memory statistics.

    Each line must be a JSON object containing a "status" dict with key
    "vm_rss" whose value is a string like "34940 kB" or an integer (kB).
    Lines that also carry a valid top-level "timestamp" (collector-log
    format, e.g. "2026-09-13T11:32:30.123+02:00") additionally contribute to
    rssavg, the time-weighted mean RSS (see time_weighted_mean()).

    Returns {"rssmax": int, "rssavg": int} in MB (rounded); rssavg is
    omitted if no line has a valid timestamp.
    Returns None on any error (missing file, empty file, no valid samples,
    parse failure).
    """
    if not os.path.exists(log_path):
        sys.stderr.write(f"Warning: memory log not found: {log_path}\n")
        return None

    rss_kb_samples = []
    timed_rss_kb_samples = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    raw = obj["status"]["vm_rss"]
                    # Accept "34940 kB", "34940kB", or bare integer/string integer
                    rss_kb = int(str(raw).split()[0])
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    sys.stderr.write(
                        f"Warning: skipping malformed line {lineno} in {log_path}\n"
                    )
                    continue

                rss_kb_samples.append(rss_kb)

                ts_seconds = _parse_timestamp_seconds(obj.get("timestamp"))
                if ts_seconds is not None:
                    timed_rss_kb_samples.append((ts_seconds, rss_kb))
    except OSError as e:
        sys.stderr.write(f"Warning: cannot read memory log {log_path}: {e}\n")
        return None

    if not rss_kb_samples:
        sys.stderr.write(
            f"Warning: no valid memory samples in {log_path}\n"
        )
        return None

    result = {
        "rssmax": round(max(rss_kb_samples) / 1024),
    }

    avg_kb = time_weighted_mean(timed_rss_kb_samples)
    if avg_kb is not None:
        result["rssavg"] = round(avg_kb / 1024)

    return result


def parse_malloc(log_path: str) -> dict | None:
    """
    Parse nmt.jsonl and return NMT total-malloc statistics.

    Each line must be a JSON object containing a "total_malloc" key (MB, the
    sum of all NMT categories' malloc'd memory, as written by
    post-process-nmt-log.py). Lines that also carry a valid top-level
    "timestamp" (collector-log format, e.g. "2026-09-13T11:32:30.123+02:00")
    additionally contribute to mallocavg, the time-weighted mean (see
    time_weighted_mean()).

    Returns {"mallocmax": float, "mallocavg": float} in MB (rounded to 2
    decimal places); mallocavg is omitted if no line has a valid timestamp.
    Returns None on any error (missing file, empty file, no valid samples,
    parse failure).
    """
    if not os.path.exists(log_path):
        sys.stderr.write(f"Warning: nmt log not found: {log_path}\n")
        return None

    malloc_samples = []
    timed_malloc_samples = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    malloc_mb = float(obj["total_malloc"])
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    sys.stderr.write(
                        f"Warning: skipping malformed line {lineno} in {log_path}\n"
                    )
                    continue

                malloc_samples.append(malloc_mb)

                ts_seconds = _parse_timestamp_seconds(obj.get("timestamp"))
                if ts_seconds is not None:
                    timed_malloc_samples.append((ts_seconds, malloc_mb))
    except OSError as e:
        sys.stderr.write(f"Warning: cannot read nmt log {log_path}: {e}\n")
        return None

    if not malloc_samples:
        sys.stderr.write(
            f"Warning: no valid malloc samples in {log_path}\n"
        )
        return None

    result = {
        "mallocmax": round(max(malloc_samples), 2),
    }

    avg = time_weighted_mean(timed_malloc_samples)
    if avg is not None:
        result["mallocavg"] = round(avg, 2)

    return result


# arena-summary.json combined key -> result.json "arena" key (bytes -> MB).
_ARENA_SUMMARY_FIELDS = {
    "in_use_peak_bytes_max": "used_mb_max",
    "in_use_peak_bytes_avg": "used_mb_avg",
    "dirty_bytes_max": "dirty_mb_max",
    "dirty_bytes_avg": "dirty_mb_avg",
    "cached_bytes_max": "cached_mb_max",
    "cached_bytes_avg": "cached_mb_avg",
}


def parse_arena_summary(summary_path: str) -> dict | None:
    """
    Read arena-report/arena-summary.json (bin/tools/arena-log-parser.py) and
    return its combined in-use peak, dirty and cached maxima and averages as
    {"used_mb_max", "used_mb_avg", "dirty_mb_max", "dirty_mb_avg",
    "cached_mb_max", "cached_mb_avg"} in MB (rounded to 2 decimal places).
    A value missing from the summary (e.g. no in-use peak in the log) is
    omitted. Returns None if the file is missing, unreadable or has none of
    these values.
    """
    if not os.path.exists(summary_path):
        return None
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            combined = json.load(f)["combined"]
        out = {dst: round(combined[src] / (1024 * 1024), 2)
               for src, dst in _ARENA_SUMMARY_FIELDS.items() if src in combined}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        sys.stderr.write(f"Warning: cannot read arena summary {summary_path}: {e}\n")
        return None
    return out or None


def parse_threads(log_path: str) -> dict | None:
    """
    Parse proclog.jsonl and return OS thread count statistics.

    Each line must be a JSON object containing a "status" dict with key
    "threads" whose value is an integer or a string representing an
    integer (thread count, from /proc/pid/status "Threads").

    Returns {"osthreads_max": int, "osthreads_avg": int}, or None on any
    error (missing file, empty file, no valid samples, parse failure).
    """
    if not os.path.exists(log_path):
        sys.stderr.write(f"Warning: proclog not found: {log_path}\n")
        return None

    thread_samples = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    thread_samples.append(int(obj["status"]["threads"]))
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    sys.stderr.write(
                        f"Warning: skipping malformed line {lineno} in {log_path}\n"
                    )
    except OSError as e:
        sys.stderr.write(f"Warning: cannot read proclog {log_path}: {e}\n")
        return None

    if not thread_samples:
        sys.stderr.write(
            f"Warning: no valid thread-count samples in {log_path}\n"
        )
        return None

    return {
        "osthreads_max": max(thread_samples),
        "osthreads_avg": round(sum(thread_samples) / len(thread_samples)),
    }


def parse_page_faults(log_path: str) -> dict | None:
    """
    Parse proclog.jsonl and return the process's page fault totals.

    Each line must be a JSON object containing a "proc_pid_stat" dict with
    keys "minflt" and "majflt" (integers or strings representing integers,
    from /proc/pid/stat). These are cumulative since process start, so the
    totals are the values of the last valid sample.

    Returns {"minflt": int, "majflt": int}, or None on any error (missing
    file, empty file, no valid samples, parse failure).
    """
    if not os.path.exists(log_path):
        sys.stderr.write(f"Warning: proclog not found: {log_path}\n")
        return None

    last = None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    stat = json.loads(line)["proc_pid_stat"]
                    last = {"minflt": int(stat["minflt"]), "majflt": int(stat["majflt"])}
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    sys.stderr.write(
                        f"Warning: skipping malformed line {lineno} in {log_path}\n"
                    )
    except OSError as e:
        sys.stderr.write(f"Warning: cannot read proclog {log_path}: {e}\n")
        return None

    if last is None:
        sys.stderr.write(
            f"Warning: no valid page fault samples in {log_path}\n"
        )
        return None

    return last


# thread-print.jsonl thread categories (see post-process-thread-print-log.py); each is an
# object {"num_threads": N, "cpu_pct": ...} in a sample, and in result.json's "threads".
_THREAD_CATEGORIES = ("c2", "c1", "gc", "mutator", "other")
# CPU time no dumped thread accounts for; {"cpu_pct": ...} only, and only with proclog.
_UNACCOUNTED = "unaccounted"


def _cpu_stats(pairs):
    """{"cpu_pct_avg", "cpu_pct_max"} from (interval_secs, cpu_pct) *pairs*: the mean
    weighted by interval and the max, 1 decimal place each."""
    total_secs = sum(interval for interval, _pct in pairs)
    return {
        "cpu_pct_avg": round(sum(interval * pct for interval, pct in pairs) / total_secs, 1),
        "cpu_pct_max": round(max(pct for _interval, pct in pairs), 1),
    }


def _cpu_pair(category, interval):
    """(interval, cpu_pct) if the sample's *category* object has a usable cpu_pct, else None."""
    pct = category.get("cpu_pct") if isinstance(category, dict) else None
    if isinstance(pct, (int, float)) and isinstance(interval, (int, float)) and interval > 0:
        return interval, pct
    return None


def parse_thread_categories(log_path: str) -> dict | None:
    """
    Parse thread-print.jsonl and return per-category thread count and CPU statistics.

    For each category of _THREAD_CATEGORIES, returns an object with:
      num_threads_max  maximum thread count (integer)
      num_threads_avg  time-weighted mean thread count (see time_weighted_mean()),
                       1 decimal place; omitted if no sample has a valid "timestamp"
      cpu_pct_avg      mean CPU % over the span of the samples with CPU values: the
                       per-sample "cpu_pct" weighted by the sample's "interval_secs",
                       1 decimal place
      cpu_pct_max      maximum per-sample "cpu_pct", 1 decimal place
    The cpu keys are omitted if no sample carries CPU values (e.g. a single sample).
    Additionally "unaccounted": {"cpu_pct_avg", "cpu_pct_max"} (same computation) if the
    samples carry unaccounted CPU values.

    Returns e.g. {"gc": {"num_threads_max": 11, ...}, ...}, or None on any error
    (missing file, empty file, no valid samples, parse failure).
    """
    if not os.path.exists(log_path):
        sys.stderr.write(f"Warning: thread-print log not found: {log_path}\n")
        return None

    counts = {c: [] for c in _THREAD_CATEGORIES}
    timed_counts = {c: [] for c in _THREAD_CATEGORIES}
    cpu = {c: [] for c in (*_THREAD_CATEGORIES, _UNACCOUNTED)}  # (interval_secs, cpu_pct)

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    sys.stderr.write(
                        f"Warning: skipping malformed line {lineno} in {log_path}\n"
                    )
                    continue

                ts_seconds = _parse_timestamp_seconds(obj.get("timestamp"))
                interval = obj.get("interval_secs")

                found_any = False
                for c in _THREAD_CATEGORIES:
                    category = obj.get(c)
                    if not isinstance(category, dict):
                        continue
                    try:
                        value = int(category["num_threads"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    found_any = True
                    counts[c].append(value)
                    if ts_seconds is not None:
                        timed_counts[c].append((ts_seconds, value))
                    pair = _cpu_pair(category, interval)
                    if pair is not None:
                        cpu[c].append(pair)
                pair = _cpu_pair(obj.get(_UNACCOUNTED), interval)
                if pair is not None:
                    cpu[_UNACCOUNTED].append(pair)

                if not found_any:
                    sys.stderr.write(
                        f"Warning: skipping line {lineno} in {log_path}: "
                        f"no thread counts found\n"
                    )
    except OSError as e:
        sys.stderr.write(f"Warning: cannot read thread-print log {log_path}: {e}\n")
        return None

    if not any(counts[c] for c in _THREAD_CATEGORIES):
        sys.stderr.write(f"Warning: no valid thread-count samples in {log_path}\n")
        return None

    result = {}
    for c in _THREAD_CATEGORIES:
        if not counts[c]:
            continue
        stats = {"num_threads_max": max(counts[c])}
        avg = time_weighted_mean(timed_counts[c])
        if avg is not None:
            stats["num_threads_avg"] = round(avg, 1)
        if cpu[c]:
            stats.update(_cpu_stats(cpu[c]))
        result[c] = stats
    if cpu[_UNACCOUNTED]:
        result[_UNACCOUNTED] = _cpu_stats(cpu[_UNACCOUNTED])

    return result


# pidstat CPU percentage fields (see post-process-pidstat-log.py's _FIELD_MAP).
# IRIX-mode pidstat: these are per-process percentages of a single CPU each,
# summed across threads/cores, so pct_usr/pct_cpu/etc. can exceed 100.
_CPU_METRICS = ("pct_usr", "pct_system", "pct_guest", "pct_wait", "pct_cpu")


def parse_cpu(log_path: str) -> dict | None:
    """
    Parse pidstat.jsonl and return per-metric CPU percentage statistics.

    For each of _CPU_METRICS, computes the maximum and the time-weighted
    mean (see time_weighted_mean()) across all samples that carry that
    metric, rounded to 2 decimal places. A metric's "_avg" is omitted if no
    sample carrying it also has a valid "timestamp".

    Returns a dict with up to two keys per metric, e.g. "pct_cpu_max",
    "pct_cpu_avg". Returns None on any error (missing
    file, empty file, no valid samples, parse failure).
    """
    if not os.path.exists(log_path):
        sys.stderr.write(f"Warning: pidstat log not found: {log_path}\n")
        return None

    values = {metric: [] for metric in _CPU_METRICS}
    timed_values = {metric: [] for metric in _CPU_METRICS}

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    sys.stderr.write(
                        f"Warning: skipping malformed line {lineno} in {log_path}\n"
                    )
                    continue

                ts_seconds = _parse_timestamp_seconds(obj.get("timestamp"))

                found_any = False
                for metric in _CPU_METRICS:
                    if metric not in obj:
                        continue
                    try:
                        value = float(obj[metric])
                    except (TypeError, ValueError):
                        continue
                    found_any = True
                    values[metric].append(value)
                    if ts_seconds is not None:
                        timed_values[metric].append((ts_seconds, value))

                if not found_any:
                    sys.stderr.write(
                        f"Warning: skipping line {lineno} in {log_path}: "
                        f"no CPU metrics found\n"
                    )
    except OSError as e:
        sys.stderr.write(f"Warning: cannot read pidstat log {log_path}: {e}\n")
        return None

    if not any(values[metric] for metric in _CPU_METRICS):
        sys.stderr.write(f"Warning: no valid CPU samples in {log_path}\n")
        return None

    result = {}
    for metric in _CPU_METRICS:
        samples = values[metric]
        if not samples:
            continue
        result[f"{metric}_max"] = round(max(samples), 2)
        avg = time_weighted_mean(timed_values[metric])
        if avg is not None:
            result[f"{metric}_avg"] = round(avg, 2)

    return result
