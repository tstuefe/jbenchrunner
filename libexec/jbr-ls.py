#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-ls — List run result directories as a formatted table.
#
# Reads the merged run metadata (info.json + run-info.json + result.json) for
# each candidate run directory and prints the selected columns as an
# auto-sized table, sorted by time ascending by default.
#
# Usage:
#   jbr-ls [-o <cols>] [-O <cols>] [-s <col>[,<col>...]]
#          [--sort-asc | --sort-desc] [-g <col>[,<col>...] | --no-header]
#          [--compare <cols>]
#          [--baseline <runid>] [--color]
#          [--markdown | --report <name> [--self-contained] [--force]]
#          [--no-autoomit] [RUNID...]
#
# Column flags:
#   -o <cols>        DEFAULT + <cols>  (comma-separated column/group names)
#   -O <cols>        <cols> only       (no DEFAULT prepended)
#                    Except with -O, the variable-width columns collectors,
#                    tags and jvmargs are moved to the end of the column list.
#
# Column groups (uppercase): DEFAULT  JVM  RESULTS  HOSTINFO  FLAGS
#
# Sort flags:
#   -s <col>[,<col>...]  Sort by column(s), comma-separated (default: time).
#                        Later columns break ties in earlier ones.
#   --sort-asc           Ascending sort (default)
#   --sort-desc          Descending sort
#
# Display flags:
#   -g|--group <col>[,<col>...]  Print a "col: value, ..." banner and a
#                        fresh header before each run of rows whose values
#                        in these columns differ from the previous row.
#                        Columns must be part of the displayed columns
#                        (see -o/-O). Typically used with -s so groups come
#                        out contiguous, e.g. -s name,jdkname -g name.
#   --no-header      Omit the header and separator lines; print only the
#                     data rows. Mutually exclusive with -g, --markdown and
#                     --report.
#   --no-autoomit    Show all columns even when every value is "-"
#   --markdown       Print as a markdown table instead of fixed-width text.
#                     With -g, each group starts with a banner row
#                     (group values in bold, in their own columns) and
#                     a repeated header row.
#                     Mutually exclusive with --report.
#   --report <name>  Write the table as a standalone HTML report into a new
#                     <project>/reports/<name> directory (index.html) instead of
#                     printing to stdout. With -g, each group starts with
#                     a full-width banner row and its own header row;
#                     all groups share one table.
#                     Mutually exclusive with --markdown.
#   --self-contained With --report, copy each displayed run's result
#                     directory into the report directory and link to the
#                     copies instead of the originals under results/.
#                     Requires --report.
#   --force          With --report, if the report directory already exists,
#                     wipe it and re-use it for the new report.
#                     Requires --report.
#
# Comparison flags:
#   --baseline <runid>   Use the matching row as the comparison baseline for
#                        --compare (row stays in its normal sort
#                        position). Requires --compare.
#                        Mutually exclusive with -g.
#                        Error if runid matches no loaded run.
#   --compare <col>[=<n>][,<col>[=<n>]...]
#                        For each named (numeric, displayed) column, append
#                        the row's value as a percent delta from its
#                        baseline row's value, e.g. "4652 (+6.9%)".
#                        Baseline defaults to the first row of the table
#                        (or, with -g, the first row of each group) when
#                        --baseline is omitted. May be combined with -g.
#                        With <col>=<n> (n: number > 0), that column's delta
#                        is only shown where the percent delta is at least
#                        +-n percent; other cells show just their value.
#   --color              Color the compare delta green/red for critjops,
#                        maxjops, result_ms, startup_ms, p50, p99, p100,
#                        p50_smooth, p99_smooth, p100_smooth, rssmax, rssavg,
#                        mallocmax, mallocavg, minflt, majflt and the arena
#                        columns (aru*, ard*, arc*) (green
#                        when the change is good for that column, red when
#                        bad), and orange/blue for an increase/decrease in
#                        any cpu_* column (value-neutral "heat"; yellow
#                        instead of orange on terminals without 256-color
#                        support). With --report, wraps the delta in a CSS span
#                        instead of an ANSI code. No effect on other
#                        compared columns, in --markdown mode, or (without
#                        --report) when the output isn't an interactive,
#                        color-capable terminal.
#
# Positional:
#   RUNID...         Zero or more bare run directory names.
#                    If omitted, reads from stdin or scans the project's results/.

import argparse
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
_PHASE_ANALYZE_DIR = os.path.join(_SCRIPTS_DIR, "phase-analyze")
_COMMON_DIR = os.path.join(_SCRIPTS_DIR, "common")

for _p in (_SCRIPTS_DIR, _PHASE_ANALYZE_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


_EPILOG = """\
Column groups (expand inline when used with -o / -O):

  DEFAULT   runid, time, name, tags
  JVM       jdkname, jdktype, jdkmajor, jdkversion
  RESULTS   duration, result_ms, stderr_pct, startup_ms, maxjops, critjops,
            hbirmax, hbirset
  LATENCY   p50, p99, p100, p99_stderr_pct
  LATENCYSMOOTH
            p50_smooth, p99_smooth, p100_smooth, p99_smooth_stderr_pct
  HOSTINFO  hostname, arch, os
  FLAGS     heapsz, heapszf, gc, jvmargs
  MEM       rssmax, rssavg, mallocmax, mallocavg
  FAULTS    minflt, majflt
  ARENA     ardmax, ardavg
  ARENAALL  arumax, aruavg, ardmax, ardavg, arcmax, arcavg
  TEMP      tempmax, tempin, tempout
  CPU       cpu_usr_avg, cpu_sys_avg, cpu_avg
  CPUALL    cpu_usr_avg, cpu_sys_avg, cpu_guest_avg,
            cpu_wait_avg, cpu_avg, cpu_usr_max, cpu_sys_max,
            cpu_guest_max, cpu_wait_max, cpu_max
  CPUMAX    cpu_usr_max, cpu_sys_max, cpu_guest_max,
            cpu_wait_max, cpu_max
  THREADS   osthreads_max, osthreads_avg, c2_max, c2_avg,
            c1_max, c1_avg, gc_max, gc_avg,
            mutator_max, mutator_avg, other_max, other_avg
  THREADCPU c2_cpu_avg, c1_cpu_avg, gc_cpu_avg, mutator_cpu_avg,
            other_cpu_avg, unaccounted_cpu_avg, c2_cpu_max, c1_cpu_max,
            gc_cpu_max, mutator_cpu_max, other_cpu_max, unaccounted_cpu_max

Columns (name used with -o / -O, -s, -g, --compare*, and as table header):

  runid          Run UUID, printed in full (clickable link to the run
                 directory when the terminal supports it)
  name           Compound run name (info.json .name)
  benchmark      Benchmark type: dacapo or specjbb
  sub            DaCapo sub-benchmark; - for specjbb
  datasize       DaCapo datasize; - for specjbb
  time           Start time, second precision (e.g. 2026-08-06T11:30:35)
  duration       Wall-clock seconds; - if no run-info.json
  collint_secs   Collector sampling interval in seconds (info.json
                 collectors.interval_secs); - if unknown
  collectors     Comma-joined list of the collectors that ran (info.json
                 collectors.enabled); - if unknown
  tags           Comma-joined tag list; empty string if no tags
  batchid        Batch UUID this run belongs to (info.json .batchid)
  repeat_no      0-based repetition index (jbr-prepare --repeat); 0 if the
                 run was not repeated; - if not recorded
  jdkname        JDK name (e.g. openjdk-25)
  jdkmajor       JDK major version integer (e.g. 21)
  jdktype        JDK type: openjdk or openj9
  jdkversion     Numerical JDK version (e.g. 21.0.4)
  hostname       Hostname where the run was executed
  arch           CPU architecture (e.g. x86_64)
  os             Operating system string
  result_ms      DaCapo result in milliseconds; - for specjbb runs
  stderr_ms      Standard error of time_ms over the last N iterations
                 (sample stddev / sqrt(N)); N is 10, 7 or 5 for runs with
                 >= 40, >= 30 or >= 20 iterations; - if the run did not
                 have >= 20 iterations, or for specjbb runs
  stderr_pct     Relative standard error in percent:
                 stderr_ms / mean(time_ms over the last N iterations) * 100;
                 - if the run did not have >= 20 iterations, or for specjbb
                 runs
  p50, p99, p100 DaCapo tail latency of the final iteration, in microseconds
                 (us): 50th/99th percentile and max, unsmoothed ("simple"
                 variant); - for benchmarks that report no latency (only
                 some do, e.g. tomcat) or for specjbb runs
  p50_smooth, p99_smooth, p100_smooth
                 Same, from the variant with 100ms metered smoothing
  p99_stderr     Standard error of p99 (us) over the last N iterations
                 (sample stddev / sqrt(N), N as for stderr_ms); - if the
                 run did not have >= 20 iterations, or as for p99
  p99_stderr_pct Relative standard error of p99 in percent:
                 p99_stderr / mean(p99 over the last N iterations) * 100;
                 - as for p99_stderr
  p99_smooth_stderr, p99_smooth_stderr_pct
                 Same, for p99_smooth
  startup_ms     DaCapo milliseconds from JVM launch to the start of the
                 first iteration; - for specjbb runs or if not recorded
  maxjops        SPECjbb max-jOPS; - for dacapo runs
  critjops       SPECjbb critical-jOPS; - for dacapo runs
  hbirmax        SPECjbb hbIRmax; - for dacapo runs
  hbirset        SPECjbb hbIRset; - for dacapo runs
  heapsz         Heap size in MB (e.g. 16384)
  heapszf        Heap-sweep factor (e.g. 2.0); - if the run was not part of
                 a heap-size sweep
  gc             GC collector name, deduced from jvmargs (e.g. g1gc, zgc,
                 shenandoahgc, gencon, metronome); - if not determinable
  jvmargs        Sanitized JVM arguments string
  rssmax         Max resident set size in MB; - if not collected
  rssavg         Time-weighted average resident set size in MB; - if not
                 collected
  mallocmax      Max NMT total malloc'd memory in MB; - if not collected
  mallocavg      Time-weighted average NMT total malloc'd memory in MB;
                 - if not collected
  minflt         Total minor page faults of the process; - if not collected
  majflt         Total major page faults of the process (disk reads, e.g.
                 swap-in or cold page cache); - if not collected
  arumax         Max arena memory in use (last tick peak, all block sizes,
                 incl. cached blocks) in MB; - if not logged
  aruavg         Time-weighted average of the above in MB; - if not logged
  ardmax         Max arena dirty memory in MB; - if not logged
  ardavg         Time-weighted average arena dirty memory in MB; - if not
                 logged
  arcmax         Max arena memory held in block caches in MB; - if not
                 logged
  arcavg         Time-weighted average of the above in MB; - if not logged
  tempmax        Max CPU temperature; - if not collected
  tempin         CPU temperature at run start; - if not collected
  tempout        CPU temperature at run end; - if not collected
  cpu_usr_max    Max %usr (pidstat, IRIX mode); - if not collected
  cpu_usr_avg    Time-weighted average %usr; - if not collected
  cpu_sys_max    Max %system; - if not collected
  cpu_sys_avg    Time-weighted average %system; - if not collected
  cpu_guest_max  Max %guest; - if not collected
  cpu_guest_avg  Time-weighted average %guest; - if not collected
  cpu_wait_max   Max %wait; - if not collected
  cpu_wait_avg   Time-weighted average %wait; - if not collected
  cpu_max        Max %CPU (total); - if not collected
  cpu_avg        Time-weighted average %CPU (total); - if not collected
  osthreads_max  Max OS thread count; - if not collected
  osthreads_avg  Average OS thread count; - if not collected
  <c>_max        Max thread count of category <c> in the thread dumps;
                 <c> is c2 (C2 compiler), c1 (C1 compiler), gc (GC),
                 mutator (Java threads running application code) or
                 other (everything else); - if not collected
  <c>_avg        Time-weighted average thread count of category <c>;
                 - if not collected
  <c>_cpu_avg    Average CPU usage of category <c> in % (100 = one core);
                 here <c> can also be unaccounted: CPU time of threads no
                 dump saw (ended between dumps, or short-lived; needs the
                 proc collector); - if not collected
  <c>_cpu_max    Max CPU usage of category <c> between two dumps, in %;
                 - if not collected

Auto-omit: columns whose value is "-" for every row are silently dropped
(disable with --no-autoomit). MEM/FAULTS/ARENAALL/TEMP/CPU/CPUALL/CPUMAX columns,
osthreads_max/osthreads_avg and the latency columns (p50, p99, ...) are never
auto-omitted.
"""


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="jbr-ls",
        description=(
            "List run result directories as a formatted table.\n\n"
            "Reads each candidate run directory, extracts the selected\n"
            "columns, and prints an auto-sized table sorted by time\n"
            "ascending by default."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Column selection (mutually exclusive)
    col_group = parser.add_mutually_exclusive_group()
    col_group.add_argument(
        "-o",
        metavar="cols",
        help=(
            "DEFAULT + <cols>: prepend DEFAULT group then append the "
            "specified comma-separated column/group names. The "
            "variable-width columns collectors, tags and jvmargs are moved to the end."
        ),
    )
    col_group.add_argument(
        "-O",
        metavar="cols",
        help=(
            "<cols> only: show only the specified comma-separated "
            "column/group names (no DEFAULT prepended), in the given "
            "order."
        ),
    )

    # Sort
    parser.add_argument(
        "-s",
        metavar="col",
        dest="sort_col",
        default="time",
        help=(
            "Sort by these columns, comma-separated (default: time). "
            "Later columns break ties in earlier ones, e.g. "
            "-s colA,colB,colC sorts by colA, then colB, then colC."
        ),
    )
    sort_dir = parser.add_mutually_exclusive_group()
    sort_dir.add_argument(
        "--sort-asc",
        action="store_true",
        default=False,
        help="Sort ascending (default).",
    )
    sort_dir.add_argument(
        "--sort-desc",
        action="store_true",
        default=False,
        help="Sort descending.",
    )

    # Display options
    parser.add_argument(
        "-g",
        "--group",
        metavar="cols",
        dest="group_col",
        default=None,
        help=(
            "Print a 'col: value, ...' banner and a fresh header before "
            "each run of rows whose values in these comma-separated "
            "columns differ from the previous row. Columns must be part "
            "of the displayed columns (see -o/-O). Typically used with "
            "-s so groups come out contiguous, e.g. -s name,jdkname -g name."
        ),
    )
    parser.add_argument(
        "--no-header",
        dest="no_header",
        action="store_true",
        default=False,
        help=(
            "Omit the header and separator lines; print only the data "
            "rows. Mutually exclusive with -g/--group, --markdown and "
            "--report."
        ),
    )
    parser.add_argument(
        "--no-autoomit",
        action="store_true",
        default=False,
        help="Show all columns even when every value is \"-\".",
    )
    output_format = parser.add_mutually_exclusive_group()
    output_format.add_argument(
        "--markdown",
        action="store_true",
        default=False,
        help=(
            "Print the table as a markdown table instead of a fixed-width "
            "plain-text table. With -g/--group, all groups share one table; "
            "each group starts with a banner row holding the group values "
            "in bold, in their own columns, and a repeated header row."
        ),
    )
    output_format.add_argument(
        "--report",
        metavar="name",
        dest="report",
        default=None,
        help=(
            "Write the table(s) as a standalone HTML report into a new "
            "'<project>/reports/<name>' directory (as index.html), instead of "
            "printing to stdout. With -g/--group, all groups share one "
            "table; each group starts with a full-width banner row and "
            "its own header row. Mutually exclusive with "
            "--markdown. See --self-contained."
        ),
    )
    parser.add_argument(
        "--self-contained",
        dest="self_contained",
        action="store_true",
        default=False,
        help=(
            "With --report, copy each displayed run's result directory "
            "into the report directory and link to the copies instead of "
            "the original directories under results/. Requires --report."
        ),
    )
    parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        default=False,
        help=(
            "With --report, if '<project>/reports/<name>' already exists, "
            "wipe it and re-use it for the new report. Requires --report."
        ),
    )

    # Baseline comparison. --baseline is mutually exclusive with -g/--group
    # and requires --compare (checked in main(), since argparse mutually-
    # exclusive groups can't express a one-directional requirement).
    parser.add_argument(
        "--baseline",
        metavar="runid",
        default=None,
        help=(
            "Use the row with this runid as the comparison baseline for "
            "--compare (the row stays in its normal sort "
            "position). Requires --compare. "
            "Mutually exclusive with -g/--group. If "
            "omitted, the baseline is the first row of the table (or, "
            "with -g, the first row of each group)."
        ),
    )
    parser.add_argument(
        "--compare",
        metavar="cols",
        default=None,
        help=(
            "Comma-separated numeric columns (must already be displayed "
            "via -o/-O). For each, append the row's value as a percent "
            "delta from its baseline row's value, e.g. '4652 (+6.9%%)'. "
            "May be combined with -g/--group. A column given as "
            "<col>=<n> (n: number > 0) only shows its delta where the "
            "percent delta (as displayed, one decimal) is at least +-n "
            "percent; other cells show just their value, without --color "
            "or plot link."
        ),
    )
    parser.add_argument(
        "--color",
        action="store_true",
        default=False,
        help=(
            "Color the compare delta green (good) or red (bad) for "
            "critjops, maxjops, result_ms, startup_ms, p50, p99, p100, "
            "p50_smooth, p99_smooth, p100_smooth, rssmax, rssavg, "
            "mallocmax, mallocavg, minflt, majflt and the arena columns "
            "(aru*, ard*, arc*); orange (hotter) or "
            "blue (cooler) for any cpu_* column (yellow instead of orange "
            "on terminals without 256-color support). With "
            "--report, this wraps the delta in a CSS span instead. No "
            "effect on other compared columns, in --markdown mode, or "
            "(without --report) when the output isn't an interactive, "
            "color-capable terminal."
        ),
    )

    # Positional run IDs
    parser.add_argument(
        "runids",
        metavar="RUNID",
        nargs="*",
        help="Bare run directory names to list (optional).",
    )

    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    from listing import (
        resolve_columns,
        resolve_sort_cols,
        resolve_group_cols,
        resolve_compare_cols,
        format_run_table,
        extract_cell,
    )
    from project import find_project, reports_dir, runs_dir
    from run_loader import discover_run_dirs, load_run
    from term_link import terminal_link

    if args.baseline is not None and args.compare is None:
        print(
            "Error: --baseline requires --compare",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.group_col is not None and args.baseline is not None:
        print(
            "Error: --baseline cannot be combined with -g/--group",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.no_header and args.group_col is not None:
        print(
            "Error: --no-header cannot be combined with -g/--group",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.no_header and (args.markdown or args.report is not None):
        print(
            "Error: --no-header cannot be combined with --markdown "
            "or --report",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.self_contained and args.report is None:
        print(
            "Error: --self-contained requires --report",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.force and args.report is None:
        print(
            "Error: --force requires --report",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        project_dir = find_project()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        resolve_sort_cols(args.sort_col)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    col_list = resolve_columns(args.o, args.O)

    try:
        group_cols = resolve_group_cols(args.group_col, col_list)
        compare_spec = resolve_compare_cols(args.compare, col_list) or {}
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    compare_cols = list(compare_spec)
    compare_thresholds = {
        col: t for col, t in compare_spec.items() if t is not None
    }

    results_dir = runs_dir(project_dir)
    run_ids = discover_run_dirs(args.runids, results_dir, sys.stdin.isatty())

    # Load each run, skipping those without result.json.
    loaded_runs = []
    for run_id in run_ids:
        run_dir = os.path.join(results_dir, run_id)
        try:
            merged = load_run(run_dir)
        except ValueError as exc:
            link = terminal_link(run_dir, run_id, stream=sys.stderr)
            print(f"Warning: skipping {link}: {exc}", file=sys.stderr)
            continue
        if merged is None:
            continue
        loaded_runs.append(merged)

    if not loaded_runs:
        sys.exit(0)

    if args.baseline is not None:
        if not any(
            extract_cell("runid", r) == args.baseline for r in loaded_runs
        ):
            print(
                f"Error: --baseline runid {args.baseline!r} does not match "
                f"any loaded run",
                file=sys.stderr,
            )
            sys.exit(1)

    # sort_asc: False only when --sort-desc given; ascending otherwise.
    sort_asc = not args.sort_desc

    report_dir = None
    link_results_dir = results_dir
    if args.report is not None:
        if args.report in ("", ".", "..") or "/" in args.report:
            print(f"Error: invalid report name: {args.report!r}", file=sys.stderr)
            sys.exit(1)
        report_dir = os.path.join(reports_dir(project_dir), args.report)
        if os.path.exists(report_dir) and args.force:
            if not os.path.isdir(report_dir) or os.path.islink(report_dir):
                print(
                    f"Error: report path exists but is not a directory: {report_dir}",
                    file=sys.stderr,
                )
                sys.exit(1)
            shutil.rmtree(report_dir)
        if os.path.exists(report_dir):
            print(
                f"Error: report directory already exists: {report_dir}",
                file=sys.stderr,
            )
            sys.exit(1)
        os.makedirs(report_dir)  # creates reports/ too, if missing
        if args.self_contained:
            link_results_dir = report_dir
            for merged in loaded_runs:
                run_id = extract_cell("runid", merged)
                if run_id == "-":
                    continue
                src = os.path.join(results_dir, run_id)
                if os.path.isdir(src):
                    shutil.copytree(src, os.path.join(report_dir, run_id))

    table = format_run_table(
        rows=loaded_runs,
        col_list=col_list,
        sort_col=args.sort_col,
        sort_asc=sort_asc,
        no_autoomit=args.no_autoomit,
        results_dir=link_results_dir,
        report_dir=report_dir,
        group_cols=group_cols,
        baseline_runid=args.baseline,
        compare_cols=compare_cols,
        markdown=args.markdown,
        html=args.report is not None,
        color=args.color,
        relative_links=args.report is not None,
        no_header=args.no_header,
        compare_thresholds=compare_thresholds,
    )

    if report_dir is not None:
        index_path = os.path.join(report_dir, "index.html")
        with open(index_path, "w") as f:
            f.write(table)
            if not table.endswith("\n"):
                f.write("\n")
        print(f"created report in {report_dir}")
    elif table:
        print(table)


if __name__ == "__main__":
    main()
