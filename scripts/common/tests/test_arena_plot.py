# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/common/arena_plot.py (parse, rows, panel selection;
rendering needs gnuplot).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import arena_plot

MIB = 1024 * 1024


def _write_combined(run_dir, samples):
    os.makedirs(os.path.join(run_dir, "arena-report"))
    with open(os.path.join(run_dir, "arena-report", "combined.json"), "w", encoding="utf-8") as f:
        json.dump({"unit": "bytes", "block_sizes": [256], "samples": samples}, f)


class ArenaPlotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.a = os.path.join(self.tmp, "row")
        self.b = os.path.join(self.tmp, "base")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_parse_sorts_and_drops_samples_without_uptime(self):
        _write_combined(self.a, [{"uptime_s": 2.0, "dirty_bytes": MIB},
                                 {"uptime_s": None, "dirty_bytes": 0},
                                 {"uptime_s": 1.0, "dirty_bytes": 2 * MIB}])
        samples = arena_plot.parse(self.a)
        self.assertEqual([s["uptime_s"] for s in samples], [1.0, 2.0])
        self.assertEqual(arena_plot.rows(samples, "dirty_bytes"), [[1.0, 2.0], [2.0, 1.0]])
        self.assertEqual(arena_plot.rows(samples, "in_use_peak_bytes"), [])

    def test_missing_report_returns_false(self):
        _write_combined(self.a, [{"uptime_s": 1.0, "dirty_bytes": MIB}])
        out = os.path.join(self.tmp, "out.svg")
        self.assertIsNone(arena_plot.parse(self.b))
        self.assertFalse(arena_plot.build_comparison_svg(self.a, self.b, out))
        self.assertFalse(os.path.exists(out))

    def test_panel_without_series_in_both_runs_is_left_out(self):
        sample = {"uptime_s": 1.0, "dirty_bytes": MIB, "cached_bytes": 0}
        _write_combined(self.a, [sample, {**sample, "uptime_s": 2.0}])
        _write_combined(self.b, [{**sample, "in_use_peak_bytes": MIB}])
        with patch("arena_plot.run_gnuplot", return_value=True) as gp:
            self.assertTrue(arena_plot.build_comparison_svg(
                self.a, self.b, os.path.join(self.tmp, "out.svg")))
        script = gp.call_args[0][0]
        self.assertIn("multiplot layout 3,1", script)
        # in-use peak only in the baseline: one line in that panel
        peak_panel = script.split("Arena in use")[1].split("Arena dirty")[0]
        self.assertIn("Baseline: base", peak_panel)
        self.assertNotIn("Row: row", peak_panel)

        shutil.rmtree(self.b)
        _write_combined(self.b, [sample])
        with patch("arena_plot.run_gnuplot", return_value=True) as gp:
            arena_plot.build_comparison_svg(self.a, self.b, os.path.join(self.tmp, "out.svg"))
        script = gp.call_args[0][0]
        self.assertIn("multiplot layout 2,1", script)
        self.assertNotIn("Arena in use", script)

    @unittest.skipIf(shutil.which("gnuplot") is None, "gnuplot not installed")
    def test_renders_svg(self):
        samples = [{"uptime_s": t, "in_use_peak_bytes": t * MIB, "dirty_bytes": MIB,
                    "cached_bytes": MIB // 2} for t in (1.0, 2.0, 3.0)]
        _write_combined(self.a, samples)
        _write_combined(self.b, samples[:2])
        out = os.path.join(self.tmp, "out.svg")
        self.assertTrue(arena_plot.build_comparison_svg(self.a, self.b, out))
        self.assertTrue(os.path.getsize(out) > 0)


if __name__ == "__main__":
    unittest.main()
