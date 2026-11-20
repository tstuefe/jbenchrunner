#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-prepare — Prepare a benchmark batch for execution.
#
# Reads one or more .run.toml files, validates all referenced configuration,
# expands benchgroup and heap-sweep entries, assembles JVM flags, and writes
# BATCH_IN_PROGRESS.json into the current project (found from the working
# directory, see docs/PRD.md §4), plus frozen copies of the .run.toml files in
# batches/<BATCHID>/, ready for jbr-execute. Multiple files are
# processed in the order given: each file's own batch-level settings apply
# only to its own entries, and the resulting run lists are concatenated.
#
# Usage:
#   jbr-prepare [options] [<file.run.toml> ...]
#   jbr-prepare --help
#
# Without a .run.toml argument, <project>/example.run.toml is used (jbr-init
# puts a copy there).
#
# Options:
#   --jvmargs "<flags>"          Apply flags (space-separated string) to all runs.
#   --jvmarg <flag>              Single flag, repeatable. Use for flags with spaces.
#   --tags <tag,...>             Comma-separated tags merged with .run.toml tags.
#   --collector-interval <secs>  Sampling interval for collectors; overrides .run.toml's
#                                 collectors.interval field (default: 10 if neither is set).
#   --dacapo-iterations <N>      Warmup iteration count for every dacapo run in the batch;
#                                 overrides .run.toml's dacapo_iterations field, which in turn
#                                 overrides each .bench.toml's 'iterations' field.
#   --repeat <N>                 Repeat each run entry N times, interleaved by round; overrides
#                                 .run.toml's repeat field (default: 1 if neither is set).
#   --sudo                        Mark the batch as sudo-mode: jbr-execute may run the root
#                                 commands of machine stabilization and --shutdown, granted
#                                 by the rules of config/jbenchrunner.sudoers.
#   --stabilize-machine           Let jbr-execute stabilize the machine (via
#                                 prepare-for-benchmarks.sh) at batch start and reset it
#                                 (reset-machine.sh) before every run. Weakens security until
#                                 reboot, hence an explicit opt-in (default off). Requires
#                                 --sudo.

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
_PHASE_PREPARE_DIR = os.path.join(_SCRIPTS_DIR, "phase-prepare")

for _p in (_SCRIPTS_DIR, _PHASE_PREPARE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="jbr-prepare",
        description=(
            "Prepare a benchmark batch for execution.\n\n"
            "Reads one or more .run.toml files, resolves all referenced configuration,\n"
            "expands benchgroup and heap-sweep entries, assembles JVM flags,\n"
            "and writes BATCH_IN_PROGRESS.json into the current project\n"
            "(found from the working directory), ready for jbr-execute.\n\n"
            "Multiple files are processed in the order given: each file's own\n"
            "batch-level settings (jvmargs, tags, heapsize_factors, etc.) apply\n"
            "only to its own entries, and the resulting run lists are concatenated.\n\n"
            "Without a .run.toml argument, example.run.toml in the project root is used."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "run_json",
        metavar="<file.run.toml>",
        nargs="*",
        help="Path(s) to the .run.toml file(s) describing the benchmark run(s). "
             "Multiple files are processed in the order given. "
             "Default: example.run.toml in the project root.",
    )
    parser.add_argument(
        "--jvmargs",
        metavar='"<flags>"',
        default="",
        help="Space-separated JVM flags applied to all runs (after run-entry flags).",
    )
    parser.add_argument(
        "--jvmarg",
        metavar="<flag>",
        action="append",
        default=[],
        help="Single JVM flag, repeatable. Use when a flag value contains spaces.",
    )
    parser.add_argument(
        "--tags",
        metavar="<tag,...>",
        default="",
        help="Comma-separated tags merged with tags from .run.toml.",
    )
    parser.add_argument(
        "--collector-interval",
        metavar="<secs>",
        type=int,
        default=None,
        help="Sampling interval in seconds for all collectors. Overrides "
             "'collectors.interval' from .run.toml; default: 10 if neither is set.",
    )
    parser.add_argument(
        "--dacapo-iterations",
        metavar="<N>",
        type=int,
        default=None,
        help="Warmup iteration count for every dacapo run in the batch. Overrides "
             "'dacapo_iterations' from .run.toml, which in turn overrides each "
             "bench's own 'iterations' field.",
    )
    parser.add_argument(
        "--repeat",
        metavar="<N>",
        type=int,
        default=None,
        help="Repeat each run entry N times, interleaved by round. Overrides "
             "'repeat' from .run.toml; default: 1 if neither is set.",
    )
    parser.add_argument(
        "--sudo",
        action="store_true",
        help="Mark the batch as sudo-mode: jbr-execute may run the root commands "
             "of machine stabilization and --shutdown, granted by the rules of "
             "config/jbenchrunner.sudoers.",
    )
    parser.add_argument(
        "--stabilize-machine",
        action="store_true",
        help="Let jbr-execute stabilize the machine (prepare-for-benchmarks.sh: ASLR off, "
             "swap off, services stopped, ...) at batch start and reset it "
             "(reset-machine.sh) before every run. Weakens security until reboot; "
             "default off. Requires --sudo.",
    )
    return parser


def _warn_if_root():
    if os.getuid() == 0:
        border = "*" * 68
        print(border, file=sys.stderr)
        print("* WARNING: jbr-prepare is running under sudo / as root.    *", file=sys.stderr)
        print("* This is almost certainly unnecessary.     *", file=sys.stderr)
        print("*                                                               *", file=sys.stderr)
        print("* If this batch needs elevated privileges for perf counters,   *", file=sys.stderr)
        print("* run jbr-prepare as a normal user and pass --sudo instead; *", file=sys.stderr)
        print("* jbr-execute will prompt for the password when it runs it. *", file=sys.stderr)
        print(border, file=sys.stderr)


def main():
    _warn_if_root()
    parser = _build_parser()
    args = parser.parse_args()

    # Assemble jvmargs: split --jvmargs string, then append --jvmarg items
    jvmargs = args.jvmargs.split() if args.jvmargs else []
    jvmargs += args.jvmarg

    # Parse tags list
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    if args.repeat is not None and args.repeat < 1:
        print("Error: --repeat must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    if args.collector_interval is not None and args.collector_interval < 1:
        print("Error: --collector-interval must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    if args.dacapo_iterations is not None and args.dacapo_iterations < 1:
        print("Error: --dacapo-iterations must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    cli_options = {
        "jvmargs": jvmargs,
        "tags": tags,
        "collector_interval": args.collector_interval,
        "dacapo_iterations": args.dacapo_iterations,
        "repeat": args.repeat,
        "sudo_mode": args.sudo,
        "stabilize_machine": args.stabilize_machine,
    }

    try:
        from prepare import run_prepare  # scripts/phase-prepare/ is on sys.path
        run_files = args.run_json
        if not run_files:
            from common import project as _project
            default_run = os.path.join(_project.find_project(), "example.run.toml")
            if not os.path.exists(default_run):
                raise FileNotFoundError(
                    f"no .run.toml file given and no default '{default_run}' found"
                )
            print(f"Note: no .run.toml file given; using '{default_run}'.")
            run_files = [default_run]
        run_prepare(run_files, cli_options)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
