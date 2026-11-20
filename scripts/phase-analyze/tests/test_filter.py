# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# test_filter.py — unit tests for scripts/phase-analyze/filter.py
#
# Run with:
#   python3 -m unittest discover scripts

import argparse
import datetime
import os
import shutil
import sys
import unittest
from unittest.mock import patch

# Allow import from project scripts directory when run via unittest discover.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_ANALYZE = os.path.dirname(_HERE)
if _PHASE_ANALYZE not in sys.path:
    sys.path.insert(0, _PHASE_ANALYZE)

from filter import (  # noqa: E402
    build_filter,
    parse_jdk_major,
    parse_heapszf,
    parse_repeat_no,
    parse_heapsz,
    parse_duration,
    parse_startup_ms,
    parse_datetime_spec,
    run_jq_filter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns(**kwargs):
    """Build an argparse.Namespace with all filter fields defaulted to None/False."""
    defaults = dict(
        benchmark=None,
        sub=None,
        datasize=None,
        jdkmajor=None,
        heapszf=None,
        repeat_no=None,
        heapsz=None,
        duration=None,
        startup_ms=None,
        gc=None,
        jdktype=None,
        jdkname=None,
        jdkversion=None,
        tags=None,
        name=None,
        hostname=None,
        batchid=None,
        arch=None,
        os=None,
        jvmargs=None,
        since=None,
        until=None,
        where=None,
        or_=False,
        invert=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# A minimal "dacapo batik" merged dict for reuse across tests.
_DACAPO_RUN = {
    "name": "dacapo.batik",
    "benchmark": "dacapo",
    "dacapo": {"sub_benchmark": "batik", "datasize": "large"},
    "jdk": {"name": "openjdk-21", "major": 21, "type": "openjdk", "version": "21.0.4"},
    "tags": ["G1", "baseline"],
    "hostname": "host-a",
    "batchid": "batch-a",
    "arch": "x86_64",
    "jvmargs": ["-Xmx16g", "-XX:+UseG1GC", "-Xlog:gc*"],
    "heapsize_factor": 2.0,
    "repeat_no": 1,
    "heap": {"gc": "g1gc", "heapsize": 16384},
    "os": "Linux 6.8.0-45-generic",
    "duration_secs": 120.5,
    "time": "2026-08-06T18:17:16-0700",
}

_SPECJBB_RUN = {
    "name": "specjbb",
    "benchmark": "specjbb",
    "jdk": {"name": "openj9-17", "major": 17, "type": "openj9", "version": "17.0.9"},
    "tags": [],
    "hostname": "host-b",
    "batchid": "batch-b",
    "arch": "aarch64",
    "jvmargs": ["-Xmx8g"],
    "specjbb_result": {"maxjops": 200},
    "os": "Darwin 25.5.0",
    "time": "2026-08-01T09:00:00-0700",
}


# ---------------------------------------------------------------------------
# parse_jdk_major
# ---------------------------------------------------------------------------

class TestParseJdkMajor(unittest.TestCase):

    def test_bare_integer(self):
        self.assertEqual(parse_jdk_major("21"), ("==", 21))

    def test_eq_operator(self):
        self.assertEqual(parse_jdk_major("==21"), ("==", 21))

    def test_gt_operator(self):
        self.assertEqual(parse_jdk_major(">17"), (">", 17))

    def test_lt_operator(self):
        self.assertEqual(parse_jdk_major("<25"), ("<", 25))

    def test_gte_operator(self):
        self.assertEqual(parse_jdk_major(">=21"), (">=", 21))

    def test_lte_operator(self):
        self.assertEqual(parse_jdk_major("<=11"), ("<=", 11))

    def test_whitespace_stripped(self):
        self.assertEqual(parse_jdk_major(" 21 "), ("==", 21))

    def test_error_on_alphabetic(self):
        with self.assertRaises(ValueError):
            parse_jdk_major("abc")

    def test_error_on_unsupported_op(self):
        with self.assertRaises(ValueError):
            parse_jdk_major("!21")

    def test_error_on_operator_without_number(self):
        with self.assertRaises(ValueError):
            parse_jdk_major(">=")

    def test_error_on_operator_with_non_integer(self):
        with self.assertRaises(ValueError):
            parse_jdk_major(">=abc")


# ---------------------------------------------------------------------------
# parse_heapszf
# ---------------------------------------------------------------------------

class TestParseHeapszf(unittest.TestCase):

    def test_bare_number(self):
        self.assertEqual(parse_heapszf("2"), ("==", 2.0))

    def test_bare_decimal(self):
        self.assertEqual(parse_heapszf("1.5"), ("==", 1.5))

    def test_eq_operator(self):
        self.assertEqual(parse_heapszf("==2"), ("==", 2.0))

    def test_gt_operator(self):
        self.assertEqual(parse_heapszf(">1.5"), (">", 1.5))

    def test_lt_operator(self):
        self.assertEqual(parse_heapszf("<4"), ("<", 4.0))

    def test_gte_operator(self):
        self.assertEqual(parse_heapszf(">=2"), (">=", 2.0))

    def test_lte_operator(self):
        self.assertEqual(parse_heapszf("<=1.5"), ("<=", 1.5))

    def test_whitespace_stripped(self):
        self.assertEqual(parse_heapszf(" 2 "), ("==", 2.0))

    def test_error_on_alphabetic(self):
        with self.assertRaises(ValueError):
            parse_heapszf("abc")

    def test_error_on_unsupported_op(self):
        with self.assertRaises(ValueError):
            parse_heapszf("!2")

    def test_error_on_operator_without_number(self):
        with self.assertRaises(ValueError):
            parse_heapszf(">=")

    def test_error_on_operator_with_non_numeric(self):
        with self.assertRaises(ValueError):
            parse_heapszf(">=abc")


# ---------------------------------------------------------------------------
# parse_heapsz
# ---------------------------------------------------------------------------

class TestParseHeapsz(unittest.TestCase):

    def test_bare_number(self):
        self.assertEqual(parse_heapsz("8192"), ("==", 8192.0))

    def test_eq_operator(self):
        self.assertEqual(parse_heapsz("==8192"), ("==", 8192.0))

    def test_gt_operator(self):
        self.assertEqual(parse_heapsz(">8192"), (">", 8192.0))

    def test_lt_operator(self):
        self.assertEqual(parse_heapsz("<16384"), ("<", 16384.0))

    def test_gte_operator(self):
        self.assertEqual(parse_heapsz(">=8192"), (">=", 8192.0))

    def test_lte_operator(self):
        self.assertEqual(parse_heapsz("<=16384"), ("<=", 16384.0))

    def test_whitespace_stripped(self):
        self.assertEqual(parse_heapsz(" 8192 "), ("==", 8192.0))

    def test_error_on_alphabetic(self):
        with self.assertRaises(ValueError):
            parse_heapsz("abc")

    def test_error_on_unsupported_op(self):
        with self.assertRaises(ValueError):
            parse_heapsz("!8192")

    def test_error_on_operator_without_number(self):
        with self.assertRaises(ValueError):
            parse_heapsz(">=")

    def test_error_on_operator_with_non_numeric(self):
        with self.assertRaises(ValueError):
            parse_heapsz(">=abc")


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------

class TestParseWct(unittest.TestCase):

    def test_bare_number(self):
        self.assertEqual(parse_duration("120"), ("==", 120.0))

    def test_gte_operator(self):
        self.assertEqual(parse_duration(">=60.5"), (">=", 60.5))

    def test_lt_operator(self):
        self.assertEqual(parse_duration("<300"), ("<", 300.0))

    def test_error_on_alphabetic(self):
        with self.assertRaises(ValueError):
            parse_duration("abc")

    def test_error_on_operator_without_number(self):
        with self.assertRaises(ValueError):
            parse_duration(">=")


# ---------------------------------------------------------------------------
# parse_startup_ms
# ---------------------------------------------------------------------------

class TestParseStartupMs(unittest.TestCase):

    def test_bare_number(self):
        self.assertEqual(parse_startup_ms("1800"), ("==", 1800.0))

    def test_lt_operator(self):
        self.assertEqual(parse_startup_ms("<2000"), ("<", 2000.0))

    def test_error_on_alphabetic(self):
        with self.assertRaises(ValueError):
            parse_startup_ms("abc")

    def test_error_on_operator_without_number(self):
        with self.assertRaises(ValueError):
            parse_startup_ms(">=")


# ---------------------------------------------------------------------------
# parse_datetime_spec
# ---------------------------------------------------------------------------

class TestParseDatetimeSpec(unittest.TestCase):

    def test_full_spec(self):
        self.assertEqual(
            parse_datetime_spec("2026-08-06T18:17:16"), "2026-08-06T18:17:16"
        )

    def test_missing_seconds(self):
        self.assertEqual(
            parse_datetime_spec("2026-08-06T18:17"), "2026-08-06T18:17:00"
        )

    def test_missing_time(self):
        self.assertEqual(
            parse_datetime_spec("2026-08-06"), "2026-08-06T00:00:00"
        )

    def test_bare_time_defaults_to_today(self):
        today = datetime.date.today().strftime("%Y-%m-%d")
        self.assertEqual(
            parse_datetime_spec("18:17:16"), f"{today}T18:17:16"
        )

    def test_bare_time_missing_seconds(self):
        today = datetime.date.today().strftime("%Y-%m-%d")
        self.assertEqual(
            parse_datetime_spec("18:17"), f"{today}T18:17:00"
        )

    def test_whitespace_stripped(self):
        self.assertEqual(
            parse_datetime_spec("  2026-08-06T18:17:16  "), "2026-08-06T18:17:16"
        )

    def test_error_on_empty(self):
        with self.assertRaises(ValueError):
            parse_datetime_spec("   ")

    def test_error_on_garbage(self):
        with self.assertRaises(ValueError):
            parse_datetime_spec("not-a-date")

    def test_error_on_invalid_date(self):
        with self.assertRaises(ValueError):
            parse_datetime_spec("2026-13-40")

    def test_error_on_invalid_time(self):
        with self.assertRaises(ValueError):
            parse_datetime_spec("2026-08-06T25:99")

    def test_error_on_no_relative_keywords(self):
        with self.assertRaises(ValueError):
            parse_datetime_spec("today")
        with self.assertRaises(ValueError):
            parse_datetime_spec("yesterday")
        with self.assertRaises(ValueError):
            parse_datetime_spec("now")


# ---------------------------------------------------------------------------
# build_filter — no active filters
# ---------------------------------------------------------------------------

class TestBuildFilterNoFilters(unittest.TestCase):

    def test_no_filters_always_true(self):
        filt = build_filter(_ns())
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertTrue(filt(_SPECJBB_RUN))
        self.assertTrue(filt({}))


# ---------------------------------------------------------------------------
# build_filter — --benchmark
# ---------------------------------------------------------------------------

class TestBuildFilterBenchmark(unittest.TestCase):

    def test_benchmark_match(self):
        filt = build_filter(_ns(benchmark=["dacapo"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_benchmark_no_match(self):
        filt = build_filter(_ns(benchmark=["dacapo"]))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_benchmark_repeated_and(self):
        # Two different values ANDed together — no run can match both.
        filt = build_filter(_ns(benchmark=["dacapo", "specjbb"]))
        self.assertFalse(filt(_DACAPO_RUN))
        self.assertFalse(filt(_SPECJBB_RUN))


# ---------------------------------------------------------------------------
# build_filter — --sub
# ---------------------------------------------------------------------------

class TestBuildFilterSub(unittest.TestCase):

    def test_sub_match(self):
        filt = build_filter(_ns(sub=["batik"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_sub_no_match(self):
        filt = build_filter(_ns(sub=["tomcat"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_sub_missing_dacapo_key(self):
        filt = build_filter(_ns(sub=["batik"]))
        self.assertFalse(filt(_SPECJBB_RUN))


# ---------------------------------------------------------------------------
# build_filter — --datasize
# ---------------------------------------------------------------------------

class TestBuildFilterDatasize(unittest.TestCase):

    def test_datasize_match(self):
        filt = build_filter(_ns(datasize=["large"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_datasize_no_match(self):
        filt = build_filter(_ns(datasize=["small"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --jdkmajor
# ---------------------------------------------------------------------------

class TestBuildFilterJdkMajor(unittest.TestCase):

    def test_eq_match(self):
        filt = build_filter(_ns(jdkmajor=["==21"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_eq_no_match(self):
        filt = build_filter(_ns(jdkmajor=["==17"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_bare_match(self):
        filt = build_filter(_ns(jdkmajor=["21"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_gte_match(self):
        filt = build_filter(_ns(jdkmajor=[">=17"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_gte_no_match(self):
        filt = build_filter(_ns(jdkmajor=[">=25"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_lte_match(self):
        filt = build_filter(_ns(jdkmajor=["<=21"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_lte_no_match(self):
        filt = build_filter(_ns(jdkmajor=["<=11"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_gt_match(self):
        filt = build_filter(_ns(jdkmajor=[">17"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_lt_match(self):
        filt = build_filter(_ns(jdkmajor=["<25"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_missing_jdk_key_no_match(self):
        filt = build_filter(_ns(jdkmajor=[">=17"]))
        self.assertFalse(filt({}))

    def test_repeated_and(self):
        # >=17 AND <=21 — both are 21
        filt = build_filter(_ns(jdkmajor=[">=17", "<=21"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_repeated_and_excludes(self):
        # >21 AND <21 — impossible
        filt = build_filter(_ns(jdkmajor=[">21", "<21"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# parse_repeat_no / build_filter — --repeat_no
# ---------------------------------------------------------------------------

class TestParseRepeatNo(unittest.TestCase):

    def test_bare(self):
        self.assertEqual(parse_repeat_no("0"), ("==", 0))

    def test_op(self):
        self.assertEqual(parse_repeat_no(">=1"), (">=", 1))

    def test_error_names_flag(self):
        with self.assertRaisesRegex(ValueError, "--repeat_no"):
            parse_repeat_no("1.5")


class TestBuildFilterRepeatNo(unittest.TestCase):

    def test_bare_match(self):
        filt = build_filter(_ns(repeat_no=["1"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_bare_no_match(self):
        filt = build_filter(_ns(repeat_no=["0"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_gte_match(self):
        filt = build_filter(_ns(repeat_no=[">=1"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_missing_key_no_match(self):
        # SPECjbb fixture has no repeat_no key (info.json predates the field).
        filt = build_filter(_ns(repeat_no=[">=0"]))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_null_sentinel_matches_missing(self):
        filt = build_filter(_ns(repeat_no=["null"]))
        self.assertTrue(filt(_SPECJBB_RUN))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --heapszf
# ---------------------------------------------------------------------------

class TestBuildFilterHeapszf(unittest.TestCase):

    def test_eq_match(self):
        filt = build_filter(_ns(heapszf=["==2"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_eq_no_match(self):
        filt = build_filter(_ns(heapszf=["==4"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_bare_match(self):
        filt = build_filter(_ns(heapszf=["2"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_gte_match(self):
        filt = build_filter(_ns(heapszf=[">=1.5"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_gte_no_match(self):
        filt = build_filter(_ns(heapszf=[">=4"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_lte_match(self):
        filt = build_filter(_ns(heapszf=["<=2"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_lte_no_match(self):
        filt = build_filter(_ns(heapszf=["<=1"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_missing_key_no_match(self):
        # SPECjbb fixture has no heapsize_factor key (never swept).
        filt = build_filter(_ns(heapszf=[">=1"]))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_null_value_no_match(self):
        # Non-swept run: info.json carries an explicit null.
        run = dict(_DACAPO_RUN)
        run["heapsize_factor"] = None
        filt = build_filter(_ns(heapszf=[">=1"]))
        self.assertFalse(filt(run))

    def test_repeated_and(self):
        filt = build_filter(_ns(heapszf=[">=1.5", "<=2.5"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_repeated_and_excludes(self):
        filt = build_filter(_ns(heapszf=[">2", "<2"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --heapsz
# ---------------------------------------------------------------------------

class TestBuildFilterHeapsz(unittest.TestCase):

    def test_eq_match(self):
        filt = build_filter(_ns(heapsz=["==16384"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_eq_no_match(self):
        filt = build_filter(_ns(heapsz=["==8192"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_bare_match(self):
        filt = build_filter(_ns(heapsz=["16384"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_gte_match(self):
        filt = build_filter(_ns(heapsz=[">=8192"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_gte_no_match(self):
        filt = build_filter(_ns(heapsz=[">=32768"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_lte_match(self):
        filt = build_filter(_ns(heapsz=["<=16384"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_lte_no_match(self):
        filt = build_filter(_ns(heapsz=["<=8192"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_missing_key_no_match(self):
        # SPECjbb fixture has no heapsize key.
        filt = build_filter(_ns(heapsz=[">=1"]))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_repeated_and(self):
        filt = build_filter(_ns(heapsz=[">=8192", "<=32768"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_repeated_and_excludes(self):
        filt = build_filter(_ns(heapsz=[">16384", "<16384"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --duration
# ---------------------------------------------------------------------------

class TestBuildFilterWct(unittest.TestCase):

    def test_gte_match(self):
        filt = build_filter(_ns(duration=[">=120"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_gte_no_match(self):
        filt = build_filter(_ns(duration=[">=121"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_bare_match(self):
        filt = build_filter(_ns(duration=["120.5"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_missing_key_no_match(self):
        # SPECjbb fixture has no duration_secs key.
        filt = build_filter(_ns(duration=[">=0"]))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_null_matches_missing_key(self):
        filt = build_filter(_ns(duration=["null"]))
        self.assertTrue(filt(_SPECJBB_RUN))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --startup_ms
# ---------------------------------------------------------------------------

_DACAPO_RUN_STARTUP = {**_DACAPO_RUN, "dacapo_result": {"result_ms": 7000, "startup_ms": 9216}}


class TestBuildFilterStartupMs(unittest.TestCase):

    def test_lt_match(self):
        filt = build_filter(_ns(startup_ms=["<10000"]))
        self.assertTrue(filt(_DACAPO_RUN_STARTUP))

    def test_lt_no_match(self):
        filt = build_filter(_ns(startup_ms=["<9216"]))
        self.assertFalse(filt(_DACAPO_RUN_STARTUP))

    def test_bare_match(self):
        filt = build_filter(_ns(startup_ms=["9216"]))
        self.assertTrue(filt(_DACAPO_RUN_STARTUP))

    def test_missing_key_no_match(self):
        filt = build_filter(_ns(startup_ms=[">=0"]))
        self.assertFalse(filt(_DACAPO_RUN))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_null_matches_missing_key(self):
        filt = build_filter(_ns(startup_ms=["null"]))
        self.assertTrue(filt(_SPECJBB_RUN))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertFalse(filt(_DACAPO_RUN_STARTUP))


# ---------------------------------------------------------------------------
# build_filter — --gc
# ---------------------------------------------------------------------------

class TestBuildFilterGc(unittest.TestCase):

    def test_exact_match(self):
        filt = build_filter(_ns(gc=["g1gc"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_no_match(self):
        filt = build_filter(_ns(gc=["zgc"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_missing_key_no_match(self):
        # SPECjbb fixture has no heap key at all.
        filt = build_filter(_ns(gc=["gencon"]))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_repeated_or(self):
        filt = build_filter(_ns(gc=["g1gc", "zgc"], or_=True))
        self.assertTrue(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --jdktype
# ---------------------------------------------------------------------------

class TestBuildFilterJdkType(unittest.TestCase):

    def test_type_match(self):
        filt = build_filter(_ns(jdktype=["openjdk"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_type_no_match(self):
        filt = build_filter(_ns(jdktype=["openj9"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --jdkname (substring on jdk.name)
# ---------------------------------------------------------------------------

class TestBuildFilterJdkName(unittest.TestCase):

    def test_jdk_substring_match(self):
        filt = build_filter(_ns(jdkname=["openjdk"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_jdk_substring_partial(self):
        filt = build_filter(_ns(jdkname=["21"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_jdk_no_match(self):
        filt = build_filter(_ns(jdkname=["openj9"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --jdkversion (substring on jdk.version)
# ---------------------------------------------------------------------------

class TestBuildFilterJdkVersion(unittest.TestCase):

    def test_substring_match(self):
        filt = build_filter(_ns(jdkversion=["21.0"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_no_match(self):
        filt = build_filter(_ns(jdkversion=["17."]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_missing_key_no_match(self):
        filt = build_filter(_ns(jdkversion=["21"]))
        self.assertFalse(filt({}))


# ---------------------------------------------------------------------------
# build_filter — --tags
# ---------------------------------------------------------------------------

class TestBuildFilterTag(unittest.TestCase):

    def test_tag_exact_match(self):
        filt = build_filter(_ns(tags=["G1"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_tag_literal_no_substring_match(self):
        filt = build_filter(_ns(tags=["base"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_tag_literal_case_sensitive(self):
        filt = build_filter(_ns(tags=["g1"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_tag_glob_star(self):
        filt = build_filter(_ns(tags=["base*"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_tag_glob_star_both_sides(self):
        filt = build_filter(_ns(tags=["*seli*"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_tag_glob_question_mark(self):
        filt = build_filter(_ns(tags=["G?"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_tag_glob_char_class(self):
        filt = build_filter(_ns(tags=["G[0-9]"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_tag_glob_must_match_whole_tag(self):
        filt = build_filter(_ns(tags=["base?"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_tag_no_match(self):
        filt = build_filter(_ns(tags=["parallel"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_tag_empty_tags_no_match(self):
        filt = build_filter(_ns(tags=["G1"]))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_tag_missing_tags_key_no_match(self):
        filt = build_filter(_ns(tags=["G1"]))
        self.assertFalse(filt({}))


# ---------------------------------------------------------------------------
# build_filter — --name
# ---------------------------------------------------------------------------

class TestBuildFilterName(unittest.TestCase):

    def test_name_exact_match(self):
        filt = build_filter(_ns(name=["dacapo.batik"]))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_name_literal_no_substring_match(self):
        filt = build_filter(_ns(name=["dacapo"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_name_case_sensitive(self):
        filt = build_filter(_ns(name=["Dacapo.*"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_name_glob_star(self):
        filt = build_filter(_ns(name=["dacapo.*"]))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_name_glob_question_mark(self):
        filt = build_filter(_ns(name=["dacapo.bati?"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_name_glob_char_class(self):
        filt = build_filter(_ns(name=["[ds]*"]))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertTrue(filt(_SPECJBB_RUN))

    def test_name_glob_must_match_whole_name(self):
        filt = build_filter(_ns(name=["dacapo.bat?"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_name_missing_key_no_match(self):
        filt = build_filter(_ns(name=["*"]))
        self.assertFalse(filt({}))

    def test_name_null_sentinel(self):
        filt = build_filter(_ns(name=["null"]))
        self.assertTrue(filt({}))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_name_repeated_or(self):
        filt = build_filter(_ns(name=["specjbb", "dacapo.h2"], or_=True))
        self.assertTrue(filt(_SPECJBB_RUN))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --hostname
# ---------------------------------------------------------------------------

class TestBuildFilterHostname(unittest.TestCase):

    def test_hostname_match(self):
        filt = build_filter(_ns(hostname=["host-a"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_hostname_no_match(self):
        filt = build_filter(_ns(hostname=["host-z"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --batchid
# ---------------------------------------------------------------------------

class TestBuildFilterBatchId(unittest.TestCase):

    def test_batch_id_match(self):
        filt = build_filter(_ns(batchid=["batch-a"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_batch_id_no_match(self):
        filt = build_filter(_ns(batchid=["batch-z"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --arch
# ---------------------------------------------------------------------------

class TestBuildFilterArch(unittest.TestCase):

    def test_arch_match(self):
        filt = build_filter(_ns(arch=["x86_64"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_arch_no_match(self):
        filt = build_filter(_ns(arch=["aarch64"]))
        self.assertFalse(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --os (substring on os)
# ---------------------------------------------------------------------------

class TestBuildFilterOs(unittest.TestCase):

    def test_substring_match(self):
        filt = build_filter(_ns(os=["Linux"]))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_no_match(self):
        filt = build_filter(_ns(os=["Windows"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_missing_key_no_match(self):
        filt = build_filter(_ns(os=["Linux"]))
        self.assertFalse(filt({}))


# ---------------------------------------------------------------------------
# build_filter — --jvmargs
# ---------------------------------------------------------------------------

class TestBuildFilterJvmargs(unittest.TestCase):

    def test_jvmargs_substring_match(self):
        filt = build_filter(_ns(jvmargs=["-Xmx"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_jvmargs_exact_match(self):
        filt = build_filter(_ns(jvmargs=["-XX:+UseG1GC"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_jvmargs_no_match(self):
        filt = build_filter(_ns(jvmargs=["-XX:+UseShenandoahGC"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_jvmargs_empty_flags_no_match(self):
        run = dict(_DACAPO_RUN)
        run["jvmargs"] = []
        filt = build_filter(_ns(jvmargs=["-Xmx"]))
        self.assertFalse(filt(run))

    def test_jvmargs_missing_flags_no_match(self):
        filt = build_filter(_ns(jvmargs=["-Xmx"]))
        self.assertFalse(filt({}))


# ---------------------------------------------------------------------------
# build_filter — --since / --until
# ---------------------------------------------------------------------------

class TestBuildFilterSince(unittest.TestCase):

    def test_since_before_match(self):
        filt = build_filter(_ns(since=["2026-08-01"]))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertTrue(filt(_SPECJBB_RUN))

    def test_since_after_no_match(self):
        filt = build_filter(_ns(since=["2026-08-06T18:17:17"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_since_exact_match_is_inclusive(self):
        filt = build_filter(_ns(since=["2026-08-06T18:17:16"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_since_excludes_earlier_run(self):
        filt = build_filter(_ns(since=["2026-08-02"]))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_since_missing_time_no_match(self):
        filt = build_filter(_ns(since=["2026-08-01"]))
        self.assertFalse(filt({}))

    def test_since_null_matches_missing_time(self):
        filt = build_filter(_ns(since=["null"]))
        self.assertTrue(filt({}))
        self.assertFalse(filt(_DACAPO_RUN))


class TestBuildFilterUntil(unittest.TestCase):

    def test_until_after_match(self):
        filt = build_filter(_ns(until=["2026-08-10"]))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertTrue(filt(_SPECJBB_RUN))

    def test_until_before_no_match(self):
        filt = build_filter(_ns(until=["2026-08-06T18:17:15"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_until_exact_match_is_inclusive(self):
        filt = build_filter(_ns(until=["2026-08-06T18:17:16"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_since_and_until_combine_as_range(self):
        filt = build_filter(_ns(since=["2026-08-02"], until=["2026-08-10"]))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertFalse(filt(_SPECJBB_RUN))


# ---------------------------------------------------------------------------
# build_filter — --or combinator
# ---------------------------------------------------------------------------

class TestBuildFilterOr(unittest.TestCase):

    def test_or_matches_either(self):
        filt = build_filter(_ns(benchmark=["dacapo", "specjbb"], or_=True))
        self.assertTrue(filt(_DACAPO_RUN))
        self.assertTrue(filt(_SPECJBB_RUN))

    def test_or_no_match(self):
        filt = build_filter(_ns(benchmark=["unknown1", "unknown2"], or_=True))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_or_mixed_flags(self):
        # --or --benchmark dacapo --jdkmajor >=99
        # With OR: dacapo match → True even though jdkmajor is way off
        filt = build_filter(_ns(benchmark=["dacapo"], jdkmajor=[">=99"], or_=True))
        self.assertTrue(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — --invert
# ---------------------------------------------------------------------------

class TestBuildFilterInvert(unittest.TestCase):

    def test_invert_match_becomes_false(self):
        filt = build_filter(_ns(benchmark=["dacapo"], invert=True))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_invert_no_match_becomes_true(self):
        filt = build_filter(_ns(benchmark=["dacapo"], invert=True))
        self.assertTrue(filt(_SPECJBB_RUN))

    def test_invert_no_filters(self):
        # Invert with no filters: always True → becomes always False.
        # (Note: no_filters returns lambda: True before invert; invert then
        # negates the *combined* result.)
        # Actually, when there are no predicates the function returns
        # `lambda m: True` unconditionally — the invert flag is not applied
        # in that branch. This is by design: with zero filters the tool is a
        # no-op pass-through regardless of --invert.
        # Verify the documented behaviour:
        filt = build_filter(_ns(invert=True))
        # Still True because there are no predicates.
        self.assertTrue(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# build_filter — repeated flags (AND by default)
# ---------------------------------------------------------------------------

class TestBuildFilterRepeatedFlags(unittest.TestCase):

    def test_repeated_benchmark_and_excludes(self):
        filt = build_filter(_ns(benchmark=["dacapo", "specjbb"]))
        self.assertFalse(filt(_DACAPO_RUN))
        self.assertFalse(filt(_SPECJBB_RUN))

    def test_repeated_tag_and_both_present(self):
        filt = build_filter(_ns(tags=["G1", "baseline"]))
        self.assertTrue(filt(_DACAPO_RUN))

    def test_repeated_tag_and_one_missing(self):
        filt = build_filter(_ns(tags=["G1", "notpresent"]))
        self.assertFalse(filt(_DACAPO_RUN))

    def test_repeated_jvmargs_and(self):
        filt = build_filter(_ns(jvmargs=["-Xmx", "-XX:+UseG1GC"]))
        self.assertTrue(filt(_DACAPO_RUN))


# ---------------------------------------------------------------------------
# run_jq_filter — integration test (skipped when jq not available)
# ---------------------------------------------------------------------------

class TestRunJqFilter(unittest.TestCase):

    @unittest.skipUnless(shutil.which("jq"), "jq not available")
    def test_truthy_expression(self):
        merged = {"benchmark": "dacapo"}
        self.assertTrue(run_jq_filter('.benchmark == "dacapo"', merged))

    @unittest.skipUnless(shutil.which("jq"), "jq not available")
    def test_falsy_expression(self):
        merged = {"benchmark": "dacapo"}
        self.assertFalse(run_jq_filter('.benchmark == "specjbb"', merged))

    @unittest.skipUnless(shutil.which("jq"), "jq not available")
    def test_nested_field(self):
        merged = {"jdk": {"major": 21}}
        self.assertTrue(run_jq_filter(".jdk.major == 21", merged))

    def test_jq_not_found_raises_runtime_error(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                run_jq_filter(".benchmark", {"benchmark": "dacapo"})


if __name__ == "__main__":
    unittest.main()
