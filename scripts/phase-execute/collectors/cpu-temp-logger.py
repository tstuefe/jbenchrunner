#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-run/collectors/cpu-temp-logger.py
# Periodically reads CPU temperature and writes JSON lines to stdout.
# Usage: cpu-temp-logger.py [-i SECONDS] [-n NUM] <pid>
# The temperature is machine-wide; <pid> (the JVM) is only a liveness anchor: the
# logger exits when that process no longer exists (PRD.md D-59).
# Requires Python 3.

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pidwait import pid_alive, sleep_while_alive  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
import collector_timestamp  # noqa: E402

INTERVAL = 10  # seconds
SCRIPT_DIR = Path(__file__).resolve().parent
READ_CPU_TEMP = SCRIPT_DIR / "read-cpu-temp.sh"


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="cpu-temp-logger.py",
        description="Periodically read CPU temperature and write JSON lines to stdout.",
    )
    parser.add_argument("pid", type=int, help="PID whose disappearance ends the logger")
    parser.add_argument(
        "-n", "--num",
        type=int, default=None, metavar="NUM",
        help="Maximum number of iterations (default: unlimited)",
    )
    parser.add_argument(
        "-i", "--interval",
        type=float, default=INTERVAL, metavar="SECONDS",
        help=f"Seconds between samples (default: {INTERVAL})",
    )
    return parser


def read_cpu_temp():
    result = subprocess.run(
        ["bash", str(READ_CPU_TEMP)],
        check=False,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(stderr or f"read-cpu-temp.sh failed with exit code {result.returncode}")

    return result.stdout.strip()


def collect_sample():
    return {
        "timestamp": collector_timestamp.now(),
        "temperature": read_cpu_temp(),
    }


def main():
    args = build_arg_parser().parse_args()
    pid = args.pid

    if pid <= 0:
        print(f"cpu-temp-logger.py: invalid pid: {pid}", file=sys.stderr)
        sys.exit(1)

    if not pid_alive(pid):
        print(f"cpu-temp-logger.py: process {pid} does not exist", file=sys.stderr)
        sys.exit(1)

    # First sample is taken immediately, before any sleep (fail-fast rule,
    # spec-execute.md 5.1); the liveness check comes after it.
    iteration = 0
    while args.num is None or iteration < args.num:
        sample = collect_sample()
        print(json.dumps(sample), flush=True)
        iteration += 1
        if args.num is None or iteration < args.num:
            if not sleep_while_alive(pid, args.interval):
                print(f"Process {pid} no longer exists, exiting", file=sys.stderr)
                break


if __name__ == "__main__":
    main()
