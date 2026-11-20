# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# test_listing.py — unit tests for scripts/phase-analyze/listing.py
#
# Run with:
#   python3 -m unittest discover scripts

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Allow import from the phase-analyze package when run via unittest discover.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_ANALYZE = os.path.dirname(_HERE)
_COMMON = os.path.join(os.path.dirname(_PHASE_ANALYZE), "common")
for _p in (_PHASE_ANALYZE, _COMMON):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from listing import (  # noqa: E402
    sanitize_jvmargs,
    extract_cell,
    resolve_columns,
    resolve_sort_cols,
    resolve_group_cols,
    resolve_compare_cols,
    format_run_table,
    GROUPS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dacapo_run(**overrides):
    """Return a minimal merged dict for a DaCapo run."""
    base = {
        "runid": "a1bcd3ca-e00c-46fd-8121-c1207ddfc6af",
        "name": "dacapo-batik-large",
        "benchmark": "dacapo",
        "dacapo": {"sub_benchmark": "batik", "datasize": "large"},
        "time": "2026-08-06T11:30:35.123456+00:00",
        "duration_secs": 123,
        "tags": ["G1", "baseline"],
        "jdk": {
            "name": "openjdk-21",
            "major": 21,
            "type": "openjdk",
            "version": "21.0.4",
        },
        "hostname": "myhost",
        "arch": "x86_64",
        "os": "Linux",
        "dacapo_result": {"result_ms": 9876},
        "heap": {"gc": "g1gc", "heapsize": 16384},
        "jvmargs": ["-Xmx16g", "-Xms1g"],
    }
    base.update(overrides)
    return base


def _specjbb_run(**overrides):
    """Return a minimal merged dict for a SPECjbb run."""
    base = {
        "runid": "b2cde4db-f11d-5700-9232-d2318eeg0000",
        "name": "specjbb-2024",
        "benchmark": "specjbb",
        "time": "2026-07-01T09:00:00",
        "duration_secs": 600,
        "tags": [],
        "jdk": {
            "name": "openjdk-17",
            "major": 17,
            "type": "openjdk",
            "version": "17.0.9",
        },
        "hostname": "otherhost",
        "arch": "aarch64",
        "os": "Linux",
        "specjbb_result": {
            "maxjops": 50000,
            "critjops": 40000,
            "hbirmax": 100,
            "hbirset": 80,
        },
        "heap": {"gc": "gencon", "heapsize": 8192},
        "jvmargs": ["-Xmx8g"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# sanitize_jvmargs
# ---------------------------------------------------------------------------

class TestSanitizeJvmflags(unittest.TestCase):

    def test_strips_xlog_prefix(self):
        flags = ["-Xlog:gc*:file=/tmp/gc.log", "-Xmx8g"]
        self.assertEqual(sanitize_jvmargs(flags), "-Xmx8g")

    def test_strips_xverbosegclog_prefix(self):
        flags = ["-Xverbosegclog:file=/tmp/gc.log,20,4m", "-Xmx4g"]
        self.assertEqual(sanitize_jvmargs(flags), "-Xmx4g")

    def test_strips_print_flags_final_exact(self):
        flags = ["-XX:+PrintFlagsFinal", "-Xmx2g"]
        self.assertEqual(sanitize_jvmargs(flags), "-Xmx2g")

    def test_strips_unlock_experimental_vm_options_exact(self):
        flags = ["-XX:+UnlockExperimentalVMOptions", "-Xmx2g"]
        self.assertEqual(sanitize_jvmargs(flags), "-Xmx2g")

    def test_strips_error_file_prefix(self):
        flags = ["-XX:ErrorFile=/tmp/err.log", "-server"]
        self.assertEqual(sanitize_jvmargs(flags), "-server")

    def test_strips_speclicense_prefix(self):
        flags = ["-Dconfig.test.specLicense=abc123", "-Xmx16g"]
        self.assertEqual(sanitize_jvmargs(flags), "-Xmx16g")

    def test_non_stripped_flags_preserved(self):
        flags = ["-Xmx16g", "-Xms1g", "-server", "-XX:+UseG1GC"]
        self.assertEqual(sanitize_jvmargs(flags), "-Xmx16g -Xms1g -server -XX:+UseG1GC")

    def test_all_stripped_returns_empty(self):
        flags = ["-Xlog:gc", "-XX:+PrintFlagsFinal"]
        self.assertEqual(sanitize_jvmargs(flags), "")

    def test_empty_list_returns_empty(self):
        self.assertEqual(sanitize_jvmargs([]), "")

    def test_prefix_match_is_not_exact(self):
        # "-XX:+PrintFlagsFinalExtra" should NOT be stripped by exact rule.
        flags = ["-XX:+PrintFlagsFinalExtra"]
        self.assertEqual(sanitize_jvmargs(flags), "-XX:+PrintFlagsFinalExtra")


# ---------------------------------------------------------------------------
# extract_cell — runid
# ---------------------------------------------------------------------------

class TestExtractCellRunid(unittest.TestCase):

    def test_runid_always_full(self):
        merged = {"runid": "a1bcd3ca-e00c-46fd-8121-c1207ddfc6af"}
        cell = extract_cell("runid", merged)
        self.assertEqual(cell, "a1bcd3ca-e00c-46fd-8121-c1207ddfc6af")

    def test_runid_short_id(self):
        merged = {"runid": "shortid"}
        cell = extract_cell("runid", merged)
        self.assertEqual(cell, "shortid")

    def test_runid_missing_returns_sentinel(self):
        cell = extract_cell("runid", {})
        self.assertEqual(cell, "-")


# ---------------------------------------------------------------------------
# extract_cell — batchid
# ---------------------------------------------------------------------------

class TestExtractCellBatchid(unittest.TestCase):

    def test_batchid_returns_value(self):
        merged = {"batchid": "b1cd3ca-e00c-46fd-8121-c1207ddfc6af"}
        cell = extract_cell("batchid", merged)
        self.assertEqual(cell, "b1cd3ca-e00c-46fd-8121-c1207ddfc6af")

    def test_batchid_missing_returns_sentinel(self):
        cell = extract_cell("batchid", {})
        self.assertEqual(cell, "-")


# ---------------------------------------------------------------------------
# extract_cell — jvmflags
# ---------------------------------------------------------------------------

class TestExtractCellJvmflags(unittest.TestCase):

    def test_sanitization_always_applied(self):
        merged = {"jvmargs": ["-Xlog:gc", "-Xmx8g"]}
        cell = extract_cell("jvmargs", merged)
        self.assertNotIn("-Xlog:", cell)
        self.assertIn("-Xmx8g", cell)

    def test_long_flags_not_truncated(self):
        long_flag = "-Xsomeflag=" + "a" * 90
        merged = {"jvmargs": [long_flag]}
        cell = extract_cell("jvmargs", merged)
        self.assertFalse(cell.endswith("..."))
        self.assertEqual(cell, long_flag)

    def test_absent_jvm_flags_key_returns_sentinel(self):
        cell = extract_cell("jvmargs", {})
        self.assertEqual(cell, "-")

    def test_empty_jvm_flags_returns_empty_string(self):
        cell = extract_cell("jvmargs", {"jvmargs": []})
        self.assertEqual(cell, "")


# ---------------------------------------------------------------------------
# extract_cell — sub and dsize (dacapo vs specjbb)
# ---------------------------------------------------------------------------

class TestExtractCellDacapoSpecific(unittest.TestCase):

    def test_sub_returns_value_for_dacapo(self):
        merged = {"dacapo": {"sub_benchmark": "batik", "datasize": "large"}}
        self.assertEqual(extract_cell("sub", merged), "batik")

    def test_sub_returns_sentinel_for_specjbb(self):
        # specjbb runs have no "dacapo" key
        merged = {"benchmark": "specjbb"}
        self.assertEqual(extract_cell("sub", merged), "-")

    def test_dsize_returns_value_for_dacapo(self):
        merged = {"dacapo": {"sub_benchmark": "batik", "datasize": "large"}}
        self.assertEqual(extract_cell("datasize", merged), "large")

    def test_dsize_returns_sentinel_for_specjbb(self):
        merged = {"benchmark": "specjbb"}
        self.assertEqual(extract_cell("datasize", merged), "-")


# ---------------------------------------------------------------------------
# extract_cell — result_ms and maxjops
# ---------------------------------------------------------------------------

class TestExtractCellResults(unittest.TestCase):

    def test_result_ms_dacapo_run(self):
        merged = {"dacapo_result": {"result_ms": 9876}}
        self.assertEqual(extract_cell("result_ms", merged), "9876")

    def test_result_ms_specjbb_run_returns_sentinel(self):
        # specjbb runs have no "dacapo_result" key
        merged = {"specjbb_result": {"maxjops": 50000}}
        self.assertEqual(extract_cell("result_ms", merged), "-")

    def test_maxjops_specjbb_run(self):
        merged = {"specjbb_result": {"maxjops": 50000}}
        self.assertEqual(extract_cell("maxjops", merged), "50000")

    def test_maxjops_dacapo_run_returns_sentinel(self):
        # dacapo runs have no "specjbb_result" key
        merged = {"dacapo_result": {"result_ms": 9876}}
        self.assertEqual(extract_cell("maxjops", merged), "-")

    def test_stderr_ms_present(self):
        merged = {"dacapo_result": {"result_ms": 9876, "stderr_ms": 12.34}}
        self.assertEqual(extract_cell("stderr_ms", merged), "12.34")

    def test_stderr_ms_absent_below_40_iterations_returns_sentinel(self):
        # stddev_ms/stderr_ms are omitted entirely below the 40-iteration
        # threshold (see parse_dacapo_results.py).
        merged = {"dacapo_result": {"result_ms": 9876}}
        self.assertEqual(extract_cell("stderr_ms", merged), "-")

    def test_stderr_ms_specjbb_run_returns_sentinel(self):
        merged = {"specjbb_result": {"maxjops": 50000}}
        self.assertEqual(extract_cell("stderr_ms", merged), "-")

    def test_stderr_pct_present(self):
        merged = {"dacapo_result": {"result_ms": 9876, "stderr_pct": 1.23}}
        self.assertEqual(extract_cell("stderr_pct", merged), "1.23")

    def test_stderr_pct_absent_returns_sentinel(self):
        merged = {"dacapo_result": {"result_ms": 9876}}
        self.assertEqual(extract_cell("stderr_pct", merged), "-")

    def test_stderr_pct_specjbb_run_returns_sentinel(self):
        merged = {"specjbb_result": {"maxjops": 50000}}
        self.assertEqual(extract_cell("stderr_pct", merged), "-")

    def test_latency_columns_present(self):
        dr = {"result_ms": 9876, "p50": 100, "p99": 300, "p100": 600,
              "p50_smooth": 110, "p99_smooth": 310, "p100_smooth": 610,
              "p99_stderr": 4.5, "p99_stderr_pct": 1.5,
              "p99_smooth_stderr": 5.5, "p99_smooth_stderr_pct": 1.77}
        merged = {"dacapo_result": dr}
        for col, val in dr.items():
            if col != "result_ms":
                self.assertEqual(extract_cell(col, merged), str(val))

    def test_latency_columns_absent_return_sentinel(self):
        for merged in ({"dacapo_result": {"result_ms": 9876}},
                       {"specjbb_result": {"maxjops": 50000}}):
            for col in ("p50", "p99_smooth", "p100", "p99_stderr_pct"):
                self.assertEqual(extract_cell(col, merged), "-")

    def test_startup_ms_present(self):
        merged = {"dacapo_result": {"result_ms": 9876, "startup_ms": 9216}}
        self.assertEqual(extract_cell("startup_ms", merged), "9216")

    def test_startup_ms_absent_returns_sentinel(self):
        merged = {"dacapo_result": {"result_ms": 9876}}
        self.assertEqual(extract_cell("startup_ms", merged), "-")

    def test_startup_ms_specjbb_run_returns_sentinel(self):
        merged = {"specjbb_result": {"maxjops": 50000}}
        self.assertEqual(extract_cell("startup_ms", merged), "-")


# ---------------------------------------------------------------------------
# extract_cell — heapsz, heapszf, gc
# ---------------------------------------------------------------------------

class TestExtractCellHeap(unittest.TestCase):

    def test_heapsz_present(self):
        merged = {"heap": {"heapsize": 16384}}
        self.assertEqual(extract_cell("heapsz", merged), "16384")

    def test_heapsz_missing_returns_sentinel(self):
        self.assertEqual(extract_cell("heapsz", {}), "-")

    def test_repeat_no_present(self):
        self.assertEqual(extract_cell("repeat_no", {"repeat_no": 2}), "2")

    def test_repeat_no_zero(self):
        self.assertEqual(extract_cell("repeat_no", {"repeat_no": 0}), "0")

    def test_repeat_no_missing_returns_sentinel(self):
        self.assertEqual(extract_cell("repeat_no", {}), "-")

    def test_heapszf_present(self):
        merged = {"heapsize_factor": 2.0}
        self.assertEqual(extract_cell("heapszf", merged), "2.0")

    def test_heapszf_missing_returns_sentinel(self):
        self.assertEqual(extract_cell("heapszf", {}), "-")

    def test_heapszf_null_returns_sentinel(self):
        # Non-swept runs carry an explicit null in info.json.
        merged = {"heapsize_factor": None}
        self.assertEqual(extract_cell("heapszf", merged), "-")

    def test_gc_present(self):
        merged = {"heap": {"gc": "g1gc"}}
        self.assertEqual(extract_cell("gc", merged), "g1gc")

    def test_gc_missing_returns_sentinel(self):
        self.assertEqual(extract_cell("gc", {}), "-")

    def test_gc_null_returns_sentinel(self):
        # Runs where the GC flag couldn't be recognized carry an explicit
        # null in info.json (see execute.detect_gc).
        merged = {"heap": {"gc": None}}
        self.assertEqual(extract_cell("gc", merged), "-")


# ---------------------------------------------------------------------------
# extract_cell — memory (rssmax, rssavg)
# ---------------------------------------------------------------------------

class TestExtractCellMemory(unittest.TestCase):

    def test_rss_max_present(self):
        merged = {"memory": {"rssmax": 4096, "rssavg": 2048}}
        self.assertEqual(extract_cell("rssmax", merged), "4096")

    def test_rss_avg_present(self):
        merged = {"memory": {"rssmax": 4096, "rssavg": 2048}}
        self.assertEqual(extract_cell("rssavg", merged), "2048")

    def test_rss_missing_memory_key_returns_sentinel(self):
        # No proclog collected for this run.
        self.assertEqual(extract_cell("rssmax", {}), "-")
        self.assertEqual(extract_cell("rssavg", {}), "-")

    def test_rss_avg_missing_from_memory_dict_returns_sentinel(self):
        # e.g. no timestamped proclog lines, so rssavg couldn't be computed.
        merged = {"memory": {"rssmax": 4096}}
        self.assertEqual(extract_cell("rssavg", merged), "-")


# ---------------------------------------------------------------------------
# extract_cell — malloc (mallocmax, mallocavg)
# ---------------------------------------------------------------------------

class TestExtractCellMalloc(unittest.TestCase):

    def test_malloc_max_present(self):
        merged = {"malloc": {"mallocmax": 48.83, "mallocavg": 42.5}}
        self.assertEqual(extract_cell("mallocmax", merged), "48.83")

    def test_malloc_avg_present(self):
        merged = {"malloc": {"mallocmax": 48.83, "mallocavg": 42.5}}
        self.assertEqual(extract_cell("mallocavg", merged), "42.5")

    def test_malloc_missing_key_returns_sentinel(self):
        # No nmt.log collected for this run.
        self.assertEqual(extract_cell("mallocmax", {}), "-")
        self.assertEqual(extract_cell("mallocavg", {}), "-")

    def test_malloc_avg_missing_from_malloc_dict_returns_sentinel(self):
        # e.g. no timestamped nmt.jsonl lines, so mallocavg couldn't be computed.
        merged = {"malloc": {"mallocmax": 48.83}}
        self.assertEqual(extract_cell("mallocavg", merged), "-")


class TestExtractCellPageFaults(unittest.TestCase):

    def test_page_faults_present(self):
        merged = {"page_faults": {"minflt": 4922269, "majflt": 72}}
        self.assertEqual(extract_cell("minflt", merged), "4922269")
        self.assertEqual(extract_cell("majflt", merged), "72")

    def test_page_faults_missing_returns_sentinel(self):
        self.assertEqual(extract_cell("minflt", {}), "-")
        self.assertEqual(extract_cell("majflt", {}), "-")


class TestExtractCellArena(unittest.TestCase):

    def test_arena_present(self):
        merged = {"arena": {"used_mb_max": 10.5, "used_mb_avg": 3.0, "dirty_mb_max": 2.0,
                            "dirty_mb_avg": 1.25, "cached_mb_max": 0.5, "cached_mb_avg": 0.1}}
        self.assertEqual(
            [extract_cell(c, merged) for c in GROUPS["ARENAALL"]],
            ["10.5", "3.0", "2.0", "1.25", "0.5", "0.1"])

    def test_arena_missing_returns_sentinel(self):
        self.assertEqual(extract_cell("ardavg", {}), "-")
        # no in-use peak in the log
        self.assertEqual(extract_cell("arumax", {"arena": {"dirty_mb_max": 2.0}}), "-")


# ---------------------------------------------------------------------------
# extract_cell — threads (osthreads_max, osthreads_avg)
# ---------------------------------------------------------------------------

class TestExtractCellThreads(unittest.TestCase):

    def test_osthreads_max_present(self):
        merged = {"threads": {"osthreads_max": 24, "osthreads_avg": 18}}
        self.assertEqual(extract_cell("osthreads_max", merged), "24")

    def test_osthreads_avg_present(self):
        merged = {"threads": {"osthreads_max": 24, "osthreads_avg": 18}}
        self.assertEqual(extract_cell("osthreads_avg", merged), "18")

    def test_osthreads_missing_threads_key_returns_sentinel(self):
        # No proclog collected for this run.
        self.assertEqual(extract_cell("osthreads_max", {}), "-")
        self.assertEqual(extract_cell("osthreads_avg", {}), "-")

    def test_thread_category_columns(self):
        merged = {"threads": {"osthreads_max": 24,
                              "gc": {"num_threads_max": 11, "num_threads_avg": 10.5,
                                     "cpu_pct_avg": 22.7, "cpu_pct_max": 180.4},
                              "mutator": {"num_threads_max": 3, "num_threads_avg": 1.2}}}
        self.assertEqual(extract_cell("gc_max", merged), "11")
        self.assertEqual(extract_cell("gc_avg", merged), "10.5")
        self.assertEqual(extract_cell("gc_cpu_avg", merged), "22.7")
        self.assertEqual(extract_cell("gc_cpu_max", merged), "180.4")
        self.assertEqual(extract_cell("mutator_avg", merged), "1.2")
        # Key absent (no CPU values), category absent, or no threads section at all.
        self.assertEqual(extract_cell("mutator_cpu_avg", merged), "-")
        self.assertEqual(extract_cell("c2_max", merged), "-")
        self.assertEqual(extract_cell("other_avg", {}), "-")


class TestExtractCellCollint(unittest.TestCase):

    def test_present(self):
        self.assertEqual(extract_cell("collint_secs", {"collectors": {"interval_secs": 5}}), "5")

    def test_null_returns_sentinel(self):
        self.assertEqual(extract_cell("collint_secs", {"collectors": {"interval_secs": None}}), "-")

    def test_missing_returns_sentinel(self):
        self.assertEqual(extract_cell("collint_secs", {}), "-")


class TestExtractCellCollectors(unittest.TestCase):

    def test_enabled_joined(self):
        merged = {"collectors": {"enabled": ["cpu_temp", "pidstat", "nmt"]}}
        self.assertEqual(extract_cell("collectors", merged), "cpu_temp,pidstat,nmt")

    def test_empty_list_is_empty_string(self):
        self.assertEqual(extract_cell("collectors", {"collectors": {"enabled": []}}), "")

    def test_missing_returns_sentinel(self):
        self.assertEqual(extract_cell("collectors", {}), "-")
        self.assertEqual(extract_cell("collectors", {"collectors": None}), "-")

    def test_moved_to_end_without_O(self):
        self.assertEqual(resolve_columns("collectors,duration", None),
                         ["runid", "time", "name", "duration", "tags", "collectors"])

# ---------------------------------------------------------------------------
# extract_cell — temperature (tempmax, tempin, tempout)
# ---------------------------------------------------------------------------

class TestExtractCellTemperature(unittest.TestCase):

    def test_tempmax_present(self):
        merged = {"temperatures": {"tempmax": 75, "tempin": 40, "tempout": 60}}
        self.assertEqual(extract_cell("tempmax", merged), "75")

    def test_tempin_present(self):
        merged = {"temperatures": {"tempmax": 75, "tempin": 40, "tempout": 60}}
        self.assertEqual(extract_cell("tempin", merged), "40")

    def test_tempout_present(self):
        merged = {"temperatures": {"tempmax": 75, "tempin": 40, "tempout": 60}}
        self.assertEqual(extract_cell("tempout", merged), "60")

    def test_temp_missing_temperatures_key_returns_sentinel(self):
        # No temperature sensor/collector for this run.
        self.assertEqual(extract_cell("tempmax", {}), "-")
        self.assertEqual(extract_cell("tempin", {}), "-")
        self.assertEqual(extract_cell("tempout", {}), "-")


# ---------------------------------------------------------------------------
# extract_cell — cpu (cpu_<metric>_<max|avg>, cpu_<max|avg>)
# ---------------------------------------------------------------------------

class TestExtractCellCpu(unittest.TestCase):

    _CPU = {
        "pct_usr_max": 810.0, "pct_usr_avg": 465.75,
        "pct_system_max": 42.0, "pct_system_avg": 30.25,
        "pct_guest_max": 0.0, "pct_guest_avg": 0.0,
        "pct_wait_max": 0.0, "pct_wait_avg": 0.0,
        "pct_cpu_max": 842.0, "pct_cpu_avg": 496.0,
    }

    # column name -> result.json key under "cpu"
    _COLS = {
        "cpu_usr_max": "pct_usr_max", "cpu_usr_avg": "pct_usr_avg",
        "cpu_sys_max": "pct_system_max", "cpu_sys_avg": "pct_system_avg",
        "cpu_guest_max": "pct_guest_max", "cpu_guest_avg": "pct_guest_avg",
        "cpu_wait_max": "pct_wait_max", "cpu_wait_avg": "pct_wait_avg",
        "cpu_max": "pct_cpu_max", "cpu_avg": "pct_cpu_avg",
    }

    def test_all_ten_fields_present(self):
        merged = {"cpu": dict(self._CPU)}
        for col, field in self._COLS.items():
            self.assertEqual(extract_cell(col, merged), str(self._CPU[field]))

    def test_missing_cpu_key_returns_sentinel(self):
        # No pidstat collected for this run.
        for col in self._COLS:
            self.assertEqual(extract_cell(col, {}), "-")

    def test_field_missing_from_cpu_dict_returns_sentinel(self):
        # e.g. no timestamped pidstat samples, so avg couldn't be computed.
        merged = {"cpu": {"pct_usr_max": 810.0}}
        self.assertEqual(extract_cell("cpu_usr_avg", merged), "-")


# ---------------------------------------------------------------------------
# extract_cell — tags
# ---------------------------------------------------------------------------

class TestExtractCellTags(unittest.TestCase):

    def test_tags_comma_joined(self):
        merged = {"tags": ["G1", "baseline"]}
        self.assertEqual(extract_cell("tags", merged), "G1,baseline")

    def test_empty_tags_list_returns_empty_string(self):
        merged = {"tags": []}
        self.assertEqual(extract_cell("tags", merged), "")

    def test_missing_tags_key_returns_sentinel(self):
        # Key absent entirely → "-"
        self.assertEqual(extract_cell("tags", {}), "-")

    def test_single_tag(self):
        merged = {"tags": ["perf"]}
        self.assertEqual(extract_cell("tags", merged), "perf")


# ---------------------------------------------------------------------------
# extract_cell — time
# ---------------------------------------------------------------------------

class TestExtractCellTime(unittest.TestCase):

    def test_iso_with_fractional_and_tz(self):
        merged = {"time": "2026-08-06T11:30:35.123456+00:00"}
        cell = extract_cell("time", merged)
        self.assertEqual(cell, "2026-08-06T11:30:35")

    def test_iso_with_z_suffix(self):
        merged = {"time": "2026-08-06T11:30:35Z"}
        cell = extract_cell("time", merged)
        self.assertEqual(cell, "2026-08-06T11:30:35")

    def test_iso_no_suffix(self):
        merged = {"time": "2026-08-06T11:30:35"}
        cell = extract_cell("time", merged)
        self.assertEqual(cell, "2026-08-06T11:30:35")

    def test_missing_timestamp_returns_sentinel(self):
        self.assertEqual(extract_cell("time", {}), "-")


# ---------------------------------------------------------------------------
# resolve_columns
# ---------------------------------------------------------------------------

class TestResolveColumns(unittest.TestCase):

    def test_no_args_returns_default(self):
        cols = resolve_columns(None, None)
        self.assertEqual(cols, ["runid", "time", "name", "tags"])

    def test_o_appends_to_default(self):
        cols = resolve_columns("gc", None)
        self.assertIn("gc", cols)
        # DEFAULT columns must also be present
        for c in GROUPS["DEFAULT"]:
            self.assertIn(c, cols)

    def test_o_default_columns_come_before_extra(self):
        cols = resolve_columns("gc", None)
        idx_name = cols.index("name")
        idx_gc = cols.index("gc")
        self.assertLess(idx_name, idx_gc)

    def test_O_replaces_default(self):
        cols = resolve_columns(None, "JVM")
        jvm_cols = GROUPS["JVM"]
        for c in jvm_cols:
            self.assertIn(c, cols)
        # "name" and "time" from DEFAULT should NOT be present
        self.assertNotIn("name", cols)
        self.assertNotIn("time", cols)

    def test_O_omits_runid_when_not_requested(self):
        cols = resolve_columns(None, "JVM")
        self.assertNotIn("runid", cols)

    def test_o_preserves_dedup_order_without_forcing_runid_first(self):
        cols = resolve_columns("tags", None)
        self.assertEqual(cols, ["runid", "time", "name", "tags"])

    def test_o_moves_trailing_cols_to_end(self):
        cols = resolve_columns("FLAGS,hostname", None)
        self.assertEqual(cols, ["runid", "time", "name", "heapsz", "heapszf",
                                "gc", "hostname", "tags", "jvmargs"])

    def test_O_keeps_trailing_cols_in_place(self):
        cols = resolve_columns(None, "tags,runid,jvmargs,gc")
        self.assertEqual(cols, ["tags", "runid", "jvmargs", "gc"])

    def test_runid_never_duplicated(self):
        # Even if DEFAULT is expanded multiple times, runid appears once.
        cols = resolve_columns("DEFAULT", None)
        self.assertEqual(cols.count("runid"), 1)

    def test_O_with_runid_explicit_not_duplicated(self):
        cols = resolve_columns(None, "runid,JVM")
        self.assertEqual(cols.count("runid"), 1)

    def test_group_expansion_inline(self):
        cols = resolve_columns(None, "HOSTINFO")
        for c in GROUPS["HOSTINFO"]:
            self.assertIn(c, cols)

    def test_results_group_expansion(self):
        cols = resolve_columns(None, "RESULTS")
        for c in ("duration", "result_ms", "stderr_pct", "startup_ms", "maxjops",
                  "critjops", "hbirmax", "hbirset"):
            self.assertIn(c, cols)
        self.assertNotIn("stderr_ms", cols)

    def test_latency_group_expansion(self):
        self.assertEqual(resolve_columns(None, "LATENCY"),
                         ["p50", "p99", "p100", "p99_stderr_pct"])
        self.assertEqual(resolve_columns(None, "LATENCYSMOOTH"),
                         ["p50_smooth", "p99_smooth", "p100_smooth", "p99_smooth_stderr_pct"])

    def test_mem_group_expansion(self):
        cols = resolve_columns(None, "MEM")
        for c in ("rssmax", "rssavg", "mallocmax", "mallocavg"):
            self.assertIn(c, cols)

    def test_faults_group_expansion(self):
        self.assertEqual(resolve_columns(None, "FAULTS")[-2:], ["minflt", "majflt"])

    def test_arena_group_expansion(self):
        self.assertEqual(resolve_columns(None, "ARENA")[-2:], ["ardmax", "ardavg"])
        self.assertEqual(resolve_columns(None, "ARENAALL")[-6:],
                         ["arumax", "aruavg", "ardmax", "ardavg", "arcmax", "arcavg"])

    def test_temp_group_expansion(self):
        cols = resolve_columns(None, "TEMP")
        for c in ("tempmax", "tempin", "tempout"):
            self.assertIn(c, cols)

    def test_cpu_group_expansion_is_usr_sys_total_avg(self):
        cols = resolve_columns(None, "CPU")
        for c in ("cpu_usr_avg", "cpu_sys_avg", "cpu_avg"):
            self.assertIn(c, cols)
        # No guest/wait or _max columns should sneak into the CPU group.
        self.assertNotIn("cpu_guest_avg", cols)
        self.assertNotIn("cpu_wait_avg", cols)
        self.assertNotIn("cpu_usr_max", cols)

    def test_cpuall_group_expansion_is_all_avg_and_max(self):
        cols = resolve_columns(None, "CPUALL")
        for m in ("usr_", "sys_", "guest_", "wait_", ""):
            for s in ("avg", "max"):
                self.assertIn(f"cpu_{m}{s}", cols)

    def test_cpumax_group_expansion_is_all_max(self):
        cols = resolve_columns(None, "CPUMAX")
        for c in ("cpu_usr_max", "cpu_sys_max", "cpu_guest_max",
                  "cpu_wait_max", "cpu_max"):
            self.assertIn(c, cols)
        self.assertNotIn("cpu_usr_avg", cols)

    def test_threads_group_expansion(self):
        cols = resolve_columns(None, "THREADS")
        for c in ("osthreads_max", "osthreads_avg",
                  "c2_max", "c2_avg", "c1_max", "c1_avg",
                  "gc_max", "gc_avg", "mutator_max", "mutator_avg",
                  "other_max", "other_avg"):
            self.assertIn(c, cols)
        self.assertNotIn("gc_cpu_avg", cols)

    def test_threadcpu_group_expansion(self):
        cols = resolve_columns(None, "THREADCPU")
        for c in ("c2", "c1", "gc", "mutator", "other", "unaccounted"):
            self.assertIn(f"{c}_cpu_avg", cols)
            self.assertIn(f"{c}_cpu_max", cols)

    def test_unaccounted_cpu_columns(self):
        merged = {"threads": {"unaccounted": {"cpu_pct_avg": 71.3, "cpu_pct_max": 423.1}}}
        self.assertEqual(extract_cell("unaccounted_cpu_avg", merged), "71.3")
        self.assertEqual(extract_cell("unaccounted_cpu_max", merged), "423.1")
        self.assertEqual(extract_cell("unaccounted_cpu_avg", {"threads": {}}), "-")


# ---------------------------------------------------------------------------
# format_run_table — header
# ---------------------------------------------------------------------------

class TestFormatRunTableHeader(unittest.TestCase):

    def test_header_present_by_default(self):
        runs = [_dacapo_run()]
        table = format_run_table(runs, ["runid", "name", "time"])
        lines = table.splitlines()
        # First line is the header, second is separator
        self.assertIn("runid", lines[0])
        self.assertIn("name", lines[0])
        self.assertTrue(lines[1].startswith("-"))

    def test_header_is_column_name(self):
        runs = [_dacapo_run(memory={"rssmax": 4096, "rssavg": 2048},
                            cpu={"pct_cpu_max": 842.0})]
        table = format_run_table(runs, ["runid", "rssmax", "cpu_max"])
        header = table.splitlines()[0].split()
        self.assertEqual(header, ["runid", "rssmax", "cpu_max"])

    def test_no_header_prints_only_data_rows(self):
        runs = [_dacapo_run(), _dacapo_run()]
        table = format_run_table(runs, ["runid", "name", "time"], no_header=True)
        lines = table.splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertNotIn("runid", line)
            self.assertFalse(line.startswith("-"))


# ---------------------------------------------------------------------------
# format_run_table — auto-omit
# ---------------------------------------------------------------------------

class TestFormatRunTableAutoOmit(unittest.TestCase):

    def test_all_dash_column_dropped(self):
        # Use two dacapo runs: maxjops will always be "-" (no specjbb_result)
        runs = [_dacapo_run(), _dacapo_run(runid="aaaabbbb-0000-0000-0000-000000000000")]
        col_list = ["runid", "name", "maxjops"]
        table = format_run_table(runs, col_list)
        # "maxjops" header should not appear — column was auto-omitted
        self.assertNotIn("maxjops", table)

    def test_thread_category_columns_dropped_but_osthreads_kept(self):
        runs = [_dacapo_run()]
        col_list = ["runid", "osthreads_max", "gc_max", "mutator_avg", "gc_cpu_avg"]
        table = format_run_table(runs, col_list)
        self.assertIn("osthreads_max", table)
        self.assertNotIn("gc_max", table)
        self.assertNotIn("mutator_avg", table)
        self.assertNotIn("gc_cpu_avg", table)

    def test_latency_columns_never_dropped(self):
        runs = [_dacapo_run()]
        col_list = ["runid", "p50", "p99_smooth", "p99_stderr_pct"]
        table = format_run_table(runs, col_list)
        for c in col_list:
            self.assertIn(c, table)

    def test_no_autoomit_preserves_all_dash_column(self):
        runs = [_dacapo_run()]
        col_list = ["runid", "name", "maxjops"]
        table = format_run_table(runs, col_list, no_autoomit=True)
        self.assertIn("maxjops", table)

    def test_non_candidate_column_never_autoomited(self):
        # "name" is not in AUTO_OMIT_CANDIDATES; should never be dropped.
        merged = {"runid": "abcd1234efgh5678", "name": "-", "time": "2026-01-01T00:00:00"}
        runs = [merged]
        col_list = ["runid", "name", "time"]
        table = format_run_table(runs, col_list)
        self.assertIn("name", table)

    def test_heapszf_dropped_when_no_run_was_swept(self):
        runs = [_dacapo_run(), _specjbb_run()]
        col_list = ["runid", "name", "heapszf"]
        table = format_run_table(runs, col_list)
        self.assertNotIn("heapszf", table)

    def test_heapszf_kept_when_some_run_was_swept(self):
        runs = [_dacapo_run(heapsize_factor=2.0), _specjbb_run()]
        col_list = ["runid", "name", "heapszf"]
        table = format_run_table(runs, col_list)
        self.assertIn("heapszf", table)


# ---------------------------------------------------------------------------
# format_run_table — sort
# ---------------------------------------------------------------------------

class TestFormatRunTableSort(unittest.TestCase):

    def _two_runs(self):
        old = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001",
            time="2026-01-01T00:00:00",
        )
        new = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002",
            time="2026-12-31T23:59:59",
        )
        return old, new

    def test_sort_descending(self):
        old, new = self._two_runs()
        table = format_run_table(
            [old, new], ["runid", "time"],
            sort_col="time", sort_asc=False
        )
        lines = table.splitlines()[2:]  # skip header + separator
        # Newer run (bbbb) should be first
        self.assertIn("bbbb", lines[0])
        self.assertIn("aaaa", lines[1])

    def test_sort_ascending(self):
        old, new = self._two_runs()
        table = format_run_table(
            [old, new], ["runid", "time"],
            sort_col="time", sort_asc=True
        )
        lines = table.splitlines()[2:]  # skip header + separator
        # Older run (aaaa) should be first
        self.assertIn("aaaa", lines[0])
        self.assertIn("bbbb", lines[1])

    def test_default_sort_ascending_by_time(self):
        old, new = self._two_runs()
        table = format_run_table(
            [old, new], ["runid", "time"],
            sort_col="time"
        )
        lines = table.splitlines()[2:]  # skip header + separator
        # Older run (aaaa) should be first
        self.assertIn("aaaa", lines[0])
        self.assertIn("bbbb", lines[1])

    def _three_runs_for_multi_sort(self):
        a = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001",
            name="b-run",
            jdk={"name": "openjdk-21", "major": 21, "type": "openjdk",
                 "version": "21.0.4"},
        )
        b = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002",
            name="a-run",
            jdk={"name": "openjdk-21", "major": 21, "type": "openjdk",
                 "version": "21.0.4"},
        )
        c = _dacapo_run(
            runid="cccc0000-0000-0000-0000-000000000003",
            name="z-run",
            jdk={"name": "openjdk-17", "major": 17, "type": "openjdk",
                 "version": "17.0.9"},
        )
        return a, b, c

    def test_multi_column_sort_breaks_ties(self):
        a, b, c = self._three_runs_for_multi_sort()
        table = format_run_table(
            [a, b, c], ["runid", "jdkmajor", "name"],
            sort_col="jdkmajor,name", sort_asc=True
        )
        lines = table.splitlines()[2:]  # skip header + separator
        # Ascending: jdkmajor 17 (cccc) first, then 21-group ordered by
        # name: a-run (bbbb) before b-run (aaaa).
        self.assertIn("cccc", lines[0])
        self.assertIn("bbbb", lines[1])
        self.assertIn("aaaa", lines[2])

    def test_multi_column_sort_descending_applies_to_all_columns(self):
        a, b, c = self._three_runs_for_multi_sort()
        table = format_run_table(
            [a, b, c], ["runid", "jdkmajor", "name"],
            sort_col="jdkmajor,name", sort_asc=False
        )
        lines = table.splitlines()[2:]  # skip header + separator
        self.assertIn("aaaa", lines[0])
        self.assertIn("bbbb", lines[1])
        self.assertIn("cccc", lines[2])

    def test_resolve_sort_cols_splits_and_strips(self):
        self.assertEqual(
            resolve_sort_cols("jdkmajor, name , time"),
            ["jdkmajor", "name", "time"],
        )

    def test_resolve_sort_cols_defaults_to_time(self):
        self.assertEqual(resolve_sort_cols(""), ["time"])

    def test_resolve_sort_cols_rejects_unknown_column(self):
        with self.assertRaises(ValueError):
            resolve_sort_cols("jdkmajor,bogus_col")


# ---------------------------------------------------------------------------
# resolve_group_cols
# ---------------------------------------------------------------------------

class TestResolveGroupCols(unittest.TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(resolve_group_cols(None, ["runid", "name"]))

    def test_empty_string_returns_none(self):
        self.assertIsNone(resolve_group_cols("", ["runid", "name"]))

    def test_valid_single_column(self):
        self.assertEqual(
            resolve_group_cols("name", ["runid", "name", "time"]), ["name"]
        )

    def test_valid_multi_column_strips_whitespace(self):
        self.assertEqual(
            resolve_group_cols(" name , jdkname ", ["runid", "name", "jdkname"]),
            ["name", "jdkname"],
        )

    def test_unknown_column_rejected(self):
        with self.assertRaises(ValueError):
            resolve_group_cols("bogus_col", ["runid", "name"])

    def test_column_not_displayed_rejected(self):
        # "jdkname" is a real column but not part of col_list.
        with self.assertRaises(ValueError):
            resolve_group_cols("jdkname", ["runid", "name"])


# ---------------------------------------------------------------------------
# format_run_table — grouping (-g / --group)
# ---------------------------------------------------------------------------

class TestFormatRunTableGrouping(unittest.TestCase):

    def _runs_for_grouping(self):
        # Two "b-run" rows (different jdk) then one "z-run" row, already in
        # the order format_run_table will emit them for
        # sort_col="name,jdk", sort_asc=True.
        r1 = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001",
            name="b-run",
            jdk={"name": "openjdk-17", "major": 17, "type": "openjdk", "version": "17.0.9"},
        )
        r2 = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002",
            name="b-run",
            jdk={"name": "openjdk-21", "major": 21, "type": "openjdk", "version": "21.0.4"},
        )
        r3 = _dacapo_run(
            runid="cccc0000-0000-0000-0000-000000000003",
            name="z-run",
            jdk={"name": "openjdk-21", "major": 21, "type": "openjdk", "version": "21.0.4"},
        )
        return r1, r2, r3

    def test_banner_on_first_row_and_on_change(self):
        r1, r2, r3 = self._runs_for_grouping()
        table = format_run_table(
            [r1, r2, r3], ["runid", "name", "jdkname"],
            sort_col="name", sort_asc=True, group_cols=["name"],
        )
        lines = table.splitlines()
        # First group banner right at the top, then header + separator.
        self.assertEqual(lines[0], "name: b-run")
        self.assertIn("runid", lines[1])
        self.assertTrue(lines[2].startswith("-"))
        self.assertIn("aaaa", lines[3])
        self.assertIn("bbbb", lines[4])
        # Group changes to "z-run" -> new banner + header + separator.
        self.assertEqual(lines[5], "name: z-run")
        self.assertIn("runid", lines[6])
        self.assertTrue(lines[7].startswith("-"))
        self.assertIn("cccc", lines[8])

    def test_no_banner_repeat_within_same_group(self):
        r1, r2, _ = self._runs_for_grouping()
        table = format_run_table(
            [r1, r2], ["runid", "name"],
            sort_col="name", sort_asc=True, group_cols=["name"],
        )
        self.assertEqual(table.count("name: b-run"), 1)

    def test_multi_column_banner_format(self):
        r1, r2, r3 = self._runs_for_grouping()
        table = format_run_table(
            [r1, r2, r3], ["runid", "name", "jdkname"],
            sort_col="name,jdkname", sort_asc=True, group_cols=["name", "jdkname"],
        )
        lines = table.splitlines()
        self.assertEqual(lines[0], "name: b-run, jdkname: openjdk-17")
        # Second row shares name but differs in jdk -> new banner.
        banner_lines = [l for l in lines if l.startswith("name:")]
        self.assertIn("name: b-run, jdkname: openjdk-21", banner_lines)
        self.assertIn("name: z-run, jdkname: openjdk-21", banner_lines)

    def test_no_group_cols_no_banner(self):
        r1, r2, _ = self._runs_for_grouping()
        table = format_run_table([r1, r2], ["runid", "name"], sort_col="name")
        self.assertNotIn("name: b-run", table)

    def test_group_column_exempt_from_autoomit(self):
        # "heapszf" is an auto-omit candidate; grouping by it must keep it
        # visible even though every value is "-".
        r1, r2, _ = self._runs_for_grouping()
        table = format_run_table(
            [r1, r2], ["runid", "name", "heapszf"],
            sort_col="name", group_cols=["heapszf"],
        )
        self.assertIn("heapszf", table)


# ---------------------------------------------------------------------------
# format_run_table — column widths auto-size
# ---------------------------------------------------------------------------

class TestFormatRunTableColumnWidths(unittest.TestCase):

    def test_column_widths_fit_longest_value(self):
        short_name = _dacapo_run(name="ab")
        long_name = _dacapo_run(
            runid="cccc0000-0000-0000-0000-000000000003",
            name="a-very-long-benchmark-name",
        )
        table = format_run_table(
            [short_name, long_name], ["runid", "name"],
            sort_col="time"
        )
        # All "name" cells should be padded to the same width.
        # The header line must be wide enough for the longest value.
        lines = table.splitlines()
        # Data rows should all have the same length (or longer — rstrip trims trailing)
        # but the long name must appear intact.
        self.assertIn("a-very-long-benchmark-name", table)


# ---------------------------------------------------------------------------
# format_run_table — mixed dacapo+specjbb, auto-omit by type
# ---------------------------------------------------------------------------

class TestFormatRunTableMixedBenchmarks(unittest.TestCase):

    def test_mixed_result_columns_preserved_when_not_all_dash(self):
        # One dacapo + one specjbb: result_ms has a value for dacapo,
        # maxjops has a value for specjbb.  Neither should be auto-omitted.
        dacapo = _dacapo_run()
        specjbb = _specjbb_run()
        col_list = ["runid", "result_ms", "maxjops"]
        table = format_run_table([dacapo, specjbb], col_list, no_autoomit=True)
        self.assertIn("result_ms", table)
        self.assertIn("maxjops", table)


# ---------------------------------------------------------------------------
# format_run_table — runid hyperlink
# ---------------------------------------------------------------------------

class TestFormatRunTableRunidLink(unittest.TestCase):

    def test_no_results_dir_no_link(self):
        runs = [_dacapo_run()]
        table = format_run_table(runs, ["runid", "name"])
        self.assertNotIn("\x1b]8;;", table)
        self.assertIn(runs[0]["runid"], table)

    def test_results_dir_given_but_unsupported_terminal_no_link(self):
        runs = [_dacapo_run()]
        with patch.object(sys.stdout, "isatty", return_value=False):
            table = format_run_table(runs, ["runid", "name"], results_dir="/results")
        self.assertNotIn("\x1b]8;;", table)
        self.assertIn(runs[0]["runid"], table)

    def test_results_dir_given_and_supported_terminal_links(self):
        runs = [_dacapo_run()]
        with patch.object(sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=False):
            os.environ.pop("TERM", None)
            table = format_run_table(runs, ["runid", "name"], results_dir="/results")
        run_id = runs[0]["runid"]
        self.assertIn("\x1b]8;;file:///results/{}\x1b\\".format(run_id), table)
        self.assertIn(run_id, table)

    def test_missing_runid_not_wrapped(self):
        merged = {"name": "no-id-run", "time": "2026-01-01T00:00:00"}
        with patch.object(sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=False):
            os.environ.pop("TERM", None)
            table = format_run_table([merged], ["runid", "name"], results_dir="/results")
        self.assertNotIn("\x1b]8;;", table)


# ---------------------------------------------------------------------------
# format_run_table — markdown output
# ---------------------------------------------------------------------------

class TestFormatRunTableMarkdown(unittest.TestCase):

    def test_basic_markdown_table_shape(self):
        runs = [_dacapo_run()]
        table = format_run_table(runs, ["runid", "name"], markdown=True)
        lines = table.splitlines()
        self.assertEqual(len(lines), 3)
        header, separator, data = lines
        self.assertTrue(header.startswith("| ") and header.endswith(" |"))
        self.assertEqual(separator, "| --- | --- |")
        self.assertIn(runs[0]["runid"], data)

    def test_pipe_in_cell_escaped(self):
        run = _dacapo_run(jvmargs=["-Dfoo=a|b"])
        table = format_run_table([run], ["runid", "jvmargs"], markdown=True)
        self.assertIn("a\\|b", table)
        # After removing escaped pipes, exactly 3 real "|" delimiters remain
        # for a 2-column row: leading, between the two cells, and trailing.
        data_row = table.splitlines()[-1]
        self.assertEqual(data_row.replace("\\|", "").count("|"), 3)

    def test_runid_plain_text_even_with_results_dir(self):
        runs = [_dacapo_run()]
        with patch.object(sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=False):
            os.environ.pop("TERM", None)
            table = format_run_table(
                runs, ["runid", "name"], results_dir="/results", markdown=True
            )
        self.assertNotIn("\x1b]8;;", table)
        self.assertIn(runs[0]["runid"], table)

    def test_grouped_markdown_single_table_with_banner_rows(self):
        r1 = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="b-run",
        )
        r2 = _dacapo_run(
            runid="cccc0000-0000-0000-0000-000000000003", name="z-run",
        )
        table = format_run_table(
            [r1, r2], ["runid", "name"],
            sort_col="name", sort_asc=True, group_cols=["name"], markdown=True,
        )
        self.assertIn("|  | **b-run** |", table)
        self.assertIn("|  | **z-run** |", table)
        self.assertEqual(table.count("| --- | --- |"), 1)
        lines = table.splitlines()
        self.assertEqual(lines[0], "| runid | name |")
        self.assertEqual(lines[2], "|  | **b-run** |")
        z = lines.index("|  | **z-run** |")
        self.assertEqual(lines[z + 1], "| **runid** | **name** |")
        self.assertEqual(table.count("| **runid** | **name** |"), 1)


# ---------------------------------------------------------------------------
# format_run_table — html output
# ---------------------------------------------------------------------------

class TestFormatRunTableHtml(unittest.TestCase):

    def test_basic_html_document_shape(self):
        runs = [_dacapo_run()]
        table = format_run_table(runs, ["runid", "name"], html=True)
        self.assertTrue(table.startswith("<!DOCTYPE html>"))
        self.assertTrue(table.rstrip().endswith("</html>"))
        self.assertEqual(table.count("<table>"), 1)
        self.assertIn("<th>runid</th><th>name</th>", table)
        self.assertIn(runs[0]["runid"], table)

    def test_cell_text_is_html_escaped(self):
        run = _dacapo_run(jvmargs=["-Dfoo=<a & b>"])
        table = format_run_table([run], ["runid", "jvmargs"], html=True)
        self.assertIn("&lt;a &amp; b&gt;", table)
        self.assertNotIn("<a & b>", table)

    def test_runid_no_link_without_results_dir(self):
        runs = [_dacapo_run()]
        table = format_run_table(runs, ["runid", "name"], html=True)
        self.assertNotIn("<a href", table)
        self.assertIn(runs[0]["runid"], table)

    def test_runid_linked_to_result_dir_with_results_dir(self):
        runs = [_dacapo_run()]
        table = format_run_table(
            runs, ["runid", "name"], results_dir="/results", html=True
        )
        self.assertNotIn("\x1b]8;;", table)
        run_id = runs[0]["runid"]
        expected_href = Path(os.path.join("/results", run_id)).as_uri()
        self.assertIn(f'<a href="{expected_href}">{run_id}</a>', table)

    def test_links_relative_to_report_dir_with_relative_links(self):
        runs = [_dacapo_run(memory={"rssmax": 4096})]
        run_id = runs[0]["runid"]
        with tempfile.TemporaryDirectory() as report_dir:
            os.makedirs(os.path.join(report_dir, run_id))
            open(os.path.join(report_dir, run_id, "system_map.svg"), "w").close()
            table = format_run_table(
                runs, ["runid", "rssmax"], results_dir=report_dir,
                report_dir=report_dir, html=True, relative_links=True,
            )
        self.assertNotIn("file://", table)
        self.assertIn(f'<a href="{run_id}">{run_id}</a>', table)
        self.assertIn(f'<a href="{run_id}/system_map.svg">', table)

    def test_links_absolute_without_relative_links(self):
        runs = [_dacapo_run(memory={"rssmax": 4096})]
        run_id = runs[0]["runid"]
        with tempfile.TemporaryDirectory() as report_dir:
            os.makedirs(os.path.join(report_dir, run_id))
            svg_path = os.path.join(report_dir, run_id, "system_map.svg")
            open(svg_path, "w").close()
            table = format_run_table(
                runs, ["runid", "rssmax"], results_dir=report_dir,
                report_dir=report_dir, html=True,
            )
        self.assertIn(f'<a href="{Path(svg_path).as_uri()}">', table)

    def test_comparison_plot_link_relative_to_report_dir(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", memory={"rssmax": 1000},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", memory={"rssmax": 1100},
        )
        with tempfile.TemporaryDirectory() as report_dir, \
                patch("listing.build_system_map_comparison_svg", return_value=True):
            table = format_run_table(
                [baseline, other], ["runid", "rssmax"], results_dir="/results",
                report_dir=report_dir, baseline_runid=baseline["runid"],
                compare_cols=["rssmax"], html=True,
            )
        expected = "system_map_compare_{}_{}.svg".format(other["runid"], baseline["runid"])
        self.assertIn(f'(<a href="{expected}">', table)

    def test_malloc_cell_linked_to_nmt_malloc_svg(self):
        runs = [_dacapo_run(malloc={"mallocavg": 42.5})]
        run_id = runs[0]["runid"]
        with tempfile.TemporaryDirectory() as report_dir:
            os.makedirs(os.path.join(report_dir, run_id))
            open(os.path.join(report_dir, run_id, "nmt-malloc.svg"), "w").close()
            table = format_run_table(
                runs, ["runid", "mallocavg"], results_dir=report_dir,
                report_dir=report_dir, html=True, relative_links=True,
            )
        self.assertIn(f'<a href="{run_id}/nmt-malloc.svg">', table)

    def test_malloc_cell_not_linked_without_nmt_malloc_svg(self):
        runs = [_dacapo_run(malloc={"mallocavg": 42.5})]
        run_id = runs[0]["runid"]
        with tempfile.TemporaryDirectory() as report_dir:
            os.makedirs(os.path.join(report_dir, run_id))
            # An RSS plot must not be picked up for a malloc cell.
            open(os.path.join(report_dir, run_id, "system_map.svg"), "w").close()
            table = format_run_table(
                runs, ["runid", "mallocavg"], results_dir=report_dir,
                report_dir=report_dir, html=True, relative_links=True,
            )
        self.assertNotIn(".svg", table)

    def test_malloc_comparison_plot_link(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", malloc={"mallocavg": 40.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", malloc={"mallocavg": 44.0},
        )
        with tempfile.TemporaryDirectory() as report_dir, \
                patch("listing.build_nmt_comparison_svg", return_value=True) as nmt_build, \
                patch("listing.build_system_map_comparison_svg", return_value=True) as sm_build:
            table = format_run_table(
                [baseline, other], ["runid", "mallocavg"], results_dir="/results",
                report_dir=report_dir, baseline_runid=baseline["runid"],
                compare_cols=["mallocavg"], html=True,
            )
        expected = "nmt_compare_{}_{}.svg".format(other["runid"], baseline["runid"])
        self.assertIn(f'(<a href="{expected}">', table)
        self.assertEqual(nmt_build.call_count, 1)
        sm_build.assert_not_called()

    def test_rss_and_malloc_compared_get_separate_plots(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001",
            memory={"rssmax": 1000}, malloc={"mallocavg": 40.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002",
            memory={"rssmax": 1100}, malloc={"mallocavg": 44.0},
        )
        with tempfile.TemporaryDirectory() as report_dir, \
                patch("listing.build_nmt_comparison_svg", return_value=True), \
                patch("listing.build_system_map_comparison_svg", return_value=True):
            table = format_run_table(
                [baseline, other], ["runid", "rssmax", "mallocavg"],
                results_dir="/results", report_dir=report_dir,
                baseline_runid=baseline["runid"],
                compare_cols=["rssmax", "mallocavg"], html=True,
            )
        ids = (other["runid"], baseline["runid"])
        self.assertIn('(<a href="system_map_compare_{}_{}.svg">'.format(*ids), table)
        self.assertIn('(<a href="nmt_compare_{}_{}.svg">'.format(*ids), table)

    def test_only_total_cpu_cells_linked_to_pidstat_cpu_svg(self):
        runs = [_dacapo_run(cpu={"pct_usr_max": 300.0, "pct_cpu_avg": 350.0,
                                 "pct_cpu_max": 800.0})]
        run_id = runs[0]["runid"]
        with tempfile.TemporaryDirectory() as report_dir:
            os.makedirs(os.path.join(report_dir, run_id))
            open(os.path.join(report_dir, run_id, "pidstat-cpu.svg"), "w").close()
            table = format_run_table(
                runs, ["runid", "cpu_usr_max", "cpu_avg", "cpu_max"],
                results_dir=report_dir, report_dir=report_dir, html=True,
                relative_links=True,
            )
        self.assertEqual(table.count(f'<a href="{run_id}/pidstat-cpu.svg">'), 2)
        self.assertIn("<td>300.0</td>", table)

    def test_cpu_comparison_plot_link(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001",
            cpu={"pct_cpu_avg": 400.0, "pct_system_max": 50.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002",
            cpu={"pct_cpu_avg": 440.0, "pct_system_max": 60.0},
        )
        with tempfile.TemporaryDirectory() as report_dir, \
                patch("listing.build_cpu_comparison_svg", return_value=True) as cpu_build:
            table = format_run_table(
                [baseline, other], ["runid", "cpu_avg", "cpu_sys_max"],
                results_dir="/results", report_dir=report_dir,
                baseline_runid=baseline["runid"],
                compare_cols=["cpu_avg", "cpu_sys_max"], html=True,
            )
        expected = "cpu_compare_{}_{}.svg".format(other["runid"], baseline["runid"])
        # Only the total column links to the comparison, not cpu_sys_max.
        self.assertEqual(table.count(f'(<a href="{expected}">'), 1)
        self.assertEqual(cpu_build.call_count, 1)

    def test_page_fault_cells_linked_to_pagefaults_svg(self):
        runs = [_dacapo_run(page_faults={"minflt": 1000, "majflt": 70})]
        run_id = runs[0]["runid"]
        with tempfile.TemporaryDirectory() as report_dir:
            os.makedirs(os.path.join(report_dir, run_id))
            open(os.path.join(report_dir, run_id, "pagefaults.svg"), "w").close()
            table = format_run_table(
                runs, ["runid", "minflt", "majflt"], results_dir=report_dir,
                report_dir=report_dir, html=True, relative_links=True,
            )
        self.assertEqual(table.count(f'<a href="{run_id}/pagefaults.svg">'), 2)

    def test_page_fault_comparison_plot_link_and_color(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001",
            page_faults={"minflt": 1000, "majflt": 70},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002",
            page_faults={"minflt": 1100, "majflt": 63},
        )
        with tempfile.TemporaryDirectory() as report_dir, \
                patch("listing.build_page_faults_comparison_svg", return_value=True) as pf_build:
            table = format_run_table(
                [baseline, other], ["runid", "minflt", "majflt"],
                results_dir="/results", report_dir=report_dir,
                baseline_runid=baseline["runid"],
                compare_cols=["minflt", "majflt"], html=True, color=True,
            )
        expected = "faults_compare_{}_{}.svg".format(other["runid"], baseline["runid"])
        # One plot per pair, linked from both columns.
        self.assertEqual(table.count(f'(<a href="{expected}">'), 2)
        self.assertEqual(pf_build.call_count, 1)
        # Lower is better.
        self.assertIn('<span class="delta-bad">(<a', table)
        self.assertIn('<span class="delta-good">(<a', table)

    def test_ardavg_cell_linked_to_arena_combined_svg(self):
        runs = [_dacapo_run(arena={"dirty_mb_max": 2.0, "dirty_mb_avg": 1.0})]
        run_id = runs[0]["runid"]
        with tempfile.TemporaryDirectory() as report_dir:
            os.makedirs(os.path.join(report_dir, run_id, "arena-report"))
            open(os.path.join(report_dir, run_id, "arena-report", "combined-bytes.svg"), "w").close()
            table = format_run_table(
                runs, ["runid", "ardmax", "ardavg"], results_dir=report_dir,
                report_dir=report_dir, html=True, relative_links=True,
            )
        # only ardavg links to the plot
        self.assertEqual(table.count(f'<a href="{run_id}/arena-report/combined-bytes.svg">'), 1)
        self.assertIn("<td>2.0</td>", table)

    def test_ardavg_cell_prefers_combined_all_svg(self):
        runs = [_dacapo_run(arena={"dirty_mb_avg": 1.0})]
        run_id = runs[0]["runid"]
        with tempfile.TemporaryDirectory() as report_dir:
            os.makedirs(os.path.join(report_dir, run_id, "arena-report"))
            for name in ("combined.svg", "combined-bytes.svg", "combined-all.svg"):
                open(os.path.join(report_dir, run_id, "arena-report", name), "w").close()
            table = format_run_table(
                runs, ["runid", "ardavg"], results_dir=report_dir,
                report_dir=report_dir, html=True, relative_links=True,
            )
        self.assertIn(f'<a href="{run_id}/arena-report/combined-all.svg">', table)
        self.assertNotIn("combined.svg", table)
        self.assertNotIn("combined-bytes.svg", table)

    def test_arena_comparison_plot_link_and_color(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001",
            arena={"dirty_mb_max": 2.0, "dirty_mb_avg": 1.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002",
            arena={"dirty_mb_max": 1.0, "dirty_mb_avg": 1.5},
        )
        with tempfile.TemporaryDirectory() as report_dir, \
                patch("listing.build_arena_comparison_svg", return_value=True) as ar_build:
            table = format_run_table(
                [baseline, other], ["runid", "ardmax", "ardavg"],
                results_dir="/results", report_dir=report_dir,
                baseline_runid=baseline["runid"],
                compare_cols=["ardmax", "ardavg"], html=True, color=True,
            )
        expected = "arena_compare_{}_{}.svg".format(other["runid"], baseline["runid"])
        # One plot per pair, linked from both columns.
        self.assertEqual(table.count(f'(<a href="{expected}">'), 2)
        self.assertEqual(ar_build.call_count, 1)
        # Lower is better.
        self.assertIn('<span class="delta-bad">(<a', table)
        self.assertIn('<span class="delta-good">(<a', table)

    def test_malloc_comparison_no_link_when_build_fails(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", malloc={"mallocavg": 40.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", malloc={"mallocavg": 44.0},
        )
        with tempfile.TemporaryDirectory() as report_dir, \
                patch("listing.build_nmt_comparison_svg", return_value=False):
            table = format_run_table(
                [baseline, other], ["runid", "mallocavg"], results_dir="/results",
                report_dir=report_dir, baseline_runid=baseline["runid"],
                compare_cols=["mallocavg"], html=True,
            )
        self.assertIn("44.0 (+10.0%)", table)
        self.assertNotIn(".svg", table)

    def test_runid_dash_not_linked(self):
        run = _dacapo_run()
        run.pop("runid", None)
        table = format_run_table(
            [run], ["runid", "name"], results_dir="/results", html=True
        )
        self.assertNotIn("<a href", table)
        self.assertIn("<td>-</td>", table)

    def test_grouped_html_single_table_with_banner_rows(self):
        r1 = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="b-run",
        )
        r2 = _dacapo_run(
            runid="cccc0000-0000-0000-0000-000000000003", name="z-run",
        )
        table = format_run_table(
            [r1, r2], ["runid", "name"],
            sort_col="name", sort_asc=True, group_cols=["name"], html=True,
        )
        self.assertIn('<tr class="group"><th colspan="2">name: b-run</th></tr>', table)
        self.assertIn('<tr class="group"><th colspan="2">name: z-run</th></tr>', table)
        self.assertEqual(table.count("<table>"), 1)
        self.assertEqual(table.count("<thead>"), 0)
        self.assertEqual(table.count("<tbody>"), 2)
        header = "<tr><th>runid</th><th>name</th></tr>"
        self.assertIn('name: b-run</th></tr>\n' + header, table)
        self.assertIn('name: z-run</th></tr>\n' + header, table)

    def test_compare_delta_colored_with_css_span(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            dacapo_result={"result_ms": 1000},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            dacapo_result={"result_ms": 1069},
        )
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms"],
            baseline_runid=baseline["runid"], compare_cols=["result_ms"],
            html=True, color=True,
        )
        self.assertIn('<span class="delta-bad">(+6.9%)</span>', table)

    def test_compare_cpu_delta_colored_with_heat_css_span(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            cpu={"pct_cpu_avg": 400.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            cpu={"pct_cpu_avg": 440.0},
        )
        table = format_run_table(
            [baseline, other], ["runid", "name", "cpu_avg"],
            baseline_runid=baseline["runid"], compare_cols=["cpu_avg"],
            html=True, color=True,
        )
        self.assertIn('<span class="delta-hot">(+10.0%)</span>', table)
        self.assertIn(".delta-hot {", table)

    def test_compare_delta_plain_without_color(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            dacapo_result={"result_ms": 1000},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            dacapo_result={"result_ms": 1069},
        )
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms"],
            baseline_runid=baseline["runid"], compare_cols=["result_ms"],
            html=True,
        )
        self.assertIn("1069 (+6.9%)", table)
        self.assertNotIn("<span", table)


# ---------------------------------------------------------------------------
# format_run_table — compare
# ---------------------------------------------------------------------------

class TestFormatRunTableCompare(unittest.TestCase):

    def _runs(self, baseline_ms, other_ms):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            dacapo_result={"result_ms": baseline_ms},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            dacapo_result={"result_ms": other_ms},
        )
        return baseline, other

    def test_computable_delta_appended(self):
        baseline, other = self._runs(1000, 1069)
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms"],
            baseline_runid=baseline["runid"], compare_cols=["result_ms"],
        )
        self.assertIn("1069 (+6.9%)", table)
        # No separate "_rel"-style column header.
        self.assertNotIn("result_ms_rel", table)
        self.assertNotIn("Result_msRel", table)

    def test_baseline_cell_left_plain(self):
        baseline, other = self._runs(1000, 1069)
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms"],
            baseline_runid=baseline["runid"], compare_cols=["result_ms"],
        )
        lines = table.splitlines()
        baseline_line = next(l for l in lines if "baseline-run" in l)
        self.assertIn("1000", baseline_line)
        self.assertNotIn("(", baseline_line)

    def test_non_computable_delta_left_plain(self):
        # Missing baseline value -> delta undefined; other cell must stay
        # untouched.
        baseline, other = self._runs(0, 100)
        baseline["dacapo_result"] = {}
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms"],
            baseline_runid=baseline["runid"], compare_cols=["result_ms"],
        )
        lines = table.splitlines()
        other_line = next(l for l in lines if "other-run" in l)
        self.assertIn("100", other_line)
        self.assertNotIn("(", other_line)

    def test_column_count_unchanged(self):
        baseline, other = self._runs(1000, 1069)
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms"],
            baseline_runid=baseline["runid"], compare_cols=["result_ms"],
        )
        header = table.splitlines()[0]
        # Exactly 3 requested columns, no extra "_rel" column inserted.
        self.assertEqual(len(header.split()), 3)

    def test_zero_baseline_left_plain(self):
        baseline, other = self._runs(0, 100)
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms"],
            baseline_runid=baseline["runid"],
            compare_cols=["result_ms"],
        )
        other_line = next(l for l in table.splitlines() if "other-run" in l)
        self.assertIn("100", other_line)
        self.assertNotIn("(", other_line)


class TestFormatRunTableCompareThreshold(unittest.TestCase):

    def _table(self, baseline_ms, other_ms, threshold, **kwargs):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            dacapo_result={"result_ms": baseline_ms},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            dacapo_result={"result_ms": other_ms},
        )
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms"],
            baseline_runid=baseline["runid"], compare_cols=["result_ms"],
            compare_thresholds={"result_ms": threshold},
            **kwargs,
        )
        return next(l for l in table.splitlines() if "other-run" in l)

    def test_delta_below_threshold_hidden(self):
        line = self._table(1000, 1040, 5)
        self.assertIn("1040", line)
        self.assertNotIn("(", line)

    def test_negative_delta_below_threshold_hidden(self):
        line = self._table(1000, 960, 5)
        self.assertNotIn("(", line)

    def test_delta_above_threshold_shown(self):
        self.assertIn("1069 (+6.9%)", self._table(1000, 1069, 5))
        self.assertIn("931 (-6.9%)", self._table(1000, 931, 5))

    def test_float_threshold(self):
        self.assertIn("(+0.6%)", self._table(1000, 1006, 0.5))
        self.assertNotIn("(", self._table(1000, 1004, 0.5))

    def test_rounded_delta_at_threshold_shown(self):
        # 1049.6/1000 -> +4.96%, displayed as +5.0%: not hidden at 5.
        self.assertIn("(+5.0%)", self._table(1000, 1049.6, 5))


    def test_hidden_delta_not_colored(self):
        line = self._table(1000, 1040, 5, html=True, color=True)
        self.assertNotIn("delta-", line)

    def test_thresholds_per_column(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            dacapo_result={"result_ms": 1000}, cpu={"pct_cpu_avg": 400.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            dacapo_result={"result_ms": 1030}, cpu={"pct_cpu_avg": 412.0},
        )
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms", "cpu_avg"],
            baseline_runid=baseline["runid"],
            compare_cols=["result_ms", "cpu_avg"],
            compare_thresholds={"result_ms": 2, "cpu_avg": 5},
        )
        line = next(l for l in table.splitlines() if "other-run" in l)
        self.assertIn("1030 (+3.0%)", line)   # 3% >= 2: shown
        self.assertEqual(line.count("("), 1)  # cpu_avg 3% < 5: hidden

    def test_column_without_threshold_always_shown(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            dacapo_result={"result_ms": 1000}, cpu={"pct_cpu_avg": 400.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            dacapo_result={"result_ms": 1001}, cpu={"pct_cpu_avg": 401.0},
        )
        table = format_run_table(
            [baseline, other], ["runid", "name", "result_ms", "cpu_avg"],
            baseline_runid=baseline["runid"],
            compare_cols=["result_ms", "cpu_avg"],
            compare_thresholds={"cpu_avg": 5},
        )
        line = next(l for l in table.splitlines() if "other-run" in l)
        self.assertIn("1001 (+0.1%)", line)
        self.assertEqual(line.count("("), 1)


class TestResolveCompareCols(unittest.TestCase):

    COLS = ["runid", "name", "result_ms", "rssavg"]

    def test_none(self):
        self.assertIsNone(resolve_compare_cols(None, self.COLS))
        self.assertIsNone(resolve_compare_cols("", self.COLS))

    def test_plain_cols_have_no_threshold(self):
        self.assertEqual(
            resolve_compare_cols("result_ms,rssavg", self.COLS),
            {"result_ms": None, "rssavg": None},
        )

    def test_thresholds(self):
        self.assertEqual(
            resolve_compare_cols("result_ms=2,rssavg=0.5", self.COLS),
            {"result_ms": 2.0, "rssavg": 0.5},
        )

    def test_mixed_keeps_order(self):
        self.assertEqual(
            list(resolve_compare_cols("rssavg=5,result_ms", self.COLS).items()),
            [("rssavg", 5.0), ("result_ms", None)],
        )

    def test_duplicate_same_threshold_ok(self):
        self.assertEqual(
            resolve_compare_cols("result_ms=2,result_ms=2", self.COLS),
            {"result_ms": 2.0},
        )

    def test_duplicate_different_threshold_rejected(self):
        with self.assertRaises(ValueError):
            resolve_compare_cols("result_ms=2,result_ms=3", self.COLS)

    def test_duplicate_without_threshold_does_not_conflict(self):
        for arg in ("result_ms=2,result_ms", "result_ms,result_ms=2"):
            with self.subTest(arg=arg):
                self.assertEqual(resolve_compare_cols(arg, self.COLS),
                                 {"result_ms": 2.0})

    def test_invalid_thresholds_rejected(self):
        for bad in ("result_ms=", "result_ms=0", "result_ms=-1",
                    "result_ms=x", "result_ms=nan", "result_ms=inf"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                resolve_compare_cols(bad, self.COLS)

    def test_unknown_col_with_threshold_rejected(self):
        with self.assertRaises(ValueError):
            resolve_compare_cols("nosuchcol=2", self.COLS)


class TestFormatRunTableCompareBaselineOptional(unittest.TestCase):

    def test_implicit_baseline_is_first_post_sort_row(self):
        # No baseline_runid: the first row after sorting (a-run, since
        # sort_col="name" ascending) is the implicit baseline.
        low = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="a-run",
            dacapo_result={"result_ms": 1000},
        )
        high = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="b-run",
            dacapo_result={"result_ms": 1069},
        )
        table = format_run_table(
            [high, low], ["runid", "name", "result_ms"],
            sort_col="name", sort_asc=True, compare_cols=["result_ms"],
        )
        lines = table.splitlines()
        a_line = next(l for l in lines if "a-run" in l)
        b_line = next(l for l in lines if "b-run" in l)
        self.assertIn("1000", a_line)
        self.assertNotIn("(", a_line)
        self.assertIn("1069 (+6.9%)", b_line)

    def test_explicit_baseline_stays_in_sort_position(self):
        # An explicit baseline is no longer pulled to the front of the
        # table; it stays wherever the sort order puts it.
        other = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="a-run",
            dacapo_result={"result_ms": 1069},
        )
        baseline = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="z-run",
            dacapo_result={"result_ms": 1000},
        )
        table = format_run_table(
            [other, baseline], ["runid", "name", "result_ms"],
            sort_col="name", sort_asc=True,
            baseline_runid=baseline["runid"], compare_cols=["result_ms"],
        )
        lines = table.splitlines()
        a_idx = next(i for i, l in enumerate(lines) if "a-run" in l)
        z_idx = next(i for i, l in enumerate(lines) if "z-run" in l)
        self.assertLess(a_idx, z_idx)
        self.assertIn("1069 (+6.9%)", lines[a_idx])
        self.assertIn("1000", lines[z_idx])
        self.assertNotIn("(", lines[z_idx])


class TestFormatRunTableCompareWithGrouping(unittest.TestCase):

    def test_each_group_uses_its_own_first_row_as_baseline(self):
        g1_base = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="grp1",
            dacapo_result={"result_ms": 1000},
        )
        g1_other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="grp1",
            dacapo_result={"result_ms": 1100},
        )
        g2_base = _dacapo_run(
            runid="cccc0000-0000-0000-0000-000000000003", name="grp2",
            dacapo_result={"result_ms": 2000},
        )
        g2_other = _dacapo_run(
            runid="dddd0000-0000-0000-0000-000000000004", name="grp2",
            dacapo_result={"result_ms": 1800},
        )
        table = format_run_table(
            [g1_base, g1_other, g2_base, g2_other],
            ["runid", "name", "result_ms"],
            sort_col="runid", sort_asc=True,
            group_cols=["name"], compare_cols=["result_ms"],
        )
        lines = table.splitlines()
        g1_base_line = next(l for l in lines if "aaaa" in l)
        g1_other_line = next(l for l in lines if "bbbb" in l)
        g2_base_line = next(l for l in lines if "cccc" in l)
        g2_other_line = next(l for l in lines if "dddd" in l)
        self.assertNotIn("(", g1_base_line)
        self.assertIn("1100 (+10.0%)", g1_other_line)
        self.assertNotIn("(", g2_base_line)
        self.assertIn("1800 (-10.0%)", g2_other_line)


class TestFormatRunTableColor(unittest.TestCase):

    def _dacapo_pair(self, baseline_ms, other_ms):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            dacapo_result={"result_ms": baseline_ms},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            dacapo_result={"result_ms": other_ms},
        )
        return baseline, other

    def _colorable_env(self):
        env = dict(os.environ)
        env.pop("NO_COLOR", None)
        env["TERM"] = "xterm-256color"
        return patch.dict(os.environ, env, clear=True)

    def test_no_color_flag_leaves_output_plain(self):
        baseline, other = self._dacapo_pair(1000, 1069)
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "result_ms"],
                baseline_runid=baseline["runid"], compare_cols=["result_ms"],
            )
        self.assertNotIn("\x1b[", table)

    def test_result_ms_increase_is_bad_and_red(self):
        # result_ms: lower is better, so a +6.9% increase is bad.
        baseline, other = self._dacapo_pair(1000, 1069)
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "result_ms"],
                baseline_runid=baseline["runid"], compare_cols=["result_ms"],
                color=True,
            )
        self.assertIn("\x1b[31m(+6.9%)\x1b[0m", table)
        self.assertNotIn("\x1b[32m", table)

    def test_result_ms_decrease_is_good_and_green(self):
        baseline, other = self._dacapo_pair(1069, 1000)
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "result_ms"],
                baseline_runid=baseline["runid"], compare_cols=["result_ms"],
                color=True,
            )
        self.assertIn("\x1b[32m(-6.5%)\x1b[0m", table)
        self.assertNotIn("\x1b[31m", table)

    def test_maxjops_increase_is_good_and_green(self):
        # maxjops: higher is better, so a +10.0% increase is good.
        baseline = _specjbb_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            specjbb_result={"maxjops": 1000},
        )
        other = _specjbb_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            specjbb_result={"maxjops": 1100},
        )
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "maxjops"],
                baseline_runid=baseline["runid"], compare_cols=["maxjops"],
                color=True,
            )
        self.assertIn("\x1b[32m(+10.0%)\x1b[0m", table)
        self.assertNotIn("\x1b[31m", table)

    def test_rss_max_increase_is_bad_and_red(self):
        # rssmax: lower is better, so an increase is bad.
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            memory={"rssmax": 1000},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            memory={"rssmax": 1100},
        )
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "rssmax"],
                baseline_runid=baseline["runid"], compare_cols=["rssmax"],
                color=True,
            )
        self.assertIn("\x1b[31m(+10.0%)\x1b[0m", table)
        self.assertNotIn("\x1b[32m", table)

    def test_malloc_avg_increase_is_bad_and_red(self):
        # mallocavg: lower is better, so an increase is bad.
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            malloc={"mallocavg": 40.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            malloc={"mallocavg": 44.0},
        )
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "mallocavg"],
                baseline_runid=baseline["runid"], compare_cols=["mallocavg"],
                color=True,
            )
        self.assertIn("\x1b[31m(+10.0%)\x1b[0m", table)
        self.assertNotIn("\x1b[32m", table)

    def test_malloc_max_decrease_is_good_and_green(self):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            malloc={"mallocmax": 44.0},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            malloc={"mallocmax": 40.0},
        )
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "mallocmax"],
                baseline_runid=baseline["runid"], compare_cols=["mallocmax"],
                color=True,
            )
        self.assertIn("\x1b[32m(-9.1%)\x1b[0m", table)
        self.assertNotIn("\x1b[31m", table)

    def _cpu_pair(self, col, json_key, baseline_val, other_val):
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            cpu={json_key: baseline_val},
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            cpu={json_key: other_val},
        )
        return format_run_table(
            [baseline, other], ["runid", "name", col],
            baseline_runid=baseline["runid"], compare_cols=[col],
            color=True,
        )

    def test_cpu_increase_is_hot_and_orange(self):
        # CPU columns are value-neutral: an increase is "hotter" (orange).
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = self._cpu_pair("cpu_avg", "pct_cpu_avg", 400.0, 440.0)
        self.assertIn("\x1b[38;5;215m(+10.0%)\x1b[0m", table)

    def test_cpu_decrease_is_cold_and_blue(self):
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = self._cpu_pair("cpu_usr_max", "pct_usr_max", 400.0, 360.0)
        self.assertIn("\x1b[34m(-10.0%)\x1b[0m", table)

    def test_cpu_increase_is_yellow_without_256_colors(self):
        env = dict(os.environ)
        env.pop("NO_COLOR", None)
        env.pop("COLORTERM", None)
        env["TERM"] = "xterm"
        with patch.object(sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, env, clear=True):
            table = self._cpu_pair("cpu_sys_avg", "pct_system_avg", 10.0, 11.0)
        self.assertIn("\x1b[33m(+10.0%)\x1b[0m", table)

    def test_uncolored_compare_column_stays_plain(self):
        # duration is not among the colorable columns, even under --color.
        baseline = _dacapo_run(
            runid="aaaa0000-0000-0000-0000-000000000001", name="baseline-run",
            duration_secs=100,
        )
        other = _dacapo_run(
            runid="bbbb0000-0000-0000-0000-000000000002", name="other-run",
            duration_secs=110,
        )
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "duration"],
                baseline_runid=baseline["runid"], compare_cols=["duration"],
                color=True,
            )
        self.assertIn("110 (+10.0%)", table)
        self.assertNotIn("\x1b[", table)

    def test_baseline_row_never_colored(self):
        baseline, other = self._dacapo_pair(1000, 1069)
        with patch.object(sys.stdout, "isatty", return_value=True), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "result_ms"],
                baseline_runid=baseline["runid"], compare_cols=["result_ms"],
                color=True,
            )
        baseline_line = next(l for l in table.splitlines() if "baseline-run" in l)
        self.assertNotIn("\x1b[", baseline_line)

    def test_no_color_when_not_a_tty(self):
        baseline, other = self._dacapo_pair(1000, 1069)
        with patch.object(sys.stdout, "isatty", return_value=False), self._colorable_env():
            table = format_run_table(
                [baseline, other], ["runid", "name", "result_ms"],
                baseline_runid=baseline["runid"], compare_cols=["result_ms"],
                color=True,
            )
        self.assertIn("1069 (+6.9%)", table)
        self.assertNotIn("\x1b[", table)

    def test_no_color_when_no_color_env_set(self):
        baseline, other = self._dacapo_pair(1000, 1069)
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        env["TERM"] = "xterm-256color"
        with patch.object(sys.stdout, "isatty", return_value=True), \
                patch.dict(os.environ, env, clear=True):
            table = format_run_table(
                [baseline, other], ["runid", "name", "result_ms"],
                baseline_runid=baseline["runid"], compare_cols=["result_ms"],
                color=True,
            )
        self.assertIn("1069 (+6.9%)", table)
        self.assertNotIn("\x1b[", table)


if __name__ == "__main__":
    unittest.main()
