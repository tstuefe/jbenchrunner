# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# test_movecopy.py — unit tests for scripts/phase-other/movecopy.py
#
# Run with:
#   python3 -m unittest discover scripts

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_OTHER = os.path.dirname(_HERE)
_SCRIPTS_DIR = os.path.dirname(_PHASE_OTHER)
_COMMON_DIR = os.path.join(_SCRIPTS_DIR, "common")
for _p in (_PHASE_OTHER, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from movecopy import (  # noqa: E402
    find_collisions,
    plan_operations,
    remove_existing,
    run_movecopy,
    validate_destination_root,
)


class TestValidateDestinationRoot(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_nonexistent_path_ok(self):
        validate_destination_root(os.path.join(self.tmpdir, "does-not-exist"))

    def test_existing_directory_ok(self):
        validate_destination_root(self.tmpdir)

    def test_existing_file_rejected(self):
        path = os.path.join(self.tmpdir, "afile")
        with open(path, "w") as fh:
            fh.write("x")
        with self.assertRaises(ValueError):
            validate_destination_root(path)


class TestPlanOperations(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.results_dir = os.path.join(self.tmpdir, "results")
        self.to_dir = os.path.join(self.tmpdir, "archive")
        os.makedirs(self.results_dir)
        os.makedirs(os.path.join(self.results_dir, "RUN1"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_existing_source_planned(self):
        planned, missing = plan_operations(["RUN1"], self.results_dir, self.to_dir)
        self.assertEqual(missing, [])
        self.assertEqual(len(planned), 1)
        run_id, src, dst = planned[0]
        self.assertEqual(run_id, "RUN1")
        self.assertEqual(src, os.path.join(self.results_dir, "RUN1"))
        self.assertEqual(dst, os.path.join(self.to_dir, "RUN1"))

    def test_missing_source_reported(self):
        planned, missing = plan_operations(["NOPE"], self.results_dir, self.to_dir)
        self.assertEqual(planned, [])
        self.assertEqual(missing, ["NOPE"])

    def test_mixed(self):
        os.makedirs(os.path.join(self.results_dir, "RUN2"))
        planned, missing = plan_operations(
            ["RUN1", "NOPE", "RUN2"], self.results_dir, self.to_dir
        )
        self.assertEqual([p[0] for p in planned], ["RUN1", "RUN2"])
        self.assertEqual(missing, ["NOPE"])


class TestFindCollisions(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_collision(self):
        planned = [("RUN1", "/src/RUN1", os.path.join(self.tmpdir, "RUN1"))]
        self.assertEqual(find_collisions(planned), [])

    def test_collision_detected(self):
        dst = os.path.join(self.tmpdir, "RUN1")
        os.makedirs(dst)
        planned = [("RUN1", "/src/RUN1", dst)]
        self.assertEqual(find_collisions(planned), ["RUN1"])


class TestRemoveExisting(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_removes_directory(self):
        d = os.path.join(self.tmpdir, "d")
        os.makedirs(d)
        with open(os.path.join(d, "f"), "w") as fh:
            fh.write("x")
        remove_existing(d)
        self.assertFalse(os.path.exists(d))

    def test_removes_file(self):
        f = os.path.join(self.tmpdir, "f")
        with open(f, "w") as fh:
            fh.write("x")
        remove_existing(f)
        self.assertFalse(os.path.exists(f))


class TestRunMovecopy(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.results_dir = os.path.join(self.tmpdir, "results")
        self.to_dir = os.path.join(self.tmpdir, "archive")
        os.makedirs(self.results_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_run(self, run_id, content="data"):
        d = os.path.join(self.results_dir, run_id)
        os.makedirs(d)
        with open(os.path.join(d, "info.json"), "w") as fh:
            fh.write(content)
        return d

    def test_move_relocates_directory(self):
        self._make_run("RUN1", "hello")
        out = io.StringIO()
        with redirect_stdout(out):
            run_movecopy(["RUN1"], self.results_dir, self.to_dir, force=False, mode="move")

        self.assertFalse(os.path.exists(os.path.join(self.results_dir, "RUN1")))
        dst_info = os.path.join(self.to_dir, "RUN1", "info.json")
        self.assertTrue(os.path.exists(dst_info))
        with open(dst_info) as fh:
            self.assertEqual(fh.read(), "hello")
        self.assertIn("moved to", out.getvalue())

    def test_copy_leaves_source_in_place(self):
        self._make_run("RUN1", "hello")
        out = io.StringIO()
        with redirect_stdout(out):
            run_movecopy(["RUN1"], self.results_dir, self.to_dir, force=False, mode="copy")

        self.assertTrue(os.path.exists(os.path.join(self.results_dir, "RUN1", "info.json")))
        self.assertTrue(os.path.exists(os.path.join(self.to_dir, "RUN1", "info.json")))
        self.assertIn("copied to", out.getvalue())

    def test_missing_run_warns_and_is_skipped(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            run_movecopy(["NOPE"], self.results_dir, self.to_dir, force=False, mode="move")

        self.assertIn("Warning", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        # Nothing to do -> destination root is never created.
        self.assertFalse(os.path.exists(self.to_dir))

    def test_collision_without_force_raises_and_touches_nothing(self):
        self._make_run("RUN1", "hello")
        os.makedirs(os.path.join(self.to_dir, "RUN1"))
        with open(os.path.join(self.to_dir, "RUN1", "existing"), "w") as fh:
            fh.write("preexisting")

        with self.assertRaises(RuntimeError):
            run_movecopy(["RUN1"], self.results_dir, self.to_dir, force=False, mode="move")

        # Source untouched, and the pre-existing destination file untouched.
        self.assertTrue(os.path.exists(os.path.join(self.results_dir, "RUN1")))
        self.assertTrue(os.path.exists(os.path.join(self.to_dir, "RUN1", "existing")))

    def test_collision_with_force_replaces_destination(self):
        self._make_run("RUN1", "new-content")
        os.makedirs(os.path.join(self.to_dir, "RUN1"))
        with open(os.path.join(self.to_dir, "RUN1", "stale"), "w") as fh:
            fh.write("old")

        out = io.StringIO()
        with redirect_stdout(out):
            run_movecopy(["RUN1"], self.results_dir, self.to_dir, force=True, mode="move")

        self.assertFalse(os.path.exists(os.path.join(self.to_dir, "RUN1", "stale")))
        with open(os.path.join(self.to_dir, "RUN1", "info.json")) as fh:
            self.assertEqual(fh.read(), "new-content")

    def test_collision_on_one_run_aborts_the_whole_batch(self):
        # A batch of two runs where only one collides must move neither.
        self._make_run("RUN1", "one")
        self._make_run("RUN2", "two")
        os.makedirs(os.path.join(self.to_dir, "RUN2"))

        with self.assertRaises(RuntimeError):
            run_movecopy(
                ["RUN1", "RUN2"], self.results_dir, self.to_dir, force=False, mode="move"
            )

        # RUN1 (no collision) must still be untouched — all-or-nothing.
        self.assertTrue(os.path.exists(os.path.join(self.results_dir, "RUN1")))
        self.assertFalse(os.path.exists(os.path.join(self.to_dir, "RUN1")))


if __name__ == "__main__":
    unittest.main()
