#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
JVM flag assembly and validation logic.

This module implements the flag ordering logic (spec §2) and the -Xmx/-Xms parsing and
conflict-detection rules (spec §3.1 steps 1g-1h).
"""

import re


def assemble_flags(
    bench_flags=None,
    sweep_flags=None,
    profile_flags_list=None,
    run_top_flags=None,
    run_entry_flags=None,
    cli_flags=None,
    jdktype_extra_flags=None,
    gc_log_flag=None,
) -> list[str]:
    """
    Assemble the complete list of JVM flags in the fixed order defined by spec §2:

    1. Bench flags — requirements for the benchmark (from .bench.toml) + sweep flags.
    2. Profile flags — sets of flags from .profile.toml files.
    3. Run-entry flags — from .run.toml: top-level jvmargs, then per-run jvmargs.
    4. CLI flags — --jvmargs and --jvmarg arguments.
    5. JDK type flags + GC log flag — from detected JDK type.
    """
    assembled = []

    # 1. Bench flags + sweep flags
    if bench_flags:
        assembled.extend(bench_flags)
    if sweep_flags:
        assembled.extend(sweep_flags)

    # 2. Profile flags (can be a flat list of strings or list of lists of strings)
    if profile_flags_list:
        for item in profile_flags_list:
            if isinstance(item, str):
                assembled.append(item)
            elif isinstance(item, (list, tuple)):
                assembled.extend(item)

    # 3. Run-entry flags: run_top_flags followed by run_entry_flags
    if run_top_flags:
        assembled.extend(run_top_flags)
    if run_entry_flags:
        assembled.extend(run_entry_flags)

    # 4. CLI flags
    if cli_flags:
        assembled.extend(cli_flags)

    # 5. JDK type flags + GC log flag
    if jdktype_extra_flags:
        assembled.extend(jdktype_extra_flags)
    if gc_log_flag:
        assembled.append(gc_log_flag)

    return [flag.strip() for flag in assembled]


def _parse_to_bytes(val_part: str) -> int:
    """
    Parse a heap size suffix string (e.g. '1024m', '1g') to its bytes equivalent.
    Supports K, M, G, T (case-insensitive) suffixes. No suffix is treated as bytes.
    Raises ValueError on invalid formats.
    """
    m = re.match(r"^(\d+)([kmgtKMGT]?)$", val_part)
    if not m:
        raise ValueError(f"Invalid size format: '{val_part}'")
    val_str, suffix = m.groups()
    val = int(val_str)
    suffix = suffix.lower()

    if suffix == 'k':
        return val * 1024
    elif suffix == 'm':
        return val * 1024 * 1024
    elif suffix == 'g':
        return val * 1024 * 1024 * 1024
    elif suffix == 't':
        return val * 1024 * 1024 * 1024 * 1024
    elif suffix == '':
        return val
    else:
        raise ValueError(f"Unknown suffix: '{suffix}'")


def parse_xmx_mb(flags: list[str]) -> int:
    """
    Parses the last -Xmx flag from the assembled flag list and converts it to integer MB.
    Supported suffixes are k/m/g/t (case-insensitive).
    Raises ValueError if absent or invalid.
    """
    last_xmx = None
    for flag in flags:
        if flag.startswith("-Xmx"):
            last_xmx = flag

    if last_xmx is None:
        raise ValueError("Missing -Xmx flag in assembled JVM flags")

    val_part = last_xmx[4:]  # Strip '-Xmx'
    bytes_val = _parse_to_bytes(val_part)

    # Convert bytes to megabytes (MB)
    return bytes_val // (1024 * 1024)


def check_flag_conflicts(flags: list[str]) -> None:
    """
    Verify that there are no conflicting -Xmx or -Xms values in the flag list.
    Two or more -Xmx (or -Xms) flags with DIFFERENT resolved values = validation error.
    Same-value duplicates are silently accepted.
    Raises ValueError if a conflict is found or a flag is malformed.
    """
    xmx_values = set()
    xms_values = set()

    for flag in flags:
        if flag.startswith("-Xmx"):
            val_part = flag[4:]
            try:
                bytes_val = _parse_to_bytes(val_part)
                xmx_values.add(bytes_val)
            except ValueError as e:
                raise ValueError(f"Invalid -Xmx flag '{flag}': {e}")
        elif flag.startswith("-Xms"):
            val_part = flag[4:]
            try:
                bytes_val = _parse_to_bytes(val_part)
                xms_values.add(bytes_val)
            except ValueError as e:
                raise ValueError(f"Invalid -Xms flag '{flag}': {e}")

    if len(xmx_values) > 1:
        raise ValueError(
            f"Conflicting -Xmx values found in flags: "
            f"{[f for f in flags if f.startswith('-Xmx')]}"
        )
    if len(xms_values) > 1:
        raise ValueError(
            f"Conflicting -Xms values found in flags: "
            f"{[f for f in flags if f.startswith('-Xms')]}"
        )
