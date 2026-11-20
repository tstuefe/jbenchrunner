# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/common/collector_probe.py

Run with:
    python3 -m unittest scripts/common/tests/test_collector_probe.py
"""

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import collector_probe  # noqa: E402
from common.collector_probe import unavailable_reason  # noqa: E402


def _completed(returncode, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


class TestUnavailableReason(unittest.TestCase):

    def test_machine_independent_collectors_always_available(self):
        for name in ("nmt", "system_map", "thread_print", "no_such_collector"):
            with self.subTest(name=name):
                self.assertIsNone(unavailable_reason(name))

    def test_binaries(self):
        for name in ("pidstat", "perf"):
            with self.subTest(name=name):
                with patch("shutil.which", return_value=None):
                    self.assertIn(f"'{name}' not found on PATH", unavailable_reason(name))
                with patch("shutil.which", return_value=f"/usr/bin/{name}"):
                    self.assertIsNone(unavailable_reason(name))

    def test_proc(self):
        with patch("os.path.isfile", return_value=False):
            self.assertIn("no Linux procfs", unavailable_reason("proc"))
        with patch("os.path.isfile", return_value=True):
            self.assertIsNone(unavailable_reason("proc"))

    def test_cpu_temp_runs_read_script(self):
        with patch("subprocess.run", return_value=_completed(0)) as run:
            self.assertIsNone(unavailable_reason("cpu_temp"))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "bash")
        self.assertTrue(os.path.isfile(cmd[1]), cmd[1])
        self.assertEqual(os.path.basename(cmd[1]), "read-cpu-temp.sh")

    def test_cpu_temp_failure_reports_stderr(self):
        with patch("subprocess.run", return_value=_completed(1, "unsupported architecture\n")):
            self.assertEqual(unavailable_reason("cpu_temp"),
                             "CPU temperature not readable: unsupported architecture")

    def test_cpu_temp_cannot_start(self):
        for exc in (OSError("no bash"), subprocess.TimeoutExpired("bash", 30)):
            with self.subTest(exc=type(exc).__name__):
                with patch("subprocess.run", side_effect=exc):
                    self.assertIn("CPU temperature not readable", unavailable_reason("cpu_temp"))

    def test_probes_cover_the_machine_dependent_collectors(self):
        self.assertEqual(set(collector_probe._PROBES), {"cpu_temp", "pidstat", "proc", "perf"})


if __name__ == "__main__":
    unittest.main()
