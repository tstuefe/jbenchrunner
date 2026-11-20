#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/tests/test_post_process_pidstat_log.py
#
# Test suite for scripts/phase-post-process/post-process-pidstat-log.py.
#
# Strategy: golden-file comparison for the happy path, plus targeted unit
# tests for error paths and specific output properties.
#
# Run with:  python3 -m unittest discover -s scripts/phase-post-process/tests  (from repo root)
#        or: python3 -m unittest discover -s scripts/phase-post-process/tests test_post_process_pidstat_log

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
from datetime import datetime

# Allow importing the module under test directly.
_HERE = os.path.dirname(__file__)
_PHASE_PP_DIR = os.path.join(_HERE, "..")
sys.path.insert(0, _PHASE_PP_DIR)

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "post_process_pidstat_log",
    os.path.join(_PHASE_PP_DIR, "post-process-pidstat-log.py"),
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

parse_log = _MOD.parse_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLES_DIR = os.path.join(_HERE, "post_process_pidstat_log", "input")
EXPECTED_DIR = os.path.join(_HERE, "post_process_pidstat_log", "expected")


def _input(name):
    return os.path.join(EXAMPLES_DIR, name + ".txt")


def _expected(name):
    with open(os.path.join(EXPECTED_DIR, name + ".json")) as fh:
        return json.load(fh)


def _write_tmp(content: str) -> str:
    """Write *content* to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    f.write(textwrap.dedent(content))
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Golden-file test
# ---------------------------------------------------------------------------

class TestGoldenFiles(unittest.TestCase):
    """Parses an example log and compares it to the pre-generated golden JSON
    fixture in scripts/phase-post-process/tests/post_process_pidstat_log/expected/."""

    def test_basic(self):
        """Three flattened -h/-H samples, plus a trailing 'Average:' row that
        must be dropped."""
        name = "pidstat-example-basic"
        result = parse_log(_input(name))
        expected = _expected(name)

        # "timestamp" is recorded in the local timezone of whichever host runs
        # post-processing (see post-process-pidstat-log.py), so its offset
        # varies by machine/DST even though the underlying instant does not.
        # Compare timestamps as absolute instants and everything else exactly.
        self.assertEqual(len(result), len(expected))
        for actual_row, expected_row in zip(result, expected):
            actual_row = dict(actual_row)
            expected_row = dict(expected_row)
            actual_ts = datetime.fromisoformat(actual_row.pop("timestamp"))
            expected_ts = datetime.fromisoformat(expected_row.pop("timestamp"))
            self.assertEqual(actual_ts, expected_ts, msg=f"Golden mismatch for {name}")
            self.assertEqual(actual_row, expected_row, msg=f"Golden mismatch for {name}")
        self.assertEqual(len(result), 3)


# ---------------------------------------------------------------------------
# Structural / property tests
# ---------------------------------------------------------------------------

class TestOutputStructure(unittest.TestCase):
    """Verify invariants that must hold for every parsed sample."""

    def test_cli_writes_json_lines(self):
        path = _input("pidstat-example-basic")
        result = subprocess.run(
            [sys.executable, _SPEC.origin, path],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines)
        samples = [json.loads(line) for line in lines]
        self.assertEqual(samples, parse_log(path))
        for sample in samples:
            self.assertEqual(list(sample)[:2], ["timestamp", "pid"])

    def test_sample_keys_present(self):
        for s in parse_log(_input("pidstat-example-basic")):
            self.assertIn("timestamp", s)
            self.assertIn("pid", s)
            self.assertIn("pct_cpu", s)
            self.assertIn("vsz", s)
            self.assertIn("rss", s)

    def test_uid_pid_command_not_carried_through(self):
        """UID/PID/Command columns must not leak into the JSON record as
        their raw pidstat names (PID is exposed only as the top-level 'pid')."""
        for s in parse_log(_input("pidstat-example-basic")):
            self.assertNotIn("UID", s)
            self.assertNotIn("PID", s)
            self.assertNotIn("Command", s)
            self.assertNotIn("Time", s)

    def test_pid_type_is_int(self):
        for s in parse_log(_input("pidstat-example-basic")):
            self.assertIsInstance(s["pid"], int)

    def test_timestamp_is_iso8601(self):
        from datetime import datetime
        for s in parse_log(_input("pidstat-example-basic")):
            datetime.fromisoformat(s["timestamp"])

    def test_timestamp_advances(self):
        from datetime import datetime
        samples = parse_log(_input("pidstat-example-basic"))
        ts = [datetime.fromisoformat(s["timestamp"]) for s in samples]
        for a, b in zip(ts, ts[1:]):
            self.assertLess(a, b)

    def test_average_row_dropped(self):
        """The trailing 'Average:' summary row must not appear as a sample."""
        samples = parse_log(_input("pidstat-example-basic"))
        self.assertEqual(len(samples), 3)


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------

class TestErrorPaths(unittest.TestCase):
    """Verify that malformed inputs exit with a non-zero status."""

    def _assert_exits(self, content: str, expected_code: int = 1):
        path = _write_tmp(content)
        try:
            with open(os.devnull, "w") as devnull, \
                    unittest.mock.patch("sys.stderr", devnull), \
                    self.assertRaises(SystemExit) as ctx:
                parse_log(path)
            self.assertEqual(ctx.exception.code, expected_code)
        finally:
            os.unlink(path)

    def test_empty_file_exits(self):
        self._assert_exits("")

    def test_bad_datetime_header_exits(self):
        self._assert_exits(
            "NOT-A-DATE\n"
            "Monitoring PID 1234\n"
        )

    def test_missing_pid_line_exits(self):
        self._assert_exits("2026-08-06T11:21:22.000+02:00\n")

    def test_malformed_pid_line_exits(self):
        self._assert_exits(
            "2026-08-06T11:21:22.000+02:00\n"
            "PID is 1234\n"
        )

    def test_no_data_lines_returns_empty(self):
        """A valid header but no data lines must produce an empty sample list
        (no exit)."""
        path = _write_tmp(
            "2026-08-06T11:21:22.000+02:00\n"
            "Monitoring PID 1234\n"
            "Linux 6.8.0 (host) \t08/06/2026 \t_x86_64_\t(8 CPU)\n"
        )
        try:
            result = parse_log(path)
            self.assertEqual(result, [])
        finally:
            os.unlink(path)

    def test_lines_before_header_skipped(self):
        """Lines seen before the '#'-prefixed header (e.g. the sysinfo
        banner) must not be mistaken for data rows."""
        path = _write_tmp(
            "2026-08-06T11:21:22.000+02:00\n"
            "Monitoring PID 1234\n"
            "Linux 6.8.0 (host) \t08/06/2026 \t_x86_64_\t(8 CPU)\n"
            "#      Time   UID       PID    %usr\n"
            "  1754471682     0     1234    5.00\n"
        )
        try:
            result = parse_log(path)
            self.assertEqual(len(result), 1)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
