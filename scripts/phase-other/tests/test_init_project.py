# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-other/init_project.py and bin/jbr-init (spec-other.md §5)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_OTHER_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_PHASE_OTHER_DIR, ".."))
_COMMON_DIR = os.path.join(_SCRIPTS_DIR, "common")
_JBR_INIT = os.path.abspath(os.path.join(_SCRIPTS_DIR, "..", "bin", "jbr-init"))
for _p in (_PHASE_OTHER_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from init_project import init_project  # noqa: E402


class TestInitProject(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _marker(self, path):
        with open(os.path.join(path, "jbr-project.json")) as fh:
            return json.load(fh)

    def test_creates_missing_dir_and_empty_marker(self):
        target = os.path.join(self.tmp, "a", "b")
        self.assertEqual(init_project(target), target)
        self.assertEqual(self._marker(target), {})
        self.assertEqual(os.listdir(target), ["jbr-project.json"])

    def test_description_stored(self):
        target = os.path.join(self.tmp, "p")
        init_project(target, "GC shootout")
        self.assertEqual(self._marker(target), {"description": "GC shootout"})

    def test_existing_project_rejected(self):
        target = os.path.join(self.tmp, "p")
        init_project(target, "first")
        with self.assertRaises(RuntimeError):
            init_project(target, "second")
        self.assertEqual(self._marker(target), {"description": "first"})

    def test_nested_project_rejected(self):
        outer = os.path.join(self.tmp, "outer")
        init_project(outer)
        inner = os.path.join(outer, "inner")
        with self.assertRaises(RuntimeError) as ctx:
            init_project(inner)
        self.assertIn("nested", str(ctx.exception))
        self.assertFalse(os.path.exists(inner))

    def test_file_in_the_way_rejected(self):
        target = os.path.join(self.tmp, "file")
        open(target, "w").close()
        with self.assertRaises(RuntimeError):
            init_project(target)

    def test_example_run_copied(self):
        src = os.path.join(self.tmp, "ex.run.toml")
        with open(src, "w") as fh:
            fh.write("# example\n")
        target = os.path.join(self.tmp, "p")
        init_project(target, example_run=src)
        with open(os.path.join(target, "example.run.toml")) as fh:
            self.assertEqual(fh.read(), "# example\n")

    def test_cli_copies_example_run(self):
        target = os.path.join(self.tmp, "cli")
        subprocess.run([_JBR_INIT, target], capture_output=True, text=True, check=True)
        repo_example = os.path.join(_SCRIPTS_DIR, "..", "config", "run", "example.run.toml")
        with open(repo_example) as a, open(os.path.join(target, "example.run.toml")) as b:
            self.assertEqual(a.read(), b.read())

    def test_cli_prints_absolute_path_only(self):
        target = os.path.join(self.tmp, "cli")
        out = subprocess.run([_JBR_INIT, "-m", "x", target],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, target + "\n")

    def test_cli_error_exit_code(self):
        target = os.path.join(self.tmp, "cli")
        init_project(target)
        proc = subprocess.run([_JBR_INIT, target],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Error:", proc.stderr)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
