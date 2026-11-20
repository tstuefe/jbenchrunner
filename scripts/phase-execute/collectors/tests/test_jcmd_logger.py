#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-execute/collectors/jcmd-logger.sh

Uses a fake `jcmd` binary (a tiny shell stub under a fake java_home/bin/)
rather than a real JDK, since this script has no NMT-specific knowledge of
its own — it just runs whatever --cmd it's given.
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

COLLECTOR = Path(__file__).resolve().parent.parent / "jcmd-logger.sh"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")


def run(args, timeout=30):
    return subprocess.run(
        ["bash", str(COLLECTOR)] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _make_fake_java_home(tmp_dir, body="echo fake-jcmd-output; exit 0"):
    """Create <tmp_dir>/bin/jcmd, a stub script, executable."""
    bin_dir = os.path.join(tmp_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    jcmd_path = os.path.join(bin_dir, "jcmd")
    with open(jcmd_path, "w") as f:
        f.write(f"#!/bin/bash\n{body}\n")
    st = os.stat(jcmd_path)
    os.chmod(jcmd_path, st.st_mode | stat.S_IEXEC)
    return tmp_dir


@unittest.skipUnless(sys.platform.startswith("linux"), "requires /proc")
class TestJcmdLoggerBasic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.target = subprocess.Popen(["sleep", "60"])
        cls.tmp = tempfile.mkdtemp()
        cls.java_home = _make_fake_java_home(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.target.terminate()
        cls.target.wait()
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _common_args(self):
        return ["--java-home", self.java_home, "--cmd", "VM.native_memory summary"]

    def test_basic_run_exit_zero(self):
        result = run(["-i", "1", "-n", "2"] + self._common_args() + [str(self.target.pid)], timeout=15)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_single_shot_zero_interval(self):
        """-n 1 -i 0 must take exactly one immediate sample then stop."""
        result = run(["-i", "0", "-n", "1"] + self._common_args() + [str(self.target.pid)], timeout=15)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        timestamps = [ln for ln in result.stdout.splitlines() if TS_RE.match(ln)]
        self.assertEqual(len(timestamps), 1)

    def test_timestamp_printed_before_each_sample(self):
        result = run(["-i", "1", "-n", "1"] + self._common_args() + [str(self.target.pid)], timeout=15)
        first_line = result.stdout.splitlines()[0]
        self.assertRegex(first_line, TS_RE)

    def test_jcmd_output_captured(self):
        result = run(["-i", "1", "-n", "1"] + self._common_args() + [str(self.target.pid)], timeout=15)
        self.assertIn("fake-jcmd-output", result.stdout)

    def test_cmd_split_into_separate_jcmd_arguments(self):
        """--cmd 'VM.info' style two-word subcommand must reach jcmd as two args."""
        java_home = _make_fake_java_home(
            tempfile.mkdtemp(), body='echo "argc=$#"; echo "args=$*"'
        )
        result = run(
            ["-i", "1", "-n", "1", "--java-home", java_home, "--cmd", "VM.native_memory summary",
             str(self.target.pid)],
            timeout=15,
        )
        self.assertIn("argc=3", result.stdout)  # <pid> + 2 subcommand tokens


@unittest.skipUnless(sys.platform.startswith("linux"), "requires /proc")
class TestJcmdLoggerProcessGone(unittest.TestCase):

    def test_process_exits_mid_run(self):
        tmp = tempfile.mkdtemp()
        java_home = _make_fake_java_home(tmp)
        p = subprocess.Popen(["sleep", "1"])
        pid = p.pid
        result = run(
            ["-i", "1", "-n", "5", "--java-home", java_home, "--cmd", "VM.info", str(pid)],
            timeout=30,
        )
        p.wait()
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_exits_promptly_with_long_interval(self):
        import threading
        import time
        java_home = _make_fake_java_home(tempfile.mkdtemp())
        p = subprocess.Popen(["sleep", "2"])
        # Reap it as soon as it exits: a zombie would still count as alive.
        threading.Thread(target=p.wait, daemon=True).start()
        start = time.monotonic()
        try:
            result = run(
                ["-i", "60", "--java-home", java_home, "--cmd", "VM.info", str(p.pid)],
                timeout=15,
            )
        finally:
            p.kill()
            p.wait()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertLess(time.monotonic() - start, 10)

    def test_nonexistent_pid(self):
        tmp = tempfile.mkdtemp()
        java_home = _make_fake_java_home(tmp)
        result = run(
            ["-i", "1", "--java-home", java_home, "--cmd", "VM.info", "999999999"],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)


@unittest.skipUnless(sys.platform.startswith("linux"), "requires /proc")
class TestJcmdLoggerFailedSample(unittest.TestCase):

    def test_jcmd_nonzero_exit_does_not_abort_loop(self):
        """A single failed sample must not kill the whole collector."""
        tmp = tempfile.mkdtemp()
        java_home = _make_fake_java_home(tmp, body="exit 1")
        target = subprocess.Popen(["sleep", "60"])
        try:
            result = run(
                ["-i", "1", "-n", "2", "--java-home", java_home, "--cmd", "VM.info",
                 str(target.pid)],
                timeout=15,
            )
        finally:
            target.terminate()
            target.wait()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("jcmd exited with code 1", result.stderr)


class TestJcmdLoggerArgErrors(unittest.TestCase):
    """Argument validation runs before any /proc access, so these are portable."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.java_home = _make_fake_java_home(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _common_args(self):
        return ["--java-home", self.java_home, "--cmd", "VM.info"]

    def test_missing_interval(self):
        result = run(self._common_args() + ["12345"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--interval", result.stderr)

    def test_missing_pid(self):
        result = run(["-i", "1"] + self._common_args())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pid", result.stderr)

    def test_missing_java_home(self):
        result = run(["-i", "1", "--cmd", "VM.info", "12345"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--java-home", result.stderr)

    def test_missing_cmd(self):
        result = run(["-i", "1", "--java-home", self.java_home, "12345"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--cmd", result.stderr)

    def test_invalid_pid_zero(self):
        result = run(["-i", "1"] + self._common_args() + ["0"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid pid", result.stderr)

    def test_invalid_pid_non_integer(self):
        result = run(["-i", "1"] + self._common_args() + ["abc"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid pid", result.stderr)

    def test_invalid_interval_negative(self):
        result = run(["-i", "-1"] + self._common_args() + ["12345"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid interval", result.stderr)

    def test_invalid_interval_fractional(self):
        result = run(["-i", "0.5"] + self._common_args() + ["12345"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid interval", result.stderr)

    def test_invalid_num_zero(self):
        result = run(["-i", "1", "-n", "0"] + self._common_args() + ["12345"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid num", result.stderr)

    def test_nonexecutable_java_home(self):
        empty_home = tempfile.mkdtemp()
        result = run(["-i", "1", "--java-home", empty_home, "--cmd", "VM.info", "12345"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("jcmd binary not found", result.stderr)

    def test_help(self):
        result = run(["--help"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage", result.stderr)

    def test_unknown_option(self):
        result = run(["--bogus"])
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
