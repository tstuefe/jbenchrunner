#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-run/collectors/perf-logger.sh

Prerequisites (checked at module load):
  - 'perf' must be on PATH — missing perf raises an error.
  - Kernel perf_event_paranoia must be <= 1, OR the process must have CAP_PERFMON / CAP_SYS_ADMIN.
    If not satisfied the entire module is skipped (not counted as an error).
    Check with:  cat /proc/sys/kernel/perf_event_paranoid
    Adjust with: sudo sysctl kernel.perf_event_paranoid=1
"""

import subprocess
import sys
import unittest
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[3])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from common.collector_probe import unavailable_reason  # noqa: E402


COLLECTOR = Path(__file__).resolve().parent.parent / "perf-logger.sh"
BASIC_COUNTERS = "cycles,instructions"


def setUpModule():
    # Same machine check jbr-prepare uses (PRD.md D-82).
    reason = unavailable_reason("perf")
    if reason is not None:
        raise unittest.SkipTest(reason)
    try:
        paranoia = int(Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    except Exception:
        paranoia = 999
    if paranoia > 1:
        raise unittest.SkipTest(
            f"kernel.perf_event_paranoid={paranoia} — tests require <= 1. "
            "Run: sudo sysctl kernel.perf_event_paranoid=1"
        )


def run(args, timeout=30):
    return subprocess.run(
        ["bash", str(COLLECTOR)] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestPerfLoggerCheckRealPerf(unittest.TestCase):
    """--check against the real perf (PRD.md D-86)."""

    def test_known_counters_pass(self):
        result = run(["--check", "--counters", BASIC_COUNTERS])
        self.assertEqual(result.returncode, 0, msg=result.stdout)
        self.assertIn("cycles", result.stdout)

    def test_unknown_counter_fails_naming_it(self):
        result = run(["--check", "--counters", "cycles,cyclez"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cyclez", result.stdout)


class TestPerfLoggerBasic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.target = subprocess.Popen(["sleep", "60"])

    @classmethod
    def tearDownClass(cls):
        cls.target.terminate()
        cls.target.wait()

    def test_basic_run_exit_zero(self):
        result = run(["-i", "1", "-n", "2", "--counters", BASIC_COUNTERS, str(self.target.pid)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_output_contains_counter_name(self):
        result = run(["-i", "1", "-n", "1", "--counters", BASIC_COUNTERS, str(self.target.pid)])
        combined = result.stdout + result.stderr
        self.assertIn("cycles", combined)

    def test_output_is_csv(self):
        # -x ';': every data line is "<time>;<value>;<unit>;<event>;..."
        result = run(["-i", "1", "-n", "1", "--counters", BASIC_COUNTERS, str(self.target.pid)])
        data = [l for l in result.stdout.splitlines()[2:] if l and not l.startswith("#")]
        self.assertTrue(data, msg=result.stdout)
        self.assertTrue(any(l.split(";")[3].startswith("cycles") for l in data), msg=result.stdout)

    def test_without_counters_uses_default_set(self):
        result = run(["-i", "1", "-n", "1", str(self.target.pid)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("task-clock", result.stdout)

    def test_interval_zero_clamped(self):
        # -i 0 should be clamped to 1ms internally, not crash
        result = run(["-i", "0", "-n", "1", "--counters", BASIC_COUNTERS, str(self.target.pid)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class TestPerfLoggerArgErrors(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.target = subprocess.Popen(["sleep", "60"])

    @classmethod
    def tearDownClass(cls):
        cls.target.terminate()
        cls.target.wait()

    def test_missing_interval(self):
        result = run(["--counters", BASIC_COUNTERS, str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--interval", result.stderr)

    def test_missing_pid(self):
        result = run(["-i", "1", "--counters", BASIC_COUNTERS])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pid", result.stderr)

    def test_invalid_pid_zero(self):
        result = run(["-i", "1", "--counters", BASIC_COUNTERS, "0"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid pid", result.stderr)

    def test_invalid_pid_negative(self):
        result = run(["-i", "1", "--counters", BASIC_COUNTERS, "-5"])
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_pid_non_integer(self):
        result = run(["-i", "1", "--counters", BASIC_COUNTERS, "abc"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid pid", result.stderr)

    def test_invalid_interval_negative(self):
        result = run(["-i", "-1", "--counters", BASIC_COUNTERS, str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid interval", result.stderr)

    def test_invalid_interval_non_numeric(self):
        result = run(["-i", "foo", "--counters", BASIC_COUNTERS, str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid interval", result.stderr)

    def test_invalid_num_zero(self):
        result = run(["-i", "1", "-n", "0", "--counters", BASIC_COUNTERS, str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid num", result.stderr)

    def test_invalid_num_negative(self):
        result = run(["-i", "1", "-n", "-3", "--counters", BASIC_COUNTERS, str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)

    def test_help(self):
        result = run(["--help"])
        # usage() calls exit 1 in this script
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage", result.stderr)

    def test_unknown_option(self):
        result = run(["--bogus"])
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
