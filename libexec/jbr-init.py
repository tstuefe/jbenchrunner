#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jbr-init — Create a project (a directory holding one result set).
#
# Writes jbr-project.json into DIR (default: the current working directory),
# creating DIR if it does not exist, and copies config/run/example.run.toml
# into it as a starting point for jbr-prepare (which uses it by default). All other jbr tools find their project
# by walking up from the working directory to the nearest jbr-project.json
# (docs/PRD.md §4). Prints the project's absolute path as the only line on
# stdout, so it can be used as: cd $(jbr-init my-project)
#
# Usage:
#   jbr-init [-m|--description TEXT] [DIR]
#
# Options:
#   -m, --description TEXT  Free-text description stored in jbr-project.json.
#   -h, --help              Show this help message and exit
#
# Fails, writing nothing, if DIR already is a project, lies inside another
# project (projects cannot be nested), or is not a directory.
#
# See docs/spec-other.md §5 for full behavior.

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
        prog="jbr-init",
        description=(
            "Create a project: write jbr-project.json into DIR (default: the\n"
            "current directory), creating DIR if needed, and copy\n"
            "config/run/example.run.toml into it. Prints the project's\n"
            "absolute path."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-m", "--description",
        metavar="TEXT",
        default=None,
        help="Free-text description stored in jbr-project.json.",
    )
    parser.add_argument(
        "dir",
        nargs="?",
        default=".",
        metavar="DIR",
        help="Project directory (default: current directory).",
    )
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()

    from init_project import init_project
    from reporoot import find_reporoot

    try:
        example_run = os.path.join(find_reporoot(__file__), "config", "run",
                                   "example.run.toml")
        path = init_project(args.dir, args.description, example_run)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(path)


if __name__ == "__main__":
    main()
