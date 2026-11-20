#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/common/gnuplot_utils.py (run header / plot title, string quoting)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.gnuplot_utils import gnuplot_dquote, gnuplot_quote, plot_title, run_header  # noqa: E402


class TestRunHeader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.run_dir = os.path.join(self.tmp, "3846532a-75d5-48bb-a73f-da3aa8af45be")
        os.makedirs(self.run_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _info(self, info):
        with open(os.path.join(self.run_dir, "info.json"), "w") as f:
            json.dump(info, f)

    def test_dir_name_tags(self):
        self._info({"name": "dacapo.spring",
                    "tags": ["arena2", "log-with-fastdebug", "v2.2", "1 jit thread"]})
        self.assertEqual(run_header(self.run_dir),
                         "3846532a-75d5-48bb-a73f-da3aa8af45be - dacapo.spring - "
                         "arena2,log-with-fastdebug,v2.2,1 jit thread")

    def test_trailing_slash(self):
        self._info({"name": "dacapo.spring", "tags": []})
        self.assertEqual(run_header(self.run_dir + os.sep),
                         "3846532a-75d5-48bb-a73f-da3aa8af45be - dacapo.spring")

    def test_empty_parts_left_out(self):
        self._info({"tags": ["t1"]})
        self.assertEqual(run_header(self.run_dir),
                         "3846532a-75d5-48bb-a73f-da3aa8af45be - t1")

    def test_missing_info(self):
        self.assertIsNone(run_header(self.run_dir))

    def test_plot_title(self):
        self.assertEqual(plot_title("Heap Size", self.run_dir), "Heap Size")
        self._info({"name": "dacapo.spring", "tags": ["a", "b"]})
        self.assertEqual(plot_title("Heap Size", self.run_dir),
                         "Heap Size\n3846532a-75d5-48bb-a73f-da3aa8af45be - dacapo.spring - a,b")


class TestGnuplotDquote(unittest.TestCase):
    def test_escapes(self):
        self.assertEqual(gnuplot_dquote('a\\b"c`d`\ne'),
                         '"a\\\\b\\"c\\`d\\`\\ne"')

    @unittest.skipUnless(shutil.which("gnuplot"), "gnuplot not installed")
    def test_backquotes_not_executed_by_gnuplot(self):
        # gnuplot runs `...` inside a double-quoted string as a shell command
        # unless the backquotes are escaped.
        tmp = tempfile.mkdtemp()
        try:
            marker = os.path.join(tmp, "pwned")
            title = gnuplot_dquote(f"tag `touch {marker}` x")
            subprocess.run(["gnuplot"], input=f"set term dumb\nset title {title}\n",
                           text=True, capture_output=True)
            self.assertFalse(os.path.exists(marker))
        finally:
            shutil.rmtree(tmp)


class TestGnuplotQuote(unittest.TestCase):
    # gnuplot ends a command at a raw newline even inside a '...' literal, so
    # a newline in untrusted input (e.g. a result directory name) must never
    # reach the script: the rest of the value would run as a new command.

    def test_newline_escaped(self):
        self.assertEqual(gnuplot_quote("a\nsystem('x')\n#"),
                         '"a\\nsystem(\'x\')\\n#"')

    def test_same_as_dquote(self):
        s = "it's a \\ \"b\" `c`\nd"
        self.assertEqual(gnuplot_quote(s), gnuplot_dquote(s))

    @unittest.skipUnless(shutil.which("gnuplot"), "gnuplot not installed")
    def test_newline_not_executed_by_gnuplot(self):
        tmp = tempfile.mkdtemp()
        try:
            marker = os.path.join(tmp, "pwned")
            # '"': a "'" would be doubled by single-quote escaping, breaking the command
            title = gnuplot_quote(f'x\nsystem("touch {marker}")\n#')
            subprocess.run(["gnuplot"], input=f"set term dumb\nset title {title}\n",
                           text=True, capture_output=True)
            self.assertFalse(os.path.exists(marker))
        finally:
            shutil.rmtree(tmp)

    @unittest.skipUnless(shutil.which("gnuplot"), "gnuplot not installed")
    def test_result_dir_name_with_newline_not_executed(self):
        # End to end through a comparison plot, whose legend label is the
        # result directory's name.
        # pidstat_plot imports gnuplot_utils as a top-level module
        sys.path.insert(0, os.path.join(_SCRIPTS_DIR, "common"))
        from pidstat_plot import build_comparison_svg
        tmp = tempfile.mkdtemp()
        try:
            # no "/" in a directory name: touch a file in gnuplot's cwd
            evil = 'run\nsystem("touch pwned")\n#'
            for d in ("base", evil):
                os.makedirs(os.path.join(tmp, d))
                with open(os.path.join(tmp, d, "pidstat.jsonl"), "w") as f:
                    for i in range(3):
                        f.write(json.dumps({
                            "timestamp": f"2026-01-01T00:00:0{i}", "rss": 1024,
                            "pct_system": 1, "pct_usr": 1, "pct_guest": 0}) + "\n")
            out_svg = os.path.join(tmp, "out.svg")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertTrue(build_comparison_svg(
                    os.path.join(tmp, evil), os.path.join(tmp, "base"), out_svg))
            finally:
                os.chdir(cwd)
            self.assertFalse(os.path.exists(os.path.join(tmp, "pwned")))
            self.assertTrue(os.path.exists(out_svg))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
