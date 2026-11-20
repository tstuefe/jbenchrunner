#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-copy — Copy existing run result directories to another location.
#
# Copies one or more results/<RUNID> directories to DIR/<RUNID>/, preserving
# the RUNID as the destination directory name. The source directory is left
# in place. Operates on any existing run directory — no info.json/
# result.json is required, unlike jbr-filt/jbr-ls (D-44) or jbr-retag.
#
# Usage:
#   jbr-copy --to DIR [--force] RUNID...
#
# Options:
#   --to DIR      Required. Destination directory; each run lands at
#                 DIR/<RUNID>/. Created (with parents) if it does not
#                 already exist.
#   --force       (a) proceed even if the project's BATCH_IN_PROGRESS.json
#                 exists; (b) replace an existing DIR/<RUNID>/ instead of
#                 aborting.
#   -h, --help    Show this help message and exit
#
# Positional:
#   RUNID...      Bare run directory names. If omitted, RUNIDs are read
#                 one per line from stdin, provided stdin is not a
#                 terminal (e.g. `jbr-filt ... | jbr-copy ...`). No
#                 fallback to all runs of the project — if neither positional
#                 args nor piped stdin supply any RUNIDs, this is a hard
#                 error.
#
# See docs/spec-other.md §3 for full behavior.

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
        prog="jbr-copy",
        description=(
            "Copy existing run result directories to another location.\n\n"
            "Each results/<RUNID> is copied to DIR/<RUNID>/; the source\n"
            "is left in place. Operates on any existing run directory;\n"
            "no info.json/result.json is required. RUNIDs come from\n"
            "positional args or piped stdin; there is no fallback to\n"
            "all runs of the project."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--to",
        metavar="DIR",
        required=True,
        dest="to_dir",
        help="Destination directory; each run lands at DIR/<RUNID>/.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Proceed even if the project's BATCH_IN_PROGRESS.json exists, and "
            "replace an existing DIR/<RUNID>/ instead of aborting."
        ),
    )
    parser.add_argument(
        "runids",
        metavar="RUNID",
        nargs="*",
        help=(
            "Bare run directory names. If omitted, read one per line "
            "from stdin (stdin must not be a terminal)."
        ),
    )
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    from movecopy import run_movecopy, validate_destination_root
    from retag import check_batch_in_progress, resolve_runids

    to_dir = os.path.abspath(args.to_dir)

    try:
        validate_destination_root(to_dir)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        runids = resolve_runids(args.runids, sys.stdin.isatty())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    from project import find_project, runs_dir

    try:
        project_dir = find_project()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    results_dir = runs_dir(project_dir)

    try:
        check_batch_in_progress(project_dir, args.force)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        run_movecopy(runids, results_dir, to_dir, args.force, mode="copy")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
