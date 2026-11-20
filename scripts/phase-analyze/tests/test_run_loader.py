# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# test_run_loader.py — unit tests for scripts/phase-analyze/run_loader.py
#
# Run with:
#   python3 -m unittest discover scripts

import io
import json
import os
import sys
import tempfile
import unittest

# Allow import from project scripts directory when run via unittest discover.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_ANALYZE = os.path.dirname(_HERE)
if _PHASE_ANALYZE not in sys.path:
    sys.path.insert(0, _PHASE_ANALYZE)

from run_loader import discover_run_dirs, load_run  # noqa: E402


class TestLoadRun(unittest.TestCase):
    """Tests for load_run()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        # Clean up all temporary files/dirs created in the test.
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_json(self, filename, data):
        path = os.path.join(self.tmpdir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    # ------------------------------------------------------------------
    # Missing result.json → None
    # ------------------------------------------------------------------
    def test_missing_result_json_returns_none(self):
        # Only write info.json — result.json is absent.
        self._write_json("info.json", {"runid": "abc", "benchmark": "dacapo"})
        result = load_run(self.tmpdir)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # All three files present → merged dict
    # ------------------------------------------------------------------
    def test_all_three_files_merged(self):
        self._write_json("info.json", {"runid": "abc", "benchmark": "dacapo"})
        self._write_json("run-info.json", {"duration_secs": 42})
        self._write_json("result.json", {"dacapo_result": {"result_ms": 1234}})

        merged = load_run(self.tmpdir)

        self.assertIsNotNone(merged)
        # Keys from all three files must be present.
        self.assertEqual(merged["runid"], "abc")
        self.assertEqual(merged["benchmark"], "dacapo")
        self.assertEqual(merged["duration_secs"], 42)
        self.assertEqual(merged["dacapo_result"], {"result_ms": 1234})

    # ------------------------------------------------------------------
    # result.json present, run-info.json absent → still works
    # ------------------------------------------------------------------
    def test_missing_run_info_json_still_works(self):
        self._write_json("info.json", {"runid": "xyz", "benchmark": "specjbb"})
        self._write_json("result.json", {"specjbb_result": {"maxjops": 500}})
        # No run-info.json written.

        merged = load_run(self.tmpdir)

        self.assertIsNotNone(merged)
        self.assertEqual(merged["runid"], "xyz")
        self.assertEqual(merged["benchmark"], "specjbb")
        self.assertEqual(merged["specjbb_result"]["maxjops"], 500)
        self.assertNotIn("duration_secs", merged)

    # ------------------------------------------------------------------
    # Malformed info.json → ValueError
    # ------------------------------------------------------------------
    def test_malformed_info_json_raises(self):
        path = os.path.join(self.tmpdir, "info.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not valid json {{{")
        self._write_json("result.json", {"ok": True})

        with self.assertRaises(ValueError):
            load_run(self.tmpdir)

    # ------------------------------------------------------------------
    # Malformed run-info.json → ValueError
    # ------------------------------------------------------------------
    def test_malformed_run_info_json_raises(self):
        self._write_json("info.json", {"runid": "abc", "benchmark": "dacapo"})
        path = os.path.join(self.tmpdir, "run-info.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ broken json")
        self._write_json("result.json", {"ok": True})

        with self.assertRaises(ValueError):
            load_run(self.tmpdir)


class TestDiscoverRunDirs(unittest.TestCase):
    """Tests for discover_run_dirs()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Positional args returned directly
    # ------------------------------------------------------------------
    def test_positional_args_returned_directly(self):
        args = ["run-aaa", "run-bbb"]
        result = discover_run_dirs(args, self.tmpdir, stdin_is_tty=True)
        self.assertEqual(result, ["run-aaa", "run-bbb"])

    def test_positional_args_take_priority_over_stdin(self):
        args = ["run-aaa"]
        # Even with stdin_is_tty=False, positional args win.
        result = discover_run_dirs(args, self.tmpdir, stdin_is_tty=False)
        self.assertEqual(result, ["run-aaa"])

    # ------------------------------------------------------------------
    # stdin_is_tty=False → reads from sys.stdin
    # ------------------------------------------------------------------
    def test_stdin_read_when_not_tty(self):
        fake_stdin = io.StringIO("run-111\nrun-222\n")
        original_stdin = sys.stdin
        sys.stdin = fake_stdin
        try:
            result = discover_run_dirs([], self.tmpdir, stdin_is_tty=False)
        finally:
            sys.stdin = original_stdin

        self.assertEqual(result, ["run-111", "run-222"])

    def test_stdin_blank_lines_ignored(self):
        fake_stdin = io.StringIO("run-aaa\n\n  \nrun-bbb\n")
        original_stdin = sys.stdin
        sys.stdin = fake_stdin
        try:
            result = discover_run_dirs([], self.tmpdir, stdin_is_tty=False)
        finally:
            sys.stdin = original_stdin

        self.assertEqual(result, ["run-aaa", "run-bbb"])

    # ------------------------------------------------------------------
    # stdin_is_tty=True → scan results_dir subdirectories
    # ------------------------------------------------------------------
    def test_scan_results_dir_returns_subdirs(self):
        # Create two subdirectories and one file (file must be ignored).
        os.makedirs(os.path.join(self.tmpdir, "run-alpha"))
        os.makedirs(os.path.join(self.tmpdir, "run-beta"))
        with open(os.path.join(self.tmpdir, "not-a-dir.txt"), "w") as fh:
            fh.write("file")

        result = discover_run_dirs([], self.tmpdir, stdin_is_tty=True)

        self.assertIn("run-alpha", result)
        self.assertIn("run-beta", result)
        self.assertNotIn("not-a-dir.txt", result)
        self.assertEqual(len(result), 2)

    def test_scan_empty_results_dir_returns_empty(self):
        result = discover_run_dirs([], self.tmpdir, stdin_is_tty=True)
        self.assertEqual(result, [])

    def test_scan_nonexistent_results_dir_returns_empty(self):
        result = discover_run_dirs(
            [], "/nonexistent/path/that/does/not/exist", stdin_is_tty=True
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
