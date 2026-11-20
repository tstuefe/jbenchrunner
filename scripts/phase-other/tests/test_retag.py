# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# test_retag.py — unit tests for scripts/phase-other/retag.py
#
# Run with:
#   python3 -m unittest discover scripts

import io
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_OTHER = os.path.dirname(_HERE)
if _PHASE_OTHER not in sys.path:
    sys.path.insert(0, _PHASE_OTHER)

from retag import (  # noqa: E402
    apply_tag_ops,
    atomic_write_json,
    check_batch_in_progress,
    load_info_json,
    resolve_runids,
    validate_runid,
    validate_tag,
)


class TestResolveRunids(unittest.TestCase):
    def test_positional_args_used_when_given(self):
        result = resolve_runids(["RUN1", "RUN2"], stdin_is_tty=True)
        self.assertEqual(result, ["RUN1", "RUN2"])

    def test_positional_args_take_priority_over_stdin(self):
        # stdin_is_tty=False would normally mean "read stdin", but
        # positional args are given so stdin must not be touched.
        result = resolve_runids(["RUN1"], stdin_is_tty=False)
        self.assertEqual(result, ["RUN1"])

    def test_reads_stdin_when_no_positional_args_and_not_a_tty(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("RUN1\nRUN2\n\n")
        try:
            result = resolve_runids([], stdin_is_tty=False)
        finally:
            sys.stdin = old_stdin
        self.assertEqual(result, ["RUN1", "RUN2"])

    def test_empty_piped_stdin_yields_empty_list_not_error(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            result = resolve_runids([], stdin_is_tty=False)
        finally:
            sys.stdin = old_stdin
        self.assertEqual(result, [])

    def test_no_args_and_tty_stdin_raises(self):
        with self.assertRaises(ValueError):
            resolve_runids([], stdin_is_tty=True)

    def test_invalid_positional_runid_raises(self):
        with self.assertRaises(ValueError):
            resolve_runids(["RUN1", "../x"], stdin_is_tty=True)

    def test_invalid_stdin_runid_raises(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("RUN1\nresults/RUN2\n")
        try:
            with self.assertRaises(ValueError):
                resolve_runids([], stdin_is_tty=False)
        finally:
            sys.stdin = old_stdin


class TestValidateRunid(unittest.TestCase):
    def test_bare_name_ok(self):
        validate_runid("228d007f-0546-4a17-9584-d91a448918d9")

    def test_invalid_rejected(self):
        for bad in ("", ".", "..", "../x", "a/b", "/abs", "a\0b"):
            with self.subTest(runid=bad):
                with self.assertRaises(ValueError):
                    validate_runid(bad)


class TestValidateTag(unittest.TestCase):
    def test_valid_tag_ok(self):
        validate_tag("gc-comparison-2025")  # must not raise

    def test_empty_tag_rejected(self):
        with self.assertRaises(ValueError):
            validate_tag("")

    def test_comma_in_tag_rejected(self):
        with self.assertRaises(ValueError):
            validate_tag("foo,bar")


class TestApplyTagOps(unittest.TestCase):
    def test_add_new_tag(self):
        new_tags, lines = apply_tag_ops(["a"], adds=["b"], removes=[])
        self.assertEqual(new_tags, ["a", "b"])
        self.assertEqual(lines, ["+b"])

    def test_add_existing_tag_is_silent_noop(self):
        new_tags, lines = apply_tag_ops(["a"], adds=["a"], removes=[])
        self.assertEqual(new_tags, ["a"])
        self.assertEqual(lines, [])

    def test_remove_existing_tag(self):
        new_tags, lines = apply_tag_ops(["a", "b"], adds=[], removes=["a"])
        self.assertEqual(new_tags, ["b"])
        self.assertEqual(lines, ["-a"])

    def test_remove_absent_tag_notes_not_present(self):
        new_tags, lines = apply_tag_ops(["a"], adds=[], removes=["z"])
        self.assertEqual(new_tags, ["a"])
        self.assertEqual(lines, ["-z (not present)"])

    def test_remove_then_add_same_tag_ends_up_present(self):
        # -r X -a X : remove-then-add ordering means X ends up present.
        new_tags, lines = apply_tag_ops(["x"], adds=["x"], removes=["x"])
        self.assertEqual(new_tags, ["x"])
        self.assertEqual(lines, ["-x", "+x"])

    def test_multiple_adds_appended_in_order(self):
        new_tags, lines = apply_tag_ops([], adds=["b", "a"], removes=[])
        self.assertEqual(new_tags, ["b", "a"])
        self.assertEqual(lines, ["+b", "+a"])

    def test_existing_tag_order_preserved(self):
        new_tags, _ = apply_tag_ops(["x", "y", "z"], adds=["w"], removes=["y"])
        self.assertEqual(new_tags, ["x", "z", "w"])

    def test_no_ops_is_unchanged(self):
        new_tags, lines = apply_tag_ops(["a"], adds=[], removes=[])
        self.assertEqual(new_tags, ["a"])
        self.assertEqual(lines, [])


class TestCheckBatchInProgress(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_batch_file_ok(self):
        check_batch_in_progress(self.tmpdir, force=False)  # must not raise

    def test_batch_file_present_raises(self):
        with open(os.path.join(self.tmpdir, "BATCH_IN_PROGRESS.json"), "w") as fh:
            fh.write("{}")
        with self.assertRaises(RuntimeError):
            check_batch_in_progress(self.tmpdir, force=False)

    def test_batch_file_present_but_forced_ok(self):
        with open(os.path.join(self.tmpdir, "BATCH_IN_PROGRESS.json"), "w") as fh:
            fh.write("{}")
        check_batch_in_progress(self.tmpdir, force=True)  # must not raise


class TestLoadInfoJson(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_loads_valid_info_json(self):
        with open(os.path.join(self.tmpdir, "info.json"), "w") as fh:
            json.dump({"runid": "abc", "tags": ["x"]}, fh)
        info = load_info_json(self.tmpdir)
        self.assertEqual(info["runid"], "abc")
        self.assertEqual(info["tags"], ["x"])

    def test_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            load_info_json(self.tmpdir)

    def test_malformed_json_raises_valueerror(self):
        with open(os.path.join(self.tmpdir, "info.json"), "w") as fh:
            fh.write("{not valid json")
        with self.assertRaises(ValueError):
            load_info_json(self.tmpdir)


class TestAtomicWriteJson(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_and_read_back(self):
        path = os.path.join(self.tmpdir, "info.json")
        atomic_write_json(path, {"runid": "abc", "tags": ["a", "b"]})
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data, {"runid": "abc", "tags": ["a", "b"]})

    def test_no_leftover_temp_files(self):
        path = os.path.join(self.tmpdir, "info.json")
        atomic_write_json(path, {"a": 1})
        remaining = os.listdir(self.tmpdir)
        self.assertEqual(remaining, ["info.json"])

    def test_overwrites_existing_file(self):
        path = os.path.join(self.tmpdir, "info.json")
        atomic_write_json(path, {"tags": ["old"]})
        atomic_write_json(path, {"tags": ["new"]})
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data, {"tags": ["new"]})


if __name__ == "__main__":
    unittest.main()
