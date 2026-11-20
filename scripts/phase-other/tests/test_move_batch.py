# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-other/move_batch.py and bin/jbr-move-batch (spec-other.md §6)."""

import contextlib
import io
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
_TOOL = os.path.abspath(os.path.join(_SCRIPTS_DIR, "..", "bin", "jbr-move-batch"))
for _p in (_PHASE_OTHER_DIR, _COMMON_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from init_project import init_project  # noqa: E402
from move_batch import run_move_batch  # noqa: E402


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)


class TestMoveBatch(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = init_project(os.path.join(self.tmp, "src"))

    def _batch(self, batchid, runids, manifest="batch.json", project=None):
        project = project or self.src
        bdir = os.path.join(project, "batches", batchid)
        _write_json(os.path.join(bdir, manifest), {
            "batchid": batchid,
            "runs": [{"runid": r, "result_dir": f"results/{r}"} for r in runids],
        })
        with open(os.path.join(bdir, "x.run.toml"), "w") as fh:
            fh.write("{}")
        for r in runids:
            _write_json(os.path.join(project, "results", r, "info.json"), {"runid": r})

    def _run(self, target, batchids, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            run_move_batch(self.src, target, batchids, **kw)
        return out.getvalue(), err.getvalue()

    def _exists(self, *parts):
        return os.path.exists(os.path.join(*parts))

    def test_moves_into_new_project(self):
        self._batch("b1", ["r1", "r2"])
        self._batch("b2", ["r3"])
        target = os.path.join(self.tmp, "new")
        out, err = self._run(target, ["b1"], description="split")
        with open(os.path.join(target, "jbr-project.json")) as fh:
            self.assertEqual(json.load(fh), {"description": "split"})
        for p in ("batches/b1/batch.json", "batches/b1/x.run.toml",
                  "results/r1/info.json", "results/r2/info.json"):
            self.assertTrue(self._exists(target, p), p)
        self.assertFalse(self._exists(self.src, "batches/b1"))
        self.assertFalse(self._exists(self.src, "results/r1"))
        self.assertTrue(self._exists(self.src, "batches/b2"))
        self.assertTrue(self._exists(self.src, "results/r3"))
        self.assertIn(f"created project {target}", out)
        self.assertIn("b1: moved to", out)
        self.assertIn("(2 runs)", out)
        self.assertEqual(err, "")

    def test_moves_into_existing_project_and_aborted_batch(self):
        target = init_project(os.path.join(self.tmp, "dst"))
        self._batch("old", ["r0"], project=target)
        self._batch("b1", ["r1"], manifest="batch-aborted.json")
        self._run(target, ["b1", "b1"])
        self.assertTrue(self._exists(target, "batches/b1/batch-aborted.json"))
        self.assertTrue(self._exists(target, "results/r1"))
        self.assertTrue(self._exists(target, "results/r0"))

    def test_missing_run_warns_and_continues(self):
        self._batch("b1", ["r1", "r2"])
        shutil.rmtree(os.path.join(self.src, "results", "r2"))
        target = os.path.join(self.tmp, "new")
        out, err = self._run(target, ["b1"])
        self.assertIn("Warning:", err)
        self.assertIn("results/r2", err)
        self.assertIn("(1 runs)", out)
        self.assertTrue(self._exists(target, "results/r1"))

    def test_batch_with_no_existing_runs_fails(self):
        self._batch("b1", ["r1"])
        self._batch("b2", ["r2", "r3"])
        shutil.rmtree(os.path.join(self.src, "results", "r2"))
        shutil.rmtree(os.path.join(self.src, "results", "r3"))
        self._assert_fails(os.path.join(self.tmp, "new"), ["b1", "b2"],
                           "none of the runs")

    def test_wrong_result_dir_prefix_fails(self):
        # Manifest says runs/<RUNID>, but the run lives in results/<RUNID>.
        self._batch("b1", ["r1"])
        _write_json(os.path.join(self.src, "batches", "b1", "batch.json"),
                    {"runs": [{"result_dir": "runs/r1"}]})
        self._assert_fails(os.path.join(self.tmp, "new"), ["b1"], "b1")

    def test_batch_listing_no_runs_is_moved(self):
        _write_json(os.path.join(self.src, "batches", "b1", "batch.json"), {"runs": []})
        target = os.path.join(self.tmp, "new")
        out, _ = self._run(target, ["b1"])
        self.assertIn("(0 runs)", out)
        self.assertTrue(self._exists(target, "batches/b1/batch.json"))

    def test_dry_run_changes_nothing(self):
        self._batch("b1", ["r1"])
        target = os.path.join(self.tmp, "new")
        out, _ = self._run(target, ["b1"], dry_run=True)
        self.assertIn("would create project", out)
        self.assertIn("b1: would move to", out)
        self.assertFalse(os.path.exists(target))
        self.assertTrue(self._exists(self.src, "batches/b1"))
        self.assertTrue(self._exists(self.src, "results/r1"))

    def _assert_fails(self, target, batchids, needle, **kw):
        before = sorted(os.walk(self.tmp))
        with self.assertRaises(RuntimeError) as ctx:
            self._run(target, batchids, **kw)
        self.assertIn(needle, str(ctx.exception))
        self.assertEqual(sorted(os.walk(self.tmp)), before)

    def test_unknown_batch_fails_whole_invocation(self):
        self._batch("b1", ["r1"])
        self._assert_fails(os.path.join(self.tmp, "new"), ["b1", "nope"], "no such batch")

    def test_batch_without_manifest_fails(self):
        os.makedirs(os.path.join(self.src, "batches", "b1"))
        self._assert_fails(os.path.join(self.tmp, "new"), ["b1"], "has no batch.json")

    def test_in_progress_batch_refused(self):
        self._batch("b1", ["r1"])
        os.makedirs(os.path.join(self.src, "batches", "b2"))
        _write_json(os.path.join(self.src, "BATCH_IN_PROGRESS.json"), {"batchid": "b2"})
        self._assert_fails(os.path.join(self.tmp, "new"), ["b1", "b2"], "in progress")

    def test_other_batch_movable_while_one_in_progress(self):
        self._batch("b1", ["r1"])
        _write_json(os.path.join(self.src, "BATCH_IN_PROGRESS.json"), {"batchid": "b2"})
        target = os.path.join(self.tmp, "new")
        self._run(target, ["b1"])
        self.assertTrue(self._exists(target, "batches/b1"))

    def test_unreadable_batch_in_progress_refuses(self):
        self._batch("b1", ["r1"])
        with open(os.path.join(self.src, "BATCH_IN_PROGRESS.json"), "w") as fh:
            fh.write("{broken")
        self._assert_fails(os.path.join(self.tmp, "new"), ["b1"], "cannot read")

    def test_target_is_source(self):
        self._batch("b1", ["r1"])
        self._assert_fails(self.src, ["b1"], "source project itself")

    def test_target_inside_source(self):
        self._batch("b1", ["r1"])
        self._assert_fails(os.path.join(self.src, "sub"), ["b1"], "nested")

    def test_existing_non_project_target(self):
        self._batch("b1", ["r1"])
        os.makedirs(os.path.join(self.tmp, "plain"))
        self._assert_fails(os.path.join(self.tmp, "plain"), ["b1"], "not a project")

    def test_description_with_existing_target(self):
        self._batch("b1", ["r1"])
        target = init_project(os.path.join(self.tmp, "dst"))
        self._assert_fails(target, ["b1"], "description", description="x")

    def test_collisions_named_and_nothing_moved(self):
        target = init_project(os.path.join(self.tmp, "dst"))
        self._batch("b1", ["r1"], project=target)
        self._batch("b1", ["r1"])
        with self.assertRaises(RuntimeError) as ctx:
            self._run(target, ["b1"])
        msg = str(ctx.exception)
        self.assertIn("batches/b1", msg)
        self.assertIn("results/r1", msg)
        self.assertTrue(self._exists(self.src, "batches/b1"))
        self.assertTrue(self._exists(self.src, "results/r1"))


class TestJbrMoveBatchCli(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _cli(self, *args, cwd):
        return subprocess.run([_TOOL, *args], cwd=cwd,
                              capture_output=True, text=True)

    def test_help(self):
        r = self._cli("--help", cwd=self.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--to", r.stdout)

    def test_requires_batchid(self):
        r = self._cli("--to", "x", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)

    def test_outside_project_fails(self):
        r = self._cli("--to", "x", "b1", cwd=self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not inside a jbr project", r.stderr)

    def test_end_to_end(self):
        src = init_project(os.path.join(self.tmp, "src"))
        _write_json(os.path.join(src, "batches", "b1", "batch.json"),
                    {"runs": [{"result_dir": "results/r1"}]})
        _write_json(os.path.join(src, "results", "r1", "info.json"), {})
        r = self._cli("--to", "../dst", "b1", cwd=src)
        self.assertEqual(r.returncode, 0, r.stderr)
        dst = os.path.join(self.tmp, "dst")
        self.assertTrue(os.path.isfile(os.path.join(dst, "batches", "b1", "batch.json")))
        self.assertTrue(os.path.isdir(os.path.join(dst, "results", "r1")))


if __name__ == "__main__":
    unittest.main()
