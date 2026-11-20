# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/common/pagefaults_plot.py (parse, rate and cumulative
rows; rendering needs gnuplot).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pagefaults_plot


def _sample(ts, minflt, majflt):
    return json.dumps({"timestamp": ts, "proc_pid_stat": {"minflt": minflt, "majflt": majflt}})


class PageFaultsPlotTest(unittest.TestCase):
    def _parse(self, lines):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
        try:
            return pagefaults_plot.parse(f.name)
        finally:
            os.unlink(f.name)

    def test_parse(self):
        points = self._parse([
            _sample("2026-09-24T10:00:00.000+02:00", "5000", "60"),
            "garbage",
            _sample("2026-09-24T10:00:10.000+02:00", "25000", "70"),
        ])
        self.assertEqual(points, [(0.0, [5000, 60]), (10.0, [25000, 70])])
        self.assertEqual(pagefaults_plot.cumulative_rows(points),
                         [[0.0, 5000, 60], [10.0, 25000, 70]])

    def test_rate_rows_are_steps_over_each_interval(self):
        points = [(0.0, [5000, 60]), (10.0, [25000, 70]), (10.0, [25000, 70]),
                  (15.0, [26000, 70])]
        rows = pagefaults_plot.rate_rows(points)
        # Faults before the first sample are not drawn; the zero-length
        # interval is skipped.
        self.assertEqual(rows, [[0.0, 2000.0, 1.0], [10.0, 2000.0, 1.0],
                                [10.0, 200.0, 0.0], [15.0, 200.0, 0.0]])

    def test_rate_rows_single_sample(self):
        self.assertEqual(pagefaults_plot.rate_rows([(0.0, [5000, 60])]), [])


if __name__ == "__main__":
    unittest.main()
