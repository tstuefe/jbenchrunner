#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-post-process/aggregators.py
"""

import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock


_HERE = os.path.dirname(__file__)
_PHASE_PP_DIR = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _PHASE_PP_DIR)

from aggregators import (
    parse_temperatures,
    parse_memory,
    parse_malloc,
    parse_cpu,
    parse_threads,
    parse_page_faults,
    parse_thread_categories,
    parse_arena_summary,
    time_weighted_mean,
)


def _write_jsonl(path: str, lines: list) -> None:
    """Write a list of dicts (or raw strings) to a JSON Lines file."""
    with open(path, "w", encoding="utf-8") as f:
        for item in lines:
            if isinstance(item, str):
                f.write(item + "\n")
            else:
                f.write(json.dumps(item) + "\n")


class TestParseTemperatures(unittest.TestCase):

    # ------------------------------------------------------------------
    # Normal cases
    # ------------------------------------------------------------------

    def test_multi_sample_integer_values(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": 1, "temperature": 60000},
                {"timestamp": 2, "temperature": 65000},
                {"timestamp": 3, "temperature": 63000},
            ])
            result = parse_temperatures(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["tempin"], 60000)
        self.assertEqual(result["tempout"], 63000)
        self.assertEqual(result["tempmax"], 65000)

    def test_multi_sample_string_values(self):
        """Temperature values given as strings should still be accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": 1, "temperature": "60000"},
                {"timestamp": 2, "temperature": "70000"},
                {"timestamp": 3, "temperature": "64000"},
            ])
            result = parse_temperatures(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["tempin"], 60000)
        self.assertEqual(result["tempout"], 64000)
        self.assertEqual(result["tempmax"], 70000)

    def test_single_sample(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [{"timestamp": 1, "temperature": 55000}])
            result = parse_temperatures(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["tempin"], 55000)
        self.assertEqual(result["tempout"], 55000)
        self.assertEqual(result["tempmax"], 55000)

    def test_max_is_first_sample(self):
        """Ensure max is computed over all samples, not just start/end."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": 1, "temperature": 80000},
                {"timestamp": 2, "temperature": 60000},
                {"timestamp": 3, "temperature": 62000},
            ])
            result = parse_temperatures(path)
        finally:
            os.unlink(path)

        self.assertEqual(result["tempin"], 80000)
        self.assertEqual(result["tempmax"], 80000)

    # ------------------------------------------------------------------
    # Malformed / partial data
    # ------------------------------------------------------------------

    def test_skips_malformed_json_lines(self):
        """Valid lines are used; malformed lines emit a warning and are skipped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": 1, "temperature": 60000},
                "not valid json {{",
                {"timestamp": 3, "temperature": 62000},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_temperatures(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["tempin"], 60000)
        self.assertEqual(result["tempout"], 62000)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_skips_lines_missing_temperature_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": 1, "temp": 60000},    # wrong key
                {"timestamp": 2, "temperature": 65000},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_temperatures(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["tempin"], 65000)
        self.assertEqual(len(stderr_capture.getvalue().splitlines()), 1)

    def test_all_lines_malformed_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, ["bad", "also bad"])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_temperatures(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    # ------------------------------------------------------------------
    # Empty / missing file
    # ------------------------------------------------------------------

    def test_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            # write nothing
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_temperatures(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_nonexistent_file_returns_none(self):
        stderr_capture = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_capture):
            result = parse_temperatures("/tmp/does_not_exist_jbr_test_12345.jsonl")

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())


class TestTimeWeightedMean(unittest.TestCase):

    def test_empty_returns_none(self):
        self.assertIsNone(time_weighted_mean([]))

    def test_single_sample_returns_its_value(self):
        self.assertEqual(time_weighted_mean([(5.0, 42.0)]), 42.0)

    def test_all_same_timestamp_returns_plain_mean(self):
        """Zero elapsed time: fall back to the plain mean of the values."""
        result = time_weighted_mean([(3.0, 10.0), (3.0, 20.0), (3.0, 30.0)])
        self.assertEqual(result, 20.0)

    def test_two_points_matches_trapezoid(self):
        result = time_weighted_mean([(0.0, 10.0), (10.0, 20.0)])
        self.assertEqual(result, 15.0)

    def test_uneven_spacing_weights_by_duration(self):
        """
        A long-duration segment should dominate the mean, unlike a plain
        per-sample average.
        """
        samples = [(0.0, 0.0), (1.0, 0.0), (11.0, 100.0)]
        # area = (1*(0+0)/2) + (10*(0+100)/2) = 0 + 500 = 500; duration = 11
        result = time_weighted_mean(samples)
        self.assertAlmostEqual(result, 500.0 / 11.0)
        plain_mean = sum(v for _, v in samples) / len(samples)
        self.assertNotAlmostEqual(result, plain_mean)

    def test_unsorted_input_is_sorted_first(self):
        samples = [(10.0, 20.0), (0.0, 10.0)]
        self.assertEqual(time_weighted_mean(samples), 15.0)


class TestParseMemory(unittest.TestCase):

    # ------------------------------------------------------------------
    # Normal cases
    # ------------------------------------------------------------------

    def test_multi_sample_kb_string(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"vm_rss": "102400 kB"}},  # 100 MB
                {"status": {"vm_rss": "204800 kB"}},  # 200 MB
                {"status": {"vm_rss": "153600 kB"}},  # 150 MB
            ])
            result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["rssmax"], 200)

    def test_multi_sample_bare_integer(self):
        """vm_rss as bare integer (kB) should also work."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "status": {"vm_rss": 10240}},   # 10 MB
                {"timestamp": "2026-09-13T11:00:10.000+00:00", "status": {"vm_rss": 20480}},   # 20 MB
            ])
            result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["rssmax"], 20)
        # Two evenly-spaced points: time-weighted mean == mean of the two values.
        self.assertEqual(result["rssavg"], round((10240 + 20480) / 2 / 1024))

    def test_single_sample(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [{"timestamp": "2026-09-13T11:00:00.000+00:00", "status": {"vm_rss": "1048576 kB"}}])  # 1024 MB
            result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["rssmax"], 1024)
        self.assertEqual(result["rssavg"], 1024)

    def test_rounding(self):
        """Values that don't divide evenly should be rounded."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            # 1 kB -> rounds to 0 MB
            _write_jsonl(path, [{"timestamp": "2026-09-13T11:00:00.000+00:00", "status": {"vm_rss": "1 kB"}}])
            result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertIsInstance(result["rssmax"], int)
        self.assertIsInstance(result["rssavg"], int)

    # ------------------------------------------------------------------
    # rssavg (time-weighted mean)
    # ------------------------------------------------------------------

    def test_no_timestamps_omits_rss_avg(self):
        """Backward compat: lines without a timestamp still yield rssmax."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"vm_rss": "102400 kB"}},
                {"status": {"vm_rss": "204800 kB"}},
            ])
            result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["rssmax"], 200)
        self.assertNotIn("rssavg", result)

    def test_timestamps_produce_rss_avg(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "status": {"vm_rss": "102400 kB"}},
                {"timestamp": "2026-09-13T11:00:10.000+00:00", "status": {"vm_rss": "204800 kB"}},
            ])
            result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        # Two evenly-spaced points: time-weighted mean == mean of the two values.
        self.assertEqual(result["rssavg"], round((100 + 200) / 2))

    def test_uneven_timestamp_spacing_rss_avg(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "status": {"vm_rss": "0 kB"}},
                {"timestamp": "2026-09-13T11:00:01.000+00:00", "status": {"vm_rss": "0 kB"}},
                {"timestamp": "2026-09-13T11:00:11.000+00:00", "status": {"vm_rss": "102400 kB"}},
            ])
            result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        # area = 1*(0+0)/2 + 10*(0+100)/2 = 500 MB*s over 11s -> ~45 MB
        self.assertEqual(result["rssavg"], round(500 / 11))

    def test_invalid_timestamp_excluded_from_rss_avg_only(self):
        """A bad timestamp still counts toward rssmax, just not rssavg."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "not-a-timestamp", "status": {"vm_rss": "102400 kB"}},
                {"timestamp": "2026-09-13T11:00:10.000+00:00", "status": {"vm_rss": "204800 kB"}},
            ])
            result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["rssmax"], 200)
        # Only one line has a usable timestamp -> single-sample time-weighted mean.
        self.assertEqual(result["rssavg"], 200)

    # ------------------------------------------------------------------
    # Malformed / partial data
    # ------------------------------------------------------------------

    def test_skips_malformed_json_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"vm_rss": "10240 kB"}},
                "not json",
                {"status": {"vm_rss": "20480 kB"}},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["rssmax"], round(20480 / 1024))
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_skips_lines_missing_status_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"not_status": {"vm_rss": "10240 kB"}},
                {"status": {"vm_rss": "20480 kB"}},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["rssmax"], round(20480 / 1024))

    def test_skips_lines_missing_vm_rss_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"VmRSS": "10240 kB"}},     # wrong capitalisation
                {"status": {"vm_rss": "30720 kB"}},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["rssmax"], round(30720 / 1024))

    def test_all_lines_malformed_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, ["bad", "{malformed}"])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    # ------------------------------------------------------------------
    # Empty / missing file
    # ------------------------------------------------------------------

    def test_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_memory(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_nonexistent_file_returns_none(self):
        stderr_capture = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_capture):
            result = parse_memory("/tmp/does_not_exist_jbr_test_12345.jsonl")

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())


class TestParseArenaSummary(unittest.TestCase):
    MIB = 1024 * 1024

    def _write(self, tmp, obj):
        path = os.path.join(tmp, "arena-summary.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path

    def test_all_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"unit": "bytes", "combined": {
                "in_use_peak_bytes_avg": 3 * self.MIB, "dirty_bytes_avg": self.MIB // 2,
                "cached_bytes_avg": 0, "in_use_peak_bytes_max": 10 * self.MIB,
                "dirty_bytes_max": self.MIB, "cached_bytes_max": self.MIB // 3,
                "oom_end": {"256": 1}}, "block_sizes": []})
            self.assertEqual(parse_arena_summary(path), {
                "used_mb_max": 10.0, "used_mb_avg": 3.0, "dirty_mb_max": 1.0,
                "dirty_mb_avg": 0.5, "cached_mb_max": 0.33, "cached_mb_avg": 0.0})

    def test_missing_peak_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"combined": {"dirty_bytes_avg": self.MIB,
                                                  "dirty_bytes_max": self.MIB}})
            self.assertEqual(parse_arena_summary(path),
                             {"dirty_mb_max": 1.0, "dirty_mb_avg": 1.0})

    def test_missing_or_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(parse_arena_summary(os.path.join(tmp, "nope.json")))
            self.assertIsNone(parse_arena_summary(self._write(tmp, {"combined": {}})))
            with unittest.mock.patch("sys.stderr", io.StringIO()):
                self.assertIsNone(parse_arena_summary(self._write(tmp, "{broken")))


class TestParseMalloc(unittest.TestCase):

    # ------------------------------------------------------------------
    # Normal cases
    # ------------------------------------------------------------------

    def test_multi_sample(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"total_malloc": 100.0},
                {"total_malloc": 200.0},
                {"total_malloc": 150.0},
            ])
            result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["mallocmax"], 200.0)

    def test_single_sample(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [{"timestamp": "2026-09-13T11:00:00.000+00:00", "total_malloc": 48.83}])
            result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["mallocmax"], 48.83)
        self.assertEqual(result["mallocavg"], 48.83)

    def test_rounds_to_two_decimals(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "total_malloc": 1.111},
                {"timestamp": "2026-09-13T11:00:10.000+00:00", "total_malloc": 1.116},
            ])
            result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["mallocmax"], 1.12)
        # Two evenly-spaced points: time-weighted mean == mean of the two values.
        self.assertEqual(result["mallocavg"], round((1.111 + 1.116) / 2, 2))

    # ------------------------------------------------------------------
    # mallocavg (time-weighted mean)
    # ------------------------------------------------------------------

    def test_no_timestamps_omits_malloc_avg(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"total_malloc": 100.0},
                {"total_malloc": 200.0},
            ])
            result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["mallocmax"], 200.0)
        self.assertNotIn("mallocavg", result)

    def test_timestamps_produce_malloc_avg(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "total_malloc": 100.0},
                {"timestamp": "2026-09-13T11:00:10.000+00:00", "total_malloc": 200.0},
            ])
            result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        # Two evenly-spaced points: time-weighted mean == mean of the two values.
        self.assertEqual(result["mallocavg"], round((100.0 + 200.0) / 2, 2))

    def test_uneven_timestamp_spacing_malloc_avg(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "total_malloc": 0.0},
                {"timestamp": "2026-09-13T11:00:01.000+00:00", "total_malloc": 0.0},
                {"timestamp": "2026-09-13T11:00:11.000+00:00", "total_malloc": 100.0},
            ])
            result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        # area = 1*(0+0)/2 + 10*(0+100)/2 = 500 MB*s over 11s -> ~45.45 MB
        self.assertEqual(result["mallocavg"], round(500 / 11, 2))

    def test_invalid_timestamp_excluded_from_malloc_avg_only(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "not-a-timestamp", "total_malloc": 100.0},
                {"timestamp": "2026-09-13T11:00:10.000+00:00", "total_malloc": 200.0},
            ])
            result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["mallocmax"], 200.0)
        # Only one line has a usable timestamp -> single-sample time-weighted mean.
        self.assertEqual(result["mallocavg"], 200.0)

    # ------------------------------------------------------------------
    # Malformed / partial data
    # ------------------------------------------------------------------

    def test_skips_malformed_json_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"total_malloc": 10.0},
                "not json",
                {"total_malloc": 20.0},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["mallocmax"], 20.0)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_skips_lines_missing_total_malloc_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"total": 10.0},  # wrong key
                {"total_malloc": 30.0},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["mallocmax"], 30.0)

    def test_all_lines_malformed_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, ["bad", "{malformed}"])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    # ------------------------------------------------------------------
    # Empty / missing file
    # ------------------------------------------------------------------

    def test_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_malloc(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_nonexistent_file_returns_none(self):
        stderr_capture = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_capture):
            result = parse_malloc("/tmp/does_not_exist_jbr_test_12345.jsonl")

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())


class TestParsePageFaults(unittest.TestCase):

    def _parse(self, lines):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, lines)
            with unittest.mock.patch("sys.stderr", io.StringIO()):
                return parse_page_faults(path)
        finally:
            os.unlink(path)

    def test_last_sample_is_total(self):
        result = self._parse([
            {"proc_pid_stat": {"minflt": "3234", "majflt": "6"}},
            {"proc_pid_stat": {"minflt": "900000", "majflt": "70"}},
            {"proc_pid_stat": {"minflt": 4922269, "majflt": 72}},
        ])
        self.assertEqual(result, {"minflt": 4922269, "majflt": 72})

    def test_trailing_malformed_line_ignored(self):
        result = self._parse([
            {"proc_pid_stat": {"minflt": "100", "majflt": "1"}},
            {"proc_pid_stat": {"minflt": "200"}},
            "not json",
        ])
        self.assertEqual(result, {"minflt": 100, "majflt": 1})

    def test_large_counter_kept_exactly(self):
        big = 2 ** 63 + 1
        result = self._parse([{"proc_pid_stat": {"minflt": str(big), "majflt": "0"}}])
        self.assertEqual(result["minflt"], big)
        self.assertEqual(json.loads(json.dumps(result))["minflt"], big)

    def test_no_valid_samples_returns_none(self):
        self.assertIsNone(self._parse([{"status": {"threads": "10"}}]))

    def test_missing_file_returns_none(self):
        with unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertIsNone(parse_page_faults("/tmp/does_not_exist_jbr_test_12345.jsonl"))


class TestParseThreads(unittest.TestCase):

    # ------------------------------------------------------------------
    # Normal cases
    # ------------------------------------------------------------------

    def test_multi_sample_string(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"threads": "10"}},
                {"status": {"threads": "20"}},
                {"status": {"threads": "15"}},
            ])
            result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["osthreads_max"], 20)
        self.assertEqual(result["osthreads_avg"], round((10 + 20 + 15) / 3))

    def test_multi_sample_bare_integer(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"threads": 10}},
                {"status": {"threads": 20}},
            ])
            result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["osthreads_max"], 20)
        self.assertEqual(result["osthreads_avg"], 15)

    def test_single_sample(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [{"status": {"threads": "11"}}])
            result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["osthreads_max"], 11)
        self.assertEqual(result["osthreads_avg"], 11)

    def test_rounding(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"threads": "10"}},
                {"status": {"threads": "11"}},
            ])
            result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertIsInstance(result["osthreads_max"], int)
        self.assertIsInstance(result["osthreads_avg"], int)
        self.assertEqual(result["osthreads_avg"], round(21 / 2))

    # ------------------------------------------------------------------
    # Malformed / partial data
    # ------------------------------------------------------------------

    def test_skips_malformed_json_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"threads": "10"}},
                "not json",
                {"status": {"threads": "20"}},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["osthreads_max"], 20)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_skips_lines_missing_status_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"not_status": {"threads": "10"}},
                {"status": {"threads": "20"}},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["osthreads_max"], 20)

    def test_skips_lines_missing_threads_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"status": {"Threads": "10"}},  # wrong capitalisation
                {"status": {"threads": "30"}},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["osthreads_max"], 30)

    def test_all_lines_malformed_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, ["bad", "{malformed}"])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    # ------------------------------------------------------------------
    # Empty / missing file
    # ------------------------------------------------------------------

    def test_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_threads(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_nonexistent_file_returns_none(self):
        stderr_capture = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_capture):
            result = parse_threads("/tmp/does_not_exist_jbr_test_12345.jsonl")

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())



class TestParseCpu(unittest.TestCase):

    # ------------------------------------------------------------------
    # Normal cases
    # ------------------------------------------------------------------

    def test_multi_sample_max(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"pct_usr": "10.00", "pct_cpu": "12.00"},
                {"pct_usr": "30.00", "pct_cpu": "34.00"},
                {"pct_usr": "20.00", "pct_cpu": "22.00"},
            ])
            result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["pct_usr_max"], 30.0)
        self.assertEqual(result["pct_cpu_max"], 34.0)

    def test_all_five_metrics_present(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {
                    "timestamp": "2026-09-13T11:00:00.000+00:00",
                    "pct_usr": "1.00", "pct_system": "2.00", "pct_guest": "3.00",
                    "pct_wait": "4.00", "pct_cpu": "5.00",
                },
            ])
            result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        for metric in ("pct_usr", "pct_system", "pct_guest", "pct_wait", "pct_cpu"):
            self.assertIn(f"{metric}_max", result)
            self.assertIn(f"{metric}_avg", result)

    def test_rounds_to_two_decimals(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "pct_usr": "1.00"},
                {"timestamp": "2026-09-13T11:00:01.000+00:00", "pct_usr": "2.00"},
                {"timestamp": "2026-09-13T11:00:03.000+00:00", "pct_usr": "2.00"},
            ])
            result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        # area = 1*(1+2)/2 + 2*(2+2)/2 = 5.5 over 3s -> 1.8333...
        self.assertEqual(result["pct_usr_avg"], 1.83)

    def test_pct_can_exceed_100(self):
        """IRIX-mode pidstat: per-process CPU percentages can exceed 100."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [{"pct_cpu": "342.00"}])
            result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["pct_cpu_max"], 342.0)

    # ------------------------------------------------------------------
    # pct_xxx_avg (time-weighted mean)
    # ------------------------------------------------------------------

    def test_no_timestamps_omits_avg(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"pct_usr": "10.00"},
                {"pct_usr": "20.00"},
            ])
            result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["pct_usr_max"], 20.0)
        self.assertNotIn("pct_usr_avg", result)

    def test_timestamps_produce_avg(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "pct_usr": "10.00"},
                {"timestamp": "2026-09-13T11:00:10.000+00:00", "pct_usr": "20.00"},
            ])
            result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["pct_usr_avg"], 15.0)

    def test_uneven_timestamp_spacing_avg(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "pct_usr": "0.00"},
                {"timestamp": "2026-09-13T11:00:01.000+00:00", "pct_usr": "0.00"},
                {"timestamp": "2026-09-13T11:00:11.000+00:00", "pct_usr": "100.00"},
            ])
            result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        # area = 1*(0+0)/2 + 10*(0+100)/2 = 500 over 11s -> ~45.45
        self.assertEqual(result["pct_usr_avg"], round(500 / 11, 2))

    # ------------------------------------------------------------------
    # Malformed / partial data
    # ------------------------------------------------------------------

    def test_skips_malformed_json_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"pct_usr": "10.00"},
                "not json",
                {"pct_usr": "20.00"},
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["pct_usr_max"], 20.0)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_line_missing_a_metric_still_counts_others(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"timestamp": "2026-09-13T11:00:00.000+00:00", "pct_usr": "10.00", "pct_cpu": "12.00"},
                {"timestamp": "2026-09-13T11:00:01.000+00:00", "pct_usr": "20.00"},  # no pct_cpu on this line
            ])
            result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["pct_usr_max"], 20.0)
        self.assertEqual(result["pct_cpu_max"], 12.0)
        self.assertEqual(result["pct_cpu_avg"], 12.0)

    def test_line_with_no_metrics_warns_and_is_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, [
                {"pct_usr": "10.00"},
                {"pid": 123},  # no CPU metrics at all
            ])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNotNone(result)
        self.assertEqual(result["pct_usr_max"], 10.0)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_all_lines_malformed_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            _write_jsonl(path, ["bad", "{malformed}"])
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    # ------------------------------------------------------------------
    # Empty / missing file
    # ------------------------------------------------------------------

    def test_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            path = tf.name
        try:
            stderr_capture = io.StringIO()
            with unittest.mock.patch("sys.stderr", stderr_capture):
                result = parse_cpu(path)
        finally:
            os.unlink(path)

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_nonexistent_file_returns_none(self):
        stderr_capture = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_capture):
            result = parse_cpu("/tmp/does_not_exist_jbr_test_12345.jsonl")

        self.assertIsNone(result)
        self.assertIn("Warning", stderr_capture.getvalue())



class TestParseThreadCategories(unittest.TestCase):

    @staticmethod
    def _rec(ts, c2=2, c1=1, gc=4, mut=1, other=10, interval=None, cpu=None):
        """A thread-print.jsonl record; *cpu* maps category -> cpu_pct (default: null)."""
        cpu = cpu or {}
        counts = {"c2": c2, "c1": c1, "gc": gc, "mutator": mut, "other": other}
        rec = {"timestamp": ts, "interval_secs": interval, "num_threads": sum(counts.values())}
        for c, n in counts.items():
            rec[c] = {"num_threads": n, "cpu_pct": cpu.get(c)}
        rec["threads"] = []
        return rec

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch("sys.stderr", new_callable=io.StringIO):
                self.assertIsNone(parse_thread_categories(os.path.join(tmp, "nope.jsonl")))

    def test_max_and_time_weighted_avg(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            # mutator: 1 for 10s, then 1 -> 5 over 10s (trapezoid): area 10 + 30 = 40 over 20s = 2.0
            _write_jsonl(path, [
                self._rec("2026-09-22T20:29:00.000+02:00", mut=1),
                self._rec("2026-09-22T20:29:10.000+02:00", mut=1),
                self._rec("2026-09-22T20:29:20.000+02:00", mut=5),
            ])
            result = parse_thread_categories(path)
        self.assertEqual(result["mutator"], {"num_threads_max": 5, "num_threads_avg": 2.0})
        self.assertEqual(result["c2"], {"num_threads_max": 2, "num_threads_avg": 2.0})
        self.assertEqual(result["c1"]["num_threads_max"], 1)
        self.assertEqual(result["gc"]["num_threads_max"], 4)
        self.assertEqual(result["other"]["num_threads_max"], 10)
        self.assertEqual(set(result), {"c2", "c1", "gc", "mutator", "other"})

    def test_avg_rounded_to_one_decimal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            # c2: 2 for 20s, then 2 -> 3 over 10s: area 40 + 25 = 65 over 30s = 2.1666...
            _write_jsonl(path, [
                self._rec("2026-09-22T20:29:00.000+02:00", c2=2),
                self._rec("2026-09-22T20:29:20.000+02:00", c2=2),
                self._rec("2026-09-22T20:29:30.000+02:00", c2=3),
            ])
            result = parse_thread_categories(path)
        self.assertEqual(result["c2"]["num_threads_avg"], 2.2)

    def test_avg_omitted_without_valid_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            _write_jsonl(path, [self._rec("garbage", gc=3)])
            result = parse_thread_categories(path)
        self.assertEqual(result["gc"], {"num_threads_max": 3})

    def test_cpu_avg_weighted_by_interval_and_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            # gc: 100 % for 1 s, then 10 % for 3 s -> (100 + 30) / 4 = 32.5
            _write_jsonl(path, [
                self._rec("2026-09-22T20:29:00.000+02:00"),
                self._rec("2026-09-22T20:29:01.000+02:00", interval=1.0, cpu={"gc": 100.0}),
                self._rec("2026-09-22T20:29:04.000+02:00", interval=3.0, cpu={"gc": 10.0}),
            ])
            result = parse_thread_categories(path)
        self.assertEqual(result["gc"]["cpu_pct_avg"], 32.5)
        self.assertEqual(result["gc"]["cpu_pct_max"], 100.0)
        self.assertNotIn("cpu_pct_avg", result["c2"])  # no CPU values for c2

    def test_unaccounted_cpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            recs = [
                self._rec("2026-09-22T20:29:00.000+02:00"),
                self._rec("2026-09-22T20:29:01.000+02:00", interval=1.0, cpu={"gc": 1.0}),
                self._rec("2026-09-22T20:29:04.000+02:00", interval=3.0, cpu={"gc": 1.0}),
            ]
            for rec, pct in zip(recs, (None, 40.0, -4.0)):
                rec["unaccounted"] = {"cpu_pct": pct}
            _write_jsonl(path, recs)
            result = parse_thread_categories(path)
        # (40 * 1 - 4 * 3) / 4 = 7.0
        self.assertEqual(result["unaccounted"], {"cpu_pct_avg": 7.0, "cpu_pct_max": 40.0})

    def test_no_unaccounted_without_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            _write_jsonl(path, [self._rec("2026-09-22T20:29:00.000+02:00"),
                                self._rec("2026-09-22T20:29:01.000+02:00", interval=1.0,
                                          cpu={"gc": 1.0})])
            result = parse_thread_categories(path)
        self.assertNotIn("unaccounted", result)

    def test_cpu_omitted_for_single_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            _write_jsonl(path, [self._rec("2026-09-22T20:29:00.000+02:00")])
            result = parse_thread_categories(path)
        self.assertEqual(set(result["gc"]), {"num_threads_max", "num_threads_avg"})

    def test_malformed_lines_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            _write_jsonl(path, ["not json", {"timestamp": "2026-09-22T20:29:00.000+02:00"},
                                self._rec("2026-09-22T20:29:10.000+02:00", c1=3)])
            with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as stderr_capture:
                result = parse_thread_categories(path)
        self.assertEqual(result["c1"]["num_threads_max"], 3)
        self.assertIn("Warning", stderr_capture.getvalue())

    def test_no_valid_samples_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "thread-print.jsonl")
            _write_jsonl(path, ["bad", "also bad"])
            with unittest.mock.patch("sys.stderr", new_callable=io.StringIO):
                self.assertIsNone(parse_thread_categories(path))


if __name__ == "__main__":
    unittest.main()
