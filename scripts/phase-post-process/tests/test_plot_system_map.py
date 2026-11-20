#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-post-process/tests/test_plot_system_map.py
#
# Tests for scripts/phase-post-process/plot-system-map.py (parse step only;
# rendering needs gnuplot).

import importlib.util
import json
import os
import sys
import tempfile
import unittest

_PHASE_PP_DIR = os.path.join(os.path.dirname(__file__), "..")
_COMMON_DIR = os.path.join(_PHASE_PP_DIR, "..", "common")
sys.path.insert(0, _PHASE_PP_DIR)
sys.path.insert(0, _COMMON_DIR)

_SPEC = importlib.util.spec_from_file_location(
    "plot_system_map", os.path.join(_PHASE_PP_DIR, "plot-system-map.py"))
psm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(psm)

_MB = 1024 * 1024


def _entry(mb):
    return {"vsize": 10 * mb * _MB, "rss": mb * _MB}


class ParseTest(unittest.TestCase):
    def _parse(self, lines):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
        try:
            return psm.parse(f.name)
        finally:
            os.unlink(f.name)

    def test_series_other_combined_and_elapsed(self):
        s1 = {"timestamp": "2026-09-19T09:37:20.000+02:00", "pid": 1,
              "javaheap": _entry(4), "arena": _entry(2), "stack": _entry(3),
              "combined": _entry(1), "weird": _entry(5)}
        s2 = {"timestamp": "2026-09-19T09:37:22.000+02:00", "pid": 1,
              "javaheap": _entry(6)}
        points = self._parse([json.dumps(s1), "garbage", json.dumps(s2)])
        self.assertEqual([p[0] for p in points], [0.0, 2.0])
        names = [n for n, _, _ in psm._SERIES]
        v1 = points[0][1]
        self.assertEqual(v1[names.index("Java Heap")], 4.0)
        self.assertEqual(v1[names.index("Arenas")], 2.0)
        # stack + combined + weird -> Other/Combined
        self.assertEqual(v1[names.index("Other/Combined")], 9.0)
        self.assertEqual(points[1][1][names.index("Other/Combined")], 0.0)

    def test_total_key_excluded_from_other_combined(self):
        s1 = {"timestamp": "2026-09-19T09:37:20.000+02:00", "pid": 1,
              "total": _entry(999), "javaheap": _entry(4), "weird": _entry(5)}
        points = self._parse([json.dumps(s1)])
        names = [n for n, _, _ in psm._SERIES]
        v1 = points[0][1]
        self.assertEqual(v1[names.index("Java Heap")], 4.0)
        self.assertEqual(v1[names.index("Other/Combined")], 5.0)  # weird only, not total

    def test_mt_chunk_categories_fold_into_arenas(self):
        s1 = {"timestamp": "2026-09-19T09:37:20.000+02:00", "pid": 1,
              "arena": _entry(2), "chunk": _entry(3), "chunk_mmap": _entry(4),
              "chunk_meta": _entry(1), "weird": _entry(5)}
        points = self._parse([json.dumps(s1)])
        names = [n for n, _, _ in psm._SERIES]
        v1 = points[0][1]
        self.assertEqual(v1[names.index("Arenas")], 10.0)  # arena+chunk+chunk_mmap+chunk_meta
        self.assertEqual(v1[names.index("Other/Combined")], 5.0)  # weird only

    def test_jvm_malloc_split_from_unknown(self):
        def sm(ts, unknown_mb):
            return json.dumps({"timestamp": ts, "pid": 1,
                               "unknown": _entry(unknown_mb)})
        sm_lines = [sm("2026-09-19T09:37:20.000+02:00", 100),
                    sm("2026-09-19T09:37:30.000+02:00", 10),   # clamp case
                    sm("2026-09-19T09:38:30.000+02:00", 50)]   # no nmt within gap
        nmt_lines = [json.dumps({"timestamp": "2026-09-19T09:37:21.000+02:00",
                                 "total_malloc": 30.0}),
                     json.dumps({"timestamp": "2026-09-19T09:37:29.000+02:00",
                                 "total_malloc": 40.0})]
        with tempfile.TemporaryDirectory() as d:
            sm_path = os.path.join(d, "system.map.jsonl")
            nmt_path = os.path.join(d, "nmt.jsonl")
            open(sm_path, "w").write("\n".join(sm_lines) + "\n")
            open(nmt_path, "w").write("\n".join(nmt_lines) + "\n")
            points = psm.parse(sm_path, nmt_path)
            nonmt = psm.parse(sm_path, None)
        keys = [k for _, k, _ in psm._SERIES]
        j, u = keys.index("jvm_malloc"), keys.index("unknown")
        self.assertEqual((points[0][1][j], points[0][1][u]), (30.0, 70.0))
        self.assertEqual((points[1][1][j], points[1][1][u]), (40.0, 0.0))
        self.assertEqual((points[2][1][j], points[2][1][u]), (0.0, 50.0))
        self.assertEqual(nonmt[0][1][j], 0.0)
        self.assertEqual(nonmt[0][1][u], 100.0)
        self.assertLess(j, u)


if __name__ == "__main__":
    unittest.main()
