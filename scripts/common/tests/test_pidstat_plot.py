# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/common/pidstat_plot.py (parse and CPU stack rows;
rendering needs gnuplot).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pidstat_plot


def _sample(ts, usr, system, guest, cpu):
    return json.dumps({"timestamp": ts, "rss": "2048", "pct_usr": usr,
                       "pct_system": system, "pct_guest": guest,
                       "pct_wait": "9.00", "pct_cpu": cpu})


class CpuStackTest(unittest.TestCase):
    def test_parse_and_stack_rows(self):
        lines = [
            _sample("2026-09-24T10:00:00.000+02:00", "30.00", "10.00", "1.00", "41.00"),
            "garbage",
            _sample("2026-09-24T10:00:02.000+02:00", "50.00", "20.00", "0.00", "70.00"),
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
        try:
            rss, cpu = pidstat_plot.parse(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(rss, [(0.0, 2.0), (2.0, 2.0)])
        # Stack order bottom to top: system, user, guest.
        self.assertEqual(cpu, [(0.0, [10.0, 30.0, 1.0]), (2.0, [20.0, 50.0, 0.0])])
        rows = pidstat_plot.build_cpu_rows(cpu)
        # Top of the stack is pidstat's total %CPU.
        self.assertEqual(rows, [[0.0, 0.0, 10.0, 40.0, 41.0],
                                [2.0, 0.0, 20.0, 70.0, 70.0]])


if __name__ == "__main__":
    unittest.main()
