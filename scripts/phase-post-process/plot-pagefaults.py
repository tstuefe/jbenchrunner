#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Plot minor and major page fault rates over time (two panels) from a
# proclog.jsonl produced by post-process-proc-log.py. Each rate covers the
# interval between two samples and is drawn as a step over it.
#
# Usage: plot-pagefaults.py <result-dir> [<result-dir> ...]
#
# For each result directory, reads proclog.jsonl and produces
# pagefaults.svg in that directory.

import sys
import os
import argparse

_COMMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "common"))
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)

from pagefaults_plot import build_rate_svg


def main():
    ap = argparse.ArgumentParser(
        description="Plot minor and major page fault rates over time from proclog.jsonl in result directories")
    ap.add_argument("result_dirs", metavar="<result-dir>", nargs='+',
                    help="one or more result directories containing proclog.jsonl")
    args = ap.parse_args()

    any_failure = False

    for result_dir in args.result_dirs:
        if not os.path.isdir(result_dir):
            print(f"Error: {result_dir}: not a directory", file=sys.stderr)
            sys.exit(1)

        if not os.path.isfile(os.path.join(result_dir, "proclog.jsonl")):
            print(f"{result_dir}: ignored (no proclog.jsonl)")
            continue

        out_svg = os.path.join(result_dir, "pagefaults.svg")
        if build_rate_svg(result_dir, out_svg):
            print(f"Written: {out_svg}")
        else:
            print(f"{result_dir}: pagefaults.svg not written (fewer than two usable samples, "
                  f"or gnuplot failed)", file=sys.stderr)
            any_failure = True

    if any_failure:
        sys.exit(1)


if __name__ == "__main__":
    import signal
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except AttributeError:
        pass
    main()
