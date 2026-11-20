# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# failures.py — core logic for jbr-failures (docs/spec-other.md §4).
#
# Scans the project's results/ for run directories whose run did not complete: status
# FAILED or STOPPED in run-info.json, or "unknown" when info.json or
# run-info.json is missing or unreadable. Unlike jbr-filt/jbr-ls these
# directories have no result.json, so info.json and run-info.json are read
# directly.

import json
import os

from term_link import terminal_link

_LISTED_STATUSES = ("FAILED", "STOPPED")

UNKNOWN = "unknown"

_COLUMNS = ("runid", "time", "duration", "status", "status_info", "exit_code",
            "name", "jdkname", "tags")


def _read_json(path: str):
    """Return the parsed object in *path*, or None if missing/unreadable/not an object."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_failure(run_dir: str):
    """Return a merged info.json + run-info.json dict for *run_dir* if it is to
    be listed, or None if not (e.g. DONE, RUNNING).

    A directory whose info.json or run-info.json is missing or unreadable, or
    whose run-info.json has no status, gets status "unknown" (and whatever
    fields could be read).
    """
    info = _read_json(os.path.join(run_dir, "info.json"))
    run_info = _read_json(os.path.join(run_dir, "run-info.json"))
    merged = {}
    merged.update(info or {})
    merged.update(run_info or {})
    if info is None or run_info is None or "status" not in run_info:
        merged["status"] = UNKNOWN
    elif run_info["status"] not in _LISTED_STATUSES:
        return None
    merged["runid"] = os.path.basename(run_dir)
    return merged


def scan_failures(results_dir: str) -> list:
    """Return the merged dicts of all listed run directories under
    *results_dir*, sorted by time ascending (runs without a time first)."""
    runs = []
    if not os.path.isdir(results_dir):
        return runs
    for name in os.listdir(results_dir):
        run_dir = os.path.join(results_dir, name)
        if not os.path.isdir(run_dir):
            continue
        run = load_failure(run_dir)
        if run is not None:
            runs.append(run)
    runs.sort(key=lambda r: (str(r.get("time", "")), r["runid"]))
    return runs


def _cell(run: dict, col: str) -> str:
    if col == "time":
        val = run.get("time")
        # Second precision, no UTC offset — same as jbr-ls.
        return str(val)[:19] if val is not None else "-"
    if col == "duration":
        val = run.get("duration_secs")
    elif col == "jdkname":
        jdk = run.get("jdk")
        val = jdk.get("name") if isinstance(jdk, dict) else None
    elif col == "tags":
        tags = run.get("tags")
        val = ",".join(tags) if tags else None
    else:
        val = run.get(col)
    return str(val) if val is not None else "-"


def format_table(runs: list, results_dir: str) -> str:
    """Return *runs* as an auto-sized table with a header line. The runid
    column is a terminal hyperlink to the run directory."""
    rows = [[_cell(run, col) for col in _COLUMNS] for run in runs]
    widths = [len(col) for col in _COLUMNS]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    fmt = "  ".join("{{:<{}}}".format(w) for w in widths)
    lines = [fmt.format(*_COLUMNS).rstrip(), "  ".join("-" * w for w in widths)]
    for row in rows:
        # Pad before wrapping so the OSC 8 escape bytes don't affect alignment.
        row[0] = terminal_link(os.path.join(results_dir, row[0]), row[0].ljust(widths[0]))
        lines.append(fmt.format(*row).rstrip())
    return "\n".join(lines)
