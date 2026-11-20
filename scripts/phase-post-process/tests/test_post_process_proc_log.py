#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-post-process/post-process-proc-log.py"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "post-process-proc-log.py"

_spec = importlib.util.spec_from_file_location("post_process_proc_log", SCRIPT)
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)

STAT = ("4242 (my proc) S 1 4242 4242 0 -1 4194560 100 0 0 0 7 3 0 0 20 0 12 0 "
        "555 1000000 250 18446744073709551615 1 1 0 0 0 0 0 0 0 0 0 0 17 3 0 0 0 0 0 "
        "0 0 0 0 0 0 0 0\n")
STATUS = ("Name:\tjava\nState:\tS (sleeping)\nVmRSS:\t  1024 kB\nThreads:\t12\n"
          "Cpus_allowed:\tff\nBrandNewKey:\t1\n")


def sample(ts, pid=4242):
    return (f"{ts}\n=== /proc/{pid}/stat ===\n{STAT}"
            f"=== /proc/{pid}/status ===\n{STATUS}")


class ParseLogTest(unittest.TestCase):
    def _parse(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(text)
        warnings = []
        try:
            return pp.parse_log(f.name, warn=warnings.append), warnings
        finally:
            os.unlink(f.name)

    def test_record_shape(self):
        samples, _ = self._parse(sample("2026-09-19T09:37:20.000+02:00"))
        self.assertEqual(len(samples), 1)
        rec = samples[0]
        self.assertEqual(list(rec), ["timestamp", "pid", "proc_pid_stat", "status"])
        self.assertEqual(rec["timestamp"], "2026-09-19T09:37:20.000+02:00")
        self.assertEqual(rec["pid"], 4242)

    def test_stat_fields_with_space_in_comm(self):
        rec = self._parse(sample("2026-09-19T09:37:20.000+02:00"))[0][0]
        stat = rec["proc_pid_stat"]
        self.assertEqual(stat["comm"], "my proc")
        self.assertEqual(stat["state"], "S")
        self.assertEqual(stat["num_threads"], "12")
        self.assertEqual(stat["rss"], "250")
        self.assertEqual(len(stat), len(pp.STAT_FIELDS))

    def test_status_translation_ignores_and_unknown_warning(self):
        text = sample("2026-09-19T09:37:20.000+02:00") + sample("2026-09-19T09:37:30.000+02:00")
        samples, warnings = self._parse(text)
        self.assertEqual(len(samples), 2)
        status = samples[0]["status"]
        self.assertEqual(status, {"name": "java", "state": "S (sleeping)",
                                  "vm_rss": "1024 kB", "threads": "12"})
        # unknown key warned once across the whole file
        self.assertEqual(len([w for w in warnings if "BrandNewKey" in w]), 1)

    def test_sample_without_status_skipped(self):
        broken = ("2026-09-19T09:37:20.000+02:00\n=== /proc/4242/stat ===\n" + STAT)
        samples, warnings = self._parse(broken + sample("2026-09-19T09:37:30.000+02:00"))
        self.assertEqual([s["timestamp"] for s in samples], ["2026-09-19T09:37:30.000+02:00"])
        self.assertTrue(any("lacks" in w for w in warnings))

    def test_cut_off_last_sample_skipped(self):
        text = sample("2026-09-19T09:37:20.000+02:00") + sample("2026-09-19T09:37:30.000+02:00")
        samples, warnings = self._parse(text.rstrip("\n").rsplit("\n", 1)[0] + "\nThre")
        self.assertEqual(len(samples), 1)
        self.assertTrue(any("cut off" in w for w in warnings))

    def test_no_samples_exits(self):
        with self.assertRaises(SystemExit):
            self._parse("garbage\n")


if __name__ == "__main__":
    unittest.main()
