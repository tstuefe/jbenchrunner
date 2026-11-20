#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Parse SPECjbb2015 benchmark stdout log and output a JSON representation matching
the specjbb_result schema to stdout.

The script looks for the single summary line produced by SPECjbb2015:

    RUN RESULT: hbIR (max attempted) = 220, hbIR (settled) = 200, max-jOPS = 136, critical-jOPS = 0

Usage:
    parse_specjbb_results.py [-h | --help] <stdout.log>
"""

import argparse
import json
import os
import re
import sys


# Matches the SPECjbb2015 composite-mode result summary line.
RUN_RESULT_RE = re.compile(
    r"RUN RESULT:\s+"
    r"hbIR \(max attempted\)\s*=\s*(\d+),\s*"
    r"hbIR \(settled\)\s*=\s*(\d+),\s*"
    r"max-jOPS\s*=\s*(\d+),\s*"
    r"critical-jOPS\s*=\s*(\d+)"
)


def parse_specjbb_output(content: str) -> dict:
    """
    Parse the lines of SPECjbb2015 standard output into a specjbb_result dict.
    Raises ValueError if the expected result line is not found.
    """
    for line in content.splitlines():
        m = RUN_RESULT_RE.search(line)
        if m:
            hbirmax, hbirset, maxjops, critjops = m.groups()
            return {
                "hbirmax": int(hbirmax),
                "hbirset": int(hbirset),
                "maxjops": int(maxjops),
                "critjops": int(critjops),
            }

    raise ValueError("No 'RUN RESULT:' line found in SPECjbb output")


def main():
    parser = argparse.ArgumentParser(
        description="Parse SPECjbb2015 benchmark stdout log and print JSON specjbb_result."
    )
    parser.add_argument("logfile", help="Path to SPECjbb2015 stdout log file")
    args = parser.parse_args()

    if not os.path.exists(args.logfile):
        sys.stderr.write(f"Error: file not found: {args.logfile}\n")
        sys.exit(1)

    try:
        with open(args.logfile, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        sys.stderr.write(f"Error reading file '{args.logfile}': {e}\n")
        sys.exit(1)

    try:
        result = parse_specjbb_output(content)
    except Exception as e:
        sys.stderr.write(f"Error parsing SPECjbb output: {e}\n")
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
