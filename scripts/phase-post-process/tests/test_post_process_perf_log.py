#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/tests/test_post_process_perf_log.py
#
# Test suite for scripts/phase-post-process/post-process-perf-log.py.
#
# Strategy: golden-file comparison for happy-path tests (one per example log),
# plus targeted unit tests for error paths and specific output properties.
#
# Run with:  python3 -m unittest discover -s scripts/phase-post-process/tests  (from repo root)
#        or: python3 -m unittest discover -s scripts/phase-post-process/tests test_post_process_perf_log

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock

# Allow importing the module under test directly.
_HERE = os.path.dirname(__file__)
_PHASE_PP_DIR = os.path.join(_HERE, "..")
sys.path.insert(0, _PHASE_PP_DIR)

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "post_process_perf_log",
    os.path.join(_PHASE_PP_DIR, "post-process-perf-log.py"),
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

parse_log = _MOD.parse_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLES_DIR = os.path.join(_HERE, "post_process_perf_log", "input")
EXPECTED_DIR = os.path.join(_HERE, "post_process_perf_log", "expected")


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
# Golden-file tests — one per example log
# ---------------------------------------------------------------------------

class TestGoldenFiles(unittest.TestCase):
    """Each test parses an example log and compares it to the pre-generated
    golden JSON fixture in scripts/phase-post-process/tests/post_process_perf_log/expected/."""

    def _check(self, name):
        result = parse_log(_input(name))
        expected = _expected(name)
        self.assertEqual(result, expected, msg=f"Golden mismatch for {name}")

    def test_without_multiplexing(self):
        """Plain numeric counters, no unit, no multiplexing."""
        self._check("perf-example-without-multiplexing")

    def test_with_multiplexing(self):
        """Counters with a trailing (pct%) token — multiplex_pct must be != 100."""
        name = "perf-example-with-multiplexing"
        self._check(name)
        # Sanity: at least one counter should have a pct < 100
        first_sample = _expected(name)[0]
        pcts = [c["multiplex_pct"] for c in first_sample["counters"].values()]
        self.assertTrue(any(p < 100.0 for p in pcts))

    def test_user_mode_only(self):
        """Events with :u modifier — non-alphanum chars replaced by underscore in the event key."""
        name = "perf-example-without-multiplexing-user-mode-only"
        self._check(name)
        first_sample = _expected(name)[0]
        self.assertIn("cycles_u", first_sample["counters"])

    def test_user_kernel_mode_separate(self):
        """Both :u and :k variants of the same event in a single interval."""
        name = "perf-example-without-multiplexing-user-kernel-mode-separate"
        self._check(name)
        first_sample = _expected(name)[0]
        self.assertIn("cycles_u", first_sample["counters"])
        self.assertIn("cycles_k", first_sample["counters"])

    def test_with_unit_and_not_supported(self):
        """Unit column present (Joules); <not supported> values map to null."""
        name = "perf-example-without-multiplexing-with-unit"
        self._check(name)
        first_sample = _expected(name)[0]
        energy = first_sample["counters"]["power_energy_pkg"]
        self.assertIsNone(energy["value"])
        self.assertEqual(energy["unit"], "Joules")

    def test_default_events(self):
        """perf's default set: decimal task-clock, metric columns dropped,
        metric-only (topdown) lines skipped, <not counted> maps to null."""
        name = "perf-example-default-events"
        self._check(name)
        first = _expected(name)[0]["counters"]
        self.assertEqual(first["task_clock"], {"value": 1001.42, "unit": "msec",
                                               "multiplex_pct": 100.0})
        self.assertIsNone(first["branch_misses"]["value"])
        self.assertEqual(len(first), 8)


# ---------------------------------------------------------------------------
# Structural / property tests
# ---------------------------------------------------------------------------

class TestOutputStructure(unittest.TestCase):
    """Verify invariants that must hold for every parsed sample."""

    def test_cli_writes_json_lines(self):
        path = _input("perf-example-without-multiplexing")
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

    def _samples(self, name):
        return parse_log(_input(name))

    def test_sample_keys_present(self):
        """Every sample must have timestamp, pid, and counters keys."""
        for name in (
            "perf-example-without-multiplexing",
            "perf-example-with-multiplexing",
            "perf-example-without-multiplexing-user-mode-only",
            "perf-example-without-multiplexing-user-kernel-mode-separate",
            "perf-example-without-multiplexing-with-unit",
            "perf-example-default-events",
        ):
            with self.subTest(name=name):
                for s in self._samples(name):
                    self.assertIn("timestamp", s)
                    self.assertIn("pid", s)
                    self.assertIn("counters", s)

    def test_pid_type_is_int(self):
        samples = self._samples("perf-example-without-multiplexing")
        for s in samples:
            self.assertIsInstance(s["pid"], int)

    def test_timestamp_is_iso8601(self):
        """Timestamps must be parseable ISO 8601 strings."""
        from datetime import datetime
        samples = self._samples("perf-example-without-multiplexing")
        for s in samples:
            # fromisoformat will raise if the format is wrong
            datetime.fromisoformat(s["timestamp"])

    def test_timestamp_advances(self):
        """Timestamps must be strictly increasing within a log."""
        from datetime import datetime
        samples = self._samples("perf-example-with-multiplexing")
        ts = [datetime.fromisoformat(s["timestamp"]) for s in samples]
        for a, b in zip(ts, ts[1:]):
            self.assertLess(a, b)

    def test_counter_value_is_int_or_none(self):
        """Counter values of plain counts must be int (>= 0) or None for not-counted/not-supported."""
        for sample in self._samples("perf-example-without-multiplexing-with-unit"):
            for key, counter in sample["counters"].items():
                v = counter["value"]
                self.assertTrue(
                    v is None or (isinstance(v, int) and v >= 0),
                    msg=f"Bad value for {key}: {v!r}",
                )

    def test_multiplex_pct_defaults_to_100(self):
        """Non-multiplexed logs must have multiplex_pct == 100.0 for all counters."""
        for sample in self._samples("perf-example-without-multiplexing"):
            for key, counter in sample["counters"].items():
                self.assertEqual(
                    counter["multiplex_pct"], 100.0,
                    msg=f"Expected 100.0 for {key}, got {counter['multiplex_pct']}",
                )

    def test_trailing_slash_stripped_from_event_name(self):
        """Event names like 'power/energy-pkg/' must have the trailing slash removed."""
        for sample in self._samples("perf-example-without-multiplexing-with-unit"):
            for key in sample["counters"]:
                self.assertFalse(
                    key.endswith("/"),
                    msg=f"Trailing slash not stripped from event key: {key!r}",
                )

    def test_sample_count_matches_distinct_timestamps(self):
        """Number of samples must equal the number of distinct relative timestamps."""
        for name, expected_count in (
            ("perf-example-without-multiplexing", 6),
            ("perf-example-without-multiplexing-user-kernel-mode-separate", 8),
            ("perf-example-without-multiplexing-with-unit", 3),
        ):
            with self.subTest(name=name):
                self.assertEqual(len(self._samples(name)), expected_count)


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
        """An empty file must exit with code 1."""
        self._assert_exits("")

    def test_bad_datetime_header_exits(self):
        """A non-ISO-8601 first line must exit with code 1."""
        self._assert_exits(
            "NOT-A-DATE\n"
            "Monitoring PID 1234\n"
        )

    def test_missing_pid_line_exits(self):
        """A file with only the datetime line (no PID line) must exit with code 1."""
        self._assert_exits("2026-08-06T11:21:22.000+02:00\n")

    def test_malformed_pid_line_exits(self):
        """A second line that doesn't match 'Monitoring PID <n>' must exit with code 1."""
        self._assert_exits(
            "2026-08-06T11:21:22.000+02:00\n"
            "PID is 1234\n"
        )

    def test_no_data_lines_returns_empty(self):
        """A valid header but no data lines must produce an empty sample list (no exit)."""
        path = _write_tmp(
            "2026-08-06T11:21:22.000+02:00\n"
            "Monitoring PID 1234\n"
            "# just a comment\n"
        )
        try:
            result = parse_log(path)
            self.assertEqual(result, [])
        finally:
            os.unlink(path)

    def test_unrecognised_data_lines_skipped(self):
        """Lines that don't match the data pattern are silently skipped."""
        path = _write_tmp(
            "2026-08-06T11:21:22.000+02:00\n"
            "Monitoring PID 1234\n"
            "10.000000000;100;;cycles;10000000000;100.00;;\n"
            "this line is garbage and should be ignored\n"
            "event syntax error: 'cyclez'\n"
            "20.000000000;200;;cycles;10000000000;100.00;;\n"
        )
        try:
            result = parse_log(path)
            self.assertEqual(len(result), 2)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
