# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# run_loader.py — shared utilities for the analysis phase.
#
# Provides two functions used by both jbr-filt and jbr-ls:
#
#   discover_run_dirs(positional_args, results_dir, stdin_is_tty) -> list[str]
#       Resolves the set of candidate run directory names (bare RUNIDs) using
#       the three-way priority from spec §1.1.1 / §1.2.1:
#         1. positional args if non-empty
#         2. stdin (one RUNID per line) if stdin is not a terminal
#         3. all subdirectories of results_dir otherwise
#
#   load_run(run_dir: str) -> dict | None
#       Merges info.json + run-info.json + result.json into one flat dict.
#       Returns None silently when result.json is absent.
#       Raises ValueError on malformed JSON in info.json or run-info.json
#       (when result.json is present).

import json
import os
import sys


def discover_run_dirs(
    positional_args: list,
    results_dir: str,
    stdin_is_tty: bool,
) -> list:
    """Return a list of bare RUNID strings to process.

    Priority (per spec §1.1.1 / §1.2.1):
      1. positional_args — returned directly if non-empty.
      2. stdin           — read one RUNID per line if stdin is not a terminal.
      3. scan            — list all subdirectories of results_dir.

    Parameters
    ----------
    positional_args : list[str]
        RUNID values supplied on the command line (may be empty).
    results_dir : str
        Path to the results directory to scan when no other input is available.
    stdin_is_tty : bool
        Pass ``sys.stdin.isatty()`` from the caller.  Allows unit tests to
        inject False without monkey-patching sys.stdin.

    Returns
    -------
    list[str]
        Bare RUNID strings (directory names, not full paths).
    """
    if positional_args:
        return list(positional_args)

    if not stdin_is_tty:
        lines = sys.stdin.read().splitlines()
        return [line for line in lines if line.strip()]

    # Scan results_dir for subdirectories.
    if not os.path.isdir(results_dir):
        return []
    entries = [
        name
        for name in os.listdir(results_dir)
        if os.path.isdir(os.path.join(results_dir, name))
    ]
    return entries


def load_run(run_dir: str) -> dict:
    """Load and merge the three per-run JSON files into one dict.

    Merges top-level keys from info.json, run-info.json, and result.json.
    Per PRD §7 the three files have disjoint key sets, so a simple update
    union is safe.

    Parameters
    ----------
    run_dir : str
        Path to the run directory (e.g. ``results/<RUNID>``).

    Returns
    -------
    dict or None
        Merged dict of all top-level keys, or ``None`` if ``result.json`` is
        absent (directory not yet post-processed; silently skipped).

    Raises
    ------
    ValueError
        If ``result.json`` is present but ``info.json`` or ``run-info.json``
        contains malformed / unparseable JSON.  ``run-info.json`` being absent
        is *not* an error.
    """
    result_path = os.path.join(run_dir, "result.json")
    if not os.path.exists(result_path):
        return None

    merged = {}

    # --- info.json ---
    info_path = os.path.join(run_dir, "info.json")
    with open(info_path, "r", encoding="utf-8") as fh:
        try:
            merged.update(json.load(fh))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"malformed JSON in {info_path}: {exc}"
            ) from exc

    # --- run-info.json (optional) ---
    run_info_path = os.path.join(run_dir, "run-info.json")
    if os.path.exists(run_info_path):
        with open(run_info_path, "r", encoding="utf-8") as fh:
            try:
                merged.update(json.load(fh))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"malformed JSON in {run_info_path}: {exc}"
                ) from exc

    # --- result.json ---
    with open(result_path, "r", encoding="utf-8") as fh:
        try:
            merged.update(json.load(fh))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"malformed JSON in {result_path}: {exc}"
            ) from exc

    return merged
