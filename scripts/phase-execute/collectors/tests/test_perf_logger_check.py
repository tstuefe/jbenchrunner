#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for perf-logger.sh --check (PRD.md D-86), against a fake 'perf' on
PATH, so they run on any machine. Run with:

    python3 -m unittest discover scripts
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

COLLECTOR = Path(__file__).resolve().parent.parent / "perf-logger.sh"

# Prints its arguments to stdout and a line to stderr, exits with $FAKE_PERF_RC.
_FAKE_PERF = """#!/bin/bash
echo "args: $*"
echo "from stderr" >&2
exit "${FAKE_PERF_RC:-0}"
"""


class TestPerfLoggerCheck(unittest.TestCase):

    def setUp(self):
        self.bindir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.bindir, True)
        perf = os.path.join(self.bindir, "perf")
        with open(perf, "w", encoding="utf-8") as fh:
            fh.write(_FAKE_PERF)
        os.chmod(perf, 0o755)

    def _run(self, args, rc=0):
        env = dict(os.environ, PATH=self.bindir + os.pathsep + os.environ["PATH"],
                   FAKE_PERF_RC=str(rc))
        return subprocess.run(["bash", str(COLLECTOR)] + args, capture_output=True,
                              text=True, timeout=30, env=env)

    def test_runs_same_command_on_true(self):
        result = self._run(["--check", "--counters", "cycles,cpu/event=0x3c,umask=0/"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("args: stat -x ; -e cycles,cpu/event=0x3c,umask=0/ -- true",
                      result.stdout)

    def test_default_set_without_counters(self):
        result = self._run(["--check"])
        self.assertIn("args: stat -x ; -- true", result.stdout)

    def test_perf_exit_code_and_stderr_passed_through(self):
        result = self._run(["--check", "--counters", "cyclez"], rc=129)
        self.assertEqual(result.returncode, 129)
        self.assertIn("from stderr", result.stdout)

    def test_pid_or_interval_rejected(self):
        for extra in (["12345"], ["-i", "10"], ["-n", "1"]):
            with self.subTest(extra=extra):
                result = self._run(["--check"] + extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--check takes no pid", result.stderr)
                self.assertNotIn("args:", result.stdout)


if __name__ == "__main__":
    unittest.main()
