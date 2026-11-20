#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-move-batch — Move whole batches to another project.
#
# Moves each batches/<BATCHID>/ of the current project, together with every
# run directory named by a result_dir in its batch.json/batch-aborted.json,
# into the project DIR. DIR is created as a project (like jbr-init) if it
# does not exist; if it exists, it must be a project. All checks run first;
# on any failure nothing is created or moved.
#
# Usage:
#   jbr-move-batch --to DIR [-m|--description TEXT] [-n|--dry-run] BATCHID...
#
# Options:
#   --to DIR                Required. Target project directory.
#   -m, --description TEXT  Description for a newly created target project.
#   -n, --dry-run           Check and print what would be done; change nothing.
#   -h, --help              Show this help message and exit
#
# See docs/spec-other.md §6 for full behavior.

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
        prog="jbr-move-batch",
        description=(
            "Move whole batches (batch information and run result\n"
            "directories) from the current project into the project DIR,\n"
            "which is created if it does not exist."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--to",
        metavar="DIR",
        required=True,
        dest="to_dir",
        help="Target project directory; created as a project if missing.",
    )
    parser.add_argument(
        "-m", "--description",
        metavar="TEXT",
        default=None,
        help="Description for a newly created target project.",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        default=False,
        help="Run all checks and print what would be done, but change nothing.",
    )
    parser.add_argument(
        "batchids",
        metavar="BATCHID",
        nargs="+",
        help="IDs of the batches to move.",
    )
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    from project import find_project
    from move_batch import run_move_batch

    try:
        source = find_project()
        run_move_batch(source, args.to_dir, args.batchids,
                       args.description, args.dry_run)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
