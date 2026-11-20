#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-status — Display the status of the current or last benchmark batch.
#
# Works on the current project (found from the working directory, see
# docs/PRD.md §4). Reads its BATCH_IN_PROGRESS.json (current batch in
# progress) or, failing that, the newest batches/<BATCHID>/batch.json or
# batch-aborted.json (last finished batch) and prints a formatted status
# table. If the active-batch link points to another project, a final note
# says so. Never modifies any file.
#
# Usage:
#   jbr-status [-a|--all] [--list-runids]
#   jbr-status --help
#
# Options:
#   -a, --all        Also show the JVM path (java_home) and JVM args columns.
#   --list-runids    Only print the run IDs, one per line (no table, no header).

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
_PHASE_EXECUTE_DIR = os.path.join(_SCRIPTS_DIR, "phase-execute")
_COMMON_DIR = os.path.join(_SCRIPTS_DIR, "common")

for _p in (_SCRIPTS_DIR, _PHASE_EXECUTE_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="jbr-status",
        description=(
            "Display the status of the current or last benchmark batch.\n\n"
            "Works on the current project. Reads its BATCH_IN_PROGRESS.json if\n"
            "present, otherwise the newest finished batch in batches/. Prints a\n"
            "formatted table of run entries with their current status. Read-only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        dest="show_all",
        help="Also show the JVM path (java_home) and JVM args columns.",
    )
    parser.add_argument(
        "--list-runids",
        action="store_true",
        help=(
            "Only print the run IDs, one per line, plain text (no table, "
            "no header)."
        ),
    )
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    try:
        import project as _project
        from status import format_batch_table, list_run_ids
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        project_dir = _project.find_project()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    note = None
    if _project.active_batch_conflict(project_dir) is not None:
        note = f"Note: active batch in {_project.read_active_batch()}"

    batch_file = _project.batch_file(project_dir)
    if os.path.exists(batch_file):
        label = "Current batch in progress:"
        try:
            with open(batch_file, encoding="utf-8") as fh:
                batch = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        newest = _project.newest_finished_batch(project_dir)
        if newest is None:
            print("No batch found.")
            if note:
                print(note)
            sys.exit(0)
        _, batch, aborted = newest
        label = "Last aborted batch:" if aborted else "Last finished batch:"

    if args.list_runids:
        for run_id in list_run_ids(batch):
            print(run_id)
        if note:
            print(note, file=sys.stderr)
        return

    try:
        table = format_batch_table(batch, project_dir, show_all=args.show_all)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(label)
    print(table)
    if note:
        print(note)


if __name__ == "__main__":
    main()
