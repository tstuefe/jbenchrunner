# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-other/failures.py.

Covers:
- load_failure: FAILED and STOPPED are listed; DONE and RUNNING are not.
- load_failure: missing/malformed info.json or run-info.json, or a
  run-info.json without status, gives status "unknown".
- scan_failures: skips plain files, sorts by time with timeless runs first,
  missing results dir gives an empty list.
- format_table: header, "-" for missing values, jdkname and tags cells.
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_OTHER_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_COMMON_DIR = os.path.abspath(os.path.join(_PHASE_OTHER_DIR, "..", "common"))
for _p in (_PHASE_OTHER_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from failures import UNKNOWN, format_table, load_failure, scan_failures  # noqa: E402

_INFO = {"name": "dacapo.h2o", "jdk": {"name": "jdk21"}, "tags": ["a", "b"]}


class _ResultsDirTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.results = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _make_run(self, runid, run_info=None, info=_INFO, raw=None):
        """Create results/<runid>. *raw* maps file names to literal content."""
        run_dir = os.path.join(self.results, runid)
        os.makedirs(run_dir)
        for name, obj in (("info.json", info), ("run-info.json", run_info)):
            if obj is not None:
                with open(os.path.join(run_dir, name), "w") as fh:
                    json.dump(obj, fh)
        for name, text in (raw or {}).items():
            with open(os.path.join(run_dir, name), "w") as fh:
                fh.write(text)
        return run_dir


class TestLoadFailure(_ResultsDirTest):

    def test_failed_listed(self):
        run = load_failure(self._make_run(
            "r1", {"status": "FAILED", "exit_code": 3, "status_info": "oom"}))
        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(run["status_info"], "oom")
        self.assertEqual(run["name"], "dacapo.h2o")
        self.assertEqual(run["runid"], "r1")

    def test_stopped_listed(self):
        run = load_failure(self._make_run("r1", {"status": "STOPPED"}))
        self.assertEqual(run["status"], "STOPPED")

    def test_done_and_running_not_listed(self):
        self.assertIsNone(load_failure(self._make_run("r1", {"status": "DONE"})))
        self.assertIsNone(load_failure(self._make_run("r2", {"status": "RUNNING"})))

    def test_missing_run_info_unknown(self):
        run = load_failure(self._make_run("r1"))
        self.assertEqual(run["status"], UNKNOWN)
        self.assertEqual(run["name"], "dacapo.h2o")

    def test_missing_info_unknown(self):
        run = load_failure(self._make_run("r1", {"status": "DONE"}, info=None))
        self.assertEqual(run["status"], UNKNOWN)

    def test_malformed_run_info_unknown(self):
        run = load_failure(self._make_run("r1", raw={"run-info.json": "{nope"}))
        self.assertEqual(run["status"], UNKNOWN)

    def test_run_info_without_status_unknown(self):
        run = load_failure(self._make_run("r1", {"pid": 1}))
        self.assertEqual(run["status"], UNKNOWN)


class TestScanFailures(_ResultsDirTest):

    def test_sorted_and_filtered(self):
        self._make_run("late", {"status": "FAILED", "time": "2026-09-02T10:00:00.000+02:00"})
        self._make_run("early", {"status": "STOPPED", "time": "2026-09-01T10:00:00.000+02:00"})
        self._make_run("ok", {"status": "DONE", "time": "2026-09-01T11:00:00.000+02:00"})
        self._make_run("broken")
        with open(os.path.join(self.results, "BATCH_IN_PROGRESS.json"), "w") as fh:
            fh.write("{}")
        runids = [r["runid"] for r in scan_failures(self.results)]
        self.assertEqual(runids, ["broken", "early", "late"])

    def test_missing_results_dir(self):
        self.assertEqual(scan_failures(os.path.join(self.results, "nope")), [])


class TestFormatTable(_ResultsDirTest):

    def test_table(self):
        self._make_run("r1", {"status": "FAILED", "time": "2026-09-01T10:00:00.123+02:00",
                              "duration_secs": 10.2, "exit_code": 3,
                              "status_info": "version mismatch"})
        self._make_run("r2")
        table = format_table(scan_failures(self.results), self.results)
        lines = table.splitlines()
        self.assertEqual(lines[0].split(),
                         ["runid", "time", "duration", "status", "status_info",
                          "exit_code", "name", "jdkname", "tags"])
        self.assertEqual(len(lines), 4)
        # r2 has no time, so it sorts first.
        self.assertEqual(lines[2].split(),
                         ["r2", "-", "-", "unknown", "-", "-", "dacapo.h2o", "jdk21", "a,b"])
        self.assertIn("2026-09-01T10:00:00  10.2", lines[3])
        self.assertIn("version mismatch", lines[3])


if __name__ == "__main__":
    unittest.main()
