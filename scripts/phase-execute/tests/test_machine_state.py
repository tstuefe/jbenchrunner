# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-execute/machine_state.py.

Covers:
- _read_first_line / _read_int: present, missing, non-numeric
- _distinct_values: uniform, non-uniform, empty
- _cpu_governor / _cpu_scaling_max_freq_khz: no sysfs paths -> None; uniform values
- _turbo_boost: intel path, amd path, neither present
- _swap_off: header-only (off) vs. header+entry (on)
- capture_stabilization_state: real-machine smoke test of overall shape
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_EXECUTE_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _PHASE_EXECUTE_DIR not in sys.path:
    sys.path.insert(0, _PHASE_EXECUTE_DIR)

import machine_state  # noqa: E402


class TestReadHelpers(unittest.TestCase):

    def test_read_first_line_present(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("hello world\nsecond line\n")
            path = fh.name
        try:
            self.assertEqual(machine_state._read_first_line(path), "hello world")
        finally:
            os.remove(path)

    def test_read_first_line_missing_file(self):
        self.assertIsNone(machine_state._read_first_line("/nonexistent/path/xyz"))

    def test_read_int_present(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("42\n")
            path = fh.name
        try:
            self.assertEqual(machine_state._read_int(path), 42)
        finally:
            os.remove(path)

    def test_read_int_non_numeric(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("performance\n")
            path = fh.name
        try:
            self.assertIsNone(machine_state._read_int(path))
        finally:
            os.remove(path)

    def test_read_int_missing_file(self):
        self.assertIsNone(machine_state._read_int("/nonexistent/path/xyz"))


class TestDistinctValues(unittest.TestCase):

    def _write_files(self, tmpdir, contents):
        paths = []
        for i, content in enumerate(contents):
            path = os.path.join(tmpdir, f"f{i}")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content + "\n")
            paths.append(path)
        return paths

    def test_uniform_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = self._write_files(tmpdir, ["performance"] * 3)
            result = machine_state._distinct_values(paths, machine_state._read_first_line)
        self.assertEqual(result, {"values": ["performance"], "uniform": True})

    def test_non_uniform_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = self._write_files(tmpdir, ["performance", "powersave"])
            result = machine_state._distinct_values(paths, machine_state._read_first_line)
        self.assertEqual(set(result["values"]), {"performance", "powersave"})
        self.assertFalse(result["uniform"])

    def test_empty_paths(self):
        result = machine_state._distinct_values([], machine_state._read_first_line)
        self.assertEqual(result, {"values": [], "uniform": True})

    def test_unreadable_paths_are_skipped(self):
        result = machine_state._distinct_values(
            ["/nonexistent/a", "/nonexistent/b"], machine_state._read_first_line
        )
        self.assertEqual(result, {"values": [], "uniform": True})


class TestCpuGovernor(unittest.TestCase):

    def test_no_cpufreq_paths_returns_none(self):
        with patch.object(machine_state.glob, "glob", return_value=[]):
            self.assertIsNone(machine_state._cpu_governor())

    def test_uniform_governor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i in range(4):
                path = os.path.join(tmpdir, f"cpu{i}")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("performance\n")
                paths.append(path)
            with patch.object(machine_state.glob, "glob", return_value=paths):
                result = machine_state._cpu_governor()
        self.assertEqual(result, {"values": ["performance"], "uniform": True})


class TestStabilizationWarnings(unittest.TestCase):

    def _run(self, governors):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i, gov in enumerate(governors):
                path = os.path.join(tmpdir, f"cpu{i}")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(gov + "\n")
                paths.append(path)
            with patch.object(machine_state.glob, "glob", return_value=paths):
                return machine_state.stabilization_warnings()

    def test_all_performance_no_warning(self):
        self.assertEqual(self._run(["performance"] * 4), [])

    def test_some_not_performance_warns(self):
        warnings = self._run(["performance", "powersave", "powersave", "performance"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 of 4 CPUs", warnings[0])
        self.assertIn("seen: performance, powersave", warnings[0])

    def test_no_cpufreq_warns(self):
        with patch.object(machine_state.glob, "glob", return_value=[]):
            warnings = machine_state.stabilization_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn("could not verify CPU governor", warnings[0])


class TestCpuScalingMaxFreq(unittest.TestCase):

    def test_no_cpufreq_paths_returns_none(self):
        with patch.object(machine_state.glob, "glob", return_value=[]):
            self.assertIsNone(machine_state._cpu_scaling_max_freq_khz())

    def test_uniform_freq(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for i in range(2):
                path = os.path.join(tmpdir, f"cpu{i}")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("3600000\n")
                paths.append(path)
            with patch.object(machine_state.glob, "glob", return_value=paths):
                result = machine_state._cpu_scaling_max_freq_khz()
        self.assertEqual(result, {"values": [3600000], "uniform": True})


class TestTurboBoost(unittest.TestCase):

    def test_intel_disabled(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("1\n")
            path = fh.name
        try:
            with patch.object(machine_state, "_INTEL_NO_TURBO_PATH", path):
                result = machine_state._turbo_boost()
        finally:
            os.remove(path)
        self.assertEqual(result, {"mechanism": "intel", "disabled": True})

    def test_intel_enabled(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("0\n")
            path = fh.name
        try:
            with patch.object(machine_state, "_INTEL_NO_TURBO_PATH", path):
                result = machine_state._turbo_boost()
        finally:
            os.remove(path)
        self.assertEqual(result, {"mechanism": "intel", "disabled": False})

    def test_amd_disabled(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("0\n")
            path = fh.name
        try:
            with (
                patch.object(machine_state, "_INTEL_NO_TURBO_PATH", "/nonexistent"),
                patch.object(machine_state, "_AMD_BOOST_PATH", path),
            ):
                result = machine_state._turbo_boost()
        finally:
            os.remove(path)
        self.assertEqual(result, {"mechanism": "amd", "disabled": True})

    def test_neither_mechanism_present(self):
        with (
            patch.object(machine_state, "_INTEL_NO_TURBO_PATH", "/nonexistent/a"),
            patch.object(machine_state, "_AMD_BOOST_PATH", "/nonexistent/b"),
        ):
            result = machine_state._turbo_boost()
        self.assertEqual(result, {"mechanism": None, "disabled": None})


class TestSwapOff(unittest.TestCase):

    def test_swap_off_header_only(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
            path = fh.name
        try:
            with patch.object(machine_state, "_PROC_SWAPS_PATH", path):
                self.assertTrue(machine_state._swap_off())
        finally:
            os.remove(path)

    def test_swap_on_with_entry(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
            fh.write("/dev/zram0\t\t\t\tpartition\t8388604\t\t0\t\t100\n")
            path = fh.name
        try:
            with patch.object(machine_state, "_PROC_SWAPS_PATH", path):
                self.assertFalse(machine_state._swap_off())
        finally:
            os.remove(path)

    def test_missing_proc_swaps_returns_none(self):
        with patch.object(machine_state, "_PROC_SWAPS_PATH", "/nonexistent/swaps"):
            self.assertIsNone(machine_state._swap_off())


class TestServiceStatus(unittest.TestCase):

    def test_reads_systemctl_output(self):
        with patch.object(
            machine_state.subprocess, "run",
            return_value=type("R", (), {"stdout": "inactive\n"})(),
        ):
            self.assertEqual(machine_state._service_status("cron"), "inactive")

    def test_missing_systemctl_returns_none(self):
        with patch.object(machine_state.subprocess, "run", side_effect=OSError):
            self.assertIsNone(machine_state._service_status("cron"))


class TestCaptureStabilizationState(unittest.TestCase):
    """Real-machine smoke test: no mocking, just verifies overall shape."""

    def test_returns_expected_shape(self):
        state = machine_state.capture_stabilization_state()

        self.assertIn("cmdline", state)
        self.assertIsInstance(state["cmdline"], (str, type(None)))

        self.assertIn("cpu", state)
        for key in ("governor", "scaling_max_freq_khz", "turbo_boost"):
            self.assertIn(key, state["cpu"])

        self.assertIn("kernel", state)
        for key in ("randomize_va_space", "perf_event_paranoid"):
            self.assertIn(key, state["kernel"])

        self.assertIn("memory", state)
        for key in ("swap_off", "overcommit_memory", "overcommit_ratio"):
            self.assertIn(key, state["memory"])

        self.assertIn("services", state)
        self.assertEqual(set(state["services"]), set(machine_state._STABILIZED_SERVICES))


class TestPerfParanoidWarning(unittest.TestCase):
    """stabilization_warnings(perf_enabled=True) checks perf_event_paranoid (PRD.md D-85)."""

    def _run(self, level, perf_enabled=True):
        with tempfile.TemporaryDirectory() as tmpdir:
            gov = os.path.join(tmpdir, "cpu0")
            with open(gov, "w", encoding="utf-8") as fh:
                fh.write("performance\n")
            paranoid = os.path.join(tmpdir, "perf_event_paranoid")
            if level is not None:
                with open(paranoid, "w", encoding="utf-8") as fh:
                    fh.write(f"{level}\n")
            with patch.object(machine_state.glob, "glob", return_value=[gov]), \
                 patch.object(machine_state, "_PERF_EVENT_PARANOID_PATH", paranoid):
                return machine_state.stabilization_warnings(perf_enabled=perf_enabled)

    def test_level_zero_or_below_no_warning(self):
        for level in (-1, 0):
            with self.subTest(level=level):
                self.assertEqual(self._run(level), [])

    def test_higher_levels_warn_with_effect(self):
        for level, effect in ((1, "no whole-CPU counting"),
                              (2, "user space only"),
                              (4, "cannot count at all")):
            with self.subTest(level=level):
                warnings = self._run(level)
                self.assertEqual(len(warnings), 1)
                self.assertIn(f"perf_event_paranoid is {level}, not 0", warnings[0])
                self.assertIn(effect, warnings[0])

    def test_not_checked_when_perf_disabled(self):
        self.assertEqual(self._run(2, perf_enabled=False), [])

    def test_unreadable_no_warning(self):
        self.assertEqual(self._run(None), [])


if __name__ == "__main__":
    unittest.main()
