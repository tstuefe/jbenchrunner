# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-execute/failure_classifier.py.

Covers:
- exit_signal: subprocess (-N) and shell (128+N) encodings; plain exit codes.
- classify_failure: rule order crash > signal > oom > version mismatch.
- classify_failure: SIGABRT without a crash file is "signal 6".
- classify_failure: OOM only counted in the last 100 lines of stderr.log, and
  not for flag names like ExitOnOutOfMemoryError; stdout.log is ignored.
- classify_failure: no match returns None.
"""

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_EXECUTE_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _PHASE_EXECUTE_DIR not in sys.path:
    sys.path.insert(0, _PHASE_EXECUTE_DIR)

from failure_classifier import classify_failure, exit_signal  # noqa: E402


class TestExitSignal(unittest.TestCase):

    def test_negative(self):
        self.assertEqual(exit_signal(-6), 6)

    def test_shell_encoding(self):
        self.assertEqual(exit_signal(134), 6)

    def test_plain_exit_codes(self):
        for code in (None, 0, 1, 3, 127, 128, 255):
            self.assertIsNone(exit_signal(code), code)


class TestClassifyFailure(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, text):
        with open(os.path.join(self.dir, name), "w") as fh:
            fh.write(text)

    def test_hs_err_crash(self):
        self._write("hs_err_pid4711.log", "#\n")
        self.assertEqual(classify_failure(self.dir, -6), "crash")

    def test_javacore_crash(self):
        self._write("javacore.20260927.080000.4711.0001.txt", "")
        self.assertEqual(classify_failure(self.dir, 134), "crash")

    def test_sigabrt_without_crash_file(self):
        self.assertEqual(classify_failure(self.dir, -6), "signal 6")

    def test_crash_file_needs_sigabrt(self):
        self._write("hs_err_pid4711.log", "#\n")
        self.assertEqual(classify_failure(self.dir, -9), "signal 9")

    def test_signal_beats_oom(self):
        self._write("stderr.log", "java.lang.OutOfMemoryError: Java heap space\n")
        self.assertEqual(classify_failure(self.dir, -9), "signal 9")

    def test_oom(self):
        self._write("stderr.log",
                    "Exception in thread \"main\" java.lang.OutOfMemoryError: Java heap space\n")
        self.assertEqual(classify_failure(self.dir, 1), "oom")

    def test_oom_outside_tail_ignored(self):
        self._write("stderr.log",
                    "java.lang.OutOfMemoryError: Java heap space\n" + "x\n" * 100)
        self.assertIsNone(classify_failure(self.dir, 1))

    def test_oom_flag_name_ignored(self):
        self._write("stderr.log", "bool ExitOnOutOfMemoryError = false\n")
        self.assertIsNone(classify_failure(self.dir, 1))

    def test_stdout_ignored(self):
        self._write("stdout.log", "java.lang.OutOfMemoryError: Java heap space\n")
        self.assertIsNone(classify_failure(self.dir, 1))

    def test_oom_beats_version_mismatch(self):
        self._write("stderr.log",
                    "Only Java versions 8-17 are supported, system version is 28\n"
                    "java.lang.OutOfMemoryError: Java heap space\n")
        self.assertEqual(classify_failure(self.dir, 1), "oom")

    def test_version_mismatch_h2o(self):
        self._write("stderr.log",
                    "Only Java versions 8-17 are supported, system version is 28-internal\n")
        self.assertEqual(classify_failure(self.dir, 3), "version mismatch")

    def test_version_mismatch_cassandra(self):
        self._write("stderr.log",
                    "Java version '28-internal' is incompatable with cassandra: ...\n")
        self.assertEqual(classify_failure(self.dir, 255), "version mismatch")

    def test_no_match(self):
        self._write("stderr.log", "something else went wrong\n")
        self.assertIsNone(classify_failure(self.dir, 1))

    def test_no_stderr_log(self):
        self.assertIsNone(classify_failure(self.dir, 1))


if __name__ == "__main__":
    unittest.main()
