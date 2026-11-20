#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-filt — Filter run result directories by field values.
#
# Reads the merged run object (info.json + run-info.json + result.json) for
# each candidate run directory, evaluates the active filter set, and prints
# the names of matching directories to stdout (one per line).  Output is
# suitable for piping into jbr-ls or other tools.  Each printed RUNID is a
# clickable terminal hyperlink to its run directory when stdout is an
# interactive terminal that supports it; piped/redirected output stays
# plain text.
#
# Usage:
#   jbr-filt [FILTER...] [RUNID...]
#
# Every convenience filter flag below also accepts the reserved value "null"
# (case-insensitive) instead of its normal argument, meaning "this field is
# missing or JSON null", e.g. --gc null matches runs with no heap.gc.
#
# --batchid additionally accepts the special keyword "last" (case-
# insensitive), meaning the newest finished batch in the project's batches/.
#
# Works on the results/ of the current project (found from the working
# directory, see docs/PRD.md §4).
#
# --since/--until take a date/time specification: "YYYY-MM-DDTHH:MM:SS"
# (matching the time field / jbr-ls's "time" column), with the date,
# time, or seconds component omittable. A missing seconds field defaults
# to ":00", a missing time defaults to "00:00:00", and a missing date
# defaults to today, e.g. "2026-08-06", "18:17", "2026-08-06T18:17:16".
#
# Options:
#   -b, --benchmark <val>     Match exact benchmark name (repeatable)
#   --sub <val>               Match exact dacapo sub-benchmark (repeatable)
#   -d, --datasize <val>      Match exact dacapo datasize (repeatable)
#   --jdkmajor <op><n>        Numeric JDK major comparison, e.g. >=21 (repeatable)
#   --heapszf <op><n>         Numeric heap-sweep factor comparison, e.g. >=2 (repeatable)
#   --repeat_no <op><n>       Numeric 0-based repetition index comparison, e.g. 0 (repeatable)
#   -S, --heapsz <op><n>      Numeric heap size comparison in MB, e.g. >=8192 (repeatable)
#   --duration <op><n>        Numeric wall-clock seconds comparison, e.g. >=120 (repeatable)
#   --startup_ms <op><n>      Numeric dacapo startup ms comparison, e.g. <2000 (repeatable)
#   --gc <val>                Match exact GC collector name, e.g. g1gc, gencon (repeatable)
#   --jdktype <val>           Match exact jdk.type (repeatable)
#   --jdkname <val>           Substring match on jdk.name (repeatable)
#   --jdkversion <val>        Substring match on jdk.version (repeatable)
#   -t, --tags <val>          Exact or glob (*, ?, [..]) match on any tag (repeatable)
#   -N, --name <val>          Glob (*, ?, [..]) match on the compound run name,
#                             e.g. dacapo.h2 (repeatable)
#   -H, --hostname <val>      Match exact hostname (repeatable)
#   --batchid <val>           Match exact batchid (repeatable); "last" resolves
#                             to the newest finished batch in batches/
#   --arch <val>              Match exact arch (repeatable)
#   --os <val>                Substring match on os (repeatable)
#   --jvmargs <val>           Substring match in any jvmargs element (repeatable)
#   --since <spec>            Match runs on or newer than <spec> (repeatable)
#   --until <spec>            Match runs on or older than <spec> (repeatable)
#   --where <jq-expr>         Raw jq boolean expression; requires jq on PATH (repeatable)
#   --or                      OR all active filters (default: AND)
#   --invert, -v              Negate the combined filter result
#   -h, --help                Show this help message and exit
#
# Positional:
#   RUNID...                  Zero or more bare run directory names to filter.
#                             If omitted, reads from stdin or scans results/.

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
_PHASE_ANALYZE_DIR = os.path.join(_SCRIPTS_DIR, "phase-analyze")
_COMMON_DIR = os.path.join(_SCRIPTS_DIR, "common")

for _p in (_SCRIPTS_DIR, _PHASE_ANALYZE_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="jbr-filt",
        description=(
            "Filter run result directories by field values.\n\n"
            "Reads each candidate run directory, evaluates the active filter\n"
            "set against the merged run object, and prints matching RUNID\n"
            "names to stdout (one per line).\n\n"
            "Any convenience flag also accepts the value 'null' (case-\n"
            "insensitive) to match runs where that field is missing or JSON\n"
            "null, e.g. --gc null."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Convenience filter flags
    parser.add_argument(
        "-b", "--benchmark",
        metavar="val",
        action="append",
        help="Exact benchmark name match (repeatable).",
    )
    parser.add_argument(
        "--sub",
        metavar="val",
        action="append",
        help="Exact dacapo sub-benchmark match (repeatable).",
    )
    parser.add_argument(
        "-d", "--datasize",
        metavar="val",
        action="append",
        help="Exact dacapo datasize match (repeatable).",
    )
    parser.add_argument(
        "--jdkmajor",
        metavar="op_n",
        action="append",
        help=(
            "Numeric JDK major comparison, e.g. >=21, <=11, >17, ==21, or bare 21 "
            "(defaults to ==). Repeatable."
        ),
    )
    parser.add_argument(
        "--heapszf",
        metavar="op_n",
        action="append",
        dest="heapszf",
        help=(
            "Numeric heap-sweep factor comparison, e.g. >=2, <=1.5, >4, ==2, or "
            "bare 2 (defaults to ==). Repeatable."
        ),
    )
    parser.add_argument(
        "--repeat_no",
        metavar="op_n",
        action="append",
        dest="repeat_no",
        help=(
            "Numeric 0-based repetition index comparison (jbr-prepare --repeat), "
            "e.g. 0, >=1, <2. Repeatable."
        ),
    )
    parser.add_argument(
        "-S", "--heapsz",
        metavar="op_n",
        action="append",
        dest="heapsz",
        help=(
            "Numeric heap size comparison in MB, e.g. >=8192, <=16384, ==8192, "
            "or bare 8192 (defaults to ==). Repeatable."
        ),
    )
    parser.add_argument(
        "--duration",
        metavar="op_n",
        action="append",
        help=(
            "Numeric wall-clock seconds comparison, e.g. >=120, <=60.5, ==300, "
            "or bare 120 (defaults to ==). Repeatable."
        ),
    )
    parser.add_argument(
        "--startup_ms",
        metavar="op_n",
        action="append",
        help=(
            "Numeric comparison on dacapo_result.startup_ms (ms from JVM launch "
            "to the first iteration), e.g. <2000, >=1500, or bare 1800 "
            "(defaults to ==). Repeatable."
        ),
    )
    parser.add_argument(
        "--gc",
        metavar="val",
        action="append",
        help="Exact GC collector name match, e.g. g1gc, zgc, gencon (repeatable).",
    )
    parser.add_argument(
        "--jdktype",
        metavar="val",
        action="append",
        help="Exact jdk.type match (repeatable).",
    )
    parser.add_argument(
        "--jdkname",
        metavar="val",
        action="append",
        help="Substring match on jdk.name (repeatable).",
    )
    parser.add_argument(
        "--jdkversion",
        metavar="val",
        action="append",
        help="Substring match on jdk.version (repeatable).",
    )
    parser.add_argument(
        "-t", "--tags",
        metavar="val",
        action="append",
        help="Match any element of tags exactly, or as a glob pattern if val contains *, ? or [..]; quote globs in the shell (repeatable).",
    )
    parser.add_argument(
        "-N", "--name",
        metavar="val",
        action="append",
        help="Match the compound run name (e.g. dacapo.h2, specjbb) as a glob pattern (*, ?, [..]); without glob characters it matches exactly; quote globs in the shell (repeatable).",
    )
    parser.add_argument(
        "-H", "--hostname",
        metavar="val",
        action="append",
        help="Exact hostname match (repeatable).",
    )
    parser.add_argument(
        "--batchid",
        metavar="val",
        action="append",
        help=(
            "Exact batchid match (repeatable). The special keyword 'last' "
            "(case-insensitive) resolves to the newest finished batch in "
            "the project's batches/."
        ),
    )
    parser.add_argument(
        "--arch",
        metavar="val",
        action="append",
        help="Exact arch match (repeatable).",
    )
    parser.add_argument(
        "--os",
        metavar="val",
        action="append",
        help="Substring match on os (repeatable).",
    )
    parser.add_argument(
        "--jvmargs",
        metavar="val",
        action="append",
        help="Substring match in any jvmargs element (repeatable).",
    )
    parser.add_argument(
        "--since",
        metavar="spec",
        action="append",
        help=(
            "Match runs on or newer than the given date/time, e.g. "
            "'2026-08-06T18:17:16' (repeatable)."
        ),
    )
    parser.add_argument(
        "--until",
        metavar="spec",
        action="append",
        help=(
            "Match runs on or older than the given date/time, e.g. "
            "'2026-08-06T18:17:16' (repeatable)."
        ),
    )
    parser.add_argument(
        "--where",
        metavar="jq-expr",
        action="append",
        help=(
            "Raw jq boolean expression evaluated against the merged run object. "
            "Requires jq on PATH. Repeatable."
        ),
    )

    # Combinators
    parser.add_argument(
        "--or",
        action="store_true",
        dest="or_",
        help="OR all active filters (default: AND).",
    )
    parser.add_argument(
        "--invert", "-v",
        action="store_true",
        help="Negate the combined filter result.",
    )

    # Positional run IDs
    parser.add_argument(
        "runids",
        metavar="RUNID",
        nargs="*",
        help="Bare run directory names to filter (optional).",
    )

    return parser


def _resolve_last_batchid(project_dir: str) -> str:
    """Return the batchid of the newest finished batch in the project's
    batches/ (by started_at).

    Exits with an error message if there is none, or it lacks a batchid.
    """
    from project import newest_finished_batch

    newest = newest_finished_batch(project_dir)
    if newest is None:
        print(
            f"Error: --batchid last: no finished batch in {project_dir}/batches",
            file=sys.stderr,
        )
        sys.exit(1)
    path, batch, _ = newest
    batch_id = batch.get("batchid")
    if not batch_id:
        print(
            f"Error: --batchid last: {path} has no batchid",
            file=sys.stderr,
        )
        sys.exit(1)
    return batch_id


def main():
    parser = _build_parser()
    args = parser.parse_args()

    # Validate --jdkmajor / --heapszf / --repeat_no / --heapsz / --duration / --startup_ms arguments early so we abort
    # with a clear message.
    from filter import (
        build_filter,
        parse_jdk_major,
        parse_heapszf,
        parse_repeat_no,
        parse_heapsz,
        parse_duration,
        parse_startup_ms,
        parse_datetime_spec,
        _is_null_sentinel,
    )
    if args.jdkmajor:
        for val in args.jdkmajor:
            if _is_null_sentinel(val):
                continue
            try:
                parse_jdk_major(val)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
    if args.heapszf:
        for val in args.heapszf:
            if _is_null_sentinel(val):
                continue
            try:
                parse_heapszf(val)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
    if args.repeat_no:
        for val in args.repeat_no:
            if _is_null_sentinel(val):
                continue
            try:
                parse_repeat_no(val)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
    if args.heapsz:
        for val in args.heapsz:
            if _is_null_sentinel(val):
                continue
            try:
                parse_heapsz(val)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
    if args.duration:
        for val in args.duration:
            if _is_null_sentinel(val):
                continue
            try:
                parse_duration(val)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
    if args.startup_ms:
        for val in args.startup_ms:
            if _is_null_sentinel(val):
                continue
            try:
                parse_startup_ms(val)
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
    if args.since:
        for val in args.since:
            if _is_null_sentinel(val):
                continue
            try:
                parse_datetime_spec(val)
            except ValueError as exc:
                print(f"Error: --since {exc}", file=sys.stderr)
                sys.exit(1)
    if args.until:
        for val in args.until:
            if _is_null_sentinel(val):
                continue
            try:
                parse_datetime_spec(val)
            except ValueError as exc:
                print(f"Error: --until {exc}", file=sys.stderr)
                sys.exit(1)

    from project import find_project, runs_dir
    from run_loader import discover_run_dirs, load_run
    from term_link import terminal_link

    try:
        project_dir = find_project()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    results_dir = runs_dir(project_dir)

    if args.batchid:
        args.batchid = [
            _resolve_last_batchid(project_dir) if val.strip().lower() == "last" else val
            for val in args.batchid
        ]

    run_ids = discover_run_dirs(args.runids, results_dir, sys.stdin.isatty())

    filt = build_filter(args)

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
        try:
            if filt(merged):
                print(terminal_link(run_dir, run_id))
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
