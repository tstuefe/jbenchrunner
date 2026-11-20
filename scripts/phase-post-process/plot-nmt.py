#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Plot a stacked NMT malloc breakdown over time from an nmt.jsonl produced
# by post-process-nmt-log.py.
#
# Usage: plot-nmt.py <result-dir> [<result-dir> ...]
#
# For each result directory, reads nmt.jsonl and produces nmt-malloc.svg in
# that directory. malloc values in nmt.jsonl are already in MB.

import sys
import os
import argparse

_COMMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "common"))
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)

from gnuplot_utils import plot_title, temp_dat_files, run_gnuplot
from nmt_plot import parse, build_rows, build_gnuplot_script


def resolve_result_dir(path):
    """
    Validate that path is a directory containing nmt.jsonl.
    Returns the path to nmt.jsonl, or None if not found.
    Exits with error for non-existent directories.
    """
    if not os.path.exists(path):
        print(f"Error: {path}: no such file or directory", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(path):
        print(f"Error: {path}: not a directory", file=sys.stderr)
        sys.exit(1)

    jsonl_path = os.path.join(path, "nmt.jsonl")
    if not os.path.isfile(jsonl_path):
        return None

    return jsonl_path


def main():
    ap = argparse.ArgumentParser(
        description="Plot a stacked NMT malloc breakdown over time from nmt.jsonl in result directories")
    ap.add_argument("result_dirs", metavar="<result-dir>", nargs='+',
                    help="one or more result directories containing nmt.jsonl")
    args = ap.parse_args()

    any_failure = False

    for result_dir in args.result_dirs:
        jsonl_path = resolve_result_dir(result_dir)

        if jsonl_path is None:
            print(f"{result_dir}: ignored (no nmt.jsonl)")
            continue

        points = parse(jsonl_path)

        if not points:
            print(f"{result_dir}: ignored (no data in nmt.jsonl)")
            continue

        title = plot_title("Hotspot Malloc Usage (measured by NMT)", result_dir)
        out_svg = os.path.join(result_dir, "nmt-malloc.svg")

        rows = build_rows(points)

        with temp_dat_files() as make_dat:
            dat_path = make_dat(rows)
            gp = build_gnuplot_script(dat_path, out_svg, title)
            if not run_gnuplot(gp):
                any_failure = True
                continue
            print(f"Written: {out_svg}")

    if any_failure:
        sys.exit(1)


if __name__ == "__main__":
    import signal
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except AttributeError:
        pass
    main()
