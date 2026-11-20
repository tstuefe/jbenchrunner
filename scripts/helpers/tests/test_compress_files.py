#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/helpers/compress-files.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.abspath(os.path.join(_HERE, "..", "compress-files.py"))


@unittest.skipUnless(shutil.which("zstd"), "zstd binary not installed")
class TestCompressFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _make_run(self, name, status, files=("gc.log", "heapsizes.txt")):
        run_dir = os.path.join(self.tmp, name)
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "run-info.json"), "w") as f:
            json.dump({"status": status}, f)
        for fname in files:
            with open(os.path.join(run_dir, fname), "w") as f:
                f.write(f"{fname}\n" * 100)
        return run_dir

    def _run(self, *args):
        return subprocess.run([sys.executable, _SCRIPT, *args, self.tmp],
                              capture_output=True, text=True, check=True).stdout

    def _ls(self, run_dir):
        return sorted(os.listdir(run_dir))

    def test_compresses_done_and_failed_runs(self):
        done = self._make_run("done", "DONE")
        failed = self._make_run("failed", "FAILED", files=("gc-all.log", "gc.log"))
        out = self._run()
        self.assertEqual(self._ls(done), ["gc.log.zst", "heapsizes.txt.zst", "run-info.json"])
        self.assertEqual(self._ls(failed), ["gc-all.log.zst", "gc.log.zst", "run-info.json"])
        self.assertIn("compressed: 4 file(s)", out)

    def test_running_run_skipped(self):
        running = self._make_run("running", "RUNNING")
        out = self._run()
        self.assertEqual(self._ls(running), ["gc.log", "heapsizes.txt", "run-info.json"])
        self.assertIn("RUNNING runs skipped: 1", out)

    def test_dir_without_run_info_ignored(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        with open(os.path.join(other, "gc.log"), "w") as f:
            f.write("x\n")
        self._run()
        self.assertEqual(self._ls(other), ["gc.log"])

    def test_dry_run_changes_nothing(self):
        done = self._make_run("done", "DONE")
        out = self._run("-n")
        self.assertEqual(self._ls(done), ["gc.log", "heapsizes.txt", "run-info.json"])
        self.assertIn("would compress: 2 file(s)", out)

    def test_rerun_is_harmless(self):
        done = self._make_run("done", "DONE")
        self._run()
        out = self._run()
        self.assertEqual(self._ls(done), ["gc.log.zst", "heapsizes.txt.zst", "run-info.json"])
        self.assertIn("compressed: 0 file(s)", out)
        text = subprocess.run(["zstd", "-dc", os.path.join(done, "gc.log.zst")],
                              capture_output=True, text=True, check=True).stdout
        self.assertEqual(text, "gc.log\n" * 100)


if __name__ == "__main__":
    unittest.main()
