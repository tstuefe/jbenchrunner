#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-post-process/parse_specjbb_results.py
"""

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(__file__)
_PHASE_PP_DIR = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _PHASE_PP_DIR)

from parse_specjbb_results import parse_specjbb_output

SCRIPT_PATH = os.path.join(_PHASE_PP_DIR, "parse_specjbb_results.py")
INPUT_DIR   = os.path.join(_HERE, "parse_specjbb_results", "input")
EXPECTED_DIR = os.path.join(_HERE, "parse_specjbb_results", "expected")


class TestParseSpecjbbResults(unittest.TestCase):

    def test_golden_master(self):
        """Parse the real SPECjbb2015 stdout fixture and compare against the golden master."""
        input_path    = os.path.join(INPUT_DIR,    "specjbb-stdout.out")
        expected_path = os.path.join(EXPECTED_DIR, "specjbb-stdout.json")

        with open(input_path, "r", encoding="utf-8") as f:
            content = f.read()
        with open(expected_path, "r", encoding="utf-8") as f:
            expected = json.load(f)

        result = parse_specjbb_output(content)
        self.assertEqual(result, expected)

    def test_cli_golden_master(self):
        """CLI invocation against the fixture produces JSON matching the golden master."""
        input_path    = os.path.join(INPUT_DIR,    "specjbb-stdout.out")
        expected_path = os.path.join(EXPECTED_DIR, "specjbb-stdout.json")

        with open(expected_path, "r", encoding="utf-8") as f:
            expected = json.load(f)

        res = subprocess.run(
            [sys.executable, SCRIPT_PATH, input_path],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout), expected)

    def test_result_line_embedded_in_surrounding_output(self):
        """Result line is found even when surrounded by other log noise."""
        sample = (
            "[INFO] Some SPECjbb preamble output\n"
            "RUN RESULT: hbIR (max attempted) = 1000, hbIR (settled) = 900, "
            "max-jOPS = 5000, critical-jOPS = 4200\n"
            "[INFO] Some trailing log line\n"
        )
        result = parse_specjbb_output(sample)
        self.assertEqual(result, {
            "hbirmax": 1000,
            "hbirset": 900,
            "maxjops": 5000,
            "critjops": 4200,
        })

    def test_zero_values_accepted(self):
        """critical-jOPS of 0 (as in the fixture) is represented as integer 0."""
        sample = (
            "RUN RESULT: hbIR (max attempted) = 220, hbIR (settled) = 200, "
            "max-jOPS = 136, critical-jOPS = 0\n"
        )
        result = parse_specjbb_output(sample)
        self.assertEqual(result["critjops"], 0)
        self.assertIsInstance(result["critjops"], int)

    def test_all_fields_are_integers(self):
        """All four result fields must be Python ints."""
        sample = (
            "RUN RESULT: hbIR (max attempted) = 10, hbIR (settled) = 9, "
            "max-jOPS = 8, critical-jOPS = 7\n"
        )
        result = parse_specjbb_output(sample)
        for key in ("hbirmax", "hbirset", "maxjops", "critjops"):
            with self.subTest(key=key):
                self.assertIsInstance(result[key], int)

    def test_missing_result_line_raises_value_error(self):
        """Output without a RUN RESULT line raises ValueError."""
        sample = "Some output that contains no result line at all.\n"
        with self.assertRaises(ValueError):
            parse_specjbb_output(sample)

    def test_empty_content_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_specjbb_output("")

    def test_cli_file_not_found(self):
        res = subprocess.run(
            [sys.executable, SCRIPT_PATH, "no_such_file.out"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Error: file not found", res.stderr)

    def test_cli_missing_result_line_exits_nonzero(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".out", delete=False) as tf:
            tf.write("no result line here\n")
            tf_path = tf.name
        try:
            res = subprocess.run(
                [sys.executable, SCRIPT_PATH, tf_path],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Error", res.stderr)
        finally:
            os.unlink(tf_path)

    def test_cli_help(self):
        res = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("usage:", res.stdout.lower())


if __name__ == "__main__":
    unittest.main()
