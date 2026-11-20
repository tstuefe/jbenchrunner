#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-run/collectors/proc-logger.py
# Periodically dumps the raw /proc/<pid>/stat and /proc/<pid>/status to stdout.
# Requires Python 3 and a kernel >= 5.
#
# Each sample is:
#
#   <ISO-8601 timestamp>
#   === /proc/<pid>/stat ===
#   <raw contents>
#   === /proc/<pid>/status ===
#   <raw contents>
#
# Parsing into proclog.jsonl is done by post-process-proc-log.py.

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pidwait import pid_alive, sleep_while_alive  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
import collector_timestamp  # noqa: E402

INTERVAL = 10  # seconds


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="proc-logger.py",
        description="Periodically dump the raw /proc/<pid>/stat and /proc/<pid>/status.",
    )
    parser.add_argument("pid", type=int, help="PID to monitor")
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


def read_proc_file(pid, name):
    """Raw contents of /proc/<pid>/<name>, guaranteed to end in a newline."""
    with open(f"/proc/{pid}/{name}", "r") as f:
        raw = f.read()
    return raw if raw.endswith("\n") else raw + "\n"


def main():
    args = build_arg_parser().parse_args()
    pid = args.pid

    if pid <= 0:
        print(f"proc-logger.py: invalid pid: {pid}", file=sys.stderr)
        sys.exit(1)

    if not pid_alive(pid):
        print(f"proc-logger.py: process {pid} does not exist", file=sys.stderr)
        sys.exit(1)

    iteration = 0
    while pid_alive(pid) and (args.num is None or iteration < args.num):
        try:
            stat   = read_proc_file(pid, "stat")
            status = read_proc_file(pid, "status")
        except (FileNotFoundError, ProcessLookupError):
            # Process vanished between the alive-check and the read
            break

        timestamp = collector_timestamp.now()

        # One write per sample, so a killed collector rarely leaves a partial one.
        sys.stdout.write(
            f"{timestamp}\n"
            f"=== /proc/{pid}/stat ===\n{stat}"
            f"=== /proc/{pid}/status ===\n{status}"
        )
        sys.stdout.flush()
        iteration += 1
        if args.num is None or iteration < args.num:
            sleep_while_alive(pid, args.interval)

    if not pid_alive(pid):
        print(f"Process {pid} no longer exists, exiting", file=sys.stderr)


if __name__ == "__main__":
    main()
