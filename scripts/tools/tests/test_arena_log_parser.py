#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/tools/tests/test_arena_log_parser.py
#
# Tests for bin/tools/arena-log-parser.py (parsing, series building and
# JSON report writing; plotting is disabled since it needs gnuplot).

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

_TOOL = os.path.join(os.path.dirname(__file__), "..", "..", "..", "bin", "tools",
                     "arena-log-parser.py")
_SPEC = importlib.util.spec_from_file_location("arena_log_parser", _TOOL)
alp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(alp)

P = "[info][arena,stats]"


def _slab(t, bs, granule, in_use, dirty_granules, n=0, peak=True):
    """n scales the cumulative alloc/dealloc counters (see _slab_counters);
    peak: log "last tick peak" as in_use + 5 (False: omit the line)."""
    blocks_per_granule = granule // bs
    c = _slab_counters(n)
    return [
        f"[{t}s]{P}   ArenaSlab {bs}",
        f"[{t}s]{P}     block_size: {bs} bytes",
        f"[{t}s]{P}     granule_size: {granule} ({blocks_per_granule} blocks)",
        f"[{t}s]{P}     release boundary: 16 granules (4096 blocks, 1048576 bytes)",
        f"[{t}s]{P}     in-use blocks: {in_use} ",
    ] + ([f"[{t}s]{P}     blocks in use (last tick peak): {in_use + 5} "] if peak else []) + [
        f"[{t}s]{P}     in-use blocks in resident section: {in_use}",
        f"[{t}s]{P}     in-use blocks in burst: 0",
        f"[{t}s]{P}     Dirty granules: {dirty_granules} "
        f"({dirty_granules * blocks_per_granule} blocks, {dirty_granules * granule} bytes)",
        f"[{t}s]{P}     Block units allocated: {c['slab_alloc_blocks']}",
        f"[{t}s]{P}     Alloc ops: {c['slab_alloc_ops']}",
        f"[{t}s]{P}     Block units per alloc call, avg: 4.00",
        f"[{t}s]{P}     Block units deallocated: {c['slab_dealloc_blocks']}",
        f"[{t}s]{P}     Dealloc ops: {c['slab_dealloc_ops']}",
        f"[{t}s]{P}     Block units per dealloc call, avg: 1.00",
        f"[{t}s]{P}     Block units released: {c['slab_released_blocks']}",
    ]


def _slab_counters(n):
    return {"slab_alloc_blocks": 40 * n, "slab_alloc_ops": 10 * n,
            "slab_dealloc_blocks": 30 * n, "slab_dealloc_ops": 5 * n,
            "slab_released_blocks": 2 * n}


def _cache(t, bs, count, n=0):
    """n scales the cumulative cache counters: allocs 7n, deallocs 6n, bulk 2n."""
    return [
        f"[{t}s]{P}   ArenaBlockCache {bs}",
        f"[{t}s]{P}     cache size: 7",
        f"[{t}s]{P}     count: {count}",
        f"[{t}s]{P}     Blocks allocs: {7 * n}",
        f"[{t}s]{P}     Blocks deallocs: {6 * n}",
        f"[{t}s]{P}     Bulk deallocs: {2 * n}",
        f"[{t}s]{P}     Blocks per bulk dealloc, avg: 3.00",
    ]


def _tick(n, t, slabs, caches_per_cpu):
    """slabs: [(bs, granule, in_use, dirty_granules, counter_scale)];
    caches_per_cpu: [{bs: (count, counter_scale)}]"""
    lines = [f"[{t}s]{P} Arena Memory Manager, Tick {n}",
             f"[{t}s]{P} Arena Statistic:",
             f"[{t}s]{P} Arena heap: ",
             f"[{t}s]{P}   Num commit ops: 9",
             f"[{t}s]{P} ArenaSlabGroup"]
    for s in slabs:
        lines += _slab(t, *s)
    lines += [f"[{t}s]{P} PerCore Holder",
              f"[{t}s]{P}   CPUs/used: {len(caches_per_cpu)}/{len(caches_per_cpu)}",
              f"[{t}s]{P}   Num slow queries: 0"]
    for cpu, caches in enumerate(caches_per_cpu):
        lines.append(f"[{t}s]{P}   PerCore data for CPU: {cpu}")
        for bs, (count, scale) in caches.items():
            lines += _cache(t, bs, count, scale)
    return lines


SLABS_T0 = [(256, 65536, 21, 1, 1), (32768, 32768, 25, 572, 2), (2097152, 2097152, 0, 0, 0)]
SLABS_T1 = [(256, 65536, 30, 2, 3), (32768, 32768, 40, 384, 2), (2097152, 2097152, 1, 1, 1)]
CACHES_T0 = [{256: (2, 1), 32768: (3, 1)}, {256: (0, 0), 32768: (6, 2)}]
CACHES_T1 = [{256: (1, 4), 32768: (4, 1)}, {256: (1, 1), 32768: (0, 2)}]


def _log():
    return (["[0.002s][info][arena,init] Arena System Initialized."]
            + _tick(0, 1.150, SLABS_T0, CACHES_T0)
            + _tick(1, 2.152, SLABS_T1, CACHES_T1))


class ParseTest(unittest.TestCase):
    def test_ticks_and_values(self):
        ticks = alp.parse_log(_log())
        self.assertEqual([t["tick"] for t in ticks], [0, 1])
        self.assertEqual([t["uptime_s"] for t in ticks], [1.150, 2.152])
        t0 = ticks[0]
        self.assertEqual(t0["slabs"][256],
                         {"granule_size": 65536, "slab_in_use": 21, "slab_in_use_peak": 26,
                          "dirty_granules": 1, "dirty_blocks_logged": 256,
                          **_slab_counters(1)})
        self.assertEqual(t0["cached"], {256: 2, 32768: 9})
        # summed over both CPUs (scales 1 + 2 for 32768)
        self.assertEqual(t0["cache_counters"][32768],
                         {"cache_alloc_blocks": 21, "cache_dealloc_blocks": 18,
                          "cache_dealloc_ops": 6})

    def test_truncated_final_tick_dropped(self):
        lines = _log()
        # cut tick 1 in the middle of its cache section
        cut = max(i for i, l in enumerate(lines) if "PerCore data for CPU: 1" in l)
        ticks = alp.parse_log(lines[:cut])
        self.assertEqual([t["tick"] for t in ticks], [0])

    def test_no_ticks(self):
        self.assertEqual(alp.parse_log(["[0.002s][info][arena,init] Arena System Initialized."]), [])


def _summary_tick(n, t, counters):
    """New format: one "across all active cores" cache record per block size,
    new slab lines, OOMs section; counters: log the cumulative counters
    (scale 1) or not."""
    lines = [f"[{t}s]{P} Arena Memory Manager, Tick {n}",
             f"[{t}s]{P} Arena Statistic:",
             f"[{t}s]{P}   ArenaSlabGroup"]
    for bs, granule, in_use, dirty in [(256, 65536, 21, 1), (524288, 524288, 3, 4)]:
        slab = _slab(t, bs, granule, in_use, dirty, n=1)
        if not counters:
            slab = [l for l in slab if not any(
                k in l for k in ("Block units", "Alloc ops", "Dealloc ops"))]
        lines += slab + [f"[{t}s]{P}       max blocks in burst: 3072 ",
                         f"[{t}s]{P}       in-use blocks in burst per-quartile distribution: 0/0/0/0"]
    lines += [f"[{t}s]{P}   PerCore Holder",
              f"[{t}s]{P}     CPUs/active: 18/32",
              f"[{t}s]{P}     ArenaBlockCache 256 (across all active cores)",
              f"[{t}s]{P}       count: 15"]
    if counters:
        lines += [f"[{t}s]{P}       Block allocs: 7",
                  f"[{t}s]{P}       Block deallocs: 6",
                  f"[{t}s]{P}       Bulk deallocs: 2"]
    lines += [f"[{t}s]{P}   OOMs per block size:",
              f"[{t}s]{P}     256: 0",
              f"[{t}s]{P}     524288: 1"]
    return lines


class SummaryCacheFormatTest(unittest.TestCase):
    def test_without_counters(self):
        ticks = alp.parse_log(_summary_tick(0, 1.071, False) + _summary_tick(1, 2.071, False))
        self.assertEqual(len(ticks), 2)
        self.assertEqual(ticks[0]["cached"], {256: 15})
        per_size, combined = alp.build_series(ticks)
        s = per_size[256]["samples"][0]
        self.assertEqual((s["cached_blocks"], s["in_use_blocks"], s["dirty_blocks"]), (15, 6, 256))
        self.assertEqual(per_size[524288]["samples"][0]["cached_blocks"], 0)
        for k in alp.COUNTER_KEYS:
            self.assertEqual(s[k], 0, k)
        self.assertEqual(combined["samples"][0]["cached_bytes"], 15 * 256)

    def test_with_counters(self):
        ticks = alp.parse_log(_summary_tick(0, 1.071, True))
        self.assertEqual(ticks[0]["cache_counters"][256],
                         {"cache_alloc_blocks": 7, "cache_dealloc_blocks": 6,
                          "cache_dealloc_ops": 2})
        self.assertEqual(ticks[0]["slabs"][256]["slab_alloc_blocks"], 40)

    def test_truncated_final_tick_dropped(self):
        lines = _summary_tick(0, 1.071, False) + _summary_tick(1, 2.071, False)
        cut = max(i for i, l in enumerate(lines) if "(across all active cores)" in l)
        self.assertEqual([t["tick"] for t in alp.parse_log(lines[:cut])], [0])


class SeriesTest(unittest.TestCase):
    def setUp(self):
        self.per_size, self.combined = alp.build_series(alp.parse_log(_log()))

    def test_per_size(self):
        self.assertEqual(sorted(self.per_size), [256, 32768, 2097152])
        d = self.per_size[256]
        self.assertEqual(d["granule_size"], 65536)
        # first tick: deltas are the cumulative values since startup
        self.assertEqual(d["samples"][0],
                         {"tick": 0, "uptime_s": 1.150, "cached_blocks": 2,
                          "in_use_blocks": 19, "in_use_peak_blocks": 26, "dirty_blocks": 256,
                          "cache_alloc_blocks": 7, "cache_dealloc_blocks": 6,
                          "cache_dealloc_ops": 2, **_slab_counters(1)})
        self.assertEqual(self.per_size[32768]["samples"][1]["in_use_blocks"], 36)
        self.assertEqual(self.per_size[32768]["samples"][1]["dirty_blocks"], 384)

    def test_counter_deltas(self):
        s1 = self.per_size[256]["samples"][1]
        # slab scale 1 -> 3, cache scale (1+0) -> (4+1)
        self.assertEqual({k: s1[k] for k in alp.COUNTER_KEYS},
                         {"cache_alloc_blocks": 28, "cache_dealloc_blocks": 24,
                          "cache_dealloc_ops": 8, **_slab_counters(2)})
        # unchanged counters -> zero deltas
        s1 = self.per_size[32768]["samples"][1]
        self.assertEqual({k: s1[k] for k in alp.COUNTER_KEYS},
                         {k: 0 for k in alp.COUNTER_KEYS})

    def test_slab_without_cache_has_zero_cached(self):
        s = self.per_size[2097152]["samples"][1]
        self.assertEqual((s["cached_blocks"], s["in_use_blocks"], s["dirty_blocks"]), (0, 1, 1))
        self.assertEqual((s["cache_alloc_blocks"], s["slab_alloc_blocks"]), (0, 40))

    def test_combined_bytes(self):
        c0 = self.combined["samples"][0]
        self.assertEqual(self.combined["unit"], "bytes")
        self.assertEqual(c0["cached_bytes"], 2 * 256 + 9 * 32768)
        self.assertEqual(c0["in_use_bytes"], 19 * 256 + 16 * 32768)
        self.assertEqual(c0["in_use_peak_bytes"], 26 * 256 + 30 * 32768 + 5 * 2097152)
        self.assertEqual(c0["dirty_bytes"], 65536 + 572 * 32768)
        self.assertEqual(c0["cache_alloc_bytes"], 7 * 256 + 21 * 32768)
        self.assertEqual(c0["slab_released_bytes"], 2 * 256 + 4 * 32768)
        # ops (and cache alloc blocks) summed as plain counts
        self.assertEqual(c0["slab_alloc_ops"], 10 + 20)
        self.assertEqual(c0["slab_dealloc_ops"], 5 + 10)
        self.assertEqual(c0["cache_dealloc_ops"], 2 + 6)
        self.assertEqual(c0["cache_alloc_blocks"], 7 + 21)
        self.assertNotIn("slab_alloc_blocks", c0)
        c1 = self.combined["samples"][1]
        self.assertEqual(c1["slab_alloc_bytes"], 80 * 256 + 0 * 32768 + 40 * 2097152)

    def test_combined_by_block_size(self):
        c0 = self.combined["samples"][0]
        self.assertEqual(c0["by_block_size"], {
            "256": {"in_use_peak_bytes": 26 * 256, "dirty_bytes": 65536,
                    "cached_bytes": 2 * 256},
            "32768": {"in_use_peak_bytes": 30 * 32768, "dirty_bytes": 572 * 32768,
                      "cached_bytes": 9 * 32768},
            "2097152": {"in_use_peak_bytes": 5 * 2097152, "dirty_bytes": 0,
                        "cached_bytes": 0}})
        for k in ("in_use_peak_bytes", "dirty_bytes", "cached_bytes"):
            self.assertEqual(sum(v[k] for v in c0["by_block_size"].values()), c0[k])


class MissingPeakTest(unittest.TestCase):
    def test_peak_omitted_when_not_logged(self):
        slabs = [(256, 65536, 21, 1, 1, False), (32768, 32768, 25, 572, 2)]
        per_size, combined = alp.build_series(alp.parse_log(_tick(0, 1.0, slabs, CACHES_T0)))
        self.assertNotIn("in_use_peak_blocks", per_size[256]["samples"][0])
        self.assertEqual(per_size[32768]["samples"][0]["in_use_peak_blocks"], 30)
        # combined peak needs all sizes
        self.assertNotIn("in_use_peak_bytes", combined["samples"][0])
        by_bs = combined["samples"][0]["by_block_size"]
        self.assertNotIn("in_use_peak_bytes", by_bs["256"])
        self.assertEqual(by_bs["32768"]["in_use_peak_bytes"], 30 * 32768)


class ReportTest(unittest.TestCase):
    def test_writes_json_files(self):
        per_size, combined = alp.build_series(alp.parse_log(_log()))
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "arena-report")
            alp.write_report(per_size, combined, report, "arena.log", plot=False)
            self.assertEqual(sorted(os.listdir(report)),
                             ["arena-summary.json", "blocks-2097152.json", "blocks-256.json",
                              "blocks-32768.json", "combined.json"])
            with open(os.path.join(report, "blocks-256.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["block_size"], 256)


class StackSvgsTest(unittest.TestCase):
    def _svg(self, d, name, w, h, body):
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="utf-8"  standalone="no"?>\n'
                    f'<svg  width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
                    f'xmlns="http://www.w3.org/2000/svg">\n{body}\n</svg>\n')
        return path

    def test_stacks_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._svg(tmp, "a.svg", 1400, 600, "<g id='A'/>")
            b = self._svg(tmp, "b.svg", 1200, 1000, "<g id='B'/>")
            out = os.path.join(tmp, "all.svg")
            self.assertTrue(alp.stack_svgs([a, os.path.join(tmp, "missing.svg"), b], out))
            with open(out, encoding="utf-8") as f:
                text = f.read()
        self.assertEqual(text.count("<?xml"), 1)
        self.assertIn('<svg width="1400" height="1600" viewBox="0 0 1400 1600"', text)
        self.assertIn('x="0" y="0">', text)
        self.assertIn('x="0" y="600">', text)
        self.assertLess(text.index("id='A'"), text.index("id='B'"))

    def test_nothing_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "all.svg")
            self.assertFalse(alp.stack_svgs([os.path.join(tmp, "missing.svg")], out))
            self.assertFalse(os.path.exists(out))

    @unittest.skipIf(shutil.which("gnuplot") is None, "gnuplot not installed")
    def test_report_writes_combined_all(self):
        per_size, combined = alp.build_series(alp.parse_log(_log()))
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "arena-report")
            os.makedirs(report)
            open(os.path.join(report, "combined.svg"), "w").close()
            alp.write_report(per_size, combined, report, "arena.log")
            self.assertTrue(os.path.exists(os.path.join(report, "combined-bytes.svg")))
            # stale file of the old name removed
            self.assertFalse(os.path.exists(os.path.join(report, "combined.svg")))
            with open(os.path.join(report, "combined-all.svg"), encoding="utf-8") as f:
                text = f.read()
        self.assertEqual(text.count('id="gnuplot_canvas"'), 3)

    def test_combined_plots_share_x_layout(self):
        """Same margins and autoscaled x range in all three combined plots."""
        per_size, combined = alp.build_series(alp.parse_log(_log()))
        samples = combined["samples"]
        with tempfile.TemporaryDirectory() as tmp:
            scripts = [
                alp.build_plot_script(samples, ["cached_bytes", "in_use_peak_bytes", "dirty_bytes"],
                                      "a.svg", os.path.join(tmp, "a.dat"), "t", "MiB"),
                alp.build_stacked_script(samples, combined["block_sizes"], "b.svg",
                                         os.path.join(tmp, "b.dat"), "t"),
                alp.build_impulse_script(samples, ["cache_alloc_bytes", "cache_dealloc_bytes",
                                                   "slab_alloc_bytes", "slab_dealloc_bytes"],
                                         "c.svg", os.path.join(tmp, "c.dat"), "t", "MiB"),
            ]
        for script in scripts:
            self.assertIn(alp._X_MARGINS, script)
            self.assertNotIn("xrange", script)


class SummaryTest(unittest.TestCase):
    def test_time_weighted_mean(self):
        self.assertIsNone(alp._time_weighted_mean([]))
        self.assertEqual(alp._time_weighted_mean([(1.0, 4)]), 4)
        # trapezoid: (0..1: 0->2, area 1) + (1..3: 2->2, area 4) over 3s
        self.assertAlmostEqual(alp._time_weighted_mean([(1.0, 2), (0.0, 0), (3.0, 2)]), 5 / 3)

    def test_summary(self):
        per_size, combined = alp.build_series(alp.parse_log(_log()))
        summary = alp.build_summary(per_size, combined)
        self.assertEqual(summary["unit"], "bytes")
        # two ticks -> average of the two samples
        self.assertEqual(summary["block_sizes"][0],
                         {"block_size": 256,
                          "in_use_peak_bytes_avg": round((26 + 35) / 2 * 256),
                          "dirty_bytes_avg": round((256 + 512) / 2 * 256),
                          "cached_bytes_avg": round((2 + 2) / 2 * 256)})
        c = combined["samples"]
        self.assertEqual(summary["combined"]["in_use_peak_bytes_avg"],
                         round((c[0]["in_use_peak_bytes"] + c[1]["in_use_peak_bytes"]) / 2))

    def test_summary_without_peak(self):
        slabs = [(256, 65536, 21, 1, 1, False)]
        per_size, combined = alp.build_series(alp.parse_log(_tick(0, 1.0, slabs, CACHES_T0)))
        summary = alp.build_summary(per_size, combined)
        self.assertNotIn("in_use_peak_bytes_avg", summary["block_sizes"][0])
        self.assertNotIn("in_use_peak_bytes_avg", summary["combined"])
        self.assertIn("dirty_bytes_avg", summary["combined"])
        self.assertNotIn("in_use_peak_bytes_max", summary["combined"])
        self.assertNotIn("oom_end", summary["combined"])

    def test_summary_max(self):
        per_size, combined = alp.build_series(alp.parse_log(_log()))
        summary = alp.build_summary(per_size, combined)
        c = combined["samples"]
        for k in ("in_use_peak", "dirty", "cached"):
            self.assertEqual(summary["combined"][f"{k}_bytes_max"],
                             max(s[f"{k}_bytes"] for s in c), k)
        self.assertNotIn("oom_end", summary["combined"])

    def test_summary_oom_end(self):
        ticks = alp.parse_log(_summary_tick(0, 1.071, False) + _summary_tick(1, 2.071, False))
        self.assertEqual(ticks[1]["ooms"], {256: 0, 524288: 1})
        ticks[1]["ooms"][524288] = 3
        per_size, combined = alp.build_series(ticks)
        summary = alp.build_summary(per_size, combined, ticks)
        self.assertEqual(summary["combined"]["oom_end"], {"256": 0, "524288": 3})


@unittest.skipIf(shutil.which("zstd") is None, "zstd not installed")
class CompressedLogTest(unittest.TestCase):
    def test_zst_same_as_plain(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "arena.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write("\n".join(_log()) + "\n")
            expected = alp.read_log(log)
            subprocess.run(["zstd", "-q", "--rm", log], check=True)
            self.assertEqual(alp.read_log(log + ".zst"), expected)
            self.assertEqual(alp.main([log + ".zst", "-o", tmp]), 0)
            self.assertTrue(os.path.exists(os.path.join(tmp, "arena-report", "arena-summary.json")))


class PlotScriptTest(unittest.TestCase):
    def test_script_quotes_paths_and_falls_back_to_ticks(self):
        samples = [{"tick": 0, "uptime_s": None, "c": 1, "u": 2, "d": 8}]
        with tempfile.TemporaryDirectory() as tmp:
            dat = os.path.join(tmp, "it's.dat")
            script = alp.build_plot_script(samples, ["c", "u", "d"], "out.svg", dat, "t", "y")
            self.assertIn("/it's.dat\"", script)
            self.assertIn('set xlabel "Tick"', script)
            # all bands from 0: in-use + cached (col 3) drawn before cached (col 2)
            self.assertLess(script.index("using 1:3 with filledcurves"),
                            script.index("using 1:2 with filledcurves"))
            self.assertIn('title "in-use + cached"', script)
            with open(dat, encoding="utf-8") as f:
                self.assertEqual(f.read(), "0 1.0 2.0 8.0\n")

    def test_missing_key_written_as_nan(self):
        samples = [{"tick": 0, "uptime_s": 1.0, "c": 1, "d": 8}]
        with tempfile.TemporaryDirectory() as tmp:
            dat = os.path.join(tmp, "a.dat")
            alp.build_plot_script(samples, ["c", "u", "d"], "out.svg", dat, "t", "y")
            with open(dat, encoding="utf-8") as f:
                self.assertEqual(f.read(), "1.0 1.0 NaN 8.0\n")

    def test_quote_newline_cannot_start_new_command(self):
        # gnuplot ends a command at a raw newline even inside '...', so a
        # newline in e.g. the output directory must become the "\\n" escape.
        self.assertEqual(alp._gnuplot_quote("a\nsystem('x')"), '"a\\nsystem(\'x\')"')
        with tempfile.TemporaryDirectory() as tmp:
            script = alp.build_plot_script(
                [{"tick": 0, "uptime_s": 1.0, "c": 1, "u": 2, "d": 8}], ["c", "u", "d"],
                os.path.join(tmp, "out\nsystem('x')\n.svg"), os.path.join(tmp, "a.dat"),
                "t", "y")
            self.assertFalse(any(line.startswith("system(") for line in script.splitlines()))

    def test_header_below_title(self):
        self.assertEqual(alp._title(None, "t"), "t")
        self.assertEqual(alp._title("d - dacapo.spring - a,b", "t"), "t\nd - dacapo.spring - a,b")
        samples = [{"tick": 0, "uptime_s": 1.0, "c": 1, "u": 2, "d": 8}]
        with tempfile.TemporaryDirectory() as tmp:
            script = alp.build_plot_script(samples, ["c", "u", "d"], "out.svg",
                                           os.path.join(tmp, "a.dat"),
                                           alp._title('x "y"', "t"), "Blocks")
            # double-quoted gnuplot string: generated title, "\n" escape, header
            self.assertIn('set title "t\\nx \\"y\\"" noenhanced', script)

    def test_impulse_script(self):
        samples = [{"tick": 0, "uptime_s": 1.0, "ca": 4, "cd": 3, "sa": 2, "sd": 1,
                    "cao": 40, "cdo": 10, "sao": 9, "sdo": 12},
                   {"tick": 1, "uptime_s": 2.0, "ca": 0, "cd": 0, "sa": 0, "sd": 0,
                    "cao": 0, "cdo": 0, "sao": 0, "sdo": 0}]
        with tempfile.TemporaryDirectory() as tmp:
            dat = os.path.join(tmp, "a.dat")
            script = alp.build_impulse_script(samples, ["ca", "cd", "sa", "sd"],
                                              "out.svg", dat, "t", "Blocks",
                                              ops_keys=["cao", "cdo", "sao", "sdo"])
            self.assertIn("set multiplot layout 5,1", script)
            self.assertNotIn("xrange", script)  # autoscaled, like the other plots
            titles = ["Number of blocks allocated/deallocated, Cache",
                      "Number of blocks allocated/deallocated, Slab",
                      "Number of alloc ops, Cache vs. Slab",
                      "Number of dealloc ops, Cache vs. Slab",
                      "Cache absorption: ops without slab involvement (cache ops - slab ops)"]
            idx = [script.index(f'set title "{t}"') for t in titles]
            self.assertEqual(idx, sorted(idx))
            # blocks panels: alloc up, dealloc down
            self.assertIn("using 1:2 with impulses lw 2 lc rgb '#e6820e'", script)
            self.assertIn("using 1:(-$5) with impulses lw 2 lc rgb '#9467bd'", script)
            # ops panels: slab drawn after (over) cache; deallocs down, x axis on top
            self.assertLess(script.index("using 1:6 with impulses lw 2 lc rgb '#6baed6'"),
                            script.index("using 1:8 with impulses lw 2 lc rgb '#08519c'"))
            self.assertLess(script.index("using 1:(-$7) with impulses lw 2 lc rgb '#6baed6'"),
                            script.index("using 1:(-$9) with impulses lw 2 lc rgb '#08519c'"))
            self.assertIn("set yrange [*:0]", script)
            # absorption panel: green, alloc up, dealloc down
            self.assertIn("using 1:10 with impulses lw 2 lc rgb '#74c476'", script)
            self.assertIn("using 1:(-$11) with impulses lw 2 lc rgb '#006d2c'", script)
            # only the bottom panel has the x label
            self.assertEqual(script.count('set xlabel "Uptime (s)"'), 1)
            self.assertGreater(script.index('set xlabel "Uptime (s)"'), idx[4])
            with open(dat, encoding="utf-8") as f:
                # cache - slab: alloc 40 - 9 = 31, dealloc 10 - 12 clamped to 0
                self.assertEqual(f.read().splitlines()[0],
                                 "1.0 4.0 3.0 2.0 1.0 40 10 9 12 31 0")


class TestGnuplotDquote(unittest.TestCase):
    def test_escapes_backquotes(self):
        # gnuplot runs `...` inside a double-quoted string as a shell command.
        self.assertEqual(alp._gnuplot_dquote('a\\b"c`d`\ne'),
                         '"a\\\\b\\"c\\`d\\`\\ne"')

if __name__ == "__main__":
    unittest.main()
