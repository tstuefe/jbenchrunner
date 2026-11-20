#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# compress-files.py — back-fill compression of the GC log, heapsizes.txt and arena.log in
# existing run directories.
#
# jbr-pp now zstd-compresses gc-all.log, gc.log, heapsizes.txt and arena.log once the
# arena report and the heap sizes plot are done (spec-post-process.md §1). Run directories post-processed before that, or
# never post-processed (e.g. FAILED runs), still hold them uncompressed. This script
# compresses them the same way, without re-running the post-processing.
#
# Walks the given directory (default: the current project's results/) recursively. Every directory
# holding a run-info.json is a run directory; runs whose status is RUNNING are skipped
# (the JVM may still be writing its logs), all others are compressed regardless of
# status. Files already compressed are left untouched, so the script can be re-run
# safely. A later jbr-pp reads the compressed GC log and arena.log.
#
# Usage:
#   compress-files.py [-h] [-n] [DIR]

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", "phase-post-process"))

from common.collector_files import compress_file  # noqa: E402
from common.project import find_project, runs_dir  # noqa: E402
from post_process import COMPRESSED_AFTER_USE  # noqa: E402


def _run_status(dirpath):
    """Return the status in *dirpath*/run-info.json ("" if absent/unreadable)."""
    try:
        with open(os.path.join(dirpath, "run-info.json"), encoding="utf-8") as f:
            return json.load(f).get("status", "")
    except (OSError, ValueError, AttributeError):
        return ""


def _fmt_mb(nbytes):
    return f"{nbytes / (1024 * 1024):.1f} MB"


def main():
    parser = argparse.ArgumentParser(
        prog="compress-files.py",
        description=(
            "zstd-compress " + ", ".join(COMPRESSED_AFTER_USE) + " in run directories, "
            "as jbr-pp does after post-processing them. Recurses into DIR; runs with "
            "status RUNNING are skipped."
        ),
    )
    parser.add_argument(
        "dir",
        metavar="DIR",
        nargs="?",
        help="Directory to scan recursively (default: the current project's results/).",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Only report what would be compressed.",
    )
    args = parser.parse_args()

    root = args.dir or runs_dir(find_project())
    if not os.path.isdir(root):
        print(f"Error: not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    compressed = skipped_runs = failed = 0
    bytes_before = bytes_after = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if "run-info.json" not in filenames:
            continue
        if _run_status(dirpath) == "RUNNING":
            print(f"{dirpath}: skipped (RUNNING)")
            skipped_runs += 1
            continue
        for name in COMPRESSED_AFTER_USE:
            if name not in filenames:
                continue
            path = os.path.join(dirpath, name)
            size = os.path.getsize(path)
            if args.dry_run:
                print(f"{path}: {_fmt_mb(size)}")
                compressed += 1
                bytes_before += size
                continue
            err = compress_file(path)
            if err:
                print(f"Warning: skipping {path}: {err}", file=sys.stderr)
                failed += 1
                continue
            zsize = os.path.getsize(path + ".zst")
            print(f"{path}: {_fmt_mb(size)} -> {_fmt_mb(zsize)}")
            compressed += 1
            bytes_before += size
            bytes_after += zsize

    if args.dry_run:
        print(f"would compress: {compressed} file(s), {_fmt_mb(bytes_before)}, "
              f"RUNNING runs skipped: {skipped_runs}")
    else:
        print(f"compressed: {compressed} file(s), {_fmt_mb(bytes_before)} -> "
              f"{_fmt_mb(bytes_after)}, RUNNING runs skipped: {skipped_runs}, failed: {failed}")


if __name__ == "__main__":
    main()
