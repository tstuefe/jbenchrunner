# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-execute/status.py.

Covers:
- format_batch_table: header fields (batch_id, started_at, hostname,
  total/done/failed counts) are present.
- format_batch_table: one row per run entry with correct columns.
- format_batch_table: tags column; JVM Path / JVM Args columns only with
  show_all (jvmargs space-joined).
- list_run_ids: all run ids in batch order.
- format_batch_table: empty run list produces header but no data rows.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Add scripts/phase-execute/ and scripts/common/ to sys.path so status is
# importable directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_EXECUTE_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_COMMON_DIR = os.path.abspath(os.path.join(_PHASE_EXECUTE_DIR, "..", "common"))
for _p in (_PHASE_EXECUTE_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import status  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_batch(runs=None):
    return {
        "batchid": "batch-xyz",
        "started_at": "2025-06-01T10:00:00.000+00:00",
        "hostname": "myhost",
        "runs": runs if runs is not None else [],
    }


def _make_run(seq, result_dir_rel, jdkname="jdk21", bench_json="dacapo.h2",
              bench_group=None, heap_size=4096, jvmargs=None, run_id=None,
              tags=None, java_home="/opt/jdk-21"):
    run = {
        "seq": seq,
        "runid": run_id if run_id is not None else f"run-{seq:03d}",
        "result_dir": result_dir_rel,
        "jdkname": jdkname,
        "bench_json": bench_json,
        "heapsize": heap_size,
        "jvmargs": jvmargs if jvmargs is not None else [],
        "tags": tags if tags is not None else [],
        "java_home": java_home,
    }
    if bench_group is not None:
        run["bench_group"] = bench_group
    return run


def _write_run_info(base_dir, result_dir_rel, run_status, pid=1):
    """Write a minimal run-info.json under base_dir/result_dir_rel."""
    full = os.path.join(base_dir, result_dir_rel)
    os.makedirs(full, exist_ok=True)
    data = {"status": run_status, "pid": pid, "time": "2025-06-01T10:00:00.000+00:00",
            "sudo_mode": False}
    with open(os.path.join(full, "run-info.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFormatBatchTable(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # -- header fields -------------------------------------------------------

    def test_header_contains_batch_id(self):
        batch = _make_batch()
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("batch-xyz", out)

    def test_header_contains_started_at(self):
        batch = _make_batch()
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("2025-06-01T10:00:00.000+00:00", out)

    def test_header_contains_hostname(self):
        batch = _make_batch()
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("myhost", out)

    def test_header_total_done_failed_counts(self):
        r1 = _make_run(1, "results/r1")
        r2 = _make_run(2, "results/r2")
        r3 = _make_run(3, "results/r3")
        # r1 DONE, r2 FAILED, r3 PENDING
        _write_run_info(self._tmpdir, "results/r1", "DONE")
        _write_run_info(self._tmpdir, "results/r2", "FAILED")
        batch = _make_batch(runs=[r1, r2, r3])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("3 total", out)
        self.assertIn("1 done", out)
        self.assertIn("1 failed", out)

    # -- rows ----------------------------------------------------------------

    def test_row_count_equals_run_count(self):
        runs = [_make_run(i, f"results/r{i}") for i in range(1, 5)]
        batch = _make_batch(runs=runs)
        out = status.format_batch_table(batch, self._tmpdir)
        # Each run should produce a row containing its sequence number
        for i in range(1, 5):
            # Seq numbers appear somewhere in the output (column-aligned)
            self.assertIn(str(i), out)

    def test_row_contains_jdk_name(self):
        run = _make_run(1, "results/r1", jdkname="semeru21")
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("semeru21", out)

    def test_row_contains_bench_json(self):
        run = _make_run(1, "results/r1", bench_json="dacapo.tomcat")
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("dacapo.tomcat", out)

    def test_no_heap_column(self):
        run = _make_run(1, "results/r1", heap_size=8192)
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir, show_all=True)
        self.assertNotIn("8192", out)
        self.assertNotIn("Heap", out)

    def test_header_label_prepared(self):
        out = status.format_batch_table(_make_batch(), self._tmpdir)
        self.assertIn("Prepared: 2025-06-01T10:00:00.000+00:00", out)
        self.assertNotIn("Started:", out)

    def test_tags_comma_joined(self):
        run = _make_run(1, "results/r1", tags=["baseline", "example-run"])
        out = status.format_batch_table(_make_batch(runs=[run]), self._tmpdir)
        self.assertIn("baseline,example-run", out)

    def test_no_tags_shows_dash(self):
        run = _make_run(1, "results/r1", tags=[])
        out = status.format_batch_table(_make_batch(runs=[run]), self._tmpdir)
        row = out.splitlines()[-2]
        self.assertTrue(row.endswith("-"), row)

    def test_row_status_pending_when_no_result_dir(self):
        run = _make_run(1, "results/r1")
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("PENDING", out)

    def test_row_status_done(self):
        run = _make_run(1, "results/r1")
        _write_run_info(self._tmpdir, "results/r1", "DONE")
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("DONE", out)

    def test_row_status_failed(self):
        run = _make_run(1, "results/r1")
        _write_run_info(self._tmpdir, "results/r1", "FAILED")
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("FAILED", out)

    # -- run_id column -------------------------------------------------------

    def test_row_contains_run_id(self):
        run = _make_run(1, "results/r1", run_id="run-dacapo-001")
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("run-dacapo-001", out)

    def test_column_header_run_id(self):
        batch = _make_batch(runs=[])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("RUN-ID", out)

    # -- jvmargs -----------------------------------------------------------

    def test_jvm_flags_space_joined_with_show_all(self):
        flags = ["-XX:+UseG1GC", "-Xmx16g", "-Xms1g"]
        run = _make_run(1, "results/r1", jvmargs=flags)
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir, show_all=True)
        self.assertIn("JVM Args", out)
        self.assertIn("-XX:+UseG1GC -Xmx16g -Xms1g", out)

    def test_java_home_with_show_all(self):
        run = _make_run(1, "results/r1", java_home="/opt/jdk-25")
        out = status.format_batch_table(_make_batch(runs=[run]), self._tmpdir, show_all=True)
        self.assertIn("JVM Path", out)
        self.assertIn("/opt/jdk-25", out)

    def test_default_hides_jvm_path_and_args(self):
        run = _make_run(1, "results/r1", jvmargs=["-XX:+UseG1GC"], java_home="/opt/jdk-25")
        out = status.format_batch_table(_make_batch(runs=[run]), self._tmpdir)
        self.assertNotIn("JVM Path", out)
        self.assertNotIn("JVM Args", out)
        self.assertNotIn("-XX:+UseG1GC", out)
        self.assertNotIn("/opt/jdk-25", out)

    def test_empty_jvm_flags_produces_empty_vm_options(self):
        run = _make_run(1, "results/r1", jvmargs=[])
        batch = _make_batch(runs=[run])
        # Should not raise
        out = status.format_batch_table(batch, self._tmpdir, show_all=True)
        self.assertIsInstance(out, str)

    # -- empty run list ------------------------------------------------------

    def test_empty_run_list_still_has_header(self):
        batch = _make_batch(runs=[])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("batch-xyz", out)
        self.assertIn("0 total", out)

    # -- return type ---------------------------------------------------------

    def test_returns_string(self):
        batch = _make_batch()
        result = status.format_batch_table(batch, self._tmpdir)
        self.assertIsInstance(result, str)

    # -- Info column shows pid for RUNNING rows -------------------------------

    def test_row_info_shows_pid_when_running(self):
        run = _make_run(1, "results/r1")
        _write_run_info(self._tmpdir, "results/r1", "RUNNING", pid=os.getpid())
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("RUNNING", out)
        self.assertIn(str(os.getpid()), out)

    def test_row_info_shows_pid_even_when_pid_dead(self):
        # No liveness check is performed for display purposes — a dead pid
        # simply means the harness hasn't caught up yet (e.g. still waiting
        # on collectors), so the row stays RUNNING and still shows the pid.
        run = _make_run(1, "results/r1")
        _write_run_info(self._tmpdir, "results/r1", "RUNNING", pid=2_000_000)
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("RUNNING", out)
        self.assertIn("2000000", out)

    def test_row_status_running_when_pid_missing(self):
        result_dir = os.path.join(self._tmpdir, "results/r1")
        os.makedirs(result_dir, exist_ok=True)
        data = {"status": "RUNNING", "time": "2025-06-01T10:00:00.000+00:00", "sudo_mode": False}
        with open(os.path.join(result_dir, "run-info.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        run = _make_run(1, "results/r1")
        batch = _make_batch(runs=[run])
        out = status.format_batch_table(batch, self._tmpdir)
        self.assertIn("RUNNING", out)

    # -- color suppression when not a TTY -------------------------------------

    def test_no_color_codes_when_not_a_tty(self):
        run = _make_run(1, "results/r1")
        _write_run_info(self._tmpdir, "results/r1", "DONE")
        batch = _make_batch(runs=[run])
        # Don't rely on the test runner's own stdout happening to be a
        # non-TTY (e.g. captured/piped): when tests run directly in a
        # real terminal, sys.stdout.isatty() is True and this would
        # otherwise fail spuriously.
        with patch.object(sys.stdout, "isatty", return_value=False):
            out = status.format_batch_table(batch, self._tmpdir)
        self.assertNotIn("\x1b[", out)


class TestListRunIds(unittest.TestCase):

    def test_all_run_ids_in_order(self):
        runs = [_make_run(i, f"results/r{i}") for i in (1, 2, 3)]
        self.assertEqual(status.list_run_ids(_make_batch(runs=runs)),
                         ["run-001", "run-002", "run-003"])


class TestSupportsColor(unittest.TestCase):

    def test_true_when_tty_and_not_dumb(self):
        with patch.object(sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertTrue(status._supports_color())

    def test_false_on_dumb_terminal(self):
        with patch.object(sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "dumb"}, clear=False):
            self.assertFalse(status._supports_color())

    def test_false_when_not_a_tty(self):
        with patch.object(sys.stdout, "isatty", return_value=False), \
                patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=False):
            self.assertFalse(status._supports_color())

    def test_false_when_no_color_env_set(self):
        with patch.object(sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM": "xterm-256color", "NO_COLOR": "1"}, clear=False):
            self.assertFalse(status._supports_color())


class TestColorizeStatus(unittest.TestCase):

    def test_done_is_green(self):
        with patch("status._supports_color", return_value=True):
            self.assertEqual(status._colorize_status("DONE", "DONE    "),
                              "\x1b[92mDONE    \x1b[0m")

    def test_failed_is_red(self):
        with patch("status._supports_color", return_value=True):
            self.assertEqual(status._colorize_status("FAILED", "FAILED  "),
                              "\x1b[91mFAILED  \x1b[0m")

    def test_running_is_yellow(self):
        with patch("status._supports_color", return_value=True):
            self.assertEqual(status._colorize_status("RUNNING", "RUNNING "),
                              "\x1b[93mRUNNING \x1b[0m")

    def test_stopped_is_yellow(self):
        with patch("status._supports_color", return_value=True):
            self.assertEqual(status._colorize_status("STOPPED", "STOPPED "),
                              "\x1b[93mSTOPPED \x1b[0m")

    def test_pending_is_uncolored(self):
        with patch("status._supports_color", return_value=True):
            self.assertEqual(status._colorize_status("PENDING", "PENDING "), "PENDING ")

    def test_no_color_when_unsupported(self):
        with patch("status._supports_color", return_value=False):
            self.assertEqual(status._colorize_status("DONE", "DONE    "), "DONE    ")


if __name__ == "__main__":
    unittest.main()
