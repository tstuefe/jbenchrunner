#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-post-process/post-process-system-map-log.py"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "post-process-system-map-log.py"

_spec = importlib.util.spec_from_file_location("post_process_system_map_log", SCRIPT)
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)

HEADER = """\
12266:
Memory mappings:
from, to, vsize: address range and size

from               to                        vsize prot          rss      hugetlb pgsz notes            info                                  file
========================================================================================================================================================================
"""
FOOTER = """
Number of mappings: 3
             vsize: 999 (999B)
"""


def row(vsize, rss, info, notes="com", file="-", prot="rw-p"):
    return (f"0x0000000045000000-0x0000000045001000 {vsize:>12} {prot} {rss:>12} "
            f"           0 4K   {notes:<16} {info:<37} {file}\n")


def scan(body):
    return pp.scan_table((HEADER + body + FOOTER).splitlines(keepends=True))


class TestScanTable(unittest.TestCase):

    def test_sums_per_category_lowercased(self):
        r = scan(row(100, 10, "CDS") + row(200, 20, "CDS") + row(50, 5, "JAVAHEAP"))
        self.assertEqual(r, {"cds": {"vsize": 300, "rss": 30},
                             "javaheap": {"vsize": 50, "rss": 5}})

    def test_dash_is_unknown(self):
        r = scan(row(100, 10, "-", file="/lib/x.bin") + row(1, 1, "-"))
        self.assertEqual(r, {"unknown": {"vsize": 101, "rss": 11}})

    def test_shared_libraries_split_by_permissions(self):
        r = scan(row(100, 10, "-", file="/lib/libc.so.6", prot="r-xp")
                 + row(20, 2, "-", file="/lib/libc.so.6", prot="r--p")
                 + row(30, 3, "-", file="/lib/libfoo.so", prot="rw-p")
                 + row(4, 1, "-", file="/lib/libbar.so.1.2.3", prot="r-xp"))
        self.assertEqual(r, {"sharedlib_text": {"vsize": 104, "rss": 11},
                             "sharedlib_data": {"vsize": 50, "rss": 5}})

    def test_shared_library_with_info_keeps_info_category(self):
        r = scan(row(10, 1, "CODE", file="/lib/libx.so", prot="r-xp"))
        self.assertEqual(r, {"code": {"vsize": 10, "rss": 1}})

    def test_non_shared_library_file_is_unknown(self):
        r = scan(row(10, 1, "-", file="/lib/libx.so.bak") + row(5, 1, "-", file="/usr/lib/x.sort"))
        self.assertEqual(r, {"unknown": {"vsize": 15, "rss": 2}})

    def test_stacks_combined(self):
        r = scan(row(10, 1, "STACK-12350-VM-Thread") + row(20, 2, "STACK-3-http-nio-8080-Acceptor"))
        self.assertEqual(r, {"stacks": {"vsize": 30, "rss": 3}})

    def test_comma_is_combined_and_beats_stack(self):
        r = scan(row(10, 1, "CODE,GC") + row(5, 5, "STACK-1-x,GC"))
        self.assertEqual(r, {"combined": {"vsize": 15, "rss": 6}})

    def test_notes_with_comma_do_not_shift_columns(self):
        r = scan(row(10, 1, "META", notes="com,nothp"))
        self.assertEqual(r, {"meta": {"vsize": 10, "rss": 1}})

    def test_file_with_spaces(self):
        r = scan(row(10, 1, "-", file="/mnt/my dir/lib x.so"))
        self.assertEqual(r, {"sharedlib_data": {"vsize": 10, "rss": 1}})

    def test_mt_prefix_stripped_and_camel_cased(self):
        r = scan(row(10, 1, "mtChunkMmap") + row(20, 2, "mtChunkMeta"))
        self.assertEqual(r, {"chunk_mmap": {"vsize": 10, "rss": 1},
                             "chunk_meta": {"vsize": 20, "rss": 2}})

    def test_mt_prefix_kept_when_not_followed_by_uppercase(self):
        r = scan(row(10, 1, "mtfoo"))
        self.assertEqual(r, {"mtfoo": {"vsize": 10, "rss": 1}})

    def test_stops_at_blank_line(self):
        text = HEADER + row(10, 1, "CDS") + "\n" + row(999, 999, "GC") + FOOTER
        self.assertEqual(pp.scan_table(text.splitlines(keepends=True)),
                         {"cds": {"vsize": 10, "rss": 1}})

    def test_missing_summary_is_incomplete(self):
        text = HEADER + row(10, 1, "CDS") + "\n"
        self.assertIsNone(pp.scan_table(text.splitlines(keepends=True)))

    def test_no_table_returns_none(self):
        self.assertIsNone(pp.scan_table(["garbage\n", "more\n"]))

    def test_malformed_row_skipped_with_warning(self):
        warnings = []
        text = HEADER + "not a row\n" + row(10, 1, "CDS") + FOOTER
        r = pp.scan_table(text.splitlines(keepends=True), warnings.append)
        self.assertEqual(r, {"cds": {"vsize": 10, "rss": 1}})
        self.assertEqual(len(warnings), 1)

    def test_real_example_totals_match_summary(self):
        example = (Path(__file__).resolve().parents[3]
                   / "ai" / "examples" / "example-spring-system.map.txt")
        if not example.exists():
            self.skipTest("example file not available")
        with open(example) as f:
            lines = f.readlines()
        r = pp.scan_table(lines)
        self.assertIn("stacks", r)
        self.assertIn("unknown", r)
        total_vsize = sum(v["vsize"] for v in r.values())
        summary = next(l for l in lines if l.strip().startswith("vsize:"))
        self.assertEqual(total_vsize, int(summary.split()[1]))


SAMPLE_HEADER = "2026-09-21T10:00:00.000+02:00\n"


def sample_text(body, ts="2026-09-21T10:00:00.000+02:00"):
    return ts + "\n" + HEADER + body + FOOTER


class TestParseLog(unittest.TestCase):

    def _parse(self, text, warnings=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "system.map.log"
            path.write_text(text)
            return pp.parse_log(str(path), warnings.append if warnings is not None else None)

    def test_two_samples(self):
        text = (sample_text(row(100, 10, "CDS"), "2026-09-21T10:00:00.000+02:00")
                + sample_text(row(200, 20, "CDS") + row(7, 7, "-"), "2026-09-21T10:00:10.000+02:00"))
        recs = self._parse(text)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0], {"timestamp": "2026-09-21T10:00:00.000+02:00", "pid": 12266,
                                   "total": {"vsize": 100, "rss": 10},
                                   "cds": {"vsize": 100, "rss": 10}})
        self.assertEqual(recs[1]["cds"], {"vsize": 200, "rss": 20})
        self.assertEqual(recs[1]["unknown"], {"vsize": 7, "rss": 7})
        self.assertEqual(recs[1]["total"], {"vsize": 207, "rss": 27})

    def test_truncated_sample_skipped_with_warning(self):
        warnings = []
        text = (sample_text(row(100, 10, "CDS"), "2026-09-21T10:00:00.000+02:00")
                + "2026-09-21T10:00:10.000+02:00\n" + HEADER + row(5, 5, "GC"))  # cut off
        recs = self._parse(text, warnings)
        self.assertEqual(len(recs), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2026-09-21T10:00:10.000+02:00", warnings[0])

    def test_failed_jcmd_sample_skipped(self):
        warnings = []
        text = (sample_text(row(100, 10, "CDS"))
                + "2026-09-21T10:00:10.000+02:00\n12266:\nSome error\n")
        recs = self._parse(text, warnings)
        self.assertEqual(len(recs), 1)
        self.assertEqual(len(warnings), 1)

    def test_no_timestamps_exits(self):
        with self.assertRaises(SystemExit):
            self._parse("garbage\n")

    def test_no_valid_sample_exits(self):
        with self.assertRaises(SystemExit):
            self._parse("2026-09-21T10:00:10.000+02:00\n" + HEADER)

    def test_zst_input(self):
        import shutil
        if shutil.which("zstd") is None:
            self.skipTest("zstd not available")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "system.map.log"
            path.write_text(sample_text(row(100, 10, "CDS")))
            subprocess.run(["zstd", "-q", "--rm", str(path)], check=True)
            recs = pp.parse_log(str(path) + ".zst")
        self.assertEqual(recs[0]["cds"], {"vsize": 100, "rss": 10})

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "system.map.log"
            path.write_text(sample_text(row(100, 10, "CDS")))
            res = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["cds"], {"vsize": 100, "rss": 10})


if __name__ == "__main__":
    unittest.main()
