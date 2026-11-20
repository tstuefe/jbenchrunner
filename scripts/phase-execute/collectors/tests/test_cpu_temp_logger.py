#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-run/collectors/cpu-temp-logger.py

Prerequisites:
  - read-cpu-temp.sh must succeed on this machine (correct arch, sensors available);
    if a probe read fails, the module is skipped.
"""

import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path


_SCRIPTS_DIR = str(Path(__file__).resolve().parents[3])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from common.collector_probe import unavailable_reason  # noqa: E402


def setUpModule():
    # Same machine check jbr-prepare uses (PRD.md D-82).
    reason = unavailable_reason("cpu_temp")
    if reason is not None:
        raise unittest.SkipTest(reason)


COLLECTOR = Path(__file__).resolve().parent.parent / "cpu-temp-logger.py"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")

# The PID is only a liveness anchor; this (live) test process serves as the target.
LIVE_PID = str(os.getpid())


def run(*args, timeout=15):
    return subprocess.run(
        [sys.executable, str(COLLECTOR)] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_records(stdout):
    """Parse a JSON Lines stream."""
    lines = stdout.splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError("JSON Lines output contains an empty line")
    return [json.loads(line) for line in lines]


class TestCpuTempLoggerSingleShot(unittest.TestCase):

    def test_single_shot_exit_zero(self):
        result = run("-n", "1", "-i", "0", LIVE_PID)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_single_shot_valid_json(self):
        result = run("-n", "1", "-i", "0", LIVE_PID)
        records = parse_records(result.stdout)
        self.assertEqual(len(records), 1)

    def test_single_shot_keys_and_order(self):
        result = run("-n", "1", "-i", "0", LIVE_PID)
        record = parse_records(result.stdout)[0]
        self.assertEqual(list(record)[:2], ["timestamp", "temperature"])

    def test_timestamp_format(self):
        result = run("-n", "1", "-i", "0", LIVE_PID)
        record = parse_records(result.stdout)[0]
        self.assertRegex(record["timestamp"], TS_RE)

    def test_two_shots(self):
        result = run("-n", "2", "-i", "0", LIVE_PID)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        records = parse_records(result.stdout)
        self.assertEqual(len(records), 2)

    def test_two_shots_all_keys(self):
        result = run("-n", "2", "-i", "0", LIVE_PID)
        for record in parse_records(result.stdout):
            self.assertIn("timestamp", record)
            self.assertIn("temperature", record)


class TestCpuTempLoggerProcessGone(unittest.TestCase):

    def test_exits_when_process_disappears(self):
        # No -n: the logger would run forever unless the PID's disappearance ends it.
        # The 60 s interval is far longer than the wait: it must react within about a
        # second of the death, not a whole interval later.
        target = subprocess.Popen(["sleep", "2"])
        collector = subprocess.Popen(
            [sys.executable, str(COLLECTOR), "-i", "60", str(target.pid)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            target.wait()  # reaped, so the PID is really gone
            out, err = collector.communicate(timeout=10)
        finally:
            for p in (target, collector):
                if p.poll() is None:
                    p.kill()
                    p.wait()
        self.assertEqual(collector.returncode, 0, msg=err)
        self.assertGreaterEqual(len(parse_records(out)), 1)  # first sample was taken
        self.assertIn("no longer exists", err)

    def test_first_sample_before_first_sleep(self):
        # With a long interval the first record must still appear immediately.
        target = subprocess.Popen(["sleep", "30"])
        collector = subprocess.Popen(
            [sys.executable, str(COLLECTOR), "-i", "60", str(target.pid)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            start = time.monotonic()
            line = collector.stdout.readline()
            self.assertLess(time.monotonic() - start, 10)
            self.assertIn("temperature", json.loads(line))
        finally:
            for p in (collector, target):
                p.kill()
                p.wait()


class TestCpuTempLoggerArgErrors(unittest.TestCase):

    def test_missing_interval_uses_default(self):
        # -i is optional with a default; -n 1 alone must work
        result = run("-n", "1", LIVE_PID)
        # We only check it starts without an arg-parse error;
        # it may time out waiting for the default interval so we just check exit code.
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_missing_pid(self):
        result = run("-n", "1", "-i", "0")
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_pid_zero(self):
        result = run("0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid", result.stderr.lower())

    def test_nonexistent_pid(self):
        result = run("999999999")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_help_exits(self):
        result = run("--help")
        # argparse --help exits with 0
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower() + result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
