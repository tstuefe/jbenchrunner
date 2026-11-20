# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
The timestamp format shared by all collectors (spec-execute.md §5.2) and run-info.json's
``time`` (spec-execute.md §4.2).

Every collector prints local time with millisecond precision and a colon-separated UTC
offset, e.g. ``2026-09-22T20:29:03.123+02:00``. Bash collectors produce it with
``date +%Y-%m-%dT%H:%M:%S.%3N%:z``, Python code with ``now()`` or ``to_string()`` below;
readers match it with ``TIMESTAMP_RE`` and convert it with ``parse()``. Files written in an
older format are converted by ``scripts/helpers/migrate-timestamps.py``, not accepted here.
"""

import re
from datetime import datetime

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")


def to_string(dt):
    """Return the aware datetime *dt* as a collector timestamp string."""
    return dt.isoformat(timespec="milliseconds")


def now():
    """Return the current local time as a collector timestamp string."""
    return to_string(datetime.now().astimezone())


def parse(raw):
    """Return *raw* as an aware datetime; raise ValueError if it is not a collector timestamp."""
    if not isinstance(raw, str) or not TIMESTAMP_RE.match(raw):
        raise ValueError(f"not a collector timestamp: {raw!r}")
    return datetime.fromisoformat(raw)
