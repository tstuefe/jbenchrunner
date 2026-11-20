#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-post-process/parse_dacapo_results.py
"""

import json
import math
import os
import statistics
import subprocess
import sys
import unittest

_HERE = os.path.dirname(__file__)
_PHASE_PP_DIR = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _PHASE_PP_DIR)

from parse_dacapo_results import parse_dacapo_output

SCRIPT_PATH = os.path.join(_PHASE_PP_DIR, "parse_dacapo_results.py")
EXAMPLES_DIR = os.path.join(_HERE, "parse_dacapo_results", "input", "successful")
FAILED_DIR   = os.path.join(_HERE, "parse_dacapo_results", "input", "failed")


class TestParseDacapoResults(unittest.TestCase):

    def test_parse_pmd_example(self):
        pmd_path = os.path.join(EXAMPLES_DIR, "dacapo-pmd.out")
        with open(pmd_path, "r", encoding="utf-8") as f:
            content = f.read()

        result = parse_dacapo_output(content)
        self.assertEqual(result["result_ms"], 1353)
        self.assertEqual(len(result["iterations"]), 4)
        for it in result["iterations"]:
            self.assertIn("start_ts", it)

    def test_parse_tomcat_example(self):
        tomcat_path = os.path.join(EXAMPLES_DIR, "dacapo-tomcat.out")
        with open(tomcat_path, "r", encoding="utf-8") as f:
            content = f.read()

        result = parse_dacapo_output(content)
        self.assertEqual(result["result_ms"], 3563)
        self.assertEqual(len(result["iterations"]), 4)

        # Check iteration 1 has timestamp and latency
        it1 = result["iterations"][0]
        self.assertEqual(it1["n"], 1)
        self.assertEqual(it1["time_ms"], 5326)
        self.assertIn("start_ts", it1)
        self.assertIn("latency_usec", it1)

        # Check final iteration
        it4 = result["iterations"][3]
        self.assertEqual(it4["n"], 4)
        self.assertEqual(it4["time_ms"], 3563)
        self.assertIn("start_ts", it4)

    def test_parse_all_successful_examples(self):
        example_files = [
            os.path.join(EXAMPLES_DIR, f)
            for f in os.listdir(EXAMPLES_DIR)
            if f.endswith(".out")
        ]
        self.assertGreaterEqual(len(example_files), 10)
        for filepath in example_files:
            with self.subTest(filepath=filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                result = parse_dacapo_output(content)
                self.assertIn("result_ms", result)
                self.assertIsInstance(result["result_ms"], int)
                self.assertIn("iterations", result)
                self.assertGreaterEqual(len(result["iterations"]), 1)
                for it in result["iterations"]:
                    self.assertIn("start_ts", it,
                                  msg=f"iteration {it['n']} missing start_ts in {filepath}")

    def test_failed_runs_raise_error(self):
        failed_files = [
            os.path.join(FAILED_DIR, f)
            for f in os.listdir(FAILED_DIR)
            if f.endswith(".out")
        ]
        self.assertGreaterEqual(len(failed_files), 1)
        for filepath in failed_files:
            with self.subTest(filepath=filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                with self.assertRaises(ValueError):
                    parse_dacapo_output(content)

    def test_failed_run_cli_exits_nonzero(self):
        failed_files = [
            os.path.join(FAILED_DIR, f)
            for f in os.listdir(FAILED_DIR)
            if f.endswith(".out")
        ]
        self.assertGreaterEqual(len(failed_files), 1)
        for filepath in failed_files:
            with self.subTest(filepath=filepath):
                res = subprocess.run(
                    [sys.executable, SCRIPT_PATH, filepath],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("Error", res.stderr)

    def test_single_iteration_run(self):
        sample = """
        ===== DaCapo 23.11-MR2-chopin fop starting =====
        ===== DaCapo 23.11-MR2-chopin fop PASSED in 850 msec =====
        """
        result = parse_dacapo_output(sample)
        self.assertEqual(
            result,
            {
                "iterations": [{"n": 1, "warmup": False, "time_ms": 850}],
                "result_ms": 850,
            },
        )

    def test_timestamps_parsed(self):
        sample = (
            ">>> Warmup 1 started at 2026-09-04T08:46:47.567378316Z.\n"
            "===== DaCapo 23.11-MR2-chopin pmd starting warmup 1 =====\n"
            "===== DaCapo 23.11-MR2-chopin pmd completed warmup 1 in 3629 msec =====\n"
            ">>> Warmup 2 started at 2026-09-04T08:46:51.294805536Z.\n"
            "===== DaCapo 23.11-MR2-chopin pmd starting warmup 2 =====\n"
            "===== DaCapo 23.11-MR2-chopin pmd completed warmup 2 in 1598 msec =====\n"
            ">>> Final started at 2026-09-04T08:46:54.513046249Z.\n"
            "===== DaCapo 23.11-MR2-chopin pmd starting =====\n"
            "===== DaCapo 23.11-MR2-chopin pmd PASSED in 1353 msec =====\n"
        )
        result = parse_dacapo_output(sample)
        self.assertEqual(len(result["iterations"]), 3)
        self.assertTrue(result["iterations"][0]["warmup"])
        self.assertTrue(result["iterations"][1]["warmup"])
        self.assertFalse(result["iterations"][2]["warmup"])
        self.assertEqual(result["iterations"][0]["start_ts"], "2026-09-04T08:46:47.567378316Z")
        self.assertEqual(result["iterations"][1]["start_ts"], "2026-09-04T08:46:51.294805536Z")
        self.assertEqual(result["iterations"][2]["start_ts"], "2026-09-04T08:46:54.513046249Z")
        self.assertEqual(result["first_iteration_ts"], "2026-09-04T08:46:47.567378316Z")
        self.assertEqual(result["final_iteration_ts"], "2026-09-04T08:46:54.513046249Z")

    def test_timestamps_with_offset(self):
        """Timestamps with a local-time offset (not Z) are stored verbatim."""
        sample = (
            ">>> Final started at 2026-09-04T10:46:54.513046249+02:00.\n"
            "===== DaCapo 23.11-MR2-chopin fop starting =====\n"
            "===== DaCapo 23.11-MR2-chopin fop PASSED in 850 msec =====\n"
        )
        result = parse_dacapo_output(sample)
        self.assertEqual(result["iterations"][0]["start_ts"], "2026-09-04T10:46:54.513046249+02:00")

    def test_no_timestamps_still_parses(self):
        """Output without timestamp extension lines parses fine, no start_ts key."""
        sample = (
            "===== DaCapo 23.11-MR2-chopin pmd completed warmup 1 in 1234 msec =====\n"
            "===== DaCapo 23.11-MR2-chopin pmd PASSED in 5678 msec =====\n"
        )
        result = parse_dacapo_output(sample)
        self.assertTrue(result["iterations"][0]["warmup"])
        self.assertFalse(result["iterations"][1]["warmup"])
        self.assertNotIn("start_ts", result["iterations"][0])
        self.assertNotIn("start_ts", result["iterations"][1])
        self.assertNotIn("first_iteration_ts", result)
        self.assertNotIn("final_iteration_ts", result)

    def test_single_iteration_with_latency(self):
        sample = """
        ===== DaCapo 23.11-MR2-chopin kafka starting =====
        ===== DaCapo 23.11-MR2-chopin kafka PASSED in 1200 msec =====
        ===== DaCapo tail latency, simple: 50% 100 usec, 90% 200 usec, 99% 300 usec, 99.9% 400 usec, 99.99% 500 usec, max 600 usec, measured over 1000 events =====
        ===== DaCapo tail latency, metered 100ms smoothing: 50% 110 usec, 90% 210 usec, 99% 310 usec, 99.9% 410 usec, 99.99% 510 usec, max 610 usec, measured over 1000 events =====
        ===== DaCapo tail latency, metered full smoothing: 50% 120 usec, 90% 220 usec, 99% 320 usec, 99.9% 420 usec, 99.99% 520 usec, max 620 usec, measured over 1000 events =====
        """
        result = parse_dacapo_output(sample)
        self.assertEqual(result["result_ms"], 1200)
        self.assertEqual(len(result["iterations"]), 1)
        it1 = result["iterations"][0]
        self.assertEqual(it1["n"], 1)
        self.assertEqual(it1["time_ms"], 1200)
        self.assertEqual(it1["latency_usec"]["simple"]["p50"], 100)
        self.assertEqual(it1["latency_usec"]["smooth_100ms"]["p50"], 110)
        self.assertEqual(it1["latency_usec"]["smooth_full"]["p50"], 120)

    def _make_log(self, warmup_times, final_time):
        lines = []
        for i, t in enumerate(warmup_times, start=1):
            lines.append(
                f"===== DaCapo 23.11-MR2-chopin pmd completed warmup {i} in {t} msec ====="
            )
        lines.append(f"===== DaCapo 23.11-MR2-chopin pmd PASSED in {final_time} msec =====")
        return "\n".join(lines) + "\n"

    def test_stability_metrics_present_at_40_iterations(self):
        # 39 warmups + 1 final = 40 iterations. Only the last 10 (warmups
        # 30..39 plus the final) feed the stability metrics.
        warmup_times = [500] * 30 + [110, 120, 90, 105, 95, 115, 100, 108, 92]
        final_time = 103
        result = parse_dacapo_output(self._make_log(warmup_times, final_time))

        self.assertEqual(len(result["iterations"]), 40)
        last_10 = warmup_times[-9:] + [final_time]
        raw_stddev = statistics.stdev(last_10)
        expected_stddev = round(raw_stddev, 2)
        expected_stderr = round(raw_stddev / math.sqrt(10), 2)

        self.assertEqual(result["stddev_ms"], expected_stddev)
        self.assertEqual(result["stderr_ms"], expected_stderr)
        expected_rel = round(raw_stddev / math.sqrt(10) / statistics.mean(last_10) * 100, 2)
        self.assertEqual(result["stderr_pct"], expected_rel)

    def test_stability_metrics_tail_is_staggered(self):
        # (iteration count, expected tail length): >= 40 -> 10, >= 30 -> 7,
        # >= 20 -> 5, each boundary checked from both sides.
        for num_iterations, tail_len in ((39, 7), (30, 7), (29, 5), (20, 5)):
            with self.subTest(num_iterations=num_iterations):
                times = [500] * num_iterations
                times[-tail_len:] = [100 + 7 * (i % 3) for i in range(tail_len)]
                result = parse_dacapo_output(self._make_log(times[:-1], times[-1]))

                self.assertEqual(len(result["iterations"]), num_iterations)
                tail = times[-tail_len:]
                raw_stddev = statistics.stdev(tail)
                self.assertEqual(result["stddev_ms"], round(raw_stddev, 2))
                self.assertEqual(result["stderr_ms"],
                                 round(raw_stddev / math.sqrt(tail_len), 2))

    def test_stability_metrics_absent_below_20_iterations(self):
        # 18 warmups + 1 final = 19 iterations: one short of the threshold.
        warmup_times = [500] * 18
        result = parse_dacapo_output(self._make_log(warmup_times, 103))

        self.assertEqual(len(result["iterations"]), 19)
        self.assertNotIn("stddev_ms", result)
        self.assertNotIn("stderr_ms", result)
        self.assertNotIn("stderr_pct", result)

    def test_stability_metrics_absent_for_all_successful_examples(self):
        # None of the bundled examples reach 20 iterations, so none should
        # carry stability metrics.
        example_files = [
            os.path.join(EXAMPLES_DIR, f)
            for f in os.listdir(EXAMPLES_DIR)
            if f.endswith(".out")
        ]
        for filepath in example_files:
            with self.subTest(filepath=filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                result = parse_dacapo_output(content)
                if len(result["iterations"]) < 20:
                    self.assertNotIn("stddev_ms", result)
                    self.assertNotIn("stderr_ms", result)

    @staticmethod
    def _latency_lines(p99_simple, p99_smooth):
        return (
            f"===== DaCapo tail latency, simple: 50% 100 usec, 90% 200 usec, 99% {p99_simple} usec, "
            "99.9% 400 usec, 99.99% 500 usec, max 600 usec, measured over 1000 events =====\n"
            f"===== DaCapo tail latency, metered 100ms smoothing: 50% 110 usec, 90% 210 usec, 99% {p99_smooth} usec, "
            "99.9% 410 usec, 99.99% 510 usec, max 610 usec, measured over 1000 events =====\n"
            "===== DaCapo tail latency, metered full smoothing: 50% 120 usec, 90% 220 usec, 99% 320 usec, "
            "99.9% 420 usec, 99.99% 520 usec, max 620 usec, measured over 1000 events =====\n"
        )

    def _make_latency_log(self, p99s_simple, p99s_smooth):
        n = len(p99s_simple)
        lines = []
        for i in range(1, n):
            lines.append(f"===== DaCapo 23.11-MR2-chopin tomcat completed warmup {i} in 1000 msec =====\n")
            lines.append(self._latency_lines(p99s_simple[i - 1], p99s_smooth[i - 1]))
        lines.append("===== DaCapo 23.11-MR2-chopin tomcat PASSED in 1000 msec =====\n")
        lines.append(self._latency_lines(p99s_simple[-1], p99s_smooth[-1]))
        return "".join(lines)

    def test_latency_summary_from_final_iteration(self):
        result = parse_dacapo_output(self._make_latency_log([999, 300], [999, 310]))
        self.assertEqual(result["p50"], 100)
        self.assertEqual(result["p99"], 300)
        self.assertEqual(result["p100"], 600)
        self.assertEqual(result["p50_smooth"], 110)
        self.assertEqual(result["p99_smooth"], 310)
        self.assertEqual(result["p100_smooth"], 610)
        # Fewer than 20 iterations: no p99 stderr.
        for key in ("p99_stderr", "p99_stderr_pct", "p99_smooth_stderr", "p99_smooth_stderr_pct"):
            self.assertNotIn(key, result)

    def test_latency_p99_stderr_at_40_iterations(self):
        p99s_simple = [5000] * 30 + [300, 320, 280, 310, 290, 305, 295, 315, 285, 300]
        p99s_smooth = [5000] * 30 + [400, 410, 390, 405, 395, 400, 420, 380, 400, 400]
        result = parse_dacapo_output(self._make_latency_log(p99s_simple, p99s_smooth))
        self.assertEqual(len(result["iterations"]), 40)
        for suffix, values in (("", p99s_simple[-10:]), ("_smooth", p99s_smooth[-10:])):
            stderr = statistics.stdev(values) / math.sqrt(10)
            self.assertEqual(result[f"p99{suffix}_stderr"], round(stderr, 2))
            self.assertEqual(result[f"p99{suffix}_stderr_pct"],
                             round(stderr / statistics.mean(values) * 100, 2))

    def test_latency_summary_absent_without_latency(self):
        result = parse_dacapo_output(self._make_log([500] * 39, 103))
        for key in ("p50", "p99", "p100", "p50_smooth", "p99_smooth", "p100_smooth",
                    "p99_stderr", "p99_stderr_pct", "p99_smooth_stderr", "p99_smooth_stderr_pct"):
            self.assertNotIn(key, result)

    def test_missing_passed_line_raises_error(self):
        sample = """
        ===== DaCapo 23.11-MR2-chopin pmd completed warmup 1 in 3308 msec =====
        ===== DaCapo 23.11-MR2-chopin pmd completed warmup 2 in 1442 msec =====
        Exception in thread "main" java.lang.RuntimeException: Failure
        """
        with self.assertRaises(ValueError):
            parse_dacapo_output(sample)

    def test_empty_content_raises_error(self):
        with self.assertRaises(ValueError):
            parse_dacapo_output("")

    def test_cli_execution_success(self):
        pmd_path = os.path.join(EXAMPLES_DIR, "dacapo-pmd.out")  # EXAMPLES_DIR points to successful/
        res = subprocess.run(
            [sys.executable, SCRIPT_PATH, pmd_path],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        parsed = json.loads(res.stdout)
        self.assertEqual(parsed["result_ms"], 1353)

    def test_cli_execution_file_not_found(self):
        res = subprocess.run(
            [sys.executable, SCRIPT_PATH, "non_existent_file.out"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Error: file not found", res.stderr)

    def test_cli_help(self):
        res = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("usage:", res.stdout.lower())


if __name__ == "__main__":
    unittest.main()
