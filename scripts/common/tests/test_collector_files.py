#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/common/collector_files.py"""

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.collector_files import (compress_file, open_collector_file,  # noqa: E402
                                    resolve_collector_file)

HAVE_ZSTD = shutil.which("zstd") is not None


def _compress(path_plain, path_zst):
    with open(path_zst, "wb") as out:
        subprocess.run(["zstd", "-1", "-q", "-c", path_plain], stdout=out, check=True)


class TestResolveCollectorFile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.warnings = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _touch(self, name):
        with open(os.path.join(self.tmp, name), "w") as f:
            f.write("x\n")

    def _resolve(self):
        return resolve_collector_file(self.tmp, "nmt.log", warn=self.warnings.append)

    def test_absent_returns_none(self):
        self.assertIsNone(self._resolve())
        self.assertEqual(self.warnings, [])

    def test_plain(self):
        self._touch("nmt.log")
        self.assertEqual(self._resolve(), os.path.join(self.tmp, "nmt.log"))
        self.assertEqual(self.warnings, [])

    def test_compressed(self):
        self._touch("nmt.log.zst")
        self.assertEqual(self._resolve(), os.path.join(self.tmp, "nmt.log.zst"))
        self.assertEqual(self.warnings, [])

    def test_both_plain_wins_with_warning(self):
        self._touch("nmt.log")
        self._touch("nmt.log.zst")
        self.assertEqual(self._resolve(), os.path.join(self.tmp, "nmt.log"))
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("plain", self.warnings[0])


class TestOpenPlain(unittest.TestCase):

    def test_reads_plain_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.log")
            with open(path, "w") as f:
                f.write("one\ntwo\n")
            with open_collector_file(path) as fh:
                self.assertEqual(fh.read().splitlines(), ["one", "two"])


@unittest.skipUnless(HAVE_ZSTD, "requires the zstd binary")
class TestOpenCompressed(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.warnings = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _make(self, text):
        plain = os.path.join(self.tmp, "a.log")
        with open(plain, "w") as f:
            f.write(text)
        zst = plain + ".zst"
        _compress(plain, zst)
        os.remove(plain)
        return zst

    def test_reads_whole_file(self):
        path = self._make("sample-1\nsample-2\n")
        with open_collector_file(path, warn=self.warnings.append) as fh:
            self.assertEqual(fh.read(), "sample-1\nsample-2\n")
        self.assertEqual(self.warnings, [])

    def test_iterates_lines(self):
        path = self._make("a\nb\nc\n")
        with open_collector_file(path) as fh:
            self.assertEqual([ln.rstrip("\n") for ln in fh], ["a", "b", "c"])

    def test_early_close_does_not_hang_or_warn(self):
        path = self._make("".join(f"line {i}\n" for i in range(100000)))
        with open_collector_file(path, warn=self.warnings.append) as fh:
            self.assertEqual(fh.readline(), "line 0\n")
        self.assertEqual(self.warnings, [])

    def test_truncated_file_yields_data_up_to_the_cut_and_warns(self):
        # Incompressible lines, so the frame has several 128 KB blocks; cut it in the middle.
        text = "".join(base64.b64encode(os.urandom(60)).decode() + "\n" for _ in range(20000))
        path = self._make(text)
        with open(path, "rb") as f:
            data = f.read()
        with open(path, "wb") as f:
            f.write(data[: len(data) * 6 // 10])
        with open_collector_file(path, warn=self.warnings.append) as fh:
            got = fh.read()
        self.assertGreater(len(got), 100_000)
        self.assertLess(len(got), len(text))
        self.assertTrue(text.startswith(got[: got.rfind("\n") + 1]))
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("truncated", self.warnings[0])

    def test_corrupt_file_raises(self):
        path = os.path.join(self.tmp, "bad.log.zst")
        with open(path, "wb") as f:
            f.write(b"this is not a zstd frame at all")
        with self.assertRaises(ValueError):
            with open_collector_file(path) as fh:
                fh.read()


class TestOpenCompressedWithoutZstd(unittest.TestCase):

    def test_missing_binary_raises_runtime_error(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError("zstd")):
            with self.assertRaises(RuntimeError) as ctx:
                with open_collector_file("/x/nmt.log.zst"):
                    pass
        self.assertIn("zstd", str(ctx.exception))


@unittest.skipUnless(HAVE_ZSTD, "zstd binary not installed")
class TestCompressFile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "gc.log")
        with open(self.path, "w") as f:
            f.write("line\n" * 1000)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_compresses_and_removes_original(self):
        self.assertIsNone(compress_file(self.path))
        self.assertFalse(os.path.exists(self.path))
        with open_collector_file(self.path + ".zst") as fh:
            self.assertEqual(fh.read(), "line\n" * 1000)

    def test_replaces_stale_compressed_file(self):
        with open(self.path + ".zst", "wb") as f:
            f.write(b"stale")
        self.assertIsNone(compress_file(self.path))
        with open_collector_file(self.path + ".zst") as fh:
            self.assertEqual(fh.read(), "line\n" * 1000)

    def test_missing_file_returns_error(self):
        err = compress_file(os.path.join(self.tmp, "absent.log"))
        self.assertIn("zstd failed", err)


class TestCompressFileWithoutZstd(unittest.TestCase):

    def test_missing_binary_returns_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("zstd")):
            err = compress_file("/x/gc.log")
        self.assertIn("not found", err)


if __name__ == "__main__":
    unittest.main()
