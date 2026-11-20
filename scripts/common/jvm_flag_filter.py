# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jvm_flag_filter.py — Shared list of harness-injected/operational JVM flags
# hidden from jvmargs display in jbr-status and jbr-ls.
#
# These are flags that have no bearing on runtime results (diagnostics,
# logging destinations, harness bookkeeping) and only clutter the display.
#
# Usage:
#   from jvm_flag_filter import filter_jvm_flags
#   visible = filter_jvm_flags(run_info["jvmargs"])

# Flags stripped by exact string match.
FILTERED_EXACT = {
    "-XX:+PrintFlagsFinal",
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+UnlockDiagnosticVMOptions",
    "-XX:+PrintVMInfoAtExit",
}

# Flags stripped when the flag string starts with one of these prefixes
# (covers flags that carry an attached argument, e.g. "-Xlog:gc*:file=...").
FILTERED_PREFIXES = (
    "-Xlog:",
    "-Xverbosegclog:",
    "-XX:ErrorFile=",
    "-Dconfig.test.specLicense=",
)


def filter_jvm_flags(flags: list) -> list:
    """Return *flags* with harness-injected/operational entries removed.

    A flag is dropped if it exactly matches an entry in FILTERED_EXACT, or
    starts with one of the FILTERED_PREFIXES. Order and remaining flags are
    otherwise unchanged.
    """
    return [
        flag for flag in flags
        if flag not in FILTERED_EXACT
        and not any(flag.startswith(p) for p in FILTERED_PREFIXES)
    ]
