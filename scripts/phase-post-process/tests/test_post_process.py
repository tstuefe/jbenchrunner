#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-post-process/post_process.py
and the bin/jbr-pp CLI entry point.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

_HERE = os.path.dirname(__file__)
_PHASE_PP_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_JBR_PP = os.path.join(_REPO_ROOT, "bin", "jbr-pp")
_JBR_PP_PY = os.path.join(_REPO_ROOT, "libexec", "jbr-pp.py")
sys.path.insert(0, _PHASE_PP_DIR)

from post_process import post_process_run

import importlib.util

_JBR_PP_SPEC = importlib.util.spec_from_file_location("jbr_pp_cli", _JBR_PP_PY)
_JBR_PP_MOD = importlib.util.module_from_spec(_JBR_PP_SPEC)
_JBR_PP_SPEC.loader.exec_module(_JBR_PP_MOD)

_discover_run_dirs = _JBR_PP_MOD._discover_run_dirs
_run_status = _JBR_PP_MOD._run_status

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

# Minimal valid DaCapo stdout (3 warmups + 1 measured, no timestamps, no latency)
_DACAPO_STDOUT = """\
===== DaCapo 23.11-MR2-chopin pmd completed warmup 1 in 1800 msec =====
===== DaCapo 23.11-MR2-chopin pmd completed warmup 2 in 1500 msec =====
===== DaCapo 23.11-MR2-chopin pmd completed warmup 3 in 1400 msec =====
===== DaCapo 23.11-MR2-chopin pmd PASSED in 1353 msec =====
"""

# Minimal valid SPECjbb stdout
_SPECJBB_STDOUT = (
    "RUN RESULT: hbIR (max attempted) = 220, hbIR (settled) = 200, "
    "max-jOPS = 136, critical-jOPS = 0\n"
)

# Minimal valid temps.jsonl (3 samples)
_TEMPS_LINES = [
    {"temperature": 60000},
    {"temperature": 65000},
    {"temperature": 63000},
]

# Minimal valid proclog.jsonl (2 samples: 100 MB/10 threads and 200 MB/20 threads; cumulative
# page faults 1000/5 and 3000/7)
_PROCLOG_LINES = [
    {"timestamp": "2026-09-13T11:00:00.000+00:00", "status": {"vm_rss": "102400 kB", "threads": "10"},
     "proc_pid_stat": {"minflt": "1000", "majflt": "5"}},
    {"timestamp": "2026-09-13T11:00:10.000+00:00", "status": {"vm_rss": "204800 kB", "threads": "20"},
     "proc_pid_stat": {"minflt": "3000", "majflt": "7"}},
]

# Minimal valid perflog.log (matches post-process-perf-log.py format)
_PERF_LOG = """\
2026-08-06T11:21:22.000+02:00
Monitoring PID 12345
10.000000;100000;;cycles;10000000000;100.00;;
10.000000;20000;;L1-dcache-load-misses;10000000000;100.00;;
"""

# Minimal valid pidstat.log (matches post-process-pidstat-log.py format)
_PIDSTAT_LOG = """\
2026-08-06T11:21:22.000+02:00
Monitoring PID 12345
Linux 6.8.0 (host) \t08/06/2026 \t_x86_64_\t(8 CPU)

#      Time   UID       PID    %usr %system  %guest   %wait    %CPU   CPU  minflt/s  majflt/s     VSZ      RSS   %MEM   kB_rd/s   kB_wr/s kB_ccwr/s  iodelay  Command
  1754471682     0     12345    5.00    2.00    0.00    0.00    7.00     1    120.00      0.00  1234567    89012   1.20      0.00     12.00      0.00        0  java
"""

# Minimal valid nmt.log (matches post-process-nmt-log.py format)
_NMT_LOG = """\
2026-08-06T11:21:22.000+02:00
12345:

Native Memory Tracking:

Total: reserved=2000000KB, committed=1000000KB
       malloc: 50000KB #1000, peak=55000KB #1100
       mmap:   reserved=1950000KB, committed=950000KB

-                 Java Heap (reserved=900000KB, committed=900000KB)
                            (mmap: reserved=900000KB, committed=900000KB, at peak)
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_run_dir(
    tmp_dir: str,
    benchmark: str = "dacapo",
    status: str = "DONE",
    stdout_content: str | None = None,
    include_temps: bool = True,
    include_proclog: bool = True,
    include_perflog: bool = False,
    include_pidstat: bool = False,
    include_nmt: bool = False,
) -> str:
    """
    Build a minimal run directory inside *tmp_dir* and return its path.
    """
    run_dir = os.path.join(tmp_dir, "run")
    os.makedirs(run_dir, exist_ok=True)

    # run-info.json
    with open(os.path.join(run_dir, "run-info.json"), "w") as f:
        json.dump({"status": status}, f)

    # info.json
    with open(os.path.join(run_dir, "info.json"), "w") as f:
        json.dump({"benchmark": benchmark}, f)

    # stdout.log
    if stdout_content is not False:  # False means "omit file"
        if stdout_content is None:
            stdout_content = _DACAPO_STDOUT if benchmark == "dacapo" else _SPECJBB_STDOUT
        with open(os.path.join(run_dir, "stdout.log"), "w") as f:
            f.write(stdout_content)

    # Optional collectors
    if include_temps:
        with open(os.path.join(run_dir, "temps.jsonl"), "w") as f:
            for obj in _TEMPS_LINES:
                f.write(json.dumps(obj) + "\n")

    if include_proclog:
        with open(os.path.join(run_dir, "proclog.jsonl"), "w") as f:
            for obj in _PROCLOG_LINES:
                f.write(json.dumps(obj) + "\n")

    if include_perflog:
        with open(os.path.join(run_dir, "perflog.log"), "w") as f:
            f.write(_PERF_LOG)

    if include_pidstat:
        with open(os.path.join(run_dir, "pidstat.log"), "w") as f:
            f.write(_PIDSTAT_LOG)

    if include_nmt:
        with open(os.path.join(run_dir, "nmt.log"), "w") as f:
            f.write(_NMT_LOG)

    return run_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPostProcessDacapo(unittest.TestCase):
    """Successful DaCapo run: result.json contains dacapo_result, temperatures, memory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_dacapo_result_json_created(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        result = post_process_run(run_dir)

        result_path = os.path.join(run_dir, "result.json")
        self.assertTrue(os.path.exists(result_path), "result.json must be created")

        with open(result_path) as f:
            on_disk = json.load(f)
        self.assertEqual(result, on_disk)

    def test_dacapo_result_keys(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        result = post_process_run(run_dir)

        self.assertIn("dacapo_result", result)
        self.assertNotIn("specjbb_result", result)
        self.assertIn("temperatures", result)
        self.assertIn("memory", result)
        self.assertIn("threads", result)
        self.assertIn("page_faults", result)

    def test_dacapo_result_ms(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        result = post_process_run(run_dir)

        self.assertEqual(result["dacapo_result"]["result_ms"], 1353)
        self.assertEqual(len(result["dacapo_result"]["iterations"]), 4)

    def test_dacapo_temperatures(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        result = post_process_run(run_dir)

        temps = result["temperatures"]
        self.assertEqual(temps["tempin"], 60000)
        self.assertEqual(temps["tempout"], 63000)
        self.assertEqual(temps["tempmax"], 65000)

    def test_dacapo_memory(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        result = post_process_run(run_dir)

        mem = result["memory"]
        self.assertEqual(mem["rssmax"], 200)
        self.assertEqual(mem["rssavg"], round((102400 + 204800) / 2 / 1024))

    def test_dacapo_threads(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        result = post_process_run(run_dir)

        threads = result["threads"]
        self.assertEqual(threads["osthreads_max"], 20)
        self.assertEqual(threads["osthreads_avg"], 15)

    def test_dacapo_page_faults(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        result = post_process_run(run_dir)

        # Totals are the last sample's cumulative counters.
        self.assertEqual(result["page_faults"], {"minflt": 3000, "majflt": 7})


class TestPostProcessStartupMs(unittest.TestCase):
    """dacapo_result.startup_ms = first_iteration_ts - run-info.json time."""

    _STDOUT = (
        ">>> Warmup 1 started at 2026-09-23T03:20:33.315757469+02:00[Europe/Berlin].\n"
        "===== DaCapo 23.11-MR2-chopin pmd completed warmup 1 in 1000 msec =====\n"
        ">>> Final started at 2026-09-23T03:20:34.400000000+02:00[Europe/Berlin].\n"
        "===== DaCapo 23.11-MR2-chopin pmd PASSED in 900 msec =====\n"
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def _run(self, start_time, stdout=_STDOUT):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo", stdout_content=stdout)
        run_info = {"status": "DONE"}
        if start_time is not None:
            run_info["time"] = start_time
        with open(os.path.join(run_dir, "run-info.json"), "w") as f:
            json.dump(run_info, f)
        return post_process_run(run_dir)["dacapo_result"]

    def test_startup_ms(self):
        dr = self._run("2026-09-23T03:20:24.100+02:00")
        self.assertEqual(dr["first_iteration_ts"], "2026-09-23T03:20:33.315757469+02:00[Europe/Berlin]")
        self.assertEqual(dr["final_iteration_ts"], "2026-09-23T03:20:34.400000000+02:00[Europe/Berlin]")
        self.assertEqual(dr["startup_ms"], 9216)
        keys = list(dr)
        self.assertEqual(keys.index("startup_ms"), keys.index("final_iteration_ts") + 1)

    def test_old_format_time_rejected(self):
        """Only the collector timestamp format is accepted (PRD.md D-67)."""
        self.assertNotIn("startup_ms", self._run("2026-09-23T03:20:24+0200"))

    def test_different_utc_offsets(self):
        self.assertEqual(self._run("2026-09-23T01:20:24.100+00:00")["startup_ms"], 9216)

    def test_missing_time(self):
        self.assertNotIn("startup_ms", self._run(None))

    def test_no_iteration_timestamps(self):
        stdout = "===== DaCapo 23.11-MR2-chopin pmd PASSED in 900 msec =====\n"
        dr = self._run("2026-09-23T03:20:24.100+02:00", stdout=stdout)
        self.assertNotIn("first_iteration_ts", dr)
        self.assertNotIn("startup_ms", dr)



class TestPostProcessScratchCleanup(unittest.TestCase):
    """DaCapo's scratch/ extraction directory is removed after post-processing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_dacapo_scratch_dir_removed(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        scratch_dir = os.path.join(run_dir, "scratch")
        os.makedirs(os.path.join(scratch_dir, "nested"))
        with open(os.path.join(scratch_dir, "nested", "file.txt"), "w") as f:
            f.write("dacapo extraction leftovers")

        post_process_run(run_dir)

        self.assertFalse(os.path.exists(scratch_dir), "scratch dir must be removed")

    def test_dacapo_no_scratch_dir_is_not_an_error(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        post_process_run(run_dir)  # must not raise

    def test_specjbb_scratch_dir_untouched(self):
        # specjbb runs never produce a scratch dir, but if one somehow
        # existed it should be left alone (cleanup is dacapo-specific).
        run_dir = _make_run_dir(self.tmp, benchmark="specjbb")
        scratch_dir = os.path.join(run_dir, "scratch")
        os.makedirs(scratch_dir)

        post_process_run(run_dir)

        self.assertTrue(os.path.exists(scratch_dir), "specjbb scratch dir must not be touched")


class TestPostProcessSpecjbb(unittest.TestCase):
    """Successful SPECjbb run: result.json contains specjbb_result, temperatures, memory, threads."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_specjbb_result_json_created(self):
        run_dir = _make_run_dir(self.tmp, benchmark="specjbb")
        result = post_process_run(run_dir)

        result_path = os.path.join(run_dir, "result.json")
        self.assertTrue(os.path.exists(result_path))

        with open(result_path) as f:
            on_disk = json.load(f)
        self.assertEqual(result, on_disk)

    def test_specjbb_result_keys(self):
        run_dir = _make_run_dir(self.tmp, benchmark="specjbb")
        result = post_process_run(run_dir)

        self.assertIn("specjbb_result", result)
        self.assertNotIn("dacapo_result", result)
        self.assertIn("temperatures", result)
        self.assertIn("memory", result)
        self.assertIn("threads", result)

    def test_specjbb_values(self):
        run_dir = _make_run_dir(self.tmp, benchmark="specjbb")
        result = post_process_run(run_dir)

        spec = result["specjbb_result"]
        self.assertEqual(spec["hbirmax"], 220)
        self.assertEqual(spec["hbirset"], 200)
        self.assertEqual(spec["maxjops"], 136)
        self.assertEqual(spec["critjops"], 0)


class TestPostProcessMissingCollectors(unittest.TestCase):
    """Missing collector files: result.json still produced, sections omitted with warnings."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_missing_temps_omits_section(self):
        run_dir = _make_run_dir(self.tmp, include_temps=False)
        stderr_buf = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_buf):
            result = post_process_run(run_dir)

        self.assertNotIn("temperatures", result)
        self.assertIn("memory", result)
        self.assertIn("threads", result)
        self.assertIn("Warning", stderr_buf.getvalue())

    def test_missing_proclog_omits_section(self):
        run_dir = _make_run_dir(self.tmp, include_proclog=False)
        stderr_buf = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_buf):
            result = post_process_run(run_dir)

        self.assertIn("temperatures", result)
        self.assertNotIn("memory", result)
        self.assertNotIn("threads", result)
        self.assertNotIn("page_faults", result)
        self.assertIn("Warning", stderr_buf.getvalue())

    def test_missing_both_collectors(self):
        run_dir = _make_run_dir(self.tmp, include_temps=False, include_proclog=False)
        stderr_buf = io.StringIO()
        with unittest.mock.patch("sys.stderr", stderr_buf):
            result = post_process_run(run_dir)

        self.assertNotIn("temperatures", result)
        self.assertNotIn("memory", result)
        self.assertNotIn("threads", result)
        # benchmark result must still be present
        self.assertIn("dacapo_result", result)
        result_path = os.path.join(run_dir, "result.json")
        self.assertTrue(os.path.exists(result_path))


class TestPostProcessPerflog(unittest.TestCase):
    """perflog.log present: perflog.jsonl is generated in the run directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_perflog_jsonl_created(self):
        run_dir = _make_run_dir(self.tmp, include_perflog=True)
        post_process_run(run_dir)

        perf_jsonl = os.path.join(run_dir, "perflog.jsonl")
        self.assertTrue(os.path.exists(perf_jsonl), "perflog.jsonl must be created")

    def test_perflog_jsonl_valid_json_lines(self):
        run_dir = _make_run_dir(self.tmp, include_perflog=True)
        post_process_run(run_dir)

        perf_jsonl = os.path.join(run_dir, "perflog.jsonl")
        with open(perf_jsonl) as f:
            lines = [ln for ln in f if ln.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            obj = json.loads(line)  # must not raise
            self.assertIn("timestamp", obj)
            self.assertIn("counters", obj)


class TestPostProcessPidstatMissing(unittest.TestCase):
    """pidstat.log absent: post-processing must not balk, and no pidstat.jsonl is created."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_missing_pidstat_log_is_not_an_error(self):
        run_dir = _make_run_dir(self.tmp, include_pidstat=False)
        result = post_process_run(run_dir)  # must not raise
        self.assertIn("dacapo_result", result)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "pidstat.jsonl")))
        self.assertNotIn("cpu", result)


class TestPostProcessPidstatlog(unittest.TestCase):
    """pidstat.log present: pidstat.jsonl is generated in the run directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_pidstat_jsonl_created(self):
        run_dir = _make_run_dir(self.tmp, include_pidstat=True)
        post_process_run(run_dir)

        pidstat_jsonl = os.path.join(run_dir, "pidstat.jsonl")
        self.assertTrue(os.path.exists(pidstat_jsonl), "pidstat.jsonl must be created")

    def test_pidstat_jsonl_valid_json_lines(self):
        run_dir = _make_run_dir(self.tmp, include_pidstat=True)
        post_process_run(run_dir)

        pidstat_jsonl = os.path.join(run_dir, "pidstat.jsonl")
        with open(pidstat_jsonl) as f:
            lines = [ln for ln in f if ln.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            obj = json.loads(line)  # must not raise
            self.assertIn("timestamp", obj)
            self.assertIn("pid", obj)
            self.assertIn("pct_cpu", obj)

    def test_cpu_stats_added_to_result(self):
        run_dir = _make_run_dir(self.tmp, include_pidstat=True)
        result = post_process_run(run_dir)

        self.assertIn("cpu", result)
        cpu = result["cpu"]
        # _PIDSTAT_LOG has a single sample: %usr=5.00 %system=2.00
        # %guest=0.00 %wait=0.00 %CPU=7.00 -> max == avg per metric.
        self.assertEqual(cpu["pct_usr_max"], 5.0)
        self.assertEqual(cpu["pct_usr_avg"], 5.0)
        self.assertEqual(cpu["pct_cpu_max"], 7.0)
        self.assertEqual(cpu["pct_cpu_avg"], 7.0)


class TestPostProcessStatusRejection(unittest.TestCase):
    """Runs with status != DONE must raise ValueError."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def _assert_rejected(self, status: str):
        run_dir = _make_run_dir(self.tmp, status=status)
        with self.assertRaises((ValueError, RuntimeError)) as ctx:
            post_process_run(run_dir)
        msg = str(ctx.exception).lower()
        # Error message should mention the status value
        self.assertIn(status.lower(), msg)

    def test_failed_status_rejected(self):
        self._assert_rejected("FAILED")

    def test_running_status_rejected(self):
        self._assert_rejected("RUNNING")

    def test_stopped_status_rejected(self):
        self._assert_rejected("STOPPED")

    def test_no_result_json_created_on_rejection(self):
        run_dir = _make_run_dir(self.tmp, status="FAILED")
        try:
            post_process_run(run_dir)
        except (ValueError, RuntimeError):
            pass
        result_path = os.path.join(run_dir, "result.json")
        self.assertFalse(os.path.exists(result_path))


class TestPostProcessMissingRequiredFiles(unittest.TestCase):
    """Missing stdout.log or run-info.json must raise ValueError."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_missing_stdout_log_raises(self):
        run_dir = _make_run_dir(self.tmp, stdout_content=False)
        with self.assertRaises((ValueError, RuntimeError)):
            post_process_run(run_dir)

    def test_missing_run_info_json_raises(self):
        run_dir = _make_run_dir(self.tmp)
        os.unlink(os.path.join(run_dir, "run-info.json"))
        with self.assertRaises((ValueError, RuntimeError)):
            post_process_run(run_dir)

    def test_missing_run_dir_raises(self):
        with self.assertRaises((ValueError, RuntimeError)):
            post_process_run("/tmp/does_not_exist_jbr_test_99999")

    def test_unparseable_stdout_log_raises(self):
        run_dir = _make_run_dir(self.tmp, stdout_content="no benchmark output here\n")
        with self.assertRaises((ValueError, RuntimeError)):
            post_process_run(run_dir)


class TestPostProcessIdempotency(unittest.TestCase):
    """Re-running post_process_run overwrites result.json with identical content."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_idempotent_dacapo(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        result1 = post_process_run(run_dir)
        result2 = post_process_run(run_dir)

        self.assertEqual(result1, result2)

        result_path = os.path.join(run_dir, "result.json")
        with open(result_path) as f:
            on_disk = json.load(f)
        self.assertEqual(result2, on_disk)

    def test_idempotent_specjbb(self):
        run_dir = _make_run_dir(self.tmp, benchmark="specjbb")
        result1 = post_process_run(run_dir)
        result2 = post_process_run(run_dir)

        self.assertEqual(result1, result2)


class TestCliEntryPoint(unittest.TestCase):
    """Tests for bin/jbr-pp CLI behaviour."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_help_exits_zero(self):
        proc = subprocess.run(
            [_JBR_PP, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("run-directory", proc.stdout)

    def test_help_short_flag_exits_zero(self):
        proc = subprocess.run(
            [_JBR_PP, "-h"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("run-directory", proc.stdout)

    def test_successful_run_exits_zero(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        proc = subprocess.run(
            [_JBR_PP, run_dir],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        result_path = os.path.join(run_dir, "result.json")
        self.assertTrue(os.path.exists(result_path))

    def test_nonexistent_run_dir_exits_one(self):
        proc = subprocess.run(
            [_JBR_PP, "/tmp/does_not_exist_jbr_cli_test_99999"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Error", proc.stderr)

    def test_failed_run_exits_one(self):
        run_dir = _make_run_dir(self.tmp, status="FAILED")
        proc = subprocess.run(
            [_JBR_PP, run_dir],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Error", proc.stderr)

    def test_rerun_skips_already_processed_by_default(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        post_process_run(run_dir)
        result_path = os.path.join(run_dir, "result.json")
        mtime_before = os.path.getmtime(result_path)

        proc = subprocess.run(
            [_JBR_PP, run_dir],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("1 skipped", proc.stdout)
        self.assertEqual(os.path.getmtime(result_path), mtime_before)

    def test_force_reprocesses_already_processed(self):
        run_dir = _make_run_dir(self.tmp, benchmark="dacapo")
        post_process_run(run_dir)

        proc = subprocess.run(
            [_JBR_PP, "--force", run_dir],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("Re-post-processed:", proc.stdout)

    def test_multiple_dirs_continue_on_error(self):
        good_dir = _make_run_dir(tempfile.mkdtemp(), benchmark="dacapo")
        bad_dir = os.path.join(tempfile.mkdtemp(), "missing")

        proc = subprocess.run(
            [_JBR_PP, bad_dir, good_dir],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Error", proc.stderr)
        self.assertTrue(
            os.path.exists(os.path.join(good_dir, "result.json")),
            "the good directory must still be processed despite the earlier error",
        )
        self.assertIn(
            "1 newly post-processed, 0 re-post-processed, 0 skipped, 1 failed",
            proc.stdout,
        )


class TestDiscoverRunDirs(unittest.TestCase):
    """Unit tests for jbr-pp's default results/ scan helpers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_discover_run_dirs_lists_subdirectories_only(self):
        os.makedirs(os.path.join(self.tmp, "run_b"))
        os.makedirs(os.path.join(self.tmp, "run_a"))
        with open(os.path.join(self.tmp, "not_a_dir.json"), "w") as f:
            f.write("{}")

        dirs = _discover_run_dirs(self.tmp)

        self.assertEqual(
            dirs,
            [os.path.join(self.tmp, "run_a"), os.path.join(self.tmp, "run_b")],
        )

    def test_discover_run_dirs_missing_results_dir(self):
        self.assertEqual(_discover_run_dirs(os.path.join(self.tmp, "nope")), [])

    def test_run_status_reads_status_field(self):
        run_dir = _make_run_dir(self.tmp, status="FAILED")
        self.assertEqual(_run_status(run_dir), "FAILED")

    def test_run_status_missing_file_returns_none(self):
        run_dir = os.path.join(self.tmp, "no-run-info")
        os.makedirs(run_dir)
        self.assertIsNone(_run_status(run_dir))


class TestPostProcessNmtMissing(unittest.TestCase):
    """nmt.log absent: post-processing must not balk, and no nmt.jsonl is created."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_missing_nmt_log_is_not_an_error(self):
        run_dir = _make_run_dir(self.tmp, include_nmt=False)
        result = post_process_run(run_dir)  # must not raise
        self.assertIn("dacapo_result", result)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "nmt.jsonl")))
        self.assertNotIn("malloc", result)


class TestPostProcessNmt(unittest.TestCase):
    """nmt.log present: nmt.jsonl is generated in the run directory as a raw
    time series. The only aggregate rolled up into result.json from it is
    the "malloc" section (total_malloc max/avg); other NMT categories
    are not aggregated (see docs/spec-post-process.md)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_nmt_jsonl_created(self):
        run_dir = _make_run_dir(self.tmp, include_nmt=True)
        post_process_run(run_dir)

        nmt_jsonl = os.path.join(run_dir, "nmt.jsonl")
        self.assertTrue(os.path.exists(nmt_jsonl), "nmt.jsonl must be created")

    def test_nmt_jsonl_valid_json_lines(self):
        run_dir = _make_run_dir(self.tmp, include_nmt=True)
        post_process_run(run_dir)

        nmt_jsonl = os.path.join(run_dir, "nmt.jsonl")
        with open(nmt_jsonl) as f:
            lines = [ln for ln in f if ln.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            obj = json.loads(line)  # must not raise
            self.assertIn("timestamp", obj)
            self.assertIn("pid", obj)
            self.assertIn("total", obj)
            self.assertIn("java_heap", obj)

    def _compress_nmt_log(self, run_dir):
        import shutil
        import subprocess
        if shutil.which("zstd") is None:
            self.skipTest("requires the zstd binary")
        plain = os.path.join(run_dir, "nmt.log")
        with open(plain + ".zst", "wb") as out:
            subprocess.run(["zstd", "-1", "-q", "-c", plain], stdout=out, check=True)
        os.remove(plain)

    def test_compressed_nmt_log_gives_same_nmt_jsonl(self):
        """collectors.compression: nmt.log.zst instead of nmt.log, identical nmt.jsonl."""
        plain_root = os.path.join(self.tmp, "plain")
        zst_root = os.path.join(self.tmp, "zst")
        os.makedirs(plain_root)
        os.makedirs(zst_root)

        plain_dir = _make_run_dir(plain_root, include_nmt=True)
        post_process_run(plain_dir)
        with open(os.path.join(plain_dir, "nmt.jsonl")) as f:
            expected = f.read()

        zst_dir = _make_run_dir(zst_root, include_nmt=True)
        self._compress_nmt_log(zst_dir)
        self.assertFalse(os.path.exists(os.path.join(zst_dir, "nmt.log")))
        post_process_run(zst_dir)
        with open(os.path.join(zst_dir, "nmt.jsonl")) as f:
            self.assertEqual(f.read(), expected)

    def test_nmt_not_added_to_result_json_as_raw_series(self):
        """nmt.jsonl itself is a separate raw time series; result.json gets
        no "nmt"/"native_memory" section (only the "malloc" aggregate)."""
        run_dir = _make_run_dir(self.tmp, include_nmt=True)
        result = post_process_run(run_dir)
        self.assertNotIn("nmt", result)
        self.assertNotIn("native_memory", result)

    def test_malloc_added_to_result_json(self):
        """The "malloc" section is rolled up from nmt.jsonl's total_malloc
        field (see _NMT_LOG's single sample: malloc=50000KB = 48.83 MB)."""
        run_dir = _make_run_dir(self.tmp, include_nmt=True)
        result = post_process_run(run_dir)
        self.assertIn("malloc", result)
        self.assertEqual(result["malloc"]["mallocmax"], 48.83)
        self.assertEqual(result["malloc"]["mallocavg"], 48.83)


class TestPostProcessThreadPrint(unittest.TestCase):
    """thread-print.log -> thread-print.jsonl; best-effort, never blocks result.json."""

    _HOTSPOT_LOG = (
        "2026-09-22T20:29:03.000+02:00\n1376531:\n2026-09-22 20:29:04\n"
        "Full thread dump OpenJDK 64-Bit Server VM (28 mixed mode, sharing)\n\n"
        '"main" #3 [11] prio=5 os_prio=0 cpu=1.50ms elapsed=0.35s tid=0x1 nid=11 runnable  [0x2]\n'
        "   java.lang.Thread.State: RUNNABLE\n"
        "\tat Harness.main(Harness.java:39)\n\n"
        '"GC Thread#0" os_prio=0 cpu=2.00ms elapsed=1.48s tid=0x3 nid=12 runnable  \n\n'
        "JNI global refs: 9, weak refs: 0\n"
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def _run_with_log(self, content):
        run_dir = _make_run_dir(self.tmp)
        with open(os.path.join(run_dir, "thread-print.log"), "w") as f:
            f.write(content)
        result = post_process_run(run_dir)
        return run_dir, result

    def test_missing_log_is_not_an_error(self):
        run_dir = _make_run_dir(self.tmp)
        post_process_run(run_dir)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "thread-print.jsonl")))

    def test_jsonl_created(self):
        run_dir, result = self._run_with_log(self._HOTSPOT_LOG)
        with open(os.path.join(run_dir, "thread-print.jsonl")) as f:
            recs = [json.loads(ln) for ln in f if ln.strip()]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["mutator"]["num_threads"], 1)
        self.assertEqual(recs[0]["gc"]["num_threads"], 1)
        self.assertNotIn("thread_print", result)

    def test_category_counts_added_to_threads_section(self):
        run_dir, result = self._run_with_log(self._HOTSPOT_LOG)
        threads = result["threads"]
        self.assertEqual(threads["mutator"], {"num_threads_max": 1, "num_threads_avg": 1.0})
        self.assertEqual(threads["gc"]["num_threads_max"], 1)
        self.assertEqual(threads["c2"]["num_threads_max"], 0)
        with open(os.path.join(run_dir, "result.json")) as f:
            self.assertEqual(json.load(f)["threads"]["gc"]["num_threads_max"], 1)

    def test_cpu_with_unaccounted_from_proclog(self):
        # Second dump 1 s later: main +500 ms (50 %), GC +0; the process used 80 % over
        # the 1 s timestamp span, so 30 % is unaccounted.
        second = (self._HOTSPOT_LOG
                  .replace("2026-09-22T20:29:03.000", "2026-09-22T20:29:04.000")
                  .replace("cpu=1.50ms elapsed=0.35s", "cpu=501.50ms elapsed=1.35s")
                  .replace("cpu=2.00ms elapsed=1.48s", "cpu=2.00ms elapsed=2.48s"))
        run_dir = _make_run_dir(self.tmp)
        with open(os.path.join(run_dir, "thread-print.log"), "w") as f:
            f.write(self._HOTSPOT_LOG + second)
        with open(os.path.join(run_dir, "proclog.jsonl"), "w") as f:
            for ts, ticks in (("2026-09-22T20:29:03.000+02:00", 0),
                              ("2026-09-22T20:29:04.000+02:00", 80)):
                f.write(json.dumps({"timestamp": ts, "pid": 1, "proc_pid_stat": {
                    "utime": str(ticks), "stime": "0"}, "status": {}}) + "\n")
        threads = post_process_run(run_dir)["threads"]
        self.assertEqual(threads["mutator"]["cpu_pct_avg"], 50.0)
        self.assertEqual(threads["other"]["cpu_pct_avg"], 0.0)
        self.assertEqual(threads["unaccounted"], {"cpu_pct_avg": 30.0, "cpu_pct_max": 30.0})
        self.assertTrue(os.path.exists(os.path.join(run_dir, "thread-print-cpu.svg"))
                        or shutil.which("gnuplot") is None)

    def test_no_category_counts_without_log(self):
        run_dir = _make_run_dir(self.tmp)
        result = post_process_run(run_dir)
        self.assertNotIn("gc", result.get("threads", {}))

    def test_unparseable_log_is_only_a_warning(self):
        run_dir, result = self._run_with_log(
            "2026-09-22T20:29:03.000+02:00\n91679:\nJRE 26 (OpenJ9)\n\n\"main\" prio=5 Id=1 RUNNABLE\n")
        self.assertIn("dacapo_result", result)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "thread-print.jsonl")))


# Minimal OpenJDK (G1) GC log with two collections.
_GC_LOG = """\
[0.010s][info][gc] Using G1
[0.500s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) 24M->4M(256M) 2.345ms
[1.000s][info][gc] GC(1) Pause Young (Normal) (G1 Evacuation Pause) 30M->6M(256M) 2.100ms
"""

_EXTRACT_HEAPSIZES = os.path.join(_PHASE_PP_DIR, "extract-heapsizes.py")


def _zstd_decode(path):
    return subprocess.run(["zstd", "-dc", path], capture_output=True, text=True,
                          check=True).stdout


@unittest.skipUnless(shutil.which("zstd"), "zstd binary not installed")
class TestPostProcessHeapsizesCompression(unittest.TestCase):
    """Step 7g compresses the GC log and heapsizes.txt after plotting."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.run_dir = _make_run_dir(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write_gc_log(self, text, name="gc.log"):
        with open(os.path.join(self.run_dir, name), "w") as f:
            f.write(text)

    def _exists(self, name):
        return os.path.exists(os.path.join(self.run_dir, name))

    def test_gc_log_and_heapsizes_compressed(self):
        self._write_gc_log(_GC_LOG)
        post_process_run(self.run_dir)
        self.assertFalse(self._exists("gc.log"))
        self.assertFalse(self._exists("heapsizes.txt"))
        self.assertEqual(_zstd_decode(os.path.join(self.run_dir, "gc.log.zst")), _GC_LOG)
        self.assertIn("Used(MB)",
                      _zstd_decode(os.path.join(self.run_dir, "heapsizes.txt.zst")))
        if shutil.which("gnuplot"):
            self.assertTrue(self._exists("heapsizes.svg"))

    def test_rerun_regenerates_from_compressed_gc_log(self):
        self._write_gc_log(_GC_LOG)
        post_process_run(self.run_dir)
        heapsizes_zst = os.path.join(self.run_dir, "heapsizes.txt.zst")
        first = _zstd_decode(heapsizes_zst)
        os.unlink(heapsizes_zst)
        post_process_run(self.run_dir)
        self.assertEqual(_zstd_decode(heapsizes_zst), first)
        self.assertFalse(self._exists("gc.log"))
        self.assertFalse(self._exists("heapsizes.txt"))

    def test_gc_log_without_events_still_compressed(self):
        self._write_gc_log("[0.010s][info][gc] Using G1\n")
        result = post_process_run(self.run_dir)
        self.assertIn("dacapo_result", result)
        self.assertFalse(self._exists("gc.log"))
        self.assertTrue(self._exists("gc.log.zst"))
        self.assertFalse(self._exists("heapsizes.txt.zst"))

    def test_gc_all_log_compressed(self):
        self._write_gc_log(_GC_LOG, name="gc-all.log")
        post_process_run(self.run_dir)
        self.assertFalse(self._exists("gc-all.log"))
        self.assertTrue(self._exists("gc-all.log.zst"))
        self.assertTrue(self._exists("heapsizes.txt.zst"))

    def test_zstd_failure_is_only_a_warning(self):
        self._write_gc_log(_GC_LOG)
        with unittest.mock.patch("post_process.compress_file", return_value="boom"), \
                unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = post_process_run(self.run_dir)
        self.assertIn("dacapo_result", result)
        self.assertTrue(self._exists("gc.log"))
        self.assertIn("boom", err.getvalue())


@unittest.skipUnless(shutil.which("zstd"), "zstd binary not installed")
class TestExtractHeapsizesCompressedInput(unittest.TestCase):
    """extract-heapsizes.py reads a zstd-compressed GC log like a plain one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _extract(self, arg):
        out = os.path.join(self.tmp, "out.txt")
        subprocess.run([sys.executable, _EXTRACT_HEAPSIZES, arg, "-o", out],
                       capture_output=True, text=True, check=True)
        with open(out) as f:
            return f.read()

    def test_plain_and_compressed_give_same_output(self):
        plain = os.path.join(self.tmp, "gc.log")
        with open(plain, "w") as f:
            f.write(_GC_LOG)
        expected = self._extract(plain)
        subprocess.run(["zstd", "-q", "--rm", plain], check=True)
        self.assertEqual(self._extract(plain + ".zst"), expected)
        self.assertEqual(self._extract(self.tmp), expected)


_ARENA_LOG = """\
[1.150s][info][arena,stats] Arena Memory Manager, Tick 0
[1.150s][info][arena,stats] ArenaSlabGroup
[1.150s][info][arena,stats]   ArenaSlab 256
[1.150s][info][arena,stats]     granule_size: 65536 (256 blocks)
[1.150s][info][arena,stats]     in-use blocks: 21
[1.150s][info][arena,stats]     Dirty granules: 1 (256 blocks, 65536 bytes)
[1.150s][info][arena,stats] PerCore Holder
[1.150s][info][arena,stats]   PerCore data for CPU: 0
[1.150s][info][arena,stats]   ArenaBlockCache 256
[1.150s][info][arena,stats]     count: 5
"""


class TestPostProcessArenaLog(unittest.TestCase):
    """Step 7f4 runs bin/tools/arena-log-parser.py on arena.log into arena-report/."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.run_dir = _make_run_dir(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write_arena_log(self, text):
        with open(os.path.join(self.run_dir, "arena.log"), "w") as f:
            f.write(text)

    def test_arena_report_written(self):
        self._write_arena_log(_ARENA_LOG)
        result = post_process_run(self.run_dir)
        report = os.path.join(self.run_dir, "arena-report")
        with open(os.path.join(report, "blocks-256.json")) as f:
            sample = json.load(f)["samples"][0]
        self.assertEqual((sample["cached_blocks"], sample["in_use_blocks"],
                          sample["dirty_blocks"]), (5, 16, 256))
        self.assertTrue(os.path.exists(os.path.join(report, "combined.json")))
        self.assertTrue(os.path.exists(os.path.join(report, "arena-summary.json")))
        self.assertIn("dacapo_result", result)
        # no "last tick peak" in the log -> no used_mb_*
        self.assertEqual(result["arena"], {"dirty_mb_max": 0.06, "dirty_mb_avg": 0.06,
                                           "cached_mb_max": 0.0, "cached_mb_avg": 0.0})

    @unittest.skipIf(shutil.which("zstd") is None, "zstd not installed")
    def test_arena_log_compressed_and_reread(self):
        self._write_arena_log(_ARENA_LOG)
        post_process_run(self.run_dir)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "arena.log")))
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, "arena.log.zst")))
        # a re-run parses the compressed log
        shutil.rmtree(os.path.join(self.run_dir, "arena-report"))
        post_process_run(self.run_dir)
        with open(os.path.join(self.run_dir, "arena-report", "blocks-256.json")) as f:
            self.assertEqual(json.load(f)["samples"][0]["cached_blocks"], 5)

    def test_arena_log_without_ticks_only_warns(self):
        self._write_arena_log("[2026-01-01T00:00:00.000+0100][1234] Changed Arena 0x1, new size: 100\n")
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            result = post_process_run(self.run_dir)
        self.assertIn("dacapo_result", result)
        self.assertIn("arena-log-parser.py failed", err.getvalue())
        self.assertNotIn("arena", result)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "arena-report")))

    def test_header_passed_to_tool(self):
        self._write_arena_log(_ARENA_LOG)
        with open(os.path.join(self.run_dir, "info.json"), "w") as f:
            json.dump({"benchmark": "dacapo", "name": "dacapo.spring", "tags": ["t1"]}, f)
        real_run = subprocess.run
        calls = []

        def spy(cmd, *a, **kw):
            calls.append(cmd)
            return real_run(cmd, *a, **kw)

        with unittest.mock.patch("post_process.subprocess.run", side_effect=spy):
            post_process_run(self.run_dir)
        arena_cmds = [c for c in calls if any("arena-log-parser.py" in str(x) for x in c)]
        self.assertEqual(len(arena_cmds), 1)
        # same header line as all other jbr plots: "<dir> - <name> - <tags>"
        self.assertEqual(arena_cmds[0][-2:], ["--header", "run - dacapo.spring - t1"])

    def test_no_arena_log_no_report(self):
        post_process_run(self.run_dir)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "arena-report")))


if __name__ == "__main__":
    unittest.main()
