#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-execute — Execute a prepared benchmark batch.
#
# Reads BATCH_IN_PROGRESS.json of the current project (found from the working
# directory, see docs/PRD.md §4; written by jbr-prepare) and runs all
# benchmark entries that are not yet completed, in order. On completion the
# batch file is moved to batches/<BATCHID>/batch.json. While the batch is
# active, ~/.local/state/jbenchrunner/active-batch links to it, and no batch
# of another project can be prepared or executed.
#
# Usage:
#   jbr-execute [--shutdown] [-v|--verbose]
#   jbr-execute --abort
#   jbr-execute --help
#
# Options:
#   -v, --verbose     Verbose output: print exact command line for benchmark,
#                     its end status, and command lines for individual collectors.
#   --cooldown-secs N Sleep N seconds between runs (not before the first run),
#                     to let CPU temperatures settle. Default 10; pass 0 to
#                     disable. Per-invocation; not stored in the batch.
#   --timeout-secs N  Kill a run's JVM (SIGTERM, then SIGKILL if still alive
#                     after 3s) if it has not exited after N seconds, and
#                     record it as FAILED with status_info "timeout". Meant
#                     for hung JVMs (e.g. a non-recovering GC under a too-low
#                     heap size) that would otherwise block the batch
#                     forever. Default: disabled (no timeout). Per-invocation;
#                     not stored in the batch.
#   --shutdown        On full batch completion (not on Ctrl-C), warn logged-in
#                     users (wall), wait 5 minutes, then shut the machine down.
#                     Requires the batch to be running in --sudo mode (see
#                     jbr-prepare --sudo). Per-invocation; not stored in the
#                     batch. Cannot be combined with --abort. Linux only.
#   --abort           Abandon the current batch: move BATCH_IN_PROGRESS.json to
#                     batches/<BATCHID>/batch-aborted.json. Also removes a
#                     dangling active-batch link.
#                     Cannot be combined with --shutdown.
#
# Sudo mode (running the few root commands of machine stabilization/reset and
# --shutdown via 'sudo -n', granted by the rules of config/jbenchrunner.sudoers)
# is decided once at 'jbr-prepare --sudo' time and stored in the batch;
# jbr-execute reads it from there, checks the rules at batch start and never
# prompts for a password — there is no --sudo flag here.
#
# Whether to stabilize the machine (prepare-for-benchmarks.sh once at batch
# start, reset-machine.sh before every run, both via sudo) is likewise not a
# CLI flag here — it is the explicit opt-in 'jbr-prepare --stabilize-machine',
# read from the batch. A stabilized machine is less secure until reboot, so
# jbr-execute ends with a reboot recommendation unless --shutdown ran.

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
_PHASE_EXECUTE_DIR = os.path.join(_SCRIPTS_DIR, "phase-execute")

for _p in (_SCRIPTS_DIR, _PHASE_EXECUTE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="jbr-execute",
        description=(
            "Execute a prepared benchmark batch.\n\n"
            "Reads the current project's BATCH_IN_PROGRESS.json and runs all entries that are\n"
            "not yet completed, in order.\n\n"
            "Use --abort to abandon the current batch without running any benchmarks."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output: print exact command line for benchmark, its end status, and command lines for individual collectors.",
    )
    parser.add_argument(
        "--cooldown-secs",
        type=int,
        default=10,
        metavar="N",
        help="Sleep N seconds between runs (not before the first run), to let "
             "CPU temperatures settle. Default 10; pass 0 to disable.",
    )
    parser.add_argument(
        "--timeout-secs",
        type=int,
        default=None,
        metavar="N",
        help="Kill a run's JVM if it has not exited after N seconds (SIGTERM, "
             "then SIGKILL if still alive after 3s), and record it as FAILED "
             "with status_info 'timeout'. Default: disabled.",
    )
    parser.add_argument(
        "--abort",
        action="store_true",
        help="Abandon the current batch: move BATCH_IN_PROGRESS.json to batches/<BATCHID>/batch-aborted.json.",
    )
    parser.add_argument(
        "--shutdown",
        action="store_true",
        help="On full batch completion, warn logged-in users, wait 5 minutes, "
             "then shut the machine down. Requires --sudo mode. Linux only.",
    )
    return parser


def _warn_if_root():
    if os.getuid() == 0:
        border = "*" * 68
        print(border, file=sys.stderr)
        print("* WARNING: jbr-execute is running under sudo / as root.    *", file=sys.stderr)
        print("* This is almost certainly unnecessary.                        *", file=sys.stderr)
        print("*                                                               *", file=sys.stderr)
        print("* Sudo mode is decided at 'jbr-prepare --sudo' time and     *", file=sys.stderr)
        print("* stored in the batch; run jbr-execute as a normal user —   *", file=sys.stderr)
        print("* it will prompt for the password itself if the batch needs it.*", file=sys.stderr)
        print(border, file=sys.stderr)


def main():
    _warn_if_root()
    parser = _build_parser()
    args = parser.parse_args()

    if args.abort and args.shutdown:
        print("Error: --abort cannot be combined with --shutdown", file=sys.stderr)
        sys.exit(1)

    if args.cooldown_secs < 0:
        print("Error: --cooldown-secs must not be negative", file=sys.stderr)
        sys.exit(1)

    if args.timeout_secs is not None and args.timeout_secs <= 0:
        print("Error: --timeout-secs must be positive", file=sys.stderr)
        sys.exit(1)

    try:
        from execute import run_execute  # scripts/phase-execute/ is on sys.path
        run_execute({
            "abort": args.abort,
            "shutdown": args.shutdown,
            "verbose": args.verbose,
            "cooldown_secs": args.cooldown_secs,
            "timeout_secs": args.timeout_secs,
        })
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
