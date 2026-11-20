#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Shared gnuplot plotting helpers, used by the phase-post-process plot
# scripts (plot-heapsizes.py, plot-pidstat.py, plot-system-map.py, ...) and
# by scripts/common/system_map_plot.py's on-the-fly comparison plots: safe
# string quoting, temporary .dat file handling, gnuplot subprocess
# invocation, and the "<dir> - <name> - <tags>" plot-title line read from a
# result directory's info.json.

import contextlib
import json
import os
import subprocess
import sys
import tempfile


def gnuplot_quote(s: str) -> str:
    """Return a safely-escaped gnuplot string literal for s.

    This is gnuplot_dquote: a single-quoted gnuplot literal can't be made
    safe, since it has no escape for a newline and gnuplot ends the
    command at a raw newline even inside '...'. A newline in untrusted
    input (e.g. a result directory name used in a plot title or the
    output path) would then start a new command, such as `system(...)`.
    """
    return gnuplot_dquote(s)


def gnuplot_dquote(s: str) -> str:
    """Return a safely-escaped gnuplot double-quoted string literal for s.

    Unlike gnuplot_quote's single-quoted literal, a double-quoted gnuplot
    string interprets C-style escapes, so this is what a multi-line title
    (embedded "\\n") needs. Backslashes and double quotes are escaped so
    that untrusted input (e.g. a tag name) can't close the literal early
    and inject further gnuplot commands; a real newline character in s is
    turned into the "\\n" escape so it round-trips as a line break.
    Backquotes are escaped too: gnuplot runs `...` inside a double-quoted
    string as a shell command (command-line substitution); "\\`" is a
    literal backquote.
    """
    escaped = (str(s)
               .replace("\\", "\\\\")
               .replace('"', '\\"')
               .replace("`", "\\`")
               .replace("\n", "\\n"))
    return '"' + escaped + '"'


def run_header(result_dir):
    """
    The run identification line shared by all plots of a result directory:
    "<dir> - <name> - <tag1>,<tag2>", with <dir> the directory's basename and
    name/tags read from its info.json. Empty parts are left out (with their
    " - "). Returns None if info.json is missing or malformed.
    """
    info_path = os.path.join(result_dir, "info.json")
    try:
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict):
        return None
    parts = [os.path.basename(os.path.normpath(result_dir)),
             info.get("name") or "",
             ",".join(str(t) for t in info.get("tags") or [])]
    return " - ".join(p for p in parts if p)


def plot_title(first_line, result_dir):
    """
    Two-line plot title: first_line, then run_header(result_dir). Falls back
    to just first_line if info.json is missing/malformed.
    """
    header = run_header(result_dir)
    return f"{first_line}\n{header}" if header else first_line


def write_dat(rows, f):
    """Write rows (2 or more numeric columns each) to f as whitespace-separated
    gnuplot data lines."""
    for row in rows:
        f.write(" ".join(f"{v:.3f}" for v in row) + "\n")
    f.flush()


@contextlib.contextmanager
def temp_dat_files():
    """
    Context manager yielding a `make(rows)` function that writes rows to a
    new temporary .dat file and returns its path. Every file created via
    `make` is deleted on exit, even if an exception occurs.
    """
    tmp_paths = []

    def make(rows):
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.dat', delete=False)
        tmp_paths.append(f.name)
        write_dat(rows, f)
        f.close()
        return f.name

    try:
        yield make
    finally:
        for p in tmp_paths:
            os.unlink(p)


def run_gnuplot(script: str) -> bool:
    """
    Run gnuplot with the given script fed via stdin.

    Returns True on success. On failure (including gnuplot not being
    installed), prints a message to sys.stderr and returns False.
    """
    try:
        result = subprocess.run(["gnuplot"], input=script, text=True,
                                 capture_output=True)
    except OSError as e:
        print(f"gnuplot error: {e}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"gnuplot error:\n{result.stderr}", file=sys.stderr)
        return False
    return True
