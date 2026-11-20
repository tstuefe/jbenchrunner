# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/common/project.py (PRD.md §4, §7)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import project  # noqa: E402


class _TmpBase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = patch.dict(os.environ, {"XDG_STATE_HOME": os.path.join(self.tmp, "state")})
        env.start()
        self.addCleanup(env.stop)

    def _make_project(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, project.MARKER), "w") as fh:
            fh.write("{}")
        return path


class TestFindProject(_TmpBase):

    def test_finds_marker_in_start_dir(self):
        p = self._make_project("p")
        self.assertEqual(project.find_project(p), p)

    def test_finds_marker_in_ancestor(self):
        p = self._make_project("p")
        deep = os.path.join(p, "results", "abc")
        os.makedirs(deep)
        self.assertEqual(project.find_project(deep), p)

    def test_defaults_to_cwd(self):
        p = self._make_project("p")
        cwd = os.getcwd()
        os.chdir(p)
        try:
            self.assertEqual(os.path.realpath(project.find_project()), p)
        finally:
            os.chdir(cwd)

    def test_outside_a_project_raises(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.assertIsNone(project.find_project_or_none(plain))
        with self.assertRaises(project.ProjectError) as ctx:
            project.find_project(plain)
        self.assertIn("jbr-init", str(ctx.exception))


class TestNewestFinishedBatch(_TmpBase):

    def _manifest(self, p, batchid, started_at, name=project.DONE_MANIFEST):
        d = project.batch_dir(p, batchid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as fh:
            json.dump({"batchid": batchid, "started_at": started_at}, fh)

    def test_none_without_batches(self):
        p = self._make_project("p")
        self.assertIsNone(project.newest_finished_batch(p))

    def test_newest_by_started_at_including_aborted(self):
        p = self._make_project("p")
        self._manifest(p, "a", "2026-01-01T00:00:00Z")
        self._manifest(p, "b", "2026-03-01T00:00:00Z", project.ABORTED_MANIFEST)
        self._manifest(p, "c", "2026-02-01T00:00:00Z")
        os.makedirs(project.batch_dir(p, "frozen-only"))  # no manifest: ignored
        path, batch, aborted = project.newest_finished_batch(p)
        self.assertEqual(batch["batchid"], "b")
        self.assertTrue(aborted)
        self.assertTrue(path.endswith(project.ABORTED_MANIFEST))


class TestActiveBatchLink(_TmpBase):

    def test_link_path_honours_xdg_state_home(self):
        self.assertEqual(project.active_batch_link(),
                         os.path.join(self.tmp, "state", "jbenchrunner", "active-batch"))

    def test_default_link_path_without_xdg(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_STATE_HOME")
            self.assertEqual(
                project.active_batch_link(),
                os.path.join(os.path.expanduser("~"), ".local", "state",
                             "jbenchrunner", "active-batch"))

    def test_set_and_clear(self):
        p = self._make_project("p")
        self.assertIsNone(project.active_batch_conflict(p))
        with open(project.batch_file(p), "w") as fh:
            fh.write("{}")
        project.set_active_batch(p)
        self.assertEqual(project.read_active_batch(), project.batch_file(p))
        self.assertIsNone(project.active_batch_conflict(p))
        self.assertTrue(project.clear_active_batch(p))
        self.assertIsNone(project.read_active_batch())

    def test_conflict_with_other_project(self):
        a = self._make_project("a")
        b = self._make_project("b")
        with open(project.batch_file(a), "w") as fh:
            fh.write("{}")
        project.set_active_batch(a)
        self.assertIn("another project", project.active_batch_conflict(b))
        self.assertFalse(project.clear_active_batch(b))
        self.assertEqual(project.read_active_batch(), project.batch_file(a))

    def test_dangling_link_is_a_conflict_and_clearable_anywhere(self):
        a = self._make_project("a")
        b = self._make_project("b")
        project.set_active_batch(a)  # a has no batch file: link dangles
        self.assertIn("no longer exists", project.active_batch_conflict(b))
        self.assertTrue(project.clear_active_batch(b))

    def test_set_replaces_existing_link(self):
        a = self._make_project("a")
        b = self._make_project("b")
        project.set_active_batch(a)
        project.set_active_batch(b)
        self.assertEqual(project.read_active_batch(), project.batch_file(b))


if __name__ == "__main__":
    unittest.main()
