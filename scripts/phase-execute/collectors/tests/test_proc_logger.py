#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-execute/collectors/proc-logger.py"""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


_SCRIPTS_DIR = str(Path(__file__).resolve().parents[3])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from common.collector_probe import unavailable_reason  # noqa: E402


def setUpModule():
    # Same machine check jbr-prepare uses (PRD.md D-82).
    reason = unavailable_reason("proc")
    if reason is not None:
        raise unittest.SkipTest(reason)


COLLECTOR = Path(__file__).resolve().parent.parent / "proc-logger.py"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")

def run(args, timeout=15):
    return subprocess.run(
        [sys.executable, str(COLLECTOR)] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


DELIM_RE = re.compile(r"^=== /proc/(\d+)/(stat|status) ===$")


def parse_samples(stdout):
    """Split the raw stream into [(timestamp, {"stat": text, "status": text}), ...]."""
    samples = []
    current = None
    for line in stdout.splitlines(keepends=True):
        if TS_RE.match(line.strip()):
            current = (line.strip(), {})
            samples.append(current)
            section = None
            continue
        m = DELIM_RE.match(line.strip())
        if m:
            section = m.group(2)
            current[1][section] = ""
        elif section is not None:
            current[1][section] += line
    return samples


class TestProcLoggerBasic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import subprocess as sp
        cls.target = sp.Popen(["sleep", "60"])

    @classmethod
    def tearDownClass(cls):
        cls.target.terminate()
        cls.target.wait()

    def _run1(self):
        return run([str(self.target.pid), "-n", "1", "-i", "0"])

    def test_exit_zero(self):
        result = self._run1()
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_starts_with_timestamp_then_stat_delimiter(self):
        lines = self._run1().stdout.splitlines()
        self.assertRegex(lines[0], TS_RE)
        self.assertEqual(lines[1], f"=== /proc/{self.target.pid}/stat ===")

    def test_single_shot_one_sample_with_both_sections(self):
        samples = parse_samples(self._run1().stdout)
        self.assertEqual(len(samples), 1)
        self.assertEqual(sorted(samples[0][1]), ["stat", "status"])

    def test_sections_are_raw_proc_content(self):
        sections = parse_samples(self._run1().stdout)[0][1]
        self.assertTrue(sections["stat"].startswith(f"{self.target.pid} (sleep) "))
        self.assertIn("Name:\tsleep", sections["status"])
        self.assertIn("VmRSS:", sections["status"])

    def test_two_shots(self):
        result = run([str(self.target.pid), "-n", "2", "-i", "0"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(len(parse_samples(result.stdout)), 2)


class TestProcLoggerProcessGone(unittest.TestCase):

    def test_process_exits_mid_run(self):
        import subprocess as sp
        # Start a process that dies after ~1 s; run the collector asking for
        # many fast samples so the proc disappears under it.
        p = sp.Popen(["sleep", "1"])
        pid = p.pid
        result = run([str(pid), "-n", "500", "-i", "0"], timeout=15)
        p.wait()
        # Collector must exit cleanly whether or not it managed any samples
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_exits_promptly_with_long_interval(self):
        import threading
        import time
        p = subprocess.Popen(["sleep", "2"])
        # Reap it as soon as it exits: a zombie would still count as alive.
        threading.Thread(target=p.wait, daemon=True).start()
        start = time.monotonic()
        try:
            result = run([str(p.pid), "-i", "60"], timeout=15)
        finally:
            p.kill()
            p.wait()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertLess(time.monotonic() - start, 10)


class TestProcLoggerArgErrors(unittest.TestCase):

    def test_invalid_pid_zero(self):
        result = run(["0"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid", result.stderr.lower())

    def test_invalid_pid_negative(self):
        result = run(["-1"])
        # argparse treats -1 as an unknown option; either way it must fail
        self.assertNotEqual(result.returncode, 0)

    def test_nonexistent_pid(self):
        result = run(["999999999"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_missing_pid(self):
        result = run([])
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
