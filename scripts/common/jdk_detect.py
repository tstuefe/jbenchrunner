# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# jdk_detect.py — parse `java -version` output into version, major and JDK type.
#
# Usage:
#   from common.jdk_detect import parse_java_version_output
#   info = parse_java_version_output(output)   # {"version", "major", "type"}
#
# Single source of truth for JDK detection, shared by the prepare phase
# (spec-prepare.md §3 step 1d) and the execute phase (spec-execute.md §2 step e),
# so the two can never disagree about how a given `java -version` output is read.

import re

# First quoted string on a line mentioning "version", e.g.
#   openjdk version "21.0.4" 2024-07-16
#   openjdk version "28-internal" 2027-03-23
#   java version "1.8.0_362"
_VERSION_LINE_RE = re.compile(r'version\s+"([^"]*)"')
_NUMERIC_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*")


def parse_java_version_output(output: str) -> dict:
    """Parse the combined stdout+stderr of ``java -version``.

    Returns a dict:
      - ``version``: numeric prefix of the quoted version string, e.g. ``"21.0.4"``,
                     ``"28"`` (from ``"28-internal"``), ``"1.8.0"`` (from ``"1.8.0_362"``)
      - ``major``:   integer feature version; the legacy ``1.x`` scheme maps to ``x``
                     (Java 8 = ``1.8.0`` -> ``8``)
      - ``type``:    ``"openj9"`` if the output contains ``Semeru`` or ``OpenJ9``,
                     otherwise ``"openjdk"``

    Raises RuntimeError if no version can be parsed.
    """
    version = None
    for line in output.splitlines():
        m = _VERSION_LINE_RE.search(line)
        if m:
            n = _NUMERIC_PREFIX_RE.match(m.group(1))
            if n:
                version = n.group(0)
                break
    if version is None:
        raise RuntimeError(
            f"Could not parse Java version from 'java -version' output:\n{output}"
        )

    parts = [int(p) for p in version.split(".")]
    major = parts[1] if parts[0] == 1 and len(parts) > 1 else parts[0]

    jdk_type = "openj9" if re.search(r"Semeru|OpenJ9", output) else "openjdk"

    return {"version": version, "major": major, "type": jdk_type}
