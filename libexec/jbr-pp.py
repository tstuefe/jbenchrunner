#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-pp — Post-process one or more completed benchmark run directories.
#
# Reads artefacts from each <run-directory> (stdout.log, temps.jsonl,
# proclog.jsonl, perflog.log, pidstat.log, nmt.log[.zst]) and writes an aggregated
# result.json alongside perflog.jsonl, pidstat.jsonl, and nmt.jsonl.
#
# If run-directory arguments are given, each is a direct request to process
# that run: a status other than "DONE" in run-info.json is an error for that
# run. If no arguments are given and stdin is not a terminal, bare RUNIDs are
# read from stdin instead (one per line, e.g. piped from jbr-filt) and
# resolved under the project's results/; these are treated the same as explicit arguments.
# If no arguments are given and stdin is a terminal, every subdirectory of
# the project's results/ is scanned instead: a non-DONE status there is routine (still
# running, failed, ...) and is skipped quietly. Either way, a run that
# already has a result.json is skipped unless --force is given, and one bad
# run directory does not stop the others from being processed. Run
# directories are independent of each other, so they are post-processed
# concurrently, up to --jobs at a time.
#
# Each processed run prints "Post-processed: <dir> [secs]" (no result.json
# before) or "Re-post-processed: <dir> [secs]" (result.json replaced under
# --force); skipped runs are only counted. A final summary line gives the
# counts and the total wall-clock time.
#
# Usage:
#   jbr-pp [-h | --help] [-f | --force] [-j | --jobs N] [<run-directory> ...]
#   jbr-filt ... | jbr-pp [-h | --help] [-f | --force] [-j | --jobs N]
#
# Arguments:
#   run-directory   Path to a completed run directory (e.g. results/<RUNID>/).
#                   May be given multiple times. If omitted and stdin is
#                   piped, bare RUNIDs are read from stdin instead. If
#                   omitted and stdin is a terminal, every subdirectory of
#                   the project's results/ is processed.
#
# Options:
#   -f, --force     Reprocess runs that already have a result.json
#                   (default: skip them).
#   -j, --jobs N    Number of run directories to post-process concurrently
#                   (default: 8).
#   -h, --help      Show this help message and exit.

import argparse
import concurrent.futures
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
_PHASE_PP_DIR = os.path.join(_SCRIPTS_DIR, "phase-post-process")
_COMMON_DIR = os.path.join(_SCRIPTS_DIR, "common")

for _p in (_SCRIPTS_DIR, _PHASE_PP_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="jbr-pp",
        description=(
            "Post-process one or more completed benchmark run directories.\n\n"
            "Reads artefacts from each run directory (stdout.log, temps.jsonl,\n"
            "proclog.jsonl, perflog.log, pidstat.log, nmt.log[.zst]) and writes\n"
            "result.json.\n\n"
            "A run directory named explicitly (as an argument, or as a bare\n"
            "RUNID piped in on stdin, e.g. from jbr-filt) must have status\n"
            "\"DONE\" in run-info.json, or it is reported as an error. If no\n"
            "run-directory is given and stdin is a terminal, every\n"
            "subdirectory of the project's results/ is scanned instead, and non-DONE runs\n"
            "found there are skipped quietly.\n\n"
            "Either way, a run that already has a result.json is skipped\n"
            "unless --force is given. Run directories are independent of\n"
            "each other and are post-processed concurrently, up to --jobs\n"
            "at a time."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "run_dirs",
        metavar="run-directory",
        nargs="*",
        help=(
            "Path to a completed run directory (e.g. results/<RUNID>/). "
            "May be repeated. If omitted and stdin is piped, bare RUNIDs "
            "are read from stdin instead (one per line). If omitted and "
            "stdin is a terminal, every subdirectory of the project's results/ is "
            "processed."
        ),
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Reprocess runs that already have a result.json.",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=8,
        metavar="N",
        help="Number of run directories to post-process concurrently (default: 8).",
    )
    return parser


def _run_status(run_dir):
    """Return the status field from run-info.json, or None if unavailable."""
    run_info_path = os.path.join(run_dir, "run-info.json")
    try:
        with open(run_info_path, "r", encoding="utf-8") as f:
            run_info = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return run_info.get("status")


def _discover_run_dirs(results_dir):
    if not os.path.isdir(results_dir):
        return []
    entries = [
        os.path.join(results_dir, name)
        for name in sorted(os.listdir(results_dir))
        if os.path.isdir(os.path.join(results_dir, name))
    ]
    return entries


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    from post_process import post_process_run

    # Directories named explicitly on the command line, or piped in on
    # stdin as bare RUNIDs (e.g. from jbr-filt), are a direct request to
    # process them: a non-DONE status is an error. Directories picked up
    # by the default results/ scan are processed opportunistically: a
    # non-DONE status (still running, failed, ...) is routine and skipped.
    explicit = bool(args.run_dirs) or not sys.stdin.isatty()

    if args.run_dirs:
        run_dirs = args.run_dirs
    else:
        from project import find_project, runs_dir

        try:
            project_dir = find_project()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        results_dir = runs_dir(project_dir)

        if not sys.stdin.isatty():
            run_ids = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
            run_dirs = [os.path.join(results_dir, run_id) for run_id in run_ids]
        else:
            run_dirs = _discover_run_dirs(results_dir)
            if not run_dirs:
                print(f"No run directories found in {results_dir}.")
                sys.exit(0)

    start = time.monotonic()
    new = 0
    reprocessed = 0
    skipped = 0
    failed = 0

    # Cheap pre-checks (existence, already-processed, status) are done
    # up front, sequentially; only the actual post-processing work — which
    # is independent per run directory — is farmed out to a thread pool.
    to_process = []

    for run_dir in run_dirs:
        if not os.path.isdir(run_dir):
            print(f"Error: {run_dir}: not a directory", file=sys.stderr)
            failed += 1
            continue

        result_path = os.path.join(run_dir, "result.json")
        if not args.force and os.path.exists(result_path):
            skipped += 1
            continue

        if not explicit:
            status = _run_status(run_dir)
            if status is not None and status != "DONE":
                skipped += 1
                continue

        to_process.append((run_dir, os.path.exists(result_path)))

    def _process_one(item):
        run_dir, had_result = item
        t0 = time.monotonic()
        try:
            post_process_run(run_dir)
        except (ValueError, RuntimeError) as exc:
            return (run_dir, had_result, False, str(exc), time.monotonic() - t0)
        return (run_dir, had_result, True, None, time.monotonic() - t0)

    if to_process:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
            for run_dir, had_result, success, err, secs in executor.map(_process_one, to_process):
                if success and had_result:
                    print(f"Re-post-processed: {run_dir} [{secs:.1f}s]")
                    reprocessed += 1
                elif success:
                    print(f"Post-processed: {run_dir} [{secs:.1f}s]")
                    new += 1
                else:
                    print(f"Error: {run_dir}: {err}", file=sys.stderr)
                    failed += 1

    print(
        f"{new} newly post-processed, {reprocessed} re-post-processed, "
        f"{skipped} skipped, {failed} failed [{time.monotonic() - start:.1f}s]"
    )

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
