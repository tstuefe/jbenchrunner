#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-retag — Add, remove, or list tags on existing run result directories.
#
# Mutates the `tags` field of a run's info.json (or, with no -a/-r, prints
# the current tags). Not read-only, unlike jbr-filt/jbr-ls — but scoped the
# same way: only run directories with a result.json (D-44) are in scope;
# others are silently skipped.
#
# Usage:
#   jbr-retag [-a|--add TAG]... [-r|--remove TAG]... [--force] RUNID...
#   jbr-retag [--force] RUNID...    # no -a/-r: print current tags
#
# Options:
#   -a, --add TAG      Add TAG to the run's tag set (repeatable)
#   -r, --remove TAG   Remove TAG from the run's tag set (repeatable)
#   --force            Proceed even if the project's BATCH_IN_PROGRESS.json exists
#   -h, --help         Show this help message and exit
#
# Positional:
#   RUNID...           Bare run directory names. If omitted, RUNIDs are
#                       read one per line from stdin, provided stdin is
#                       not a terminal (e.g. `jbr-filt ... | jbr-retag
#                       ...`). Unlike jbr-filt/jbr-ls there is no "all runs of
#                       the project" fallback: if neither positional args
#                       nor piped stdin supply any RUNIDs, this is a
#                       hard error, since an omitted RUNID list must
#                       never silently retarget every run in the
#                       repository.
#
# See docs/spec-other.md for full behavior.

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
_PHASE_ANALYZE_DIR = os.path.join(_SCRIPTS_DIR, "phase-analyze")
_PHASE_OTHER_DIR = os.path.join(_SCRIPTS_DIR, "phase-other")
_COMMON_DIR = os.path.join(_SCRIPTS_DIR, "common")

for _p in (_SCRIPTS_DIR, _PHASE_ANALYZE_DIR, _PHASE_OTHER_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="jbr-retag",
        description=(
            "Add, remove, or list tags on existing run result directories.\n\n"
            "With -a/-r, mutates the tags field of each given run's\n"
            "info.json. With neither, prints the current tags of each\n"
            "given run as a table. RUNIDs come from positional args or\n"
            "piped stdin; there is no fallback to all runs of the project."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-a", "--add",
        metavar="TAG",
        action="append",
        dest="add",
        help="Add TAG to the run's tag set (repeatable).",
    )
    parser.add_argument(
        "-r", "--remove",
        metavar="TAG",
        action="append",
        dest="remove",
        help="Remove TAG from the run's tag set (repeatable).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Proceed even if the project's BATCH_IN_PROGRESS.json exists.",
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


def _run_list(runids, results_dir):
    from run_loader import load_run
    from term_link import terminal_link
    from listing import extract_cell

    rows = []
    for run_id in runids:
        run_dir = os.path.join(results_dir, run_id)
        try:
            merged = load_run(run_dir)
        except ValueError as exc:
            link = terminal_link(run_dir, run_id, stream=sys.stderr)
            print(f"Warning: skipping {link}: {exc}", file=sys.stderr)
            continue
        if merged is None:
            continue
        rows.append((run_id, run_dir, merged))

    if not rows:
        return

    cols = ["runid", "tags"]
    cell_matrix = [[extract_cell(c, merged) for c in cols] for _, _, merged in rows]

    widths = [len(c) for c in cols]
    for row_cells in cell_matrix:
        for i, val in enumerate(row_cells):
            widths[i] = max(widths[i], len(val))

    for (run_id, run_dir, _merged), row_cells in zip(rows, cell_matrix):
        row_cells[0] = terminal_link(run_dir, row_cells[0].ljust(widths[0]))

    fmt = "  ".join("{{:<{}}}".format(w) for w in widths)
    print(fmt.format(*cols).rstrip())
    print("  ".join("-" * w for w in widths))
    for row_cells in cell_matrix:
        print(fmt.format(*row_cells).rstrip())


def _run_mutate(runids, results_dir, adds, removes):
    from retag import load_info_json, atomic_write_json, apply_tag_ops
    from term_link import terminal_link

    for run_id in runids:
        run_dir = os.path.join(results_dir, run_id)
        result_path = os.path.join(run_dir, "result.json")
        if not os.path.exists(result_path):
            continue  # D-44: silently skip un-post-processed runs

        info_path = os.path.join(run_dir, "info.json")
        try:
            info = load_info_json(run_dir)
        except (OSError, ValueError) as exc:
            link = terminal_link(run_dir, run_id, stream=sys.stderr)
            print(f"Warning: skipping {link}: {exc}", file=sys.stderr)
            continue

        current_tags = info.get("tags", [])
        new_tags, lines = apply_tag_ops(current_tags, adds, removes)

        link = terminal_link(run_dir, run_id)
        if not lines:
            print(f"{link}: unchanged")
            continue

        for line in lines:
            print(f"{link}: {line}")

        if new_tags != current_tags:
            info["tags"] = new_tags
            atomic_write_json(info_path, info)


def main():
    parser = _build_parser()
    args = parser.parse_args()

    adds = args.add or []
    removes = args.remove or []

    from retag import validate_tag, check_batch_in_progress, resolve_runids

    for tag in adds + removes:
        try:
            validate_tag(tag)
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

    if adds or removes:
        try:
            check_batch_in_progress(project_dir, args.force)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        _run_mutate(runids, results_dir, adds, removes)
    else:
        _run_list(runids, results_dir)


if __name__ == "__main__":
    main()
