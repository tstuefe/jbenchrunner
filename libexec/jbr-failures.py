#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-failures — List result directories of runs that did not complete.
#
# Scans the project's results/ for run directories whose run-info.json has status FAILED
# or STOPPED, or whose info.json / run-info.json is missing or unreadable
# (shown as status "unknown"). These are the directories jbr-filt/jbr-ls
# never see, since they have no result.json. Read-only.
#
# By default prints only the RUNIDs, one per line, sorted by time, suitable
# for piping (e.g. `jbr-failures | jbr-move --to DIR`).
#
# Usage:
#   jbr-failures [-a|--all]
#
# Options:
#   -a, --all     Print a table with runid, time, duration, status,
#                 status_info, exit_code, name, jdkname and tags instead.
#   -h, --help    Show this help message and exit
#
# See docs/spec-other.md §4 for full behavior.

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
_PHASE_OTHER_DIR = os.path.join(_SCRIPTS_DIR, "phase-other")
_COMMON_DIR = os.path.join(_SCRIPTS_DIR, "common")

for _p in (_SCRIPTS_DIR, _PHASE_OTHER_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="jbr-failures",
        description=(
            "List result directories of runs that did not complete.\n\n"
            "Scans the project's results/ for runs with status FAILED or STOPPED, or\n"
            "whose info.json/run-info.json is missing or unreadable\n"
            "(status \"unknown\"). Prints the RUNIDs, one per line,\n"
            "sorted by time. Read-only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        default=False,
        help=(
            "Print a table with runid, time, duration, status, "
            "status_info, exit_code, name, jdkname and tags."
        ),
    )
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    from failures import format_table, scan_failures
    from project import find_project, runs_dir

    try:
        project_dir = find_project()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    results_dir = runs_dir(project_dir)
    runs = scan_failures(results_dir)

    if args.all:
        print(format_table(runs, results_dir))
    else:
        for run in runs:
            print(run["runid"])


if __name__ == "__main__":
    main()
