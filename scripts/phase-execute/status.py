# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Batch status display for jbr-status.

Implements format_batch_table(batch, project_dir) which returns a multi-line
string describing the current state of a batch, per spec-execute.md §3 and §3.1.
"""

import json
import os
import sys

from execute import get_run_status
from jvm_flag_filter import filter_jvm_flags
from term_link import terminal_link


_STATUS_COLORS = {
    "DONE": "\x1b[92m",      # light green
    "FAILED": "\x1b[91m",    # light red
    "RUNNING": "\x1b[93m",   # light yellow
    "STOPPED": "\x1b[93m",   # light yellow
}
_ANSI_RESET = "\x1b[0m"


def _supports_color() -> bool:
    """Return True if the current stdout is a color-capable terminal.

    Colors are suppressed when NO_COLOR is set, stdout is not a TTY (e.g.
    piped/redirected output), or TERM is "dumb".
    """
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


def _colorize_status(status_text: str, padded_display_text: str) -> str:
    """Wrap an already width-padded Status cell in its ANSI color.

    padded_display_text must already be left-justified to column width
    before calling this, so the escape-code bytes never affect visible
    column alignment.
    """
    if not _supports_color():
        return padded_display_text
    color = _STATUS_COLORS.get(status_text)
    if color is None:
        return padded_display_text
    return color + padded_display_text + _ANSI_RESET


def _read_run_info_fields(run_entry: dict, project_dir: str) -> tuple:
    """Return (time, status_info, pid, duration_secs) from run-info.json.
    time and status_info are "-", pid is None, and duration_secs is "-" for
    any field that is absent or unavailable (e.g. the run hasn't started
    yet, or hasn't finished yet — duration_secs is only written on the final
    write)."""
    result_dir = run_entry.get("result_dir", "")
    run_info_path = os.path.join(project_dir, result_dir, "run-info.json")

    try:
        with open(run_info_path, encoding="utf-8") as fh:
            run_info = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return "-", "-", None, "-"
    duration_secs = run_info.get("duration_secs")
    return (
        run_info.get("time", "-"),
        run_info.get("status_info", "-"),
        run_info.get("pid"),
        str(duration_secs) if duration_secs is not None else "-",
    )


def list_run_ids(batch: dict) -> list:
    """Return the run_id of each run entry in *batch*, in original order."""
    return [run.get("runid", "") for run in batch.get("runs", [])]


def format_batch_table(batch: dict, project_dir: str, show_all: bool = False) -> str:
    """Return a formatted multi-line string describing *batch* status.

    The string contains:
    - A header with batch ID, prepared-at timestamp (the batch file's
      started_at, set by jbr-prepare), hostname, and total/done/failed counts.
    - One row per run entry with columns: #, RUN-ID (clickable link to
      the result directory), status, Info (run-info.json's optional
      status_info field), Start (run-info.json's time), Duration
      (run-info.json's duration_secs, only present once the run has finished),
      JDK, Benchmark (bench_json), Tags (comma-separated).
    - With *show_all*, additionally: JVM Path (java_home) and JVM Args
      (harness-injected/operational flags stripped per
      jvm_flag_filter.filter_jvm_flags).

    A RUNNING row shows its pid in the Info column instead of run-info.json's
    status_info field. The Status column is colored with ANSI escape codes
    when the terminal supports it (see _supports_color): DONE green, FAILED
    red, RUNNING/STOPPED yellow.

    Args:
        batch:       Parsed BATCH_IN_PROGRESS.json dict.
        project_dir: Absolute path to the project directory.
        show_all:    If True, add the JVM Path and JVM Args columns.

    Returns:
        A multi-line string ending with a newline.
    """
    runs = batch.get("runs", [])

    statuses = [get_run_status(r, project_dir) for r in runs]

    total = len(runs)
    done = sum(1 for s in statuses if s == "DONE")
    failed = sum(1 for s in statuses if s == "FAILED")

    header = (
        "Batch:    {batch_id}\n"
        "Prepared: {started_at}\n"
        "Host:     {hostname}\n"
        "Runs:     {total} total, {done} done, {failed} failed"
    ).format(
        batch_id=batch.get("batchid", ""),
        started_at=batch.get("started_at", ""),
        hostname=batch.get("hostname", ""),
        total=total,
        done=done,
        failed=failed,
    )

    lines = [header, ""]

    col_headers = (" # ", "RUN-ID", "Status ", "Info", "Start", "Duration", "JDK", "Benchmark", "Tags")
    if show_all:
        col_headers += ("JVM Path", "JVM Args")
    rows = []
    for i, (run, status) in enumerate(zip(runs, statuses), start=1):
        run_id = run.get("runid", "")
        result_dir_abs = os.path.join(project_dir, run.get("result_dir", ""))
        start, status_info, pid, duration_secs = _read_run_info_fields(run, project_dir)

        display_info = status_info
        if status == "RUNNING" and pid is not None:
            display_info = str(pid)
        row = (
            str(i),
            run_id,
            status,
            display_info,
            start,
            duration_secs,
            run.get("jdkname", ""),
            run.get("bench_json", ""),
            ",".join(run.get("tags", [])) or "-",
        )
        if show_all:
            row += (
                run.get("java_home", ""),
                " ".join(filter_jvm_flags(run.get("jvmargs", []))),
            )
        rows.append(row + (result_dir_abs,))  # result dir: for link generation, not displayed

    # Compute column widths from the visible text (rows[*][:len(col_headers)])
    # and headers.
    col_widths = [len(h) for h in col_headers]
    for row in rows:
        for j, cell in enumerate(row[:len(col_headers)]):
            col_widths[j] = max(col_widths[j], len(cell))

    fmt = "  ".join("{{:<{}}}".format(w) for w in col_widths)
    lines.append(fmt.format(*col_headers).rstrip())
    lines.append("  ".join("-" * w for w in col_widths))
    for row in rows:
        # Build the display tuple, substituting a clickable link for RUN-ID.
        # Pad to column width *before* wrapping in the OSC 8 escape codes,
        # so the invisible bytes don't get counted by a later .ljust() and
        # throw off alignment.
        display = list(row[:len(col_headers)])
        display[1] = terminal_link(row[len(col_headers)], row[1].ljust(col_widths[1]))
        display[2] = _colorize_status(row[2], display[2].ljust(col_widths[2]))
        lines.append(fmt.format(*display).rstrip())

    lines.append("")
    return "\n".join(lines)
