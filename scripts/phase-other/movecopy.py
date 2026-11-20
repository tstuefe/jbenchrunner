# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# movecopy.py — shared core logic for jbr-move and jbr-copy (docs/spec-other.md §2, §3).
#
# Both tools relocate or duplicate existing run result directories to a
# user-specified destination, preserving each RUNID as the destination leaf
# directory name. They share every behavior except the actual filesystem
# operation (shutil.move vs shutil.copytree) and the past-tense verb printed
# in the per-run output line — that shared behavior lives here.

import os
import shutil
import sys

from term_link import terminal_link


def validate_destination_root(to_dir: str) -> None:
    """Raise ValueError if *to_dir* exists but is not a directory (spec §2.7)."""
    if os.path.exists(to_dir) and not os.path.isdir(to_dir):
        raise ValueError(f"--to path exists and is not a directory: {to_dir}")


def plan_operations(run_ids: list, results_dir: str, to_dir: str) -> tuple:
    """Split *run_ids* into planned operations and missing sources.

    Returns
    -------
    (planned, missing) : tuple[list[tuple[str, str, str]], list[str]]
        planned is a list of (run_id, src, dst) for RUNIDs whose
        results/<run_id> exists, in input order. missing is the list of
        RUNIDs whose source directory does not exist (spec §2.3/§2.7).
    """
    planned = []
    missing = []
    for run_id in run_ids:
        src = os.path.join(results_dir, run_id)
        if not os.path.isdir(src):
            missing.append(run_id)
            continue
        dst = os.path.join(to_dir, run_id)
        planned.append((run_id, src, dst))
    return planned, missing


def find_collisions(planned: list) -> list:
    """Return the run_ids among *planned* whose destination already exists."""
    return [run_id for run_id, _src, dst in planned if os.path.exists(dst)]


def remove_existing(path: str) -> None:
    """Remove a pre-existing destination path (file, symlink, or directory)."""
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    else:
        shutil.rmtree(path)


def run_movecopy(
    run_ids: list,
    results_dir: str,
    to_dir: str,
    force: bool,
    mode: str,
) -> None:
    """Run the full jbr-move / jbr-copy pipeline (spec §2, §3).

    Prints a "Warning:" line to stderr for each RUNID with no source
    directory, then one confirmation line to stdout per successfully
    moved/copied run.

    Parameters
    ----------
    mode : str
        Either "move" or "copy".

    Raises
    ------
    RuntimeError
        If a destination collision is found and *force* is False — the
        whole invocation aborts before any directory is touched (spec
        §2.5). The caller is expected to print the message and exit
        non-zero.
    """
    assert mode in ("move", "copy")

    planned, missing = plan_operations(run_ids, results_dir, to_dir)
    for run_id in missing:
        link = terminal_link(
            os.path.join(results_dir, run_id), run_id, stream=sys.stderr
        )
        print(f"Warning: skipping {link}: no such run directory", file=sys.stderr)

    collisions = find_collisions(planned)
    if collisions and not force:
        names = ", ".join(collisions)
        raise RuntimeError(
            f"destination already exists for: {names} (pass --force to replace)"
        )

    if not planned:
        return

    os.makedirs(to_dir, exist_ok=True)

    verb_past = "moved" if mode == "move" else "copied"
    for run_id, src, dst in planned:
        if os.path.exists(dst):
            remove_existing(dst)
        if mode == "move":
            shutil.move(src, dst)
        else:
            shutil.copytree(src, dst)
        link = terminal_link(dst, run_id)
        print(f"{link}: {verb_past} to {dst}")
