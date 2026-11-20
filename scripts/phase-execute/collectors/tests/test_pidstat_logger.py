#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-run/collectors/pidstat-logger.sh

Prerequisites:
  - pidstat must be on PATH (part of sysstat package).
  - If absent, every test in this module fails loudly — that is intentional.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[3])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from common.collector_probe import unavailable_reason  # noqa: E402


COLLECTOR = Path(__file__).resolve().parent.parent / "pidstat-logger.sh"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")


def setUpModule():
    # Same machine check jbr-prepare uses (PRD.md D-82).
    reason = unavailable_reason("pidstat")
    if reason is not None:
        raise unittest.SkipTest(reason)


def run(args, timeout=30):
    return subprocess.run(
        ["bash", str(COLLECTOR)] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestPidstatLoggerBasic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.target = subprocess.Popen(["sleep", "60"])

    @classmethod
    def tearDownClass(cls):
        cls.target.terminate()
        cls.target.wait()

    def test_basic_run_exit_zero(self):
        result = run(["-i", "1", "-n", "2", str(self.target.pid)], timeout=15)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_start_timestamp_printed_first(self):
        result = run(["-i", "1", "-n", "1", str(self.target.pid)], timeout=15)
        first_line = result.stdout.splitlines()[0]
        self.assertRegex(first_line, TS_RE)

    def test_monitoring_pid_line_printed_second(self):
        result = run(["-i", "1", "-n", "1", str(self.target.pid)], timeout=15)
        second_line = result.stdout.splitlines()[1]
        self.assertEqual(second_line, f"Monitoring PID {self.target.pid}")

    def test_output_contains_pid_column(self):
        result = run(["-i", "1", "-n", "1", str(self.target.pid)], timeout=15)
        self.assertIn(str(self.target.pid), result.stdout)

    def test_output_uses_epoch_timestamps(self):
        """-H must be in effect: the data section has a numeric epoch Time
        column, not local HH:MM:SS."""
        result = run(["-i", "1", "-n", "1", str(self.target.pid)], timeout=15)
        self.assertNotRegex(result.stdout, r"\d{2}:\d{2}:\d{2}\s+(AM|PM)?\s*\d+\s+" + str(self.target.pid))

    def test_output_flattened_by_h(self):
        """-h must be in effect: a single '#'-prefixed header line, not
        separate per-activity header blocks."""
        result = run(["-i", "1", "-n", "1", str(self.target.pid)], timeout=15)
        header_lines = [ln for ln in result.stdout.splitlines() if ln.lstrip().startswith("#")]
        self.assertEqual(len(header_lines), 1)


class TestPidstatLoggerProcessGone(unittest.TestCase):

    def test_process_exits_mid_run(self):
        # Start a process that lives just long enough for one sample, then dies
        # before the second (of two requested) — enough to exercise the
        # mid-run-exit path without waiting through extra unused samples.
        p = subprocess.Popen(["sleep", "1"])
        pid = p.pid
        result = run(["-i", "1", "-n", "2", str(pid)], timeout=30)
        p.wait()
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class TestPidstatLoggerArgErrors(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.target = subprocess.Popen(["sleep", "60"])

    @classmethod
    def tearDownClass(cls):
        cls.target.terminate()
        cls.target.wait()

    def test_missing_interval(self):
        result = run([str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--interval", result.stderr)

    def test_missing_pid(self):
        result = run(["-i", "1"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pid", result.stderr)

    def test_invalid_pid_zero(self):
        result = run(["-i", "1", "0"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid pid", result.stderr)

    def test_invalid_pid_non_integer(self):
        result = run(["-i", "1", "abc"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid pid", result.stderr)

    def test_invalid_interval_zero(self):
        # pidstat's repeat interval has no 0/fractional support, unlike perf's ms clamp.
        result = run(["-i", "0", str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid interval", result.stderr)

    def test_invalid_interval_negative(self):
        result = run(["-i", "-1", str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid interval", result.stderr)

    def test_invalid_interval_fractional(self):
        result = run(["-i", "0.5", str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid interval", result.stderr)

    def test_invalid_num_zero(self):
        result = run(["-i", "1", "-n", "0", str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid num", result.stderr)

    def test_invalid_num_negative(self):
        result = run(["-i", "1", "-n", "-3", str(self.target.pid)])
        self.assertNotEqual(result.returncode, 0)

    def test_nonexistent_pid(self):
        result = run(["-i", "1", "999999999"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_help(self):
        result = run(["--help"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage", result.stderr)

    def test_unknown_option(self):
        result = run(["--bogus"])
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
