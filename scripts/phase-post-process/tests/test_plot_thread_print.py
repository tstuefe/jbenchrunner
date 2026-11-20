#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/tests/test_plot_thread_print.py
#
# Tests for scripts/phase-post-process/plot-thread-print.py (parse and
# script-building steps only; rendering needs gnuplot).

import importlib.util
import json
import os
import sys
import tempfile
import unittest

_PHASE_PP_DIR = os.path.join(os.path.dirname(__file__), "..")
_COMMON_DIR = os.path.join(_PHASE_PP_DIR, "..", "common")
sys.path.insert(0, _PHASE_PP_DIR)
sys.path.insert(0, _COMMON_DIR)

_SPEC = importlib.util.spec_from_file_location(
    "plot_thread_print", os.path.join(_PHASE_PP_DIR, "plot-thread-print.py"))
ptp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ptp)


def _sample(ts, mutator, gc=8, c2=2, c1=1, other=10, cpu=None):
    """A thread-print.jsonl line; *cpu* maps category -> cpu_pct (default: null)."""
    cpu = cpu or {}
    counts = {"c2": c2, "c1": c1, "gc": gc, "mutator": mutator, "other": other}
    rec = {"timestamp": ts, "interval_secs": None, "num_threads": sum(counts.values())}
    for c, n in counts.items():
        rec[c] = {"num_threads": n, "cpu_pct": cpu.get(c)}
    rec["threads"] = []
    return json.dumps(rec)


class ParseTest(unittest.TestCase):
    def _samples(self, lines):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
        try:
            return ptp._read_samples(f.name)
        finally:
            os.unlink(f.name)

    def test_cumulative_rows_in_stacking_order(self):
        rows, _series = ptp.count_rows(self._samples([
            _sample("2026-09-22T20:29:03.000+02:00", 1), "garbage",
            _sample("2026-09-22T20:29:05.000+02:00", 4, c2=3)]))
        self.assertEqual([r[0] for r in rows], [0.0, 2.0])
        # elapsed, 0, then cumulative GC, Other, C1, C2, Mutator
        self.assertEqual(rows[0][1:], [0.0, 8.0, 18.0, 19.0, 21.0, 22.0])
        self.assertEqual(rows[1][1:], [0.0, 8.0, 18.0, 19.0, 22.0, 26.0])

    def test_sample_missing_count_is_skipped(self):
        bad = json.dumps({"timestamp": "2026-09-22T20:29:03.000+02:00", "num_threads": 3})
        samples = self._samples([bad, _sample("2026-09-22T20:29:05.000+02:00", 1)])
        self.assertEqual(len(ptp.count_rows(samples)[0]), 1)

    def test_cpu_rows_are_steps_over_the_interval(self):
        cpu = {"gc": 10.0, "other": 5.0, "c1": 1.0, "c2": 2.0, "mutator": 50.0}
        rows, series = ptp.cpu_rows(self._samples([
            _sample("2026-09-22T20:29:03.000+02:00", 1),
            _sample("2026-09-22T20:29:05.500+02:00", 1, cpu=cpu)]))
        self.assertEqual(series, ptp.SERIES)  # no unaccounted values: no extra band
        # One step from the previous sample's time to this one's; first sample draws nothing.
        self.assertEqual([r[0] for r in rows], [0.0, 2.5])
        self.assertEqual(rows[0][1:], [0.0, 10.0, 15.0, 16.0, 18.0, 68.0])
        self.assertEqual(rows[1][1:], rows[0][1:])

    def test_cpu_rows_negative_drawn_as_zero(self):
        cpu = {"gc": 10.0, "other": -3.0, "c1": 0.0, "c2": 0.0, "mutator": 0.0}
        rows, series = ptp.cpu_rows(self._samples([
            _sample("2026-09-22T20:29:03.000+02:00", 1),
            _sample("2026-09-22T20:29:05.000+02:00", 1, cpu=cpu)]))
        self.assertEqual(rows[0][1:], [0.0, 10.0, 10.0, 10.0, 10.0, 10.0])

    def test_cpu_rows_unaccounted_band_on_top(self):
        cpu = {"gc": 10.0, "other": 5.0, "c1": 1.0, "c2": 2.0, "mutator": 50.0}
        second = json.loads(_sample("2026-09-22T20:29:05.000+02:00", 1, cpu=cpu))
        second["unaccounted"] = {"cpu_pct": 30.0}
        rows, series = ptp.cpu_rows(self._samples([
            _sample("2026-09-22T20:29:03.000+02:00", 1), json.dumps(second)]))
        self.assertEqual(series[-1], ptp.UNACCOUNTED_SERIES)
        self.assertEqual(series[-1][0], "Unaccounted/Short-lived")
        self.assertEqual(rows[0][1:], [0.0, 10.0, 15.0, 16.0, 18.0, 68.0, 98.0])

    def test_cpu_rows_null_unaccounted_drawn_as_zero(self):
        cpu = {"gc": 10.0, "other": 0.0, "c1": 0.0, "c2": 0.0, "mutator": 0.0}
        second = json.loads(_sample("2026-09-22T20:29:05.000+02:00", 1, cpu=cpu))
        second["unaccounted"] = {"cpu_pct": None}
        rows, series = ptp.cpu_rows(self._samples([
            _sample("2026-09-22T20:29:03.000+02:00", 1), json.dumps(second)]))
        self.assertEqual(len(series), len(ptp.SERIES) + 1)
        self.assertEqual(rows[0][-1], 10.0)

    def test_script_has_one_band_per_series_top_first(self):
        gp = ptp.build_gnuplot_script("/tmp/x.dat", "/tmp/x.svg", "t", "Threads")
        self.assertEqual(gp.count("with filledcurves"), len(ptp.SERIES))
        self.assertLess(gp.index("using 1:7:6 "), gp.index("using 1:3:2 "))


if __name__ == "__main__":
    unittest.main()
