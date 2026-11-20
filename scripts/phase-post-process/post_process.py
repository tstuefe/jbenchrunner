#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Core post-processing engine for a single benchmark run directory.

Function:
    post_process_run(run_dir: str) -> dict
        Reads run artefacts, assembles result.json, and writes it atomically.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import collector_timestamp
from common.collector_files import compress_file, resolve_collector_file
from common.gnuplot_utils import run_header
from aggregators import (parse_temperatures, parse_memory, parse_malloc, parse_cpu, parse_threads,
                         parse_thread_categories, parse_page_faults, parse_arena_summary)
from parse_dacapo_results import parse_dacapo_output
from parse_specjbb_results import parse_specjbb_output


# Paths to the collector post-processor scripts (same directory as this module).
_HERE = os.path.dirname(os.path.abspath(__file__))
_PERF_LOG_SCRIPT = os.path.join(_HERE, "post-process-perf-log.py")
_PIDSTAT_LOG_SCRIPT = os.path.join(_HERE, "post-process-pidstat-log.py")
_PLOT_PIDSTAT_SCRIPT = os.path.join(_HERE, "plot-pidstat.py")
_PROC_LOG_SCRIPT = os.path.join(_HERE, "post-process-proc-log.py")
_PLOT_PAGE_FAULTS_SCRIPT = os.path.join(_HERE, "plot-pagefaults.py")
_NMT_LOG_SCRIPT = os.path.join(_HERE, "post-process-nmt-log.py")
_PLOT_NMT_SCRIPT = os.path.join(_HERE, "plot-nmt.py")
_SYSTEM_MAP_LOG_SCRIPT = os.path.join(_HERE, "post-process-system-map-log.py")
_PLOT_SYSTEM_MAP_SCRIPT = os.path.join(_HERE, "plot-system-map.py")
_THREAD_PRINT_LOG_SCRIPT = os.path.join(_HERE, "post-process-thread-print-log.py")
_PLOT_THREAD_PRINT_SCRIPT = os.path.join(_HERE, "plot-thread-print.py")
_EXTRACT_HEAPSIZES_SCRIPT =os.path.join(_HERE, "extract-heapsizes.py")
_PLOT_HEAPSIZES_SCRIPT = os.path.join(_HERE, "plot-heapsizes.py")
# Standalone tool (also usable outside jbr), lives under bin/tools.
_ARENA_LOG_PARSER_SCRIPT = os.path.join(_SCRIPTS_DIR, "..", "bin", "tools", "arena-log-parser.py")

# Files zstd-compressed once the arena report (step 7f4) and the heap sizes plot
# are done (step 7g); also used by scripts/helpers/compress-files.py to compress
# them in older run directories.
COMPRESSED_AFTER_USE = ("gc-all.log", "gc.log", "heapsizes.txt", "arena.log")


def _atomic_write_json(path: str, data: dict) -> None:
    """Write *data* as indented JSON to *path* atomically (temp file + rename)."""
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _startup_ms(start_time, first_iteration_ts) -> int | None:
    """Milliseconds from JVM launch (run-info.json time) to the start of
    the first DaCapo iteration, or None if either timestamp is missing or
    unparseable. first_iteration_ts may carry a Java zone-id suffix
    ("...+02:00[Europe/Berlin]"), which is stripped before parsing."""
    try:
        start = collector_timestamp.parse(start_time)
        first = datetime.fromisoformat(re.sub(r"\[[^\]]*\]$", "", first_iteration_ts))
    except (ValueError, TypeError):
        return None
    return round((first - start).total_seconds() * 1000)


def _add_startup_ms(dacapo_result: dict, startup_ms: int) -> dict:
    """Return *dacapo_result* with startup_ms placed right after the
    first/final iteration timestamps."""
    anchor = "final_iteration_ts" if "final_iteration_ts" in dacapo_result else "first_iteration_ts"
    updated = {}
    for key, value in dacapo_result.items():
        updated[key] = value
        if key == anchor:
            updated["startup_ms"] = startup_ms
    return updated


def post_process_run(run_dir: str) -> dict:
    """
    Post-process a completed benchmark run directory and write result.json.

    Parameters
    ----------
    run_dir : str
        Path to the run directory (e.g. results/<RUNID>/).

    Returns
    -------
    dict
        The assembled result dict (same content written to result.json).

    Raises
    ------
    ValueError
        If run_dir does not exist, required files are missing, status is not
        DONE, benchmark type is unknown, or stdout.log cannot be parsed.
    RuntimeError
        If running the perf-log post-processor subprocess fails.
    """

    # ------------------------------------------------------------------
    # 1. Validate run_dir
    # ------------------------------------------------------------------
    if not os.path.isdir(run_dir):
        raise ValueError(f"run directory does not exist or is not a directory: {run_dir}")

    # ------------------------------------------------------------------
    # 2. Read and validate run-info.json
    # ------------------------------------------------------------------
    run_info_path = os.path.join(run_dir, "run-info.json")
    if not os.path.exists(run_info_path):
        raise ValueError(f"run-info.json not found in {run_dir}")

    with open(run_info_path, "r", encoding="utf-8") as f:
        run_info = json.load(f)

    status = run_info.get("status", "")
    if status != "DONE":
        raise ValueError(
            f"cannot post-process run with status {status!r}: "
            f"result.json is only produced for successful runs"
        )

    # ------------------------------------------------------------------
    # 3. Read info.json and detect benchmark type
    # ------------------------------------------------------------------
    info_path = os.path.join(run_dir, "info.json")
    if not os.path.exists(info_path):
        raise ValueError(f"info.json not found in {run_dir}")

    with open(info_path, "r", encoding="utf-8") as f:
        info = json.load(f)

    benchmark = info.get("benchmark", "")
    if benchmark not in ("dacapo", "specjbb"):
        raise ValueError(f"unknown benchmark type {benchmark!r} in info.json")

    # ------------------------------------------------------------------
    # 4. Parse stdout.log
    # ------------------------------------------------------------------
    stdout_log_path = os.path.join(run_dir, "stdout.log")
    if not os.path.exists(stdout_log_path):
        raise ValueError(f"stdout.log not found in {run_dir}")

    with open(stdout_log_path, "r", encoding="utf-8", errors="replace") as f:
        stdout_content = f.read()

    result = {}
    try:
        if benchmark == "dacapo":
            dacapo_result = parse_dacapo_output(stdout_content)
            startup_ms = _startup_ms(run_info.get("time"),
                                     dacapo_result.get("first_iteration_ts"))
            if startup_ms is not None:
                dacapo_result = _add_startup_ms(dacapo_result, startup_ms)
            result["dacapo_result"] = dacapo_result
        else:
            result["specjbb_result"] = parse_specjbb_output(stdout_content)
    except ValueError as e:
        raise ValueError(f"failed to parse stdout.log: {e}") from e

    # ------------------------------------------------------------------
    # 5. Collector: temperatures
    # ------------------------------------------------------------------
    temps_file = os.path.join(run_dir, "temps.jsonl")
    temps = parse_temperatures(temps_file)
    if temps is not None:
        result["temperatures"] = temps

    # ------------------------------------------------------------------
    # 6. Collector: memory
    # proclog.log (or .zst) -> proclog.jsonl first if present (older runs
    # only have the .jsonl written by the old collector; left untouched then).
    # ------------------------------------------------------------------
    proclog_file = os.path.join(run_dir, "proclog.jsonl")
    proclog_log_path = resolve_collector_file(run_dir, "proclog.log")
    if proclog_log_path is not None:
        try:
            proc = subprocess.run(
                [sys.executable, _PROC_LOG_SCRIPT, proclog_log_path],
                capture_output=True,
                text=True,
                check=True,
            )
            with open(proclog_file, "w", encoding="utf-8") as f:
                f.write(proc.stdout)
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"Warning: post-process-proc-log.py failed (exit {e.returncode}): "
                f"{e.stderr.strip()}\n"
            )
        else:
            if proc.stderr:
                sys.stderr.write(proc.stderr)
    memory = parse_memory(proclog_file)
    if memory is not None:
        result["memory"] = memory

    # ------------------------------------------------------------------
    # 6b. Collector: threads
    # ------------------------------------------------------------------
    threads = parse_threads(proclog_file)
    if threads is not None:
        result["threads"] = threads

    # ------------------------------------------------------------------
    # 6c. Collector: page faults (totals into result.json, fault rates
    # plotted as pagefaults.svg). The plot is best-effort: a plotting
    # failure (e.g. gnuplot missing) is logged but must not block
    # result.json.
    # ------------------------------------------------------------------
    page_faults = parse_page_faults(proclog_file)
    if page_faults is not None:
        result["page_faults"] = page_faults
        try:
            subprocess.run(
                [sys.executable, _PLOT_PAGE_FAULTS_SCRIPT, run_dir],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"Warning: plot-pagefaults.py failed (exit {e.returncode}): {e.stderr.strip()}\n"
            )

    # ------------------------------------------------------------------
    # 7. Perf log: convert perflog.log -> perflog.jsonl if present
    # ------------------------------------------------------------------
    perf_log_path = os.path.join(run_dir, "perflog.log")
    if os.path.exists(perf_log_path):
        perf_jsonl_path = os.path.join(run_dir, "perflog.jsonl")
        try:
            proc = subprocess.run(
                [sys.executable, _PERF_LOG_SCRIPT, perf_log_path],
                capture_output=True,
                text=True,
                check=True,
            )
            with open(perf_jsonl_path, "w", encoding="utf-8") as f:
                f.write(proc.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"post-process-perf-log.py failed (exit {e.returncode}): {e.stderr.strip()}"
            ) from e

    # ------------------------------------------------------------------
    # 7b. pidstat log: convert pidstat.log -> pidstat.jsonl if present
    # ------------------------------------------------------------------
    pidstat_log_path = os.path.join(run_dir, "pidstat.log")
    if os.path.exists(pidstat_log_path):
        pidstat_jsonl_path = os.path.join(run_dir, "pidstat.jsonl")
        try:
            proc = subprocess.run(
                [sys.executable, _PIDSTAT_LOG_SCRIPT, pidstat_log_path],
                capture_output=True,
                text=True,
                check=True,
            )
            with open(pidstat_jsonl_path, "w", encoding="utf-8") as f:
                f.write(proc.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"post-process-pidstat-log.py failed (exit {e.returncode}): {e.stderr.strip()}"
            ) from e

    # ------------------------------------------------------------------
    # 7c. Collector: cpu (from pidstat.jsonl, built in step 7b)
    # ------------------------------------------------------------------
    pidstat_jsonl_path = os.path.join(run_dir, "pidstat.jsonl")
    cpu = parse_cpu(pidstat_jsonl_path)
    if cpu is not None:
        result["cpu"] = cpu

    # ------------------------------------------------------------------
    # 7d. pidstat log: plot RSS and CPU load over time (pidstat-rss.svg,
    # pidstat-cpu.svg). Best-effort: a plotting failure (e.g. gnuplot
    # missing) is logged but must not block result.json.
    # ------------------------------------------------------------------
    try:
        subprocess.run(
            [sys.executable, _PLOT_PIDSTAT_SCRIPT, run_dir],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        sys.stderr.write(
            f"Warning: plot-pidstat.py failed (exit {e.returncode}): {e.stderr.strip()}\n"
        )

    # ------------------------------------------------------------------
    # 7e. NMT log: convert nmt.log -> nmt.jsonl if present
    # ------------------------------------------------------------------
    nmt_log_path = resolve_collector_file(run_dir, "nmt.log")  # nmt.log or nmt.log.zst
    if nmt_log_path is not None:
        nmt_jsonl_path = os.path.join(run_dir, "nmt.jsonl")
        try:
            proc = subprocess.run(
                [sys.executable, _NMT_LOG_SCRIPT, nmt_log_path],
                capture_output=True,
                text=True,
                check=True,
            )
            with open(nmt_jsonl_path, "w", encoding="utf-8") as f:
                f.write(proc.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"post-process-nmt-log.py failed (exit {e.returncode}): {e.stderr.strip()}"
            ) from e

        malloc = parse_malloc(nmt_jsonl_path)
        if malloc is not None:
            result["malloc"] = malloc

        # --------------------------------------------------------------
        # 7f. NMT: plot stacked malloc breakdown over time (nmt-malloc.svg).
        # Best-effort: a plotting failure (e.g. gnuplot missing) is logged
        # but must not block result.json.
        # --------------------------------------------------------------
        try:
            subprocess.run(
                [sys.executable, _PLOT_NMT_SCRIPT, run_dir],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"Warning: plot-nmt.py failed (exit {e.returncode}): {e.stderr.strip()}\n"
            )

    # ------------------------------------------------------------------
    # 7f2. System map: convert system.map.log (or .zst) -> system.map.jsonl if
    # present (older runs only have the .jsonl), then plot the stacked RSS
    # breakdown over time (system_map.svg). The plot is best-effort, like NMT's.
    # ------------------------------------------------------------------
    system_map_log_path = resolve_collector_file(run_dir, "system.map.log")
    if system_map_log_path is not None:
        try:
            proc = subprocess.run(
                [sys.executable, _SYSTEM_MAP_LOG_SCRIPT, system_map_log_path],
                capture_output=True,
                text=True,
                check=True,
            )
            with open(os.path.join(run_dir, "system.map.jsonl"), "w", encoding="utf-8") as f:
                f.write(proc.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"post-process-system-map-log.py failed (exit {e.returncode}): {e.stderr.strip()}"
            ) from e
    if os.path.exists(os.path.join(run_dir, "system.map.jsonl")):
        try:
            subprocess.run(
                [sys.executable, _PLOT_SYSTEM_MAP_SCRIPT, run_dir],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"Warning: plot-system-map.py failed (exit {e.returncode}): {e.stderr.strip()}\n"
            )

    # ------------------------------------------------------------------
    # 7f3. Thread dumps: convert thread-print.log (or .zst) -> thread-print.jsonl
    # if present, then plot the thread counts and CPU usage per category over time
    # (thread-print.svg, thread-print-cpu.svg) and add their max/avg to the
    # "threads" section.
    # Best-effort: only HotSpot dumps are parsed (OpenJ9 runs yield no valid
    # sample); without thread-print.jsonl the category keys are just absent.
    # ------------------------------------------------------------------
    thread_print_log_path = resolve_collector_file(run_dir, "thread-print.log")
    if thread_print_log_path is not None:
        thread_print_cmd = [sys.executable, _THREAD_PRINT_LOG_SCRIPT]
        if os.path.exists(proclog_file):
            # CPU time of threads no dump saw goes to "other" (spec-post-process.md §3c.1).
            thread_print_cmd += ["--proclog", proclog_file]
        try:
            proc = subprocess.run(
                thread_print_cmd + [thread_print_log_path],
                capture_output=True,
                text=True,
                check=True,
            )
            with open(os.path.join(run_dir, "thread-print.jsonl"), "w", encoding="utf-8") as f:
                f.write(proc.stdout)
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"Warning: post-process-thread-print-log.py failed (exit {e.returncode}): "
                f"{e.stderr.strip()}\n"
            )
    thread_print_jsonl_path = os.path.join(run_dir, "thread-print.jsonl")
    if os.path.exists(thread_print_jsonl_path):
        thread_categories = parse_thread_categories(thread_print_jsonl_path)
        if thread_categories is not None:
            result.setdefault("threads", {}).update(thread_categories)
        try:
            subprocess.run(
                [sys.executable, _PLOT_THREAD_PRINT_SCRIPT, run_dir],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"Warning: plot-thread-print.py failed (exit {e.returncode}): {e.stderr.strip()}\n"
            )

    # ------------------------------------------------------------------
    # 7f4. Arena memory manager log: if arena.log (plain or .zst) is present,
    # write per block size and combined time series, plots and a summary into
    # arena-report/; arena.log is compressed afterwards (step 7g). Best-effort:
    # a failure (e.g. an arena.log without statistics ticks) is logged but
    # must not block result.json. The plots get the same run header line as
    # all other jbr plots (gnuplot_utils.run_header). The combined maxima and
    # averages of arena-summary.json go into result.json's "arena" section.
    # ------------------------------------------------------------------
    arena_log_path = resolve_collector_file(run_dir, "arena.log")
    if arena_log_path:
        arena_cmd = [sys.executable, _ARENA_LOG_PARSER_SCRIPT, arena_log_path, "-o", run_dir]
        arena_header = run_header(run_dir)
        if arena_header:
            arena_cmd += ["--header", arena_header]
        try:
            proc = subprocess.run(
                arena_cmd,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"Warning: arena-log-parser.py failed (exit {e.returncode}): {e.stderr.strip()}\n"
            )
        else:
            if proc.stderr:
                sys.stderr.write(proc.stderr)
            arena = parse_arena_summary(os.path.join(run_dir, "arena-report", "arena-summary.json"))
            if arena is not None:
                result["arena"] = arena

    # ------------------------------------------------------------------
    # 7g. Heap sizes: extract heap sizes from gc.log/gc-all.log (plain or
    # .zst, if present) into heapsizes.txt, then plot it as heapsizes.svg.
    # Afterwards the GC log and heapsizes.txt are zstd-compressed, whether
    # or not extraction and plotting succeeded. Best-effort: a
    # missing/unparseable GC log, a plotting or a compression failure is
    # logged but must not block result.json.
    # ------------------------------------------------------------------
    heapsizes_txt_path = os.path.join(run_dir, "heapsizes.txt")
    try:
        subprocess.run(
            [sys.executable, _EXTRACT_HEAPSIZES_SCRIPT, run_dir, "-o", heapsizes_txt_path],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        sys.stderr.write(
            f"Warning: extract-heapsizes.py failed (exit {e.returncode}): {e.stderr.strip()}\n"
        )

    if os.path.exists(heapsizes_txt_path):
        heapsizes_svg_path = os.path.join(run_dir, "heapsizes.svg")
        try:
            subprocess.run(
                [sys.executable, _PLOT_HEAPSIZES_SCRIPT, heapsizes_txt_path,
                 "-o", heapsizes_svg_path],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"Warning: plot-heapsizes.py failed (exit {e.returncode}): {e.stderr.strip()}\n"
            )

    for name in COMPRESSED_AFTER_USE:
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            err = compress_file(path)
            if err:
                sys.stderr.write(f"Warning: {err}\n")

    # ------------------------------------------------------------------
    # 7h. DaCapo: remove the scratch directory DaCapo extracts benchmark
    # data into (its cwd during the run, i.e. run_dir itself). Not needed
    # once the run is post-processed; can grow to tens of MB.
    # ------------------------------------------------------------------
    if benchmark == "dacapo":
        scratch_dir = os.path.join(run_dir, "scratch")
        if os.path.isdir(scratch_dir):
            shutil.rmtree(scratch_dir)

    # ------------------------------------------------------------------
    # 8. Write result.json atomically
    # ------------------------------------------------------------------
    _atomic_write_json(os.path.join(run_dir, "result.json"), result)

    return result
