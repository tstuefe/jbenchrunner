#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-post-process/post-process-thread-print-log.py"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "post-process-thread-print-log.py"

_spec = importlib.util.spec_from_file_location("post_process_thread_print_log", SCRIPT)
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)

HEADER = """\
1376531:
2026-09-22 20:29:04
Full thread dump OpenJDK 64-Bit Server VM (28-internal-adhoc.user.source mixed mode, sharing)

Threads class SMR info:
_java_thread_list=0x00007fff8c0024c0, length=12, elements={
0x00007ffff001c1c0, 0x00007ffff01c33d0
}

"""
FOOTER = "JNI global refs: 9, weak refs: 0\n"


def java_thread(name, tid, cpu="1.50ms", state="RUNNABLE", frames=1, extra="", elapsed="0.35s"):
    text = (f'"{name}" #3 [{tid}] daemon prio=5 os_prio=0 cpu={cpu} elapsed={elapsed} '
            f"tid=0x00007ffff001c1c0 nid={tid} runnable  [0x00007ffff61fd000]\n"
            f"   java.lang.Thread.State: {state}\n")
    text += extra
    text += "\tat java.lang.Object.wait0(java.base@28-internal/Native Method)\n" * frames
    return text + "\n"


def vm_thread(name, nid, cpu="2.00ms", elapsed="1.48s"):
    return (f'"{name}" os_prio=0 cpu={cpu} elapsed={elapsed} tid=0x00007ffff0056e20 '
            f"nid={nid} runnable  \n\n")


def scan(body):
    return pp.scan_dump((HEADER + body + FOOTER).splitlines(keepends=True))


def sample_text(body, ts="2026-09-22T20:29:03.000+02:00"):
    return ts + "\n" + HEADER + body + FOOTER


class TestCategory(unittest.TestCase):

    def test_compiler_threads(self):
        self.assertEqual(pp.category_for("C2 CompilerThread0", "RUNNABLE", False), "c2")
        self.assertEqual(pp.category_for("C1 CompilerThread12", "RUNNABLE", False), "c1")

    def test_gc_threads(self):
        for name in ("GC Thread#0", "G1 Conc#1", "G1 Main Marker", "G1 Refine Control",
                     "Shenandoah Control Thread", "ZWorkerYoung#0", "ZDirector"):
            self.assertEqual(pp.category_for(name, None, False), "gc", name)

    def test_gc_names_only_for_native_threads(self):
        self.assertEqual(pp.category_for("ZooKeeper-1", None, False), "other")
        self.assertEqual(pp.category_for("ZKWorker", "RUNNABLE", True), "mutator")

    def test_mutator_needs_state_and_frame(self):
        self.assertEqual(pp.category_for("main", "RUNNABLE", True), "mutator")
        self.assertEqual(pp.category_for("Signal Dispatcher", "RUNNABLE", False),
                         "other")
        self.assertEqual(pp.category_for("VM Thread", None, False), "other")

    def test_jdk_internal_threads_are_not_mutators(self):
        for name in ("Reference Handler", "Finalizer", "Common-Cleaner"):
            self.assertEqual(pp.category_for(name, "WAITING", True), "other", name)


class TestScanDump(unittest.TestCase):

    def test_java_and_vm_threads(self):
        r = scan(java_thread("main", 1376533, cpu="148.76ms")
                 + java_thread("Finalizer", 1376654, state="WAITING (on object monitor)",
                               extra="\t- waiting on <0x00000000ffd01030> (a java.lang.Object)\n")
                 + vm_thread("GC Thread#0", 1376547, cpu="91.34ms"))
        self.assertEqual([t for t, _f in r], [
            {"name": "main", "jlt_state": "RUNNABLE", "cpu_since_start_ms": 148.76,
             "elapsed_s": 0.35, "cpu_pct": None, "os_tid": 1376533},
            {"name": "Finalizer", "jlt_state": "WAITING", "cpu_since_start_ms": 1.5,
             "elapsed_s": 0.35, "cpu_pct": None, "os_tid": 1376654},
            {"name": "GC Thread#0", "jlt_state": None, "cpu_since_start_ms": 91.34,
             "elapsed_s": 1.48, "cpu_pct": None, "os_tid": 1376547},
        ])
        self.assertEqual([f for _t, f in r], [True, True, False])

    def test_compile_task_and_lock_lines_are_not_frames(self):
        r = scan(java_thread("C1 CompilerThread0", 5, frames=0,
                             extra="   Compiling:  238   !   3       a.B::c (432 bytes)\n")
                 + java_thread("C2 CompilerThread0", 6, frames=0, extra="   No compile task\n"))
        self.assertEqual([f for _t, f in r], [False, False])

    def test_hex_nid(self):
        r = scan(vm_thread("VM Thread", "0x1a2b"))
        self.assertEqual(r[0][0]["os_tid"], 0x1a2b)

    def test_missing_cpu_elapsed_and_nid_are_null(self):
        r = scan('"odd" os_prio=0 tid=0x1\n\n')
        self.assertEqual(r[0][0], {"name": "odd", "jlt_state": None,
                                   "cpu_since_start_ms": None, "elapsed_s": None,
                                   "cpu_pct": None, "os_tid": None})

    def test_name_with_quotes(self):
        r = scan(java_thread('a "quoted" name', 7))
        self.assertEqual(r[0][0]["name"], 'a "quoted" name')

    def test_missing_trailer_is_incomplete(self):
        text = HEADER + java_thread("main", 1)
        self.assertIsNone(pp.scan_dump(text.splitlines(keepends=True)))

    def test_non_hotspot_format_is_none(self):
        text = ('91679:\nJRE 26 Mac OS X aarch64-64-Bit\n\n'
                '"main" prio=5 Id=1 RUNNABLE\n\tat Foo.bar(Foo.java:1)\n\n')
        self.assertIsNone(pp.scan_dump(text.splitlines(keepends=True)))


class TestParseLog(unittest.TestCase):

    def _parse(self, text, warnings=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "thread-print.log"
            path.write_text(text)
            return pp.parse_log(str(path), warnings.append if warnings is not None else None)

    BODY = (java_thread("main", 1) + java_thread("Reference Handler", 2)
            + java_thread("Signal Dispatcher", 3, frames=0)
            + java_thread("C2 CompilerThread0", 4, frames=0)
            + java_thread("C2 CompilerThread1", 5, frames=0)
            + java_thread("C1 CompilerThread0", 6, frames=0)
            + vm_thread("VM Thread", 7) + vm_thread("GC Thread#0", 8)
            + vm_thread("G1 Service", 9))

    def test_record(self):
        recs = self._parse(sample_text(self.BODY))
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual({k: v for k, v in rec.items() if k != "threads"}, {
            "timestamp": "2026-09-22T20:29:03.000+02:00", "interval_secs": None,
            "num_threads": 9,
            "c2": {"num_threads": 2, "cpu_pct": None},
            "c1": {"num_threads": 1, "cpu_pct": None},
            "gc": {"num_threads": 2, "cpu_pct": None},
            "mutator": {"num_threads": 1, "cpu_pct": None},
            "other": {"num_threads": 3, "cpu_pct": None},
        })
        self.assertEqual([t["os_tid"] for t in rec["threads"]], list(range(1, 10)))

    def test_truncated_sample_skipped_with_warning(self):
        warnings = []
        text = (sample_text(self.BODY)
                + "2026-09-22T20:29:05.000+02:00\n" + HEADER + java_thread("main", 1))  # cut off
        recs = self._parse(text, warnings)
        self.assertEqual(len(recs), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2026-09-22T20:29:05.000+02:00", warnings[0])

    def test_failed_jcmd_sample_skipped(self):
        warnings = []
        text = sample_text(self.BODY) + "2026-09-22T20:29:05.000+02:00\n1376531:\n"
        recs = self._parse(text, warnings)
        self.assertEqual(len(recs), 1)
        self.assertEqual(len(warnings), 1)

    def test_no_timestamps_exits(self):
        with self.assertRaises(SystemExit):
            self._parse("garbage\n")

    def test_no_valid_sample_exits(self):
        with self.assertRaises(SystemExit):
            self._parse("2026-09-22T20:29:05.000+02:00\n" + HEADER)

    def test_zst_input(self):
        import shutil
        if shutil.which("zstd") is None:
            self.skipTest("zstd not available")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "thread-print.log"
            path.write_text(sample_text(self.BODY))
            subprocess.run(["zstd", "-q", "--rm", str(path)], check=True)
            recs = pp.parse_log(str(path) + ".zst")
        self.assertEqual(recs[0]["num_threads"], 9)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "thread-print.log"
            path.write_text(sample_text(self.BODY))
            res = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["c2"]["num_threads"], 2)


def proclog_line(ts, ticks):
    """A proclog.jsonl line with *ticks* clock ticks of CPU time, split into utime/stime."""
    return json.dumps({"timestamp": ts, "pid": 1,
                       "proc_pid_stat": {"utime": str(ticks - ticks // 4),
                                         "stime": str(ticks // 4)}}) + "\n"


class TestCpu(unittest.TestCase):
    """CPU usage per thread and category (spec-post-process.md §3c.1)."""

    T0 = "2026-09-22T20:29:03.000+02:00"
    T1 = "2026-09-22T20:29:05.000+02:00"

    def _parse(self, *bodies, proclog=None, warnings=None):
        timestamps = [self.T0, self.T1, "2026-09-22T20:29:07.000+02:00"]
        text = "".join(sample_text(body, ts) for body, ts in zip(bodies, timestamps))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "thread-print.log"
            path.write_text(text)
            proclog_path = None
            if proclog is not None:
                proclog_path = Path(tmp) / "proclog.jsonl"
                proclog_path.write_text(proclog)
            return pp.parse_log(str(path), warnings.append if warnings is not None else None,
                                proclog=str(proclog_path) if proclog_path else None)

    # Interval 2.0 s (median elapsed delta): main 1000 ms -> 50 %, GC 400 ms -> 20 %.
    S0 = (java_thread("main", 1, cpu="100.00ms", elapsed="1.00s")
          + vm_thread("GC Thread#0", 8, cpu="100.00ms", elapsed="1.00s")
          + vm_thread("VM Thread", 7, cpu="10.00ms", elapsed="1.00s"))
    S1 = (java_thread("main", 1, cpu="1100.00ms", elapsed="3.00s")
          + vm_thread("GC Thread#0", 8, cpu="500.00ms", elapsed="3.00s")
          + vm_thread("VM Thread", 7, cpu="10.00ms", elapsed="3.00s"))

    def test_first_sample_has_no_cpu(self):
        first = self._parse(self.S0, self.S1)[0]
        self.assertIsNone(first["interval_secs"])
        self.assertIsNone(first["mutator"]["cpu_pct"])
        self.assertTrue(all(t["cpu_pct"] is None for t in first["threads"]))

    def test_interval_and_cpu_pct(self):
        rec = self._parse(self.S0, self.S1)[1]
        self.assertEqual(rec["interval_secs"], 2.0)
        self.assertEqual([t["cpu_pct"] for t in rec["threads"]], [50.0, 20.0, 0.0])
        self.assertEqual({c: rec[c]["cpu_pct"] for c in pp.CATEGORIES},
                         {"c2": 0.0, "c1": 0.0, "gc": 20.0, "mutator": 50.0, "other": 0.0})

    def test_new_thread_counts_fully(self):
        s1 = self.S1 + java_thread("worker", 9, cpu="300.00ms", elapsed="0.50s")
        rec = self._parse(self.S0, s1)[1]
        self.assertEqual(rec["threads"][-1]["cpu_pct"], 15.0)
        self.assertEqual(rec["mutator"]["cpu_pct"], 65.0)

    def test_reused_tid_is_new_thread(self):
        s1 = self.S1.replace("cpu=1100.00ms elapsed=3.00s", "cpu=40.00ms elapsed=0.20s")
        rec = self._parse(self.S0, s1)[1]
        self.assertEqual(rec["interval_secs"], 2.0)  # from the other two threads
        self.assertEqual(rec["threads"][0]["cpu_pct"], 2.0)

    def test_no_matching_thread_leaves_cpu_null(self):
        rec = self._parse(self.S0, vm_thread("GC Thread#1", 99, elapsed="0.10s"))[1]
        self.assertIsNone(rec["interval_secs"])
        self.assertIsNone(rec["gc"]["cpu_pct"])

    PROCLOG_100PCT = (proclog_line("2026-09-22T20:29:02.000+02:00", 0)
                      + proclog_line("2026-09-22T20:29:06.000+02:00", 400))

    def test_no_unaccounted_without_proclog(self):
        recs = self._parse(self.S0, self.S1)
        self.assertTrue(all("unaccounted" not in r for r in recs))

    def test_unaccounted_cpu(self):
        # Process: 100 % over the samples' timestamp span (2 s); the threads: 70 %.
        recs = self._parse(self.S0, self.S1, proclog=self.PROCLOG_100PCT)
        self.assertEqual(recs[0]["unaccounted"], {"cpu_pct": None})
        self.assertEqual(recs[1]["unaccounted"], {"cpu_pct": 30.0})
        self.assertEqual(recs[1]["other"]["cpu_pct"], 0.0)
        self.assertEqual(list(recs[1])[-2:], ["unaccounted", "threads"])

    def test_unaccounted_not_clamped(self):
        # Process only 50 % while the threads show 70 %: unaccounted goes negative.
        proclog = (proclog_line("2026-09-22T20:29:03.000+02:00", 0)
                   + proclog_line("2026-09-22T20:29:05.000+02:00", 100))
        rec = self._parse(self.S0, self.S1, proclog=proclog)[1]
        self.assertEqual(rec["unaccounted"]["cpu_pct"], -20.0)

    def test_proclog_not_covering_interval_gives_null(self):
        proclog = (proclog_line("2026-09-22T20:29:04.000+02:00", 0)
                   + proclog_line("2026-09-22T20:29:06.000+02:00", 400))
        rec = self._parse(self.S0, self.S1, proclog=proclog)[1]
        self.assertEqual(rec["unaccounted"], {"cpu_pct": None})

    def test_unusable_proclog_warns(self):
        warnings = []
        rec = self._parse(self.S0, self.S1, proclog="garbage\n", warnings=warnings)[1]
        self.assertNotIn("unaccounted", rec)
        self.assertEqual(len(warnings), 1)

    def test_cli_proclog_option(self):
        proclog = self.PROCLOG_100PCT
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "thread-print.log"
            path.write_text(sample_text(self.S0, self.T0) + sample_text(self.S1, self.T1))
            proclog_path = Path(tmp) / "proclog.jsonl"
            proclog_path.write_text(proclog)
            res = subprocess.run([sys.executable, str(SCRIPT), "--proclog", str(proclog_path),
                                  str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout.splitlines()[1])["unaccounted"]["cpu_pct"], 30.0)


if __name__ == "__main__":
    unittest.main()
