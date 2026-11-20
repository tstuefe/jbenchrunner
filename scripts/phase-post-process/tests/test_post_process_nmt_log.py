#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/tests/test_post_process_nmt_log.py
#
# Test suite for scripts/phase-post-process/post-process-nmt-log.py.
#
# Strategy: golden-file comparison for the happy path, plus targeted unit
# tests for the three error tiers (file/sample/category) and specific
# output properties (arena ignored, snake_case names, "at peak" handling).
#
# Run with:  python3 -m unittest discover -s scripts/phase-post-process/tests  (from repo root)
#        or: python3 -m unittest discover -s scripts/phase-post-process/tests test_post_process_nmt_log

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

# Allow importing the module under test directly.
_HERE = os.path.dirname(__file__)
_PHASE_PP_DIR = os.path.join(_HERE, "..")
sys.path.insert(0, _PHASE_PP_DIR)

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "post_process_nmt_log",
    os.path.join(_PHASE_PP_DIR, "post-process-nmt-log.py"),
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

parse_log = _MOD.parse_log
sanitize_category_name = _MOD.sanitize_category_name
kb_to_mb = _MOD.kb_to_mb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLES_DIR = os.path.join(_HERE, "post_process_nmt_log", "input")
EXPECTED_DIR = os.path.join(_HERE, "post_process_nmt_log", "expected")


def _input(name):
    return os.path.join(EXAMPLES_DIR, name + ".log")


def _expected(name):
    with open(os.path.join(EXPECTED_DIR, name + ".json")) as fh:
        return json.load(fh)


def _write_tmp(content: str) -> str:
    """Write *content* to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
    f.write(content)
    f.close()
    return f.name


def _parse_quietly(path):
    """Call parse_log, discarding stderr warnings, and return the samples."""
    buf = io.StringIO()
    with unittest.mock.patch("sys.stderr", buf):
        return parse_log(path), buf.getvalue()


# ---------------------------------------------------------------------------
# Golden-file test
# ---------------------------------------------------------------------------

class TestGoldenFiles(unittest.TestCase):
    def test_basic(self):
        """Two clean samples covering mmap-only, malloc+mmap, 'at peak',
        the zero-count-omits-# quirk, a multi-word tag, and arena-ignored."""
        result = parse_log(_input("nmt-basic"))
        expected = _expected("nmt-basic")
        self.assertEqual(result, expected)


# ---------------------------------------------------------------------------
# Structural / property tests
# ---------------------------------------------------------------------------

class TestOutputStructure(unittest.TestCase):
    def test_cli_writes_json_lines(self):
        path = _input("nmt-basic")
        result = subprocess.run(
            [sys.executable, _SPEC.origin, path],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        samples = [json.loads(line) for line in lines]
        self.assertEqual(samples, parse_log(path))

    def test_top_level_keys(self):
        for sample in parse_log(_input("nmt-basic")):
            for key in (
                "timestamp", "pid", "total",
                "total_malloc", "total_malloc_peak", "total_mmap_committed",
            ):
                self.assertIn(key, sample)

    def test_pid_is_int_totals_are_float(self):
        for sample in parse_log(_input("nmt-basic")):
            self.assertIsInstance(sample["pid"], int)
            for key in (
                "total",
                "total_malloc", "total_malloc_peak", "total_mmap_committed",
            ):
                self.assertIsInstance(sample[key], float)

    def test_timestamp_is_iso8601(self):
        from datetime import datetime
        for sample in parse_log(_input("nmt-basic")):
            datetime.fromisoformat(sample["timestamp"])

    def test_category_values_have_expected_shape(self):
        for sample in parse_log(_input("nmt-basic")):
            for key, value in sample.items():
                if key in (
                    "timestamp", "pid", "total",
                    "total_malloc", "total_malloc_peak", "total_mmap_committed",
                ):
                    continue
                self.assertEqual(
                    set(value), {"mmap_committed", "malloc", "malloc_peak"},
                    msg=f"Unexpected shape for category {key!r}: {value!r}",
                )

    def test_arena_ignored(self):
        """Thread category has arena=500KB/peak=900KB in the fixture, but its
        malloc/malloc_peak must reflect only the malloc=... line (100KB/150KB,
        i.e. 0.1/0.15 MB)."""
        first = parse_log(_input("nmt-basic"))[0]
        self.assertEqual(first["thread"], {"mmap_committed": 0.0, "malloc": 0.1, "malloc_peak": 0.15})

    def test_at_peak_shorthand(self):
        """'(at peak)' with no explicit peak=... means malloc_peak == malloc."""
        first = parse_log(_input("nmt-basic"))[0]
        self.assertEqual(first["code"]["malloc"], first["code"]["malloc_peak"])

    def test_zero_count_hash_omitted(self):
        """'(malloc=0KB tag=Logging) (peak=100KB #1)' has no '#0' after tag=Logging;
        malloc must still parse as 0 and malloc_peak as 100KB (0.1 MB)."""
        first = parse_log(_input("nmt-basic"))[0]
        self.assertEqual(first["logging"], {"mmap_committed": 0.0, "malloc": 0.0, "malloc_peak": 0.1})

    def test_multi_word_tag_parses(self):
        """tag=Native Memory Tracking contains spaces; must still parse correctly."""
        first = parse_log(_input("nmt-basic"))[0]
        self.assertEqual(
            first["native_memory_tracking"],
            {"mmap_committed": 0.0, "malloc": 0.05, "malloc_peak": 0.06},
        )

    def test_categories_vary_between_samples(self):
        """logging/native_memory_tracking/thread/code appear only in sample 1;
        gccardset only in sample 2 — categories are not zero-filled across samples."""
        samples = parse_log(_input("nmt-basic"))
        self.assertIn("logging", samples[0])
        self.assertNotIn("logging", samples[1])
        self.assertIn("gccardset", samples[1])
        self.assertNotIn("gccardset", samples[0])


# ---------------------------------------------------------------------------
# Sanitization tests
# ---------------------------------------------------------------------------

class TestSanitizeCategoryName(unittest.TestCase):
    def test_space_separated(self):
        self.assertEqual(sanitize_category_name("Java Heap"), "java_heap")
        self.assertEqual(sanitize_category_name("String Deduplication"), "string_deduplication")
        self.assertEqual(sanitize_category_name("Arena Chunk"), "arena_chunk")

    def test_already_concatenated_lowercased_whole(self):
        """No camelCase splitting: GCCardSet -> gccardset, not gc_card_set."""
        self.assertEqual(sanitize_category_name("GCCardSet"), "gccardset")

    def test_punctuation_collapsed(self):
        self.assertEqual(sanitize_category_name("  Native   Memory-Tracking! "), "native_memory_tracking")


# ---------------------------------------------------------------------------
# Unit conversion tests
# ---------------------------------------------------------------------------

class TestKbToMb(unittest.TestCase):
    def test_rounds_to_two_decimals(self):
        self.assertEqual(kb_to_mb(1024), 1.0)
        self.assertEqual(kb_to_mb(1567), 1.53)
        self.assertEqual(kb_to_mb(0), 0.0)

    def test_returns_float(self):
        self.assertIsInstance(kb_to_mb(0), float)
        self.assertIsInstance(kb_to_mb(1024), float)


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------

class TestErrorPaths(unittest.TestCase):
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

    def test_file_with_no_timestamp_lines_exits(self):
        self._assert_exits("this file has no timestamp lines at all\njust noise\n")

    def test_mixed_errors_skip_bad_samples_and_categories(self):
        """One sample with a bad pid line, one with a bad Total: block, and
        one good sample containing a category whose name sanitizes to empty
        ('!!!'). Only the good sample survives, without the '!!!' category,
        and each failure is warned about."""
        samples, stderr = _parse_quietly(_input("nmt-mixed-errors"))

        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample["timestamp"], "2026-01-01T10:00:10.000+00:00")
        self.assertIn("java_heap", sample)
        self.assertIn("class", sample)
        self.assertNotIn("!!!", sample)

        self.assertIn("skipping sample", stderr)
        self.assertIn("cannot parse pid line", stderr)
        self.assertIn("cannot parse Total: block", stderr)
        self.assertIn("skipping category", stderr)

    def test_no_categories_warns_but_keeps_sample(self):
        """A sample with a valid Total: block but no category headers at all
        must still be emitted (totals-only), with a warning."""
        path = _write_tmp(
            "2026-01-01T10:00:00.000+00:00\n"
            "12345:\n"
            "\n"
            "Native Memory Tracking:\n"
            "\n"
            "Total: reserved=100KB, committed=90KB\n"
            "       malloc: 10KB #1, peak=10KB #1\n"
            "       mmap:   reserved=90KB, committed=80KB\n"
        )
        try:
            samples, stderr = _parse_quietly(path)
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]["total"], 0.09)
            self.assertIn("no categories parsed", stderr)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
