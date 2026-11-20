# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-execute/execute.py — Sub-Tasks 2, 3, and 4.

Covers:
- load_batch: missing file → correct RuntimeError message
- check_hostname: hostname mismatch → correct RuntimeError message
- liveness_check: no RUNNING entries → is_running=False
- liveness_check: RUNNING entry with dead PID → is_running=False
- get_run_status: all five status paths (PENDING, RUNNING, DONE, FAILED, STOPPED)
- detect_jdk: openjdk/openj9/Semeru variants, non-zero exit → RuntimeError
- write_info_json: dacapo run / specjbb run field population
- write_run_info_json: initial write (no exit_code/duration_secs) / final write
- _atomic_write_json: file content is valid JSON with correct data
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

# Add scripts/phase-execute/ to sys.path so execute is importable directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_EXECUTE_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_PHASE_EXECUTE_DIR, ".."))
for _p in (_SCRIPTS_DIR, _PHASE_EXECUTE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import execute  # noqa: E402
from common.toml_writer import dumps as toml_dumps  # noqa: E402

_STATE_TMP = None
_SAVED_XDG_STATE_HOME = None


def setUpModule():
    """Point the active-batch link (PRD.md §7) into a temp dir, so tests never
    see or touch the user's real ~/.local/state/jbenchrunner."""
    global _STATE_TMP, _SAVED_XDG_STATE_HOME
    _STATE_TMP = tempfile.mkdtemp()
    _SAVED_XDG_STATE_HOME = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = _STATE_TMP


def tearDownModule():
    if _SAVED_XDG_STATE_HOME is None:
        os.environ.pop("XDG_STATE_HOME", None)
    else:
        os.environ["XDG_STATE_HOME"] = _SAVED_XDG_STATE_HOME
    shutil.rmtree(_STATE_TMP, ignore_errors=True)


def _active_batch_link():
    return os.path.join(os.environ["XDG_STATE_HOME"], "jbenchrunner", "active-batch")


def _remove_active_batch_link():
    """An interrupted batch (correctly) keeps the link; remove it between tests."""
    if os.path.lexists(_active_batch_link()):
        os.remove(_active_batch_link())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NO_COLLECTORS = {"interval_secs": 10, "compression": False, "enabled": [], "perf_events": None}


def _make_batch(hostname="testhost", runs=None, started_at="2025-01-01T00:00:00.000+00:00",
                sudo_mode=False, collectors=None):
    batch = {
        "batchid": "abc123",
        "started_at": started_at,
        "hostname": hostname,
        "sudo_mode": sudo_mode,
        "collectors": dict(collectors if collectors is not None else _NO_COLLECTORS),
        "runs": runs if runs is not None else [],
    }
    return batch


def _write_run_info(result_dir, status, pid):
    os.makedirs(result_dir, exist_ok=True)
    run_info = {"status": status, "pid": pid,
                "time": "2025-01-01T00:00:00.000+00:00", "sudo_mode": False}
    with open(os.path.join(result_dir, "run-info.json"), "w", encoding="utf-8") as fh:
        json.dump(run_info, fh)


# ---------------------------------------------------------------------------
# load_batch
# ---------------------------------------------------------------------------

class TestLoadBatch(unittest.TestCase):

    def test_missing_file_raises_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(RuntimeError) as ctx:
                execute.load_batch(tmpdir)
            self.assertIn(
                "Error: no batch found. Run 'jbr-prepare <file.run.toml>' first.",
                str(ctx.exception),
            )

    def test_loads_valid_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            batch = _make_batch()
            batch_path = os.path.join(tmpdir, "BATCH_IN_PROGRESS.json")
            with open(batch_path, "w", encoding="utf-8") as fh:
                json.dump(batch, fh)
            result = execute.load_batch(tmpdir)
            self.assertEqual(result["hostname"], "testhost")


# ---------------------------------------------------------------------------
# check_hostname
# ---------------------------------------------------------------------------

class TestCheckHostname(unittest.TestCase):

    def test_matching_hostname_no_error(self):
        batch = _make_batch(hostname="myhost")
        # Should not raise
        execute.check_hostname(batch, "myhost")

    def test_mismatched_hostname_raises_runtime_error(self):
        batch = _make_batch(hostname="prepared-host")
        with self.assertRaises(RuntimeError) as ctx:
            execute.check_hostname(batch, "other-host")
        msg = str(ctx.exception)
        self.assertIn("prepared-host", msg)
        self.assertIn("other-host", msg)
        self.assertIn(
            "Executing a batch on a different machine is not supported.", msg
        )
        self.assertIn("jbr-execute --abort", msg)


# ---------------------------------------------------------------------------
# is_pid_alive
# ---------------------------------------------------------------------------

class TestIsPidAlive(unittest.TestCase):

    def test_true_for_self(self):
        self.assertTrue(execute.is_pid_alive(os.getpid()))

    def test_false_for_dead_pid(self):
        self.assertFalse(execute.is_pid_alive(2_000_000))


# ---------------------------------------------------------------------------
# liveness_check
# ---------------------------------------------------------------------------

class TestLivenessCheck(unittest.TestCase):

    def test_no_running_entries_is_not_running(self):
        """Batch with no RUNNING run-info.json files → is_running=False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            batch = _make_batch(
                runs=[{"runid": "r1", "result_dir": "results/r1"}],
                started_at="2025-06-01T12:00:00Z",
            )
            # Write a DONE run-info (no RUNNING)
            _write_run_info(os.path.join(tmpdir, "results", "r1"), "DONE", 12345)

            is_running, n_complete, started_at = execute.liveness_check(
                batch, tmpdir
            )

            self.assertFalse(is_running)
            self.assertEqual(n_complete, 1)
            self.assertEqual(started_at, "2025-06-01T12:00:00Z")

    def test_pending_run_no_result_dir_is_not_running(self):
        """Run with no result directory (PENDING) → is_running=False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            batch = _make_batch(
                runs=[{"runid": "r1", "result_dir": "results/r1"}],
            )
            # No result directory written at all

            is_running, n_complete, started_at = execute.liveness_check(
                batch, tmpdir
            )

            self.assertFalse(is_running)
            self.assertEqual(n_complete, 0)

    def test_running_entry_dead_pid_is_not_running(self):
        """RUNNING run-info.json but PID is dead → is_running=False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            batch = _make_batch(
                runs=[{"runid": "r1", "result_dir": "results/r1"}],
            )
            # Use PID 1 is always alive, so we need a definitely-dead PID.
            # Write a large PID that almost certainly does not exist.
            dead_pid = 2_000_000
            _write_run_info(os.path.join(tmpdir, "results", "r1"), "RUNNING", dead_pid)

            is_running, n_complete, _ = execute.liveness_check(batch, tmpdir)

            # os.kill(2_000_000, 0) should raise ProcessLookupError
            self.assertFalse(is_running)

    def test_empty_runs_list(self):
        """Batch with no runs at all → is_running=False, n_complete=0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            batch = _make_batch(runs=[])
            is_running, n_complete, _ = execute.liveness_check(batch, tmpdir)
            self.assertFalse(is_running)
            self.assertEqual(n_complete, 0)

    def test_failed_run_counts_as_complete(self):
        """FAILED runs contribute to n_complete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            batch = _make_batch(
                runs=[
                    {"runid": "r1", "result_dir": "results/r1"},
                    {"runid": "r2", "result_dir": "results/r2"},
                ],
            )
            _write_run_info(os.path.join(tmpdir, "results", "r1"), "DONE", 111)
            _write_run_info(os.path.join(tmpdir, "results", "r2"), "FAILED", 222)

            is_running, n_complete, _ = execute.liveness_check(batch, tmpdir)

            self.assertFalse(is_running)
            self.assertEqual(n_complete, 2)


# ---------------------------------------------------------------------------
# get_run_status
# ---------------------------------------------------------------------------

class TestGetRunStatus(unittest.TestCase):

    def _run_entry(self, result_dir):
        return {"runid": "r1", "result_dir": result_dir}

    def test_pending_no_result_dir(self):
        """No result directory → PENDING."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = self._run_entry("results/r1")
            # result directory does not exist at all
            status = execute.get_run_status(entry, tmpdir)
            self.assertEqual(status, "PENDING")

    def test_running(self):
        """run-info.json with status=RUNNING → RUNNING."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_run_info(os.path.join(tmpdir, "results", "r1"), "RUNNING", 99999)
            entry = self._run_entry("results/r1")
            status = execute.get_run_status(entry, tmpdir)
            self.assertEqual(status, "RUNNING")

    def test_done(self):
        """run-info.json with status=DONE → DONE."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_run_info(os.path.join(tmpdir, "results", "r1"), "DONE", 11111)
            entry = self._run_entry("results/r1")
            status = execute.get_run_status(entry, tmpdir)
            self.assertEqual(status, "DONE")

    def test_failed(self):
        """run-info.json with status=FAILED → FAILED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_run_info(os.path.join(tmpdir, "results", "r1"), "FAILED", 22222)
            entry = self._run_entry("results/r1")
            status = execute.get_run_status(entry, tmpdir)
            self.assertEqual(status, "FAILED")

    def test_stopped(self):
        """run-info.json with status=STOPPED → STOPPED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_run_info(os.path.join(tmpdir, "results", "r1"), "STOPPED", 33333)
            entry = self._run_entry("results/r1")
            status = execute.get_run_status(entry, tmpdir)
            self.assertEqual(status, "STOPPED")


# ---------------------------------------------------------------------------
# _atomic_write_json
# ---------------------------------------------------------------------------

class TestAtomicWriteJson(unittest.TestCase):

    def test_file_content_is_valid_json(self):
        """Written file is valid JSON and contains the expected data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.json")
            data = {"key": "value", "num": 42}
            execute._atomic_write_json(path, data)
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.assertEqual(loaded, data)

    def test_atomicity_replaces_existing_file(self):
        """Calling _atomic_write_json on an existing file replaces it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.json")
            execute._atomic_write_json(path, {"a": 1})
            execute._atomic_write_json(path, {"b": 2})
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.assertEqual(loaded, {"b": 2})


# ---------------------------------------------------------------------------
# detect_jdk
# ---------------------------------------------------------------------------

# Simulated java -version output strings
_OPENJDK_OUTPUT = (
    'openjdk version "21.0.4" 2024-07-16\n'
    'OpenJDK Runtime Environment (build 21.0.4+7)\n'
    'OpenJDK 64-Bit Server VM (build 21.0.4+7, mixed mode, sharing)\n'
)

_OPENJ9_OUTPUT = (
    'openjdk version "11.0.15" 2022-04-19\n'
    'OpenJDK Runtime Environment AdoptOpenJDK (build 11.0.15+10)\n'
    'Eclipse OpenJ9 VM AdoptOpenJDK (build openj9-0.32.0, JRE 11 Linux amd64-64-Bit)\n'
)

_SEMERU_OUTPUT = (
    'openjdk version "17.0.6" 2023-01-17\n'
    'IBM Semeru Runtime Open Edition 17.0.6.0 (build 17.0.6+10)\n'
    'Eclipse OpenJ9 VM 17.0.6.0 (build openj9-0.36.0, JRE 17 Linux amd64-64-Bit)\n'
)


def _make_completed_process(stdout, returncode=0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


class TestDetectJdk(unittest.TestCase):

    def _run_detect(self, output, returncode=0):
        with patch("execute.subprocess.run",
                   return_value=_make_completed_process(output, returncode)):
            return execute.detect_jdk("/fake/java")

    def test_openjdk_variant(self):
        """Standard OpenJDK output → type='openjdk'."""
        info = self._run_detect(_OPENJDK_OUTPUT)
        self.assertEqual(info["type"], "openjdk")
        self.assertEqual(info["version"], "21.0.4")
        self.assertEqual(info["major"], 21)
        self.assertEqual(info["full_version_string"], _OPENJDK_OUTPUT)

    def test_openj9_variant(self):
        """Output containing 'OpenJ9' → type='openj9'."""
        info = self._run_detect(_OPENJ9_OUTPUT)
        self.assertEqual(info["type"], "openj9")
        self.assertEqual(info["version"], "11.0.15")
        self.assertEqual(info["major"], 11)

    def test_semeru_variant(self):
        """Output containing 'Semeru' → type='openj9'."""
        info = self._run_detect(_SEMERU_OUTPUT)
        self.assertEqual(info["type"], "openj9")
        self.assertEqual(info["major"], 17)

    def test_internal_build_without_dot(self):
        """'28-internal' (no dot) → major 28, not 0."""
        info = self._run_detect('openjdk version "28-internal" 2027-03-23\n')
        self.assertEqual(info["version"], "28")
        self.assertEqual(info["major"], 28)

    def test_java8_major_is_8(self):
        info = self._run_detect('java version "1.8.0_362"\n')
        self.assertEqual(info["major"], 8)

    def test_unparseable_output_raises_runtime_error(self):
        with self.assertRaises(RuntimeError):
            self._run_detect("garbage\n")

    def test_nonzero_exit_raises_runtime_error(self):
        """Non-zero exit code → RuntimeError."""
        with self.assertRaises(RuntimeError):
            self._run_detect("some error output\n", returncode=1)

    def test_binary_not_found_raises_runtime_error(self):
        """FileNotFoundError from subprocess → RuntimeError."""
        with patch("execute.subprocess.run",
                   side_effect=FileNotFoundError("no such file")):
            with self.assertRaises(RuntimeError):
                execute.detect_jdk("/nonexistent/java")

    def test_permission_error_raises_runtime_error(self):
        """PermissionError from subprocess → RuntimeError."""
        with patch("execute.subprocess.run",
                   side_effect=PermissionError("permission denied")):
            with self.assertRaises(RuntimeError):
                execute.detect_jdk("/no-perms/java")


# ---------------------------------------------------------------------------
# check_jdk_against_prepared
# ---------------------------------------------------------------------------

class TestCheckJdkAgainstPrepared(unittest.TestCase):

    _DETECTED = {"full_version_string": "x", "version": "21.0.4", "major": 21, "type": "openjdk"}

    def _check(self, run_entry):
        detected = dict(self._DETECTED)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            execute.check_jdk_against_prepared(detected, run_entry)
        self.assertEqual(detected, self._DETECTED)  # detected values never altered
        return buf.getvalue()

    def test_matching_values_no_warning(self):
        self.assertEqual(
            self._check({"java_home": "/j", "jdk_type": "openjdk", "jdk_major": 21}), "")

    def test_old_batch_without_fields_no_warning(self):
        self.assertEqual(self._check({"java_home": "/j"}), "")

    def test_major_mismatch_warns(self):
        out = self._check({"java_home": "/j", "jdk_type": "openjdk", "jdk_major": 20})
        self.assertIn("WARNING: JDK major mismatch", out)
        self.assertNotIn("type mismatch", out)

    def test_type_mismatch_warns(self):
        out = self._check({"java_home": "/j", "jdk_type": "openj9", "jdk_major": 21})
        self.assertIn("WARNING: JDK type mismatch", out)
        self.assertNotIn("major mismatch", out)


# ---------------------------------------------------------------------------
# detect_gc
# ---------------------------------------------------------------------------

class TestDetectGc(unittest.TestCase):
    def test_g1gc(self):
        self.assertEqual(execute.detect_gc(["-Xmx8g", "-XX:+UseG1GC"]), "g1gc")

    def test_zgc(self):
        self.assertEqual(execute.detect_gc(["-XX:+UseZGC"]), "zgc")

    def test_shenandoahgc(self):
        self.assertEqual(execute.detect_gc(["-XX:+UseShenandoahGC"]), "shenandoahgc")

    def test_parallelgc(self):
        self.assertEqual(execute.detect_gc(["-XX:+UseParallelGC"]), "parallelgc")

    def test_serialgc(self):
        self.assertEqual(execute.detect_gc(["-XX:+UseSerialGC"]), "serialgc")

    def test_openj9_gencon(self):
        self.assertEqual(execute.detect_gc(["-Xgcpolicy:gencon"]), "gencon")

    def test_openj9_metronome(self):
        self.assertEqual(execute.detect_gc(["-Xmx8g", "-Xgcpolicy:metronome"]), "metronome")

    def test_no_gc_flag_returns_none(self):
        self.assertIsNone(execute.detect_gc(["-Xmx8g", "-Xms8g"]))

    def test_empty_flags_returns_none(self):
        self.assertIsNone(execute.detect_gc([]))

    def test_last_flag_wins(self):
        """Two GC flags (e.g. a profile overriding a benchmark default):
        the last one in flag order wins, mirroring parse_xmx_mb."""
        self.assertEqual(
            execute.detect_gc(["-XX:+UseG1GC", "-XX:+UseZGC"]), "zgc"
        )

    def test_disable_flag_not_matched(self):
        """A '-XX:-Use...GC' (disable) flag must not be mistaken for enabling
        that collector."""
        self.assertIsNone(execute.detect_gc(["-XX:-UseG1GC"]))

    def test_unrelated_use_gc_shaped_flag_not_matched(self):
        """A flag that merely starts with -XX:+Use... and contains 'GC'
        somewhere must not false-positive (only an exact trailing 'GC'
        after 'Use<Name>' counts)."""
        self.assertIsNone(execute.detect_gc(["-XX:+UseGCOverheadLimit"]))


# ---------------------------------------------------------------------------
# write_info_json
# ---------------------------------------------------------------------------

def _make_dacapo_run_entry():
    return {
        "runid":       "run-dacapo-001",
        "benchmark":    "dacapo",
        "sub_benchmark": "h2",
        "datasize":     "large",
        "iterations":   10,
        "tags":         ["tag1", "tag2"],
        "heapsize":    8192,
        "jvmargs":    ["-XX:+UseG1GC"],
        "jdkname":      "temurin-21",
        "java_home":    "/opt/jdks/temurin-21",
    }


def _make_specjbb_run_entry():
    return {
        "runid":       "run-specjbb-001",
        "benchmark":    "specjbb",
        "sub_benchmark": "",
        "datasize":     "",
        "iterations":   0,
        "tags":         [],
        "heapsize":    16384,
        "jvmargs":    ["-Xmx16g", "-XX:+UseG1GC"],
        "jdkname":      "temurin-17",
        "java_home":    "/opt/jdks/temurin-17",
    }


def _make_jdk_info():
    return {
        "full_version_string": _OPENJDK_OUTPUT,
        "version": "21.0.4",
        "major": 21,
        "type": "openjdk",
    }


class TestWriteInfoJson(unittest.TestCase):

    def _load_info(self, result_dir):
        with open(os.path.join(result_dir, "info.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_dacapo_run_has_dacapo_object_no_specjbb(self):
        """dacapo run → dacapo object present, specjbb absent, name='dacapo.h2'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["name"], "dacapo.h2")
        self.assertEqual(info["benchmark"], "dacapo")
        self.assertIn("dacapo", info)
        self.assertNotIn("specjbb", info)
        self.assertEqual(info["dacapo"]["sub_benchmark"], "h2")
        self.assertEqual(info["dacapo"]["datasize"], "large")
        self.assertEqual(info["dacapo"]["iterations"], 10)
        self.assertEqual(info["dacapo"]["dacapo_args"], [])

    def test_collectors_written_verbatim(self):
        run_entry = _make_dacapo_run_entry()
        run_entry["collectors"] = {"interval_secs": 5, "compression": True,
                                   "enabled": ["pidstat", "nmt"]}
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["collectors"], run_entry["collectors"])
        self.assertNotIn("collector_interval_secs", info)
        self.assertNotIn("nmt_enabled", info)

    def test_collectors_null_when_absent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertIsNone(info["collectors"])

    def test_dacapo_args_written_verbatim(self):
        """run_entry's dacapo_args is written verbatim into the dacapo object."""
        run_entry = _make_dacapo_run_entry()
        run_entry["dacapo_args"] = ["-t", "16"]
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["dacapo"]["dacapo_args"], ["-t", "16"])

    def test_specjbb_run_has_specjbb_object_no_dacapo(self):
        """specjbb run → specjbb object present, dacapo absent, name='specjbb'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_specjbb_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["name"], "specjbb")
        self.assertEqual(info["benchmark"], "specjbb")
        self.assertIn("specjbb", info)
        self.assertNotIn("dacapo", info)
        self.assertEqual(info["specjbb"]["mode"], "composite")

    def test_jdk_sub_object_populated(self):
        """jdk sub-object is correctly populated from jdk_info and run_entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        jdk = info["jdk"]
        self.assertEqual(jdk["name"], "temurin-21")
        self.assertEqual(jdk["path"], "/opt/jdks/temurin-21/bin/java")
        self.assertEqual(jdk["version"], "21.0.4")
        self.assertEqual(jdk["major"], 21)
        self.assertEqual(jdk["type"], "openjdk")

    def test_tags_heap_size_jvm_flags_present(self):
        """tags, heap.heap_size, and jvmargs are written correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["tags"], ["tag1", "tag2"])
        self.assertEqual(info["heap"]["heapsize"], 8192)
        self.assertEqual(info["jvmargs"], ["-XX:+UseG1GC"])

    def test_heap_gc_deduced_from_jvm_flags(self):
        """heap.gc is deduced from jvmargs (here -XX:+UseG1GC -> 'g1gc')."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["heap"]["gc"], "g1gc")

    def test_heap_gc_null_when_undetectable(self):
        """heap.gc is null when jvmargs has no recognizable GC flag."""
        run_entry = _make_dacapo_run_entry()
        run_entry["jvmargs"] = ["-Xmx8g"]
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertIsNone(info["heap"]["gc"])

    def test_heap_size_no_longer_top_level(self):
        """heap_size must not appear as a top-level info.json key (moved under heap)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertNotIn("heapsize", info)

    def test_batch_id_present(self):
        """batch_id is written verbatim as passed in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["batchid"], "batch-001")

    def test_jvm_env_absent_defaults_empty(self):
        """run_entry without jvmenv writes an empty object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["jvmenv"], {})

    def test_jvm_env_written_verbatim(self):
        """run_entry's resolved jvmenv is written verbatim."""
        run_entry = _make_dacapo_run_entry()
        run_entry["jvmenv"] = {"MALLOC_ARENA_MAX": "4"}
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["jvmenv"], {"MALLOC_ARENA_MAX": "4"})

    def test_repeat_no_absent_writes_zero(self):
        """run_entry without repeat_no (older batch file) writes 0."""
        run_entry = _make_dacapo_run_entry()
        run_entry.pop("repeat_no", None)
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["repeat_no"], 0)

    def test_repeat_no_written_verbatim(self):
        """run_entry's repeat_no is written verbatim."""
        run_entry = _make_dacapo_run_entry()
        run_entry["repeat_no"] = 2
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["repeat_no"], 2)

    def test_heapsize_factor_absent_writes_null(self):
        """run_entry without heapsize_factor (not a sweep run) writes null."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(_make_dacapo_run_entry(), _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertIsNone(info["heapsize_factor"])

    def test_heapsize_factor_written_verbatim(self):
        """run_entry's heapsize_factor (sweep run) is written verbatim."""
        run_entry = _make_dacapo_run_entry()
        run_entry["heapsize_factor"] = 2.0
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertEqual(info["heapsize_factor"], 2.0)

    def test_cpu_list_null_when_do_cpu_pinning_false(self):
        """cpu_list is null when this run's do_cpu_pinning is false, even if a
        batch-wide cpu_list was passed in (host supports pinning, run doesn't use it)."""
        run_entry = _make_dacapo_run_entry()
        run_entry["do_cpu_pinning"] = False
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir, cpu_list="0-3")
            info = self._load_info(tmpdir)
        self.assertIsNone(info["cpu_list"])

    def test_cpu_list_null_when_no_cpu_list_passed(self):
        run_entry = _make_dacapo_run_entry()
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir)
            info = self._load_info(tmpdir)
        self.assertIsNone(info["cpu_list"])

    def test_cpu_list_written_when_do_cpu_pinning_true(self):
        run_entry = _make_dacapo_run_entry()
        run_entry["do_cpu_pinning"] = True
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_info_json(run_entry, _make_jdk_info(), "batch-001", tmpdir, cpu_list="0-3")
            info = self._load_info(tmpdir)
        self.assertEqual(info["cpu_list"], "0-3")


# ---------------------------------------------------------------------------
# write_run_info_json
# ---------------------------------------------------------------------------

class TestWriteRunInfoJson(unittest.TestCase):

    def _load_run_info(self, result_dir):
        with open(os.path.join(result_dir, "run-info.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_initial_write_no_exit_code_no_duration_secs(self):
        """Initial write (exit_code/duration_secs omitted) → keys absent from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_run_info_json(
                tmpdir,
                status="RUNNING",
                pid=12345,
                start_time="2025-06-01T14:32:00.000+00:00",
                sudo_mode=False,
            )
            info = self._load_run_info(tmpdir)
        self.assertEqual(info["status"], "RUNNING")
        self.assertEqual(info["pid"], 12345)
        self.assertEqual(info["time"], "2025-06-01T14:32:00.000+00:00")
        self.assertFalse(info["sudo_mode"])
        self.assertNotIn("exit_code", info)
        self.assertNotIn("duration_secs", info)

    def test_final_write_all_keys_present(self):
        """Final write with exit_code and duration_secs → all keys present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_run_info_json(
                tmpdir,
                status="DONE",
                pid=12345,
                start_time="2025-06-01T14:32:00.000+00:00",
                sudo_mode=True,
                exit_code=0,
                duration_secs=342.7,
            )
            info = self._load_run_info(tmpdir)
        self.assertEqual(info["status"], "DONE")
        self.assertEqual(info["exit_code"], 0)
        self.assertAlmostEqual(info["duration_secs"], 342.7)
        self.assertTrue(info["sudo_mode"])

    def test_stopped_status_write(self):
        """STOPPED status with exit_code 137 is written correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_run_info_json(
                tmpdir,
                status="STOPPED",
                pid=99999,
                start_time="2025-06-01T14:32:00.000+00:00",
                sudo_mode=False,
                exit_code=137,
                duration_secs=45.1,
            )
            info = self._load_run_info(tmpdir)
        self.assertEqual(info["status"], "STOPPED")
        self.assertEqual(info["exit_code"], 137)

    def test_status_info_omitted_when_not_given(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_run_info_json(
                tmpdir, status="DONE", pid=1, start_time="2025-06-01T14:32:00.000+00:00",
                sudo_mode=False, exit_code=0, duration_secs=1.0,
            )
            info = self._load_run_info(tmpdir)
        self.assertNotIn("status_info", info)

    def test_status_info_included_when_given(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_run_info_json(
                tmpdir, status="STOPPED", pid=1, start_time="2025-06-01T14:32:00.000+00:00",
                sudo_mode=False, exit_code=137, duration_secs=1.0,
                status_info="collector",
            )
            info = self._load_run_info(tmpdir)
        self.assertEqual(info["status_info"], "collector")

    def test_stabilization_omitted_when_not_given(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_run_info_json(
                tmpdir, status="RUNNING", pid=1, start_time="2025-06-01T14:32:00.000+00:00",
                sudo_mode=False,
            )
            info = self._load_run_info(tmpdir)
        self.assertNotIn("stabilization", info)

    def test_stabilization_included_when_given(self):
        stabilization = {"cmdline": "quiet", "cpu": {"governor": None}}
        with tempfile.TemporaryDirectory() as tmpdir:
            execute.write_run_info_json(
                tmpdir, status="RUNNING", pid=1, start_time="2025-06-01T14:32:00.000+00:00",
                sudo_mode=False, stabilization=stabilization,
            )
            info = self._load_run_info(tmpdir)
        self.assertEqual(info["stabilization"], stabilization)


# ---------------------------------------------------------------------------
# Tests — Sub-Task 5: collector lifecycle management
# ---------------------------------------------------------------------------

def _make_mock_proc(poll_return=None):
    """Return a Mock that behaves like a finished (poll_return=0) or running
    (poll_return=None) subprocess.Popen."""
    proc = MagicMock()
    proc.poll.return_value = poll_return
    return proc


class TestVerifyCollectors(unittest.TestCase):
    """verify_collectors now returns (benchmark_already_exited, dead_names)."""

    def test_all_alive_returns_false_and_empty_list(self):
        collector_procs = [("cpu-temp-logger.py", _make_mock_proc(poll_return=None))]
        benchmark_proc = _make_mock_proc(poll_return=None)
        with patch("time.sleep"):
            already_exited, dead = execute.verify_collectors(collector_procs, benchmark_proc)
        self.assertFalse(already_exited)
        self.assertEqual(dead, [])

    def test_benchmark_already_exited_short_circuits(self):
        collector_procs = [("cpu-temp-logger.py", _make_mock_proc(poll_return=None))]
        benchmark_proc = _make_mock_proc(poll_return=0)
        with patch("time.sleep"):
            already_exited, dead = execute.verify_collectors(collector_procs, benchmark_proc)
        self.assertTrue(already_exited)
        self.assertEqual(dead, [])

    def test_dead_collector_is_reported_by_name(self):
        collector_procs = [
            ("cpu-temp-logger.py", _make_mock_proc(poll_return=None)),
            ("pidstat-logger.sh", _make_mock_proc(poll_return=1)),
        ]
        benchmark_proc = _make_mock_proc(poll_return=None)
        with patch("time.sleep"):
            already_exited, dead = execute.verify_collectors(collector_procs, benchmark_proc)
        self.assertFalse(already_exited)
        self.assertEqual(dead, ["pidstat-logger.sh"])


_HOST_COLLECTORS = ["cpu_temp", "pidstat", "proc", "perf"]


class TestStartCollectors(unittest.TestCase):
    """Verify argument assembly for start_collectors (Popen mocked)."""

    def _run(self, perf_events, jvm_pid=12345, interval=10,
             enabled=_HOST_COLLECTORS):
        run_entry = {"collectors": {"enabled": enabled}}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())):
                mock_popen.return_value = _make_mock_proc()
                result = execute.start_collectors(
                    run_entry, jvm_pid, tmpdir, interval, perf_events,
                )
        return result, mock_popen

    def _run_pinned(self, cpu_list_harness):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())), \
                 patch("time.sleep") as mock_sleep:
                mock_popen.return_value = _make_mock_proc()
                result = execute.start_collectors(
                    {"collectors": {"enabled": _HOST_COLLECTORS}}, 1, tmpdir, 10, ["cycles"],
                    cpu_list_harness=cpu_list_harness,
                )
        return result, mock_popen, mock_sleep

    def test_all_collectors_started_in_new_session(self):
        # Host collectors, jcmd-based (nmt, system_map): Ctrl-C
        # must reach only the runner, never the collectors (spec-execute.md 5.3).
        run_entry = {"collectors": {"enabled": _HOST_COLLECTORS + ["nmt", "system_map"]},
                     "java_home": "/opt/jdk-21"}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())):
                mock_popen.return_value = _make_mock_proc()
                result = execute.start_collectors(run_entry, 12345, tmpdir, 10, ["cycles"])
        self.assertEqual(len(result), 6)
        for call in mock_popen.call_args_list:
            self.assertEqual(call[1].get("process_group"), 0, msg=str(call))
            self.assertNotIn("start_new_session", call[1], msg=str(call))

    def test_pinned_collectors_wrapped_in_taskset_and_staggered(self):
        result, mock_popen, mock_sleep = self._run_pinned("0-1")
        self.assertEqual(len(result), 4)
        for call in mock_popen.call_args_list:
            self.assertEqual(call[0][0][:3], ["taskset", "--cpu-list", "0-1"])
        # every start but the first is delayed
        self.assertEqual(mock_sleep.call_count, 3)
        mock_sleep.assert_called_with(execute._COLLECTOR_START_STAGGER_SECS)

    def test_unpinned_collectors_not_wrapped_or_staggered(self):
        result, mock_popen, mock_sleep = self._run_pinned(None)
        for call in mock_popen.call_args_list:
            self.assertNotEqual(call[0][0][0], "taskset")
        mock_sleep.assert_not_called()

    def test_four_collectors_started_with_perf_events(self):
        result, mock_popen = self._run(["cycles", "instructions"])
        # All four collectors started
        self.assertEqual(len(result), 4)
        self.assertEqual(mock_popen.call_count, 4)

    def test_three_collectors_started_without_perf(self):
        result, mock_popen = self._run([], enabled=["cpu_temp", "pidstat", "proc"])
        # perf-logger.sh skipped
        self.assertEqual(len(result), 3)
        self.assertEqual(mock_popen.call_count, 3)
        names = [name for name, _proc in result]
        self.assertNotIn("perf-logger.sh", names)

    def test_cpu_temp_logger_args_has_pid(self):
        _result, mock_popen = self._run([], jvm_pid=99, interval=5)
        # First Popen call is cpu-temp-logger.py
        call_args = mock_popen.call_args_list[0][0][0]  # positional arg list
        self.assertEqual(call_args[0], "python3")
        self.assertIn("cpu-temp-logger.py", call_args[1])
        self.assertIn("-i", call_args)
        self.assertIn("5", call_args)
        # PID is the liveness anchor (PRD.md D-59), like every other collector
        self.assertIn("99", call_args)

    def test_pidstat_logger_args_has_pid(self):
        _result, mock_popen = self._run([], jvm_pid=777, interval=10)
        # Second Popen call is pidstat-logger.sh
        call_args = mock_popen.call_args_list[1][0][0]
        self.assertEqual(call_args[0], "bash")
        self.assertIn("pidstat-logger.sh", call_args[1])
        self.assertIn("777", call_args)

    def test_proc_logger_args_has_pid(self):
        _result, mock_popen = self._run([], jvm_pid=888, interval=10)
        call_args = mock_popen.call_args_list[2][0][0]
        self.assertEqual(call_args[0], "python3")
        self.assertIn("proc-logger.py", call_args[1])
        self.assertIn("888", call_args)

    def test_perf_logger_without_counters_for_default_set(self):
        _result, mock_popen = self._run(None)
        call_args = mock_popen.call_args_list[3][0][0]
        self.assertIn("perf-logger.sh", call_args[1])
        self.assertNotIn("--counters", call_args)

    def test_run_perf_events(self):
        self.assertEqual(execute.run_perf_events(
            {"collectors": {"perf_events": ["cycles"]}}), ["cycles"])
        self.assertIsNone(execute.run_perf_events({"collectors": {"perf_events": None}}))

    def test_perf_logger_args_with_counters_and_pid(self):
        _result, mock_popen = self._run(["cycles", "cache-misses"], jvm_pid=555, interval=15)
        # Fourth call is perf-logger.sh
        call_args = mock_popen.call_args_list[3][0][0]
        self.assertEqual(call_args[0], "bash")
        self.assertIn("perf-logger.sh", call_args[1])
        self.assertIn("--counters", call_args)
        counters_idx = call_args.index("--counters")
        self.assertEqual(call_args[counters_idx + 1], "cycles,cache-misses")
        self.assertIn("555", call_args)
        self.assertNotIn("--sudo", call_args)


    def test_return_value_contains_name_and_proc_tuples(self):
        result, mock_popen = self._run(["cycles"])
        self.assertEqual(len(result), 4)
        for item in result:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)
            name, proc = item
            self.assertIsInstance(name, str)

    def test_start_collectors_verbose_prints_command_lines(self):
        """When verbose is True, start_collectors prints the command lines to stdout."""
        import io
        run_entry = {"collectors": {"enabled": _HOST_COLLECTORS}}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())), \
                 patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                mock_popen.return_value = _make_mock_proc()
                execute.start_collectors(
                    run_entry, 12345, tmpdir, 10, ["cycles"], verbose=True
                )
                output = mock_stdout.getvalue()
                self.assertIn("Collector command line:", output)
                self.assertIn("cpu-temp-logger.py", output)
                self.assertIn("pidstat-logger.sh", output)
                self.assertIn("proc-logger.py", output)
                self.assertIn("perf-logger.sh", output)

    def test_collector_not_enabled_is_skipped(self):
        result, mock_popen = self._run(["cycles"], enabled=["cpu_temp", "proc", "perf"])
        names = [name for name, _proc in result]
        self.assertEqual(names, ["cpu-temp-logger.py", "proc-logger.py", "perf-logger.sh"])
        self.assertEqual(mock_popen.call_count, 3)

    def test_nothing_started_without_collectors_field(self):
        result, mock_popen = self._run(["cycles"], enabled=[])
        self.assertEqual(result, [])
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen:
                self.assertEqual(execute.start_collectors({}, 1, tmpdir, 10, ["cycles"]), [])
        mock_popen.assert_not_called()


class TestStartCollectorsApplicability(unittest.TestCase):
    """start_collectors honours config/collectors/<name>.collector.toml when jdk_info is given."""

    def _run(self, jdk_info, run_entry=None, reporoot=None):
        run_entry = run_entry or {"collectors": {"enabled": ["cpu_temp", "pidstat", "proc"]}}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = _make_mock_proc()
                return execute.start_collectors(
                    run_entry, 1, tmpdir, 10, [], jdk_info=jdk_info, reporoot=reporoot,
                )

    def _root(self, tmp, applies):
        d = os.path.join(tmp, "config", "collectors")
        os.makedirs(d)
        for n in ("cpu-temp-logger", "pidstat-logger", "proc-logger", "perf-logger",
                  "nmt", "system_map"):
            with open(os.path.join(d, f"{n}.collector.toml"), "w") as f:
                f.write(toml_dumps({"restrict_to": applies.get(n, [{"jdk_type": "openjdk"}, {"jdk_type": "openj9"}])}))
        return tmp

    # A collector of the batch's set that does not apply to the run's JDK is a
    # hard failure, raised before anything is started (PRD.md D-84).

    def test_applicable_collectors_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run({"type": "openjdk", "major": 21}, reporoot=self._root(tmp, {}))
        self.assertEqual([n for n, _ in result],
                         ["cpu-temp-logger.py", "pidstat-logger.sh", "proc-logger.py"])

    def test_non_applicable_collector_raises_before_starting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp, {"pidstat-logger": [{"jdk_type": "openj9"}]})
            with patch("subprocess.Popen") as mock_popen:
                with self.assertRaises(execute.CollectorsNotApplicable) as ctx:
                    execute.start_collectors(
                        {"collectors": {"enabled": ["cpu_temp", "pidstat", "proc"]}},
                        1, tmp, 10, [], jdk_info={"type": "openjdk", "major": 21},
                        reporoot=root)
            mock_popen.assert_not_called()
        self.assertEqual(ctx.exception.names, ["pidstat-logger"])

    def test_version_range_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp, {"proc-logger": [{"jdk_type": "openjdk", "min": 17}]})
            with self.assertRaises(execute.CollectorsNotApplicable) as ctx:
                self._run({"type": "openjdk", "major": 11}, reporoot=root)
        self.assertEqual(ctx.exception.names, ["proc-logger"])

    def test_jcmd_collector_on_openj9_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp, {"nmt": [{"jdk_type": "openjdk"}]})
            with self.assertRaises(execute.CollectorsNotApplicable) as ctx:
                self._run({"type": "openj9", "major": 17},
                          run_entry={"collectors": {"enabled": ["nmt"]}, "java_home": "/x"},
                          reporoot=root)
        self.assertEqual(ctx.exception.names, ["nmt"])

    def test_missing_config_is_hard_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self._run({"type": "openjdk", "major": 21}, reporoot=tmp)


class TestStartCollectorsJcmd(unittest.TestCase):
    """jcmd-based collectors (e.g. NMT), gated by run_entry's collectors.enabled."""

    def _run(self, run_entry, jvm_pid=12345, interval=10):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())):
                mock_popen.return_value = _make_mock_proc()
                result = execute.start_collectors(
                    run_entry, jvm_pid, tmpdir, interval, [],
                )
        return result, mock_popen

    def test_nmt_not_started_when_disabled(self):
        result, mock_popen = self._run({"collectors": {"enabled": []}, "java_home": "/opt/jdk-21"})
        self.assertEqual(result, [])
        mock_popen.assert_not_called()

    def test_nmt_not_started_when_absent(self):
        result, mock_popen = self._run({"java_home": "/opt/jdk-21"})
        self.assertEqual(result, [])
        mock_popen.assert_not_called()

    def test_nmt_started_when_enabled(self):
        result, mock_popen = self._run({"collectors": {"enabled": ["nmt"]}, "java_home": "/opt/jdk-21"})
        names = [name for name, _proc in result]
        self.assertEqual(names, ["nmt"])
        mock_popen.assert_called_once()

    def test_nmt_args_assembled_correctly(self):
        _result, mock_popen = self._run(
            {"collectors": {"enabled": ["nmt"]}, "java_home": "/opt/jdk-21"}, jvm_pid=777, interval=7
        )
        call_args = mock_popen.call_args_list[0][0][0]
        self.assertEqual(call_args[0], "bash")
        self.assertIn("jcmd-logger.sh", call_args[1])
        self.assertIn("-i", call_args)
        self.assertIn("7", call_args)
        self.assertIn("--java-home", call_args)
        self.assertEqual(call_args[call_args.index("--java-home") + 1], "/opt/jdk-21")
        self.assertIn("--cmd", call_args)
        self.assertEqual(
            call_args[call_args.index("--cmd") + 1], "VM.native_memory summary"
        )
        self.assertIn("777", call_args)

    def test_nmt_log_files_named_nmt(self):
        run_entry = {"collectors": {"enabled": ["nmt"]}, "java_home": "/opt/jdk-21"}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())) as mock_open:
                mock_popen.return_value = _make_mock_proc()
                execute.start_collectors(
                    run_entry, 1, tmpdir, 10, [],
                )
        opened_paths = [c.args[0] for c in mock_open.call_args_list]
        self.assertIn(os.path.join(tmpdir, "nmt.log"), opened_paths)
        self.assertIn(os.path.join(tmpdir, "nmt.err"), opened_paths)

    def test_system_map_not_started_when_disabled(self):
        result, mock_popen = self._run({"collectors": {"enabled": []}, "java_home": "/opt/jdk-21"})
        self.assertEqual(result, [])
        mock_popen.assert_not_called()

    def test_system_map_uses_jcmd_logger(self):
        result, mock_popen = self._run(
            {"collectors": {"enabled": ["system_map"]}, "java_home": "/opt/jdk-21"}, jvm_pid=777, interval=7
        )
        self.assertEqual([n for n, _p in result], ["system_map"])
        call_args = mock_popen.call_args_list[0][0][0]
        self.assertEqual(call_args[0], "bash")
        self.assertIn("jcmd-logger.sh", call_args[1])
        self.assertEqual(call_args[call_args.index("-i") + 1], "7")
        self.assertEqual(call_args[call_args.index("--java-home") + 1], "/opt/jdk-21")
        self.assertEqual(call_args[call_args.index("--cmd") + 1], "System.map")
        self.assertEqual(call_args[-1], "777")

    def test_system_map_output_files(self):
        run_entry = {"collectors": {"enabled": ["system_map"]}, "java_home": "/opt/jdk-21"}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())) as mock_open:
                mock_popen.return_value = _make_mock_proc()
                execute.start_collectors(
                    run_entry, 1, tmpdir, 10, [],
                )
        opened_paths = [c.args[0] for c in mock_open.call_args_list]
        self.assertIn(os.path.join(tmpdir, "system.map.log"), opened_paths)
        self.assertIn(os.path.join(tmpdir, "system.map.err"), opened_paths)
        self.assertNotIn(os.path.join(tmpdir, "system.map.jsonl"), opened_paths)

    def test_thread_print_not_started_when_disabled(self):
        result, mock_popen = self._run({"collectors": {"enabled": []}, "java_home": "/opt/jdk-21"})
        self.assertEqual(result, [])
        mock_popen.assert_not_called()

    def test_thread_print_uses_jcmd_logger(self):
        result, mock_popen = self._run(
            {"collectors": {"enabled": ["thread_print"]}, "java_home": "/opt/jdk-21"}, jvm_pid=777, interval=7
        )
        self.assertEqual([n for n, _p in result], ["thread_print"])
        call_args = mock_popen.call_args_list[0][0][0]
        self.assertEqual(call_args[0], "bash")
        self.assertIn("jcmd-logger.sh", call_args[1])
        self.assertEqual(call_args[call_args.index("-i") + 1], "7")
        self.assertEqual(call_args[call_args.index("--java-home") + 1], "/opt/jdk-21")
        self.assertEqual(call_args[call_args.index("--cmd") + 1], "Thread.print")
        self.assertEqual(call_args[-1], "777")

    def test_thread_print_output_files(self):
        run_entry = {"collectors": {"enabled": ["thread_print"]}, "java_home": "/opt/jdk-21"}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())) as mock_open:
                mock_popen.return_value = _make_mock_proc()
                execute.start_collectors(
                    run_entry, 1, tmpdir, 10, [],
                )
        opened_paths = [c.args[0] for c in mock_open.call_args_list]
        self.assertIn(os.path.join(tmpdir, "thread-print.log"), opened_paths)
        self.assertIn(os.path.join(tmpdir, "thread-print.err"), opened_paths)
        self.assertNotIn(os.path.join(tmpdir, "thread-print.jsonl"), opened_paths)


class TestStartCollectorsCompression(unittest.TestCase):
    """collectors.compression: NMT's stdout goes through a separate zstd process."""

    NMT = {"collectors": {"enabled": ["nmt"]}, "java_home": "/opt/jdk-21"}

    def _run(self, run_entry, compression, cpu_list_harness=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen") as mock_popen, \
                 patch("builtins.open", MagicMock(return_value=MagicMock())) as mock_open, \
                 patch("time.sleep"):
                mock_popen.side_effect = lambda *a, **k: _make_mock_proc()
                result = execute.start_collectors(
                    run_entry, 777, tmpdir, 10, [],
                    collector_compression=compression,
                    cpu_list_harness=cpu_list_harness,
                )
        return result, mock_popen, mock_open

    def test_compression_off_starts_plain_collector_only(self):
        result, mock_popen, mock_open = self._run(self.NMT, compression=False)
        self.assertEqual([n for n, _p in result], ["nmt"])
        mock_popen.assert_called_once()
        self.assertTrue(mock_open.call_args_list[0][0][0].endswith("nmt.log"))

    def test_compression_on_starts_compressor_and_collector(self):
        result, mock_popen, _ = self._run(self.NMT, compression=True)
        self.assertEqual([n for n, _p in result], ["nmt", "nmt+zstd"])
        self.assertEqual(mock_popen.call_count, 2)

    def test_compressor_started_first_and_reads_collector_pipe(self):
        result, mock_popen, _ = self._run(self.NMT, compression=True)
        zproc = result[1][1]
        zcall, ccall = mock_popen.call_args_list
        self.assertEqual(zcall[0][0], execute._COMPRESSOR_CMD)
        self.assertEqual(zcall[1]["stdin"], subprocess.PIPE)
        self.assertEqual(zcall[1]["process_group"], 0)
        self.assertIn("jcmd-logger.sh", ccall[0][0][1])
        # the collector writes into the compressor's stdin pipe
        self.assertIs(ccall[1]["stdout"], zproc.stdin)
        self.assertEqual(ccall[1]["process_group"], 0)
        # the parent must drop its own copy of the write end, or EOF never comes
        zproc.stdin.close.assert_called_once()

    def test_compressed_file_is_zst_and_err_stays_plain(self):
        _result, _popen, mock_open = self._run(self.NMT, compression=True)
        opened = [(c[0][0], c[0][1]) for c in mock_open.call_args_list]
        self.assertEqual([(os.path.basename(p), m) for p, m in opened],
                         [("nmt.log.zst", "wb"), ("nmt.err", "w")])

    def test_system_map_log_is_compressed(self):
        result, _popen, mock_open = self._run(
            {"collectors": {"enabled": ["system_map"]}, "java_home": "/opt/jdk-21"}, compression=True)
        self.assertEqual([n for n, _p in result], ["system_map", "system_map+zstd"])
        self.assertEqual(os.path.basename(mock_open.call_args_list[0][0][0]), "system.map.log.zst")

    def test_thread_print_log_is_compressed(self):
        result, _popen, mock_open = self._run(
            {"collectors": {"enabled": ["thread_print"]}, "java_home": "/opt/jdk-21"}, compression=True)
        self.assertEqual([n for n, _p in result], ["thread_print", "thread_print+zstd"])
        self.assertEqual(os.path.basename(mock_open.call_args_list[0][0][0]), "thread-print.log.zst")

    def test_only_compression_set_is_compressed(self):
        # collectors outside _COMPRESSED_LOG_STEMS get no compressor
        self.assertEqual(execute._COMPRESSED_LOG_STEMS,
                          {"nmt", "system.map", "thread-print", "proclog"})

    def test_pinned_compressor_wrapped_in_taskset(self):
        _result, mock_popen, _ = self._run(self.NMT, compression=True, cpu_list_harness="0-1")
        zargs = mock_popen.call_args_list[0][0][0]
        self.assertEqual(zargs[:3], ["taskset", "--cpu-list", "0-1"])
        self.assertEqual(zargs[3:], execute._COMPRESSOR_CMD)

    def test_compressor_has_no_nice(self):
        # PRD.md D-58: same priority as the collectors (priority inversion on shared CPUs)
        self.assertNotIn("nice", execute._COMPRESSOR_CMD)


@unittest.skipUnless(shutil.which("zstd"), "requires the zstd binary")
class TestCompressionPipelineReal(unittest.TestCase):
    """Real processes: fake jcmd-logger.sh -> real zstd -> .zst file, real stop_collectors."""

    def test_output_is_complete_after_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            collectors_dir = os.path.join(tmp, "collectors")
            result_dir = os.path.join(tmp, "result")
            os.makedirs(collectors_dir)
            os.makedirs(result_dir)
            with open(os.path.join(collectors_dir, "jcmd-logger.sh"), "w") as f:
                # exec: SIGTERM then closes the pipe at once (no orphaned child holds it)
                f.write("#!/bin/bash\necho sample-1\necho sample-2\nexec sleep 60\n")

            with patch.object(execute, "_COLLECTORS_DIR", collectors_dir):
                procs = execute.start_collectors(
                    {"collectors": {"enabled": ["nmt"]}, "java_home": "/x"}, os.getpid(), result_dir, 10, [],
                    collector_compression=True,
                )
            self.assertEqual([n for n, _p in procs], ["nmt", "nmt+zstd"])

            time.sleep(1)  # let the collector print
            # zstd has not flushed yet: the tail is still in its buffer
            with patch("time.sleep"):
                execute.stop_collectors(procs)

            for _n, p in procs:
                self.assertIsNotNone(p.poll())
            self.assertFalse(os.path.exists(os.path.join(result_dir, "nmt.log")))
            out = subprocess.run(
                ["zstd", "-dc", os.path.join(result_dir, "nmt.log.zst")],
                capture_output=True, text=True, check=True)
            self.assertEqual(out.stdout, "sample-1\nsample-2\n")


class TestStopCollectorsCompressors(unittest.TestCase):
    """Compressors end on EOF: never SIGTERMed, killed only after a drain timeout."""

    def _procs(self):
        collector = MagicMock()
        collector.poll.side_effect = [None, 0]   # running -> terminate, then gone
        compressor = MagicMock()
        return collector, compressor

    def test_compressor_not_terminated_but_awaited(self):
        collector, compressor = self._procs()
        with patch("time.sleep"):
            execute.stop_collectors([("nmt", collector), ("nmt+zstd", compressor)])
        collector.terminate.assert_called_once()
        compressor.terminate.assert_not_called()
        compressor.kill.assert_not_called()
        compressor.wait.assert_called_once_with(timeout=execute._COMPRESSOR_DRAIN_SECS)

    def test_hung_compressor_is_killed_after_drain_timeout(self):
        collector, compressor = self._procs()
        compressor.wait.side_effect = [subprocess.TimeoutExpired("zstd", 10), 0]
        with patch("time.sleep"):
            execute.stop_collectors([("nmt", collector), ("nmt+zstd", compressor)])
        compressor.terminate.assert_not_called()
        compressor.kill.assert_called_once()

    def test_compressor_drained_only_after_collector_reaped(self):
        collector, compressor = self._procs()
        order = []
        collector.wait.side_effect = lambda *a, **k: order.append("collector")
        compressor.wait.side_effect = lambda *a, **k: order.append("compressor")
        with patch("time.sleep"):
            execute.stop_collectors([("nmt+zstd", compressor), ("nmt", collector)])
        self.assertEqual(order, ["collector", "compressor"])


class TestStopCollectors(unittest.TestCase):
    """Verify stop_collectors termination / kill logic."""

    def _run_stop(self, poll_sequence):
        """Run stop_collectors with mocked collectors.

        poll_sequence is a list of return-value sequences for each proc's
        poll() method (side_effect list).
        """
        procs = []
        for seq in poll_sequence:
            proc = MagicMock()
            proc.poll.side_effect = seq
            procs.append(("collector", proc))

        with patch("time.sleep"):
            execute.stop_collectors(procs)

        return [p for _n, p in procs]

    def test_already_done_collector_not_terminated(self):
        # poll() always returns 0 (already exited)
        procs = self._run_stop([
            [0, 0, 0],  # poll at grace check, poll after terminate-sleep, poll for kill
        ])
        procs[0].terminate.assert_not_called()
        procs[0].kill.assert_not_called()

    def test_running_collector_is_terminated(self):
        # poll() returns None (still running) for first two checks, then 0 after terminate
        procs = self._run_stop([
            [None, 0],   # None → terminate; then 0 → no kill needed
        ])
        procs[0].terminate.assert_called_once()
        procs[0].kill.assert_not_called()

    def test_stubborn_collector_is_killed(self):
        # poll() stays None through both checks → gets killed
        procs = self._run_stop([
            [None, None],
        ])
        procs[0].terminate.assert_called_once()
        procs[0].kill.assert_called_once()

    def test_wait_called_on_all_collectors(self):
        procs = self._run_stop([
            [0, 0],
            [None, 0],
        ])
        for proc in procs:
            proc.wait.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — Sub-Task 6: _build_jvm_cmdline
# ---------------------------------------------------------------------------

class TestBuildJvmCmdline(unittest.TestCase):
    """Tests for execute._build_jvm_cmdline."""

    def _dacapo_entry(self, dacapo_dir):
        return {
            "benchmark":    "dacapo",
            "java_home":    "/opt/jdks/temurin-21",
            "jvmargs":    ["-Xmx8g", "-XX:+UseG1GC"],
            "dacapo_dir":   dacapo_dir,
            "sub_benchmark": "h2",
            "datasize":     "large",
            "iterations":   5,
        }

    def _specjbb_entry(self):
        return {
            "benchmark":    "specjbb",
            "java_home":    "/opt/jdks/temurin-17",
            "jvmargs":    ["-Xmx16g"],
            "specjbb_dir":  "/opt/specjbb",
        }

    def test_dacapo_valid_jar(self):
        """dacapo run with a valid jar produces the correct command structure."""
        import tempfile
        import os as _os
        with tempfile.TemporaryDirectory() as dacapo_dir:
            # Create a fake dacapo jar so glob finds it
            jar_path = _os.path.join(dacapo_dir, "dacapo-23.11-chopin.jar")
            open(jar_path, "w").close()

            entry = self._dacapo_entry(dacapo_dir)
            reporoot = "/repo"
            cmd = execute._build_jvm_cmdline(entry, reporoot)

            self.assertEqual(cmd[0], "/opt/jdks/temurin-21/bin/java")
            self.assertIn("-Xmx8g", cmd)
            self.assertIn("-XX:+UseG1GC", cmd)

            # -cp must be followed by the classpath string
            cp_idx = cmd.index("-cp")
            classpath = cmd[cp_idx + 1]
            self.assertIn(jar_path, classpath)
            self.assertIn("dacapoExtension.jar", classpath)

            # Harness invocation
            self.assertIn("Harness", cmd)
            self.assertIn("-c", cmd)
            self.assertIn("stuefe.dacapo.JBRCallback", cmd)
            self.assertIn("-n", cmd)
            n_idx = cmd.index("-n")
            self.assertEqual(cmd[n_idx + 1], "5")
            self.assertIn("-s", cmd)
            s_idx = cmd.index("-s")
            self.assertEqual(cmd[s_idx + 1], "large")
            self.assertIn("--verbose", cmd)
            self.assertIn("--print-stats", cmd)
            self.assertEqual(cmd[-1], "h2")

    def test_dacapo_args_inserted_before_sub_benchmark(self):
        """dacapo_args are inserted after --print-stats and before the trailing
        sub-benchmark name, per DaCapo Harness's 'Harness [options] <benchmark>' syntax."""
        import tempfile
        import os as _os
        with tempfile.TemporaryDirectory() as dacapo_dir:
            open(_os.path.join(dacapo_dir, "dacapo-23.11-chopin.jar"), "w").close()

            entry = self._dacapo_entry(dacapo_dir)
            entry["dacapo_args"] = ["-t", "16"]
            cmd = execute._build_jvm_cmdline(entry, "/repo")

            stats_idx = cmd.index("--print-stats")
            self.assertEqual(cmd[stats_idx + 1:], ["-t", "16", "h2"])

    def test_dacapo_args_absent_defaults_to_none_inserted(self):
        """dacapo_args absent from the run entry inserts nothing extra."""
        import tempfile
        import os as _os
        with tempfile.TemporaryDirectory() as dacapo_dir:
            open(_os.path.join(dacapo_dir, "dacapo-23.11-chopin.jar"), "w").close()

            entry = self._dacapo_entry(dacapo_dir)
            cmd = execute._build_jvm_cmdline(entry, "/repo")

            stats_idx = cmd.index("--print-stats")
            self.assertEqual(cmd[stats_idx + 1:], ["h2"])

    def test_dacapo_no_jar_raises(self):
        """dacapo run with no matching jar raises RuntimeError."""
        import tempfile
        with tempfile.TemporaryDirectory() as dacapo_dir:
            entry = self._dacapo_entry(dacapo_dir)
            with self.assertRaises(RuntimeError):
                execute._build_jvm_cmdline(entry, "/repo")

    def test_specjbb_command_structure(self):
        """specjbb run produces the correct command structure."""
        entry = self._specjbb_entry()
        cmd = execute._build_jvm_cmdline(entry, "/repo")

        self.assertEqual(cmd[0], "/opt/jdks/temurin-17/bin/java")
        self.assertIn("-Xmx16g", cmd)
        self.assertIn("-jar", cmd)
        jar_idx = cmd.index("-jar")
        self.assertTrue(cmd[jar_idx + 1].endswith("specjbb2015.jar"))
        self.assertIn("-m", cmd)
        m_idx = cmd.index("-m")
        self.assertEqual(cmd[m_idx + 1], "COMPOSITE")
        self.assertIn("-ikv", cmd)

    def test_cpu_pinning_wraps_command_in_taskset(self):
        entry = self._specjbb_entry()
        entry["do_cpu_pinning"] = True
        cmd = execute._build_jvm_cmdline(entry, "/repo", cpu_list="0-3")

        self.assertEqual(cmd[:3], ["taskset", "--cpu-list", "0-3"])
        # taskset execs into the target, so the java binary must still be the
        # very next element — no gymnastics needed to track the resulting PID.
        self.assertEqual(cmd[3], "/opt/jdks/temurin-17/bin/java")

    def test_cpu_pinning_not_wrapped_when_do_cpu_pinning_false(self):
        entry = self._specjbb_entry()
        entry["do_cpu_pinning"] = False
        cmd = execute._build_jvm_cmdline(entry, "/repo", cpu_list="0-3")

        self.assertNotIn("taskset", cmd)
        self.assertEqual(cmd[0], "/opt/jdks/temurin-17/bin/java")

    def test_cpu_pinning_not_wrapped_when_cpu_list_absent(self):
        # do_cpu_pinning true but no cpu_list given (shouldn't happen once
        # jbr-prepare's validation is in place, but must fail safe here too).
        entry = self._specjbb_entry()
        entry["do_cpu_pinning"] = True
        cmd = execute._build_jvm_cmdline(entry, "/repo", cpu_list=None)

        self.assertNotIn("taskset", cmd)
        self.assertEqual(cmd[0], "/opt/jdks/temurin-17/bin/java")

    def test_cpu_pinning_wraps_dacapo_command_too(self):
        import tempfile
        import os as _os
        with tempfile.TemporaryDirectory() as dacapo_dir:
            open(_os.path.join(dacapo_dir, "dacapo-23.11-chopin.jar"), "w").close()
            entry = self._dacapo_entry(dacapo_dir)
            entry["do_cpu_pinning"] = True
            cmd = execute._build_jvm_cmdline(entry, "/repo", cpu_list="1,3,5,7")

            self.assertEqual(cmd[:3], ["taskset", "--cpu-list", "1,3,5,7"])
            self.assertEqual(cmd[3], "/opt/jdks/temurin-21/bin/java")


# ---------------------------------------------------------------------------
# Tests — Sub-Task 7: _abort, run_execute batch loop
# ---------------------------------------------------------------------------

class TestAbort(unittest.TestCase):
    """Tests for execute._abort."""

    def setUp(self):
        _remove_active_batch_link()
        self.addCleanup(_remove_active_batch_link)

    def _make_batch_file(self, tmpdir, runs=None, ramdisk_path=None):
        """Write a minimal BATCH_IN_PROGRESS.json to tmpdir and return its path."""
        batch = _make_batch(hostname="testhost", runs=runs or [])
        if ramdisk_path is not None:
            batch["ramdisk_path"] = ramdisk_path
        batch_path = os.path.join(tmpdir, "BATCH_IN_PROGRESS.json")
        with open(batch_path, "w", encoding="utf-8") as fh:
            json.dump(batch, fh)
        return batch_path

    def test_abort_no_running_entries_moves_batch_file(self):
        """--abort with no running entries moves BATCH_IN_PROGRESS.json to
        batches/<batchid>/batch-aborted.json, unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._make_batch_file(tmpdir)
            with open(path, encoding="utf-8") as fh:
                original = fh.read()
            execute._abort(tmpdir)
            self.assertFalse(
                os.path.exists(os.path.join(tmpdir, "BATCH_IN_PROGRESS.json"))
            )
            aborted = os.path.join(tmpdir, "batches", "abc123", "batch-aborted.json")
            with open(aborted, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), original)

    def test_abort_is_running_raises_runtime_error(self):
        """--abort when liveness_check reports is_running=True raises RuntimeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_batch_file(tmpdir)
            with patch.object(
                execute, "liveness_check",
                return_value=(True, 1, "2025-01-01T10:00:00Z"),
            ):
                with self.assertRaises(RuntimeError):
                    execute._abort(tmpdir)
            # File must NOT have been deleted
            self.assertTrue(
                os.path.exists(os.path.join(tmpdir, "BATCH_IN_PROGRESS.json"))
            )

    def test_abort_missing_batch_file_raises_runtime_error(self):
        """--abort with no batch file raises RuntimeError (from load_batch)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(RuntimeError):
                execute._abort(tmpdir)

    def test_abort_sweeps_stray_ramdisk_symlink(self):
        """--abort removes a leftover ramdisk symlink + target for a run that
        never completed teardown (spec-execute.md §6.4)."""
        with tempfile.TemporaryDirectory() as reporoot:
            project_dir = reporoot
            os.makedirs(project_dir, exist_ok=True)

            ramdisk_target = os.path.join(reporoot, "ramdisk", "run-abort-001")
            os.makedirs(ramdisk_target, exist_ok=True)
            with open(os.path.join(ramdisk_target, "stdout.log"), "w") as fh:
                fh.write("partial output")

            result_dir = os.path.join(project_dir, "results", "run-abort-001")
            os.makedirs(os.path.dirname(result_dir), exist_ok=True)
            os.symlink(ramdisk_target, result_dir)

            runs = [{"runid": "run-abort-001", "seq": 1,
                     "result_dir": "results/run-abort-001"}]
            self._make_batch_file(
                project_dir, runs=runs,
                ramdisk_path=os.path.join(reporoot, "ramdisk"),
            )

            execute._abort(project_dir)

            self.assertFalse(os.path.islink(result_dir))
            self.assertFalse(os.path.exists(result_dir))
            self.assertFalse(os.path.exists(ramdisk_target))
            self.assertFalse(
                os.path.exists(os.path.join(project_dir, "BATCH_IN_PROGRESS.json"))
            )

    def test_abort_liveness_check_resolves_result_dir_against_project(self):
        """_abort resolves each run's result_dir against the project, so a
        genuinely running entry is actually detected."""
        with tempfile.TemporaryDirectory() as reporoot:
            project_dir = reporoot
            os.makedirs(project_dir, exist_ok=True)

            run_dir = os.path.join(project_dir, "results", "run-live-001")
            os.makedirs(run_dir, exist_ok=True)
            with open(os.path.join(run_dir, "run-info.json"), "w", encoding="utf-8") as fh:
                json.dump({"status": "RUNNING", "pid": os.getpid(),
                           "time": "2025-01-01T00:00:00.000+00:00", "sudo_mode": False}, fh)

            runs = [{"runid": "run-live-001", "seq": 1,
                     "result_dir": "results/run-live-001"}]
            self._make_batch_file(project_dir, runs=runs)

            with self.assertRaises(RuntimeError):
                execute._abort(project_dir)

            # Must NOT have been deleted — the batch is genuinely running.
            self.assertTrue(
                os.path.exists(os.path.join(project_dir, "BATCH_IN_PROGRESS.json"))
            )


class TestActiveBatchLink(unittest.TestCase):
    """The per-user active-batch link (PRD.md §7, spec-execute.md §1.1a)."""

    def setUp(self):
        _remove_active_batch_link()
        self.addCleanup(_remove_active_batch_link)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)

    def _write_batch(self, project, runs=None):
        path = os.path.join(project, "BATCH_IN_PROGRESS.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_make_batch(hostname="testhost", runs=runs or []), fh)
        return path

    def _link_to(self, target):
        os.makedirs(os.path.dirname(_active_batch_link()), exist_ok=True)
        os.symlink(target, _active_batch_link())

    def _options(self, **kw):
        return {"abort": False, "shutdown": False, "verbose": False, "cooldown_secs": 0, **kw}

    def test_link_set_while_running_and_removed_on_completion(self):
        batch_path = self._write_batch(self.project, runs=[
            {"seq": 1, "result_dir": "results/r1"}])
        seen = []

        def fake_execute_run(*args, **kwargs):
            seen.append(os.readlink(_active_batch_link()))
            return "DONE"

        with (
            patch.object(execute, "check_hostname"),
            patch.object(execute, "execute_run", side_effect=fake_execute_run),
        ):
            execute.run_execute(self._options(), _reporoot=self.tmp, _project_dir=self.project)
        self.assertEqual(seen, [os.path.abspath(batch_path)])
        self.assertFalse(os.path.lexists(_active_batch_link()))

    def test_interrupted_batch_keeps_link(self):
        batch_path = self._write_batch(self.project, runs=[
            {"seq": 1, "result_dir": "results/r1"}])
        with (
            patch.object(execute, "check_hostname"),
            patch.object(execute, "execute_run", side_effect=execute._StoppedByUser()),
        ):
            execute.run_execute(self._options(), _reporoot=self.tmp, _project_dir=self.project)
        self.assertEqual(os.readlink(_active_batch_link()), os.path.abspath(batch_path))

    def test_active_batch_in_other_project_blocks_execute(self):
        self._write_batch(self.project)
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self._link_to(self._write_batch(other))
        with patch.object(execute, "execute_run") as mock_exec:
            with self.assertRaises(RuntimeError) as ctx:
                execute.run_execute(self._options(), _reporoot=self.tmp, _project_dir=self.project)
        self.assertIn("another project", str(ctx.exception))
        mock_exec.assert_not_called()

    def test_abort_refuses_other_projects_batch(self):
        self._write_batch(self.project)
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self._link_to(self._write_batch(other))
        with self.assertRaises(RuntimeError):
            execute._abort(self.project)
        self.assertTrue(os.path.exists(os.path.join(self.project, "BATCH_IN_PROGRESS.json")))
        self.assertTrue(os.path.islink(_active_batch_link()))

    def test_abort_removes_dangling_link_even_without_batch(self):
        self._link_to(os.path.join(self.tmp, "gone", "BATCH_IN_PROGRESS.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            execute._abort(self.project)
        self.assertFalse(os.path.lexists(_active_batch_link()))

    def test_abort_removes_own_link(self):
        self._link_to(self._write_batch(self.project))
        with contextlib.redirect_stdout(io.StringIO()):
            execute._abort(self.project)
        self.assertFalse(os.path.lexists(_active_batch_link()))
        self.assertTrue(os.path.isfile(
            os.path.join(self.project, "batches", "abc123", "batch-aborted.json")))


class TestRunExecuteBatchLoop(unittest.TestCase):
    """Tests for execute.run_execute batch-loop behaviour."""

    def setUp(self):
        _remove_active_batch_link()
        self.addCleanup(_remove_active_batch_link)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_entry(self, seq, result_dir_rel):
        return {
            "seq": seq,
            "total": 3,
            "result_dir": result_dir_rel,
            "benchmark": "dacapo",
            "java_home": "/opt/jdk",
            "jvmargs": [],
            "dacapo_dir": "/dacapo",
            "sub_benchmark": "h2",
            "datasize": "small",
            "iterations": 1,
            "bench_json": "bench.run.toml",
            "jdkname": "temurin-21",
            "heapsize": 4096,
        }

    def _write_batch(self, tmpdir, runs, sudo_mode=False, ramdisk_path=None,
                      stabilize_machine=None, collectors=None):
        batch = _make_batch(
            hostname="testhost", runs=runs, sudo_mode=sudo_mode, collectors=collectors,
        )
        if stabilize_machine is not None:
            batch["stabilize_machine"] = stabilize_machine
        if ramdisk_path is not None:
            batch["ramdisk_path"] = ramdisk_path
        batch_path = os.path.join(tmpdir, "BATCH_IN_PROGRESS.json")
        with open(batch_path, "w", encoding="utf-8") as fh:
            json.dump(batch, fh)
        return batch_path

    def _options(self, abort=False, shutdown=False, verbose=False, cooldown_secs=0):
        return {
            "abort": abort,
            "shutdown": shutdown,
            "verbose": verbose,
            "cooldown_secs": cooldown_secs,
        }

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_run_execute_passes_verbose_flag(self):
        """run_execute correctly passes the verbose flag to execute_run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            rel = "results/run-004"
            entry = self._run_entry(seq=1, result_dir_rel=rel)
            self._write_batch(project_dir, runs=[entry])

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE") as mock_exec,
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
            ):
                opts = self._options(verbose=True)
                execute.run_execute(opts, _reporoot=tmpdir, _project_dir=tmpdir)
                mock_exec.assert_called_once_with(
                    {**entry, "collectors": _NO_COLLECTORS}, tmpdir, False,
                    batch_id="abc123",
                    collector_interval=10,
                    verbose=True,
                    timeout_secs=None,
                    ramdisk_path=None,
                    cpu_list=None,
                    cpu_list_harness=None,
                    total_runs=1,
                    project_dir=tmpdir,
                )

    def _run_with_mocked_execute_run(self, tmpdir, entries, **batch_kwargs):
        self._write_batch(tmpdir, runs=entries, **batch_kwargs)
        with (
            patch.object(execute, "check_hostname"),
            patch.object(
                execute, "liveness_check",
                return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
            ),
            patch.object(execute, "execute_run", return_value="DONE") as mock_exec,
            patch.object(execute.shutil, "copy2"),
            patch.object(execute.os, "rename"),
        ):
            execute.run_execute(self._options(), _reporoot=tmpdir, _project_dir=tmpdir)
        return mock_exec

    def test_run_execute_uses_batch_collectors_for_every_run(self):
        """Every run gets the batch's collectors (PRD.md D-84), as its own
        'collectors' and its interval as collector_interval."""
        collectors = {"interval_secs": 2, "compression": True, "enabled": ["proc"],
                      "perf_events": None}
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [self._run_entry(seq=i, result_dir_rel=f"results/run-00{i}")
                       for i in (1, 2)]
            mock_exec = self._run_with_mocked_execute_run(tmpdir, entries, collectors=collectors)
        self.assertEqual(mock_exec.call_count, 2)
        for call in mock_exec.call_args_list:
            self.assertEqual(call.args[0]["collectors"], collectors)
            self.assertEqual(call.kwargs["collector_interval"], 2)
        # the batch file itself keeps the collectors at the top level only
        for entry in entries:
            self.assertNotIn("collectors", entry)

    def test_batch_with_per_run_collectors_is_refused(self):
        """A batch from before D-84 (no top-level collectors) is not executed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = self._run_entry(seq=1, result_dir_rel="results/run-001")
            entry["collectors"] = {"interval_secs": 2, "compression": False, "enabled": []}
            batch_path = self._write_batch(tmpdir, runs=[entry])
            with open(batch_path, encoding="utf-8") as fh:
                batch = json.load(fh)
            del batch["collectors"]
            with open(batch_path, "w", encoding="utf-8") as fh:
                json.dump(batch, fh)
            with (
                patch.object(execute, "check_hostname"),
                patch.object(execute, "execute_run") as mock_exec,
            ):
                with self.assertRaisesRegex(RuntimeError, "older jbr-prepare"):
                    execute.run_execute(self._options(), _reporoot=tmpdir, _project_dir=tmpdir)
            mock_exec.assert_not_called()

    def test_done_run_is_skipped(self):
        """A run whose status is DONE is skipped (execute_run not called)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            rel = "results/run-001"
            entry = self._run_entry(seq=1, result_dir_rel=rel)
            self._write_batch(project_dir, runs=[entry])

            # Create run-info.json with DONE status
            run_dir = os.path.join(tmpdir, rel)
            _write_run_info(run_dir, status="DONE", pid=9999)

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 1, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run") as mock_exec,
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
            ):
                execute.run_execute(self._options(), _reporoot=tmpdir, _project_dir=tmpdir)
                mock_exec.assert_not_called()

    def test_stopped_run_result_dir_outside_results_not_removed(self):
        """A STOPPED run whose batch-file result_dir escapes results/ is
        refused rather than rmtree'd."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = os.path.join(tmpdir, "project")
            os.makedirs(os.path.join(project_dir, "results"))
            victim = os.path.join(tmpdir, "victim")
            rel = "results/../../victim"
            entry = self._run_entry(seq=1, result_dir_rel=rel)
            self._write_batch(project_dir, runs=[entry])
            _write_run_info(victim, status="STOPPED", pid=9999)

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE") as mock_exec,
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
            ):
                with self.assertRaises(RuntimeError):
                    execute.run_execute(self._options(), _reporoot=tmpdir,
                                        _project_dir=project_dir)
                mock_exec.assert_not_called()

            self.assertTrue(os.path.isdir(victim))

    def test_stopped_run_has_result_dir_removed_and_retried(self):
        """A STOPPED run's result directory is removed and execute_run is called."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            rel = "results/run-002"
            entry = self._run_entry(seq=1, result_dir_rel=rel)
            self._write_batch(project_dir, runs=[entry])

            run_dir = os.path.join(tmpdir, rel)
            _write_run_info(run_dir, status="STOPPED", pid=9999)
            self.assertTrue(os.path.isdir(run_dir))

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE"),
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
            ):
                execute.run_execute(self._options(), _reporoot=tmpdir, _project_dir=tmpdir)

            # Result dir must have been wiped by shutil.rmtree
            self.assertFalse(os.path.exists(run_dir))

    def test_orphaned_ramdisk_run_symlink_and_target_removed_before_retry(self):
        """An orphaned RUNNING run whose result_dir is still a ramdisk symlink
        (harness died before teardown, spec-execute.md §6.3) has both the
        symlink and its ramdisk target removed before being retried."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            rel = "results/run-ramdisk-orphan"
            entry = self._run_entry(seq=1, result_dir_rel=rel)
            self._write_batch(
                project_dir, runs=[entry],
                ramdisk_path=os.path.join(tmpdir, "ramdisk"),
            )

            ramdisk_target = os.path.join(tmpdir, "ramdisk", "run-ramdisk-orphan")
            os.makedirs(ramdisk_target, exist_ok=True)
            with open(os.path.join(ramdisk_target, "stdout.log"), "w") as fh:
                fh.write("partial output")

            run_dir = os.path.join(tmpdir, rel)
            os.makedirs(os.path.dirname(run_dir), exist_ok=True)
            os.symlink(ramdisk_target, run_dir)
            with open(os.path.join(run_dir, "run-info.json"), "w", encoding="utf-8") as fh:
                json.dump({"status": "RUNNING", "pid": 99999999,
                           "time": "2025-01-01T00:00:00.000+00:00", "sudo_mode": False}, fh)

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE"),
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
            ):
                execute.run_execute(self._options(), _reporoot=tmpdir, _project_dir=tmpdir)

            self.assertFalse(os.path.islink(run_dir))
            self.assertFalse(os.path.exists(run_dir))
            self.assertFalse(os.path.exists(ramdisk_target))

    def test_batch_file_moved_to_batches_after_all_runs(self):
        """After all runs complete, BATCH_IN_PROGRESS.json is moved to
        batches/<batchid>/batch.json and the active-batch link is removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rel = "results/run-003"
            entry = self._run_entry(seq=1, result_dir_rel=rel)
            self._write_batch(tmpdir, runs=[entry])

            # Run is DONE — skip execution
            run_dir = os.path.join(tmpdir, rel)
            _write_run_info(run_dir, status="DONE", pid=9999)

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 1, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run"),
            ):
                execute.run_execute(self._options(), _reporoot=tmpdir, _project_dir=tmpdir)

            self.assertFalse(os.path.exists(os.path.join(tmpdir, "BATCH_IN_PROGRESS.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "batches", "abc123", "batch.json")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "LAST_BATCH_IN_PROGRESS.json")))
            self.assertFalse(os.path.lexists(_active_batch_link()))

    def test_cooldown_sleeps_between_runs_but_not_after_last(self):
        """--cooldown-secs sleeps between executed runs, but not after the last one."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            entries = [
                self._run_entry(seq=1, result_dir_rel="results/run-a"),
                self._run_entry(seq=2, result_dir_rel="results/run-b"),
            ]
            self._write_batch(project_dir, runs=entries)

            sleep_calls = []
            fake_now = [0.0]

            def fake_sleep(s):
                sleep_calls.append(s)
                fake_now[0] += s

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE") as mock_exec,
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
                patch.object(execute.time, "sleep", side_effect=fake_sleep),
                patch.object(execute.time, "monotonic", side_effect=lambda: fake_now[0]),
            ):
                execute.run_execute(
                    self._options(cooldown_secs=5), _reporoot=tmpdir, _project_dir=tmpdir
                )

            # Both runs executed, and the total time slept equals exactly one
            # 5s cooldown window (between run 1 and run 2, none after run 2).
            self.assertEqual(mock_exec.call_count, 2)
            self.assertAlmostEqual(sum(sleep_calls), 5)

    def test_no_cooldown_by_default(self):
        """cooldown_secs=0 (the default) never sleeps between runs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            entries = [
                self._run_entry(seq=1, result_dir_rel="results/run-a"),
                self._run_entry(seq=2, result_dir_rel="results/run-b"),
            ]
            self._write_batch(project_dir, runs=entries)

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE"),
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
                patch.object(execute.time, "sleep") as mock_sleep,
            ):
                execute.run_execute(self._options(), _reporoot=tmpdir, _project_dir=tmpdir)

            mock_sleep.assert_not_called()

    def test_cooldown_interrupted_by_sigint_stops_batch(self):
        """SIGINT during the cooldown sleep stops the batch immediately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            entries = [
                self._run_entry(seq=1, result_dir_rel="results/run-a"),
                self._run_entry(seq=2, result_dir_rel="results/run-b"),
            ]
            self._write_batch(project_dir, runs=entries)

            def fake_sleep(_secs):
                execute._sigint_received = True

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE") as mock_exec,
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
                patch.object(execute.time, "sleep", side_effect=fake_sleep),
            ):
                execute._sigint_received = False
                execute.run_execute(
                    self._options(cooldown_secs=5), _reporoot=tmpdir, _project_dir=tmpdir
                )
                execute._sigint_received = False  # reset for other tests

            # Only the first run executed; the second was never reached.
            self.assertEqual(mock_exec.call_count, 1)

    def _run_stabilize(self, tmpdir, sudo_mode, stabilize_machine,
                       system="Linux", stabilize_side_effect=None,
                       reset_side_effect=None):
        """Run a two-run batch; return (mock_stabilize, mock_exec, stdout).

        The per-run reset mock is left in self._last_reset."""
        import io
        entries = [
            self._run_entry(seq=1, result_dir_rel="results/run-a"),
            self._run_entry(seq=2, result_dir_rel="results/run-b"),
        ]
        self._write_batch(tmpdir, runs=entries, sudo_mode=sudo_mode,
                          stabilize_machine=stabilize_machine)
        with (
            patch.object(execute, "check_hostname"),
            patch.object(
                execute, "liveness_check",
                return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
            ),
            patch.object(execute, "execute_run", return_value="DONE") as mock_exec,
            patch.object(execute, "_stabilize_machine",
                         side_effect=stabilize_side_effect) as mock_stabilize,
            patch.object(execute, "_reset_machine",
                         side_effect=reset_side_effect) as mock_reset,
            patch.object(execute, "stabilization_warnings", return_value=[]),
            patch.object(execute.platform, "system", return_value=system),
            patch.object(execute.shutil, "copy2"),
            patch.object(execute.os, "rename"),
            patch.object(execute.subprocess, "run", return_value=MagicMock(returncode=0)),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            self._last_exec = mock_exec
            self._last_reset = mock_reset
            try:
                execute.run_execute(self._options(), _reporoot=tmpdir, _project_dir=tmpdir)
            finally:
                execute._sigint_received = False
        return mock_stabilize, mock_exec, mock_stdout.getvalue()

    def test_stabilize_machine_runs_once_at_batch_start(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_stabilize, mock_exec, _ = self._run_stabilize(
                tmpdir, sudo_mode=True, stabilize_machine=True)
            mock_stabilize.assert_called_once()
            self.assertEqual(mock_exec.call_count, 2)
            # reset before every executed run, including the first
            self.assertEqual(self._last_reset.call_count, 2)

    def test_stabilize_machine_off_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_stabilize, mock_exec, _ = self._run_stabilize(
                tmpdir, sudo_mode=True, stabilize_machine=None)
            mock_stabilize.assert_not_called()
            self._last_reset.assert_not_called()
            self.assertEqual(mock_exec.call_count, 2)

    def test_stabilize_machine_skipped_with_warning_when_not_sudo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_stabilize, mock_exec, out = self._run_stabilize(
                tmpdir, sudo_mode=False, stabilize_machine=True)
            mock_stabilize.assert_not_called()
            self._last_reset.assert_not_called()
            self.assertEqual(mock_exec.call_count, 2)
            self.assertIn("WARNING: stabilize_machine", out)

    def test_stabilize_machine_skipped_with_warning_when_not_linux(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_stabilize, mock_exec, out = self._run_stabilize(
                tmpdir, sudo_mode=True, stabilize_machine=True, system="Darwin")
            mock_stabilize.assert_not_called()
            self._last_reset.assert_not_called()
            self.assertEqual(mock_exec.call_count, 2)
            self.assertIn("not Linux", out)

    def test_stabilize_machine_failure_aborts_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(RuntimeError):
                self._run_stabilize(tmpdir, sudo_mode=True, stabilize_machine=True,
                                    stabilize_side_effect=RuntimeError("boom"))
            self._last_exec.assert_not_called()

    def test_stabilize_machine_explicitly_off_never_resets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_stabilize, mock_exec, _ = self._run_stabilize(
                tmpdir, sudo_mode=True, stabilize_machine=False)
            mock_stabilize.assert_not_called()
            self._last_reset.assert_not_called()
            self.assertEqual(mock_exec.call_count, 2)

    def test_reset_machine_failure_aborts_batch(self):
        """A failing reset-machine.sh raises, so no run is reached."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(RuntimeError):
                self._run_stabilize(tmpdir, sudo_mode=True, stabilize_machine=True,
                                    reset_side_effect=RuntimeError("boom"))
            self._last_exec.assert_not_called()

    def test_stabilize_machine_invokes_script_without_sudo(self):
        # The script runs as the invoking user and sudoes only the
        # privileged commands itself.
        import io
        with (
            patch.object(execute.subprocess, "run",
                         return_value=MagicMock(returncode=0)) as mock_run,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            execute._stabilize_machine()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(len(cmd), 1)
        self.assertTrue(cmd[0].endswith("phase-execute/prepare-for-benchmarks.sh"))
        self.assertTrue(os.access(cmd[0], os.X_OK))

    def test_stabilize_machine_script_failure_raises(self):
        import io
        with (
            patch.object(execute.subprocess, "run", return_value=MagicMock(returncode=1)),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            with self.assertRaises(RuntimeError):
                execute._stabilize_machine()

    def test_shutdown_without_sudo_mode_raises(self):
        """--shutdown requires the batch to be in --sudo mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            entry = self._run_entry(seq=1, result_dir_rel="results/run-a")
            self._write_batch(project_dir, runs=[entry], sudo_mode=False)

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE") as mock_exec,
            ):
                with self.assertRaises(RuntimeError):
                    execute.run_execute(self._options(shutdown=True), _reporoot=tmpdir, _project_dir=tmpdir)

            mock_exec.assert_not_called()

    def test_shutdown_fires_after_full_completion(self):
        """--shutdown runs the shutdown sequence once all runs complete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            entries = [
                self._run_entry(seq=1, result_dir_rel="results/run-a"),
                self._run_entry(seq=2, result_dir_rel="results/run-b"),
            ]
            self._write_batch(project_dir, runs=entries, sudo_mode=True)

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE"),
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
                patch.object(execute.subprocess, "run", return_value=MagicMock(returncode=0)),
                patch.object(execute, "_run_shutdown_sequence") as mock_shutdown,
            ):
                execute.run_execute(self._options(shutdown=True), _reporoot=tmpdir, _project_dir=tmpdir)

            mock_shutdown.assert_called_once()

    def test_shutdown_not_fired_when_interrupted_by_sigint(self):
        """A batch stopped by Ctrl-C must not trigger --shutdown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = tmpdir
            os.makedirs(project_dir, exist_ok=True)
            entries = [
                self._run_entry(seq=1, result_dir_rel="results/run-a"),
                self._run_entry(seq=2, result_dir_rel="results/run-b"),
            ]
            self._write_batch(project_dir, runs=entries, sudo_mode=True)

            def fake_sleep(_secs):
                execute._sigint_received = True

            with (
                patch.object(execute, "check_hostname"),
                patch.object(
                    execute, "liveness_check",
                    return_value=(False, 0, "2025-01-01T00:00:00.000+00:00"),
                ),
                patch.object(execute, "execute_run", return_value="DONE"),
                patch.object(execute.shutil, "copy2"),
                patch.object(execute.os, "rename"),
                patch.object(execute.subprocess, "run", return_value=MagicMock(returncode=0)),
                patch.object(execute.time, "sleep", side_effect=fake_sleep),
                patch.object(execute, "_run_shutdown_sequence") as mock_shutdown,
            ):
                execute._sigint_received = False
                execute.run_execute(
                    self._options(shutdown=True, cooldown_secs=5), _reporoot=tmpdir, _project_dir=tmpdir
                )
                execute._sigint_received = False  # reset for other tests

            mock_shutdown.assert_not_called()


class TestResetMachineHelper(unittest.TestCase):
    """Tests for execute._reset_machine."""

    def test_invokes_reset_machine_script(self):
        with patch.object(
            execute.subprocess, "run", return_value=MagicMock(returncode=0)
        ) as mock_run:
            execute._reset_machine("/some/reporoot")
        mock_run.assert_called_once()
        script_path = mock_run.call_args[0][0][0]
        self.assertTrue(script_path.endswith("reset-machine.sh"))

    def test_raises_runtime_error_on_nonzero_exit(self):
        with patch.object(execute.subprocess, "run", return_value=MagicMock(returncode=1)):
            with self.assertRaises(RuntimeError):
                execute._reset_machine("/some/reporoot")


class TestShutdownSequence(unittest.TestCase):
    """Tests for execute._run_shutdown_sequence (--shutdown)."""

    def test_warns_waits_then_shuts_down(self):
        with (
            patch.object(execute.subprocess, "run", return_value=MagicMock(returncode=0)) as mock_run,
            patch.object(execute.time, "sleep"),
            patch.object(execute, "_SHUTDOWN_DELAY_SECS", 0),
        ):
            execute._sigint_received = False
            self.assertTrue(execute._run_shutdown_sequence())

        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertEqual(calls[0][0], "wall")
        self.assertEqual(calls[-1], ["sudo", "-n", "shutdown", "-h", "now"])

    def test_sigint_during_wait_cancels_shutdown(self):
        def fake_sleep(_secs):
            execute._sigint_received = True

        with (
            patch.object(execute.subprocess, "run", return_value=MagicMock(returncode=0)) as mock_run,
            patch.object(execute.time, "sleep", side_effect=fake_sleep),
        ):
            execute._sigint_received = False
            self.assertFalse(execute._run_shutdown_sequence())
            execute._sigint_received = False  # reset for other tests

        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertNotIn(["sudo", "-n", "shutdown", "-h", "now"], calls)


class TestSigintHandler(unittest.TestCase):
    """Tests for execute._install_sigint_handler."""

    def test_sigint_handler_prints_feedback(self):
        import io
        import signal

        handler = None

        def fake_signal(sig, h):
            nonlocal handler
            handler = h

        with patch("signal.signal", side_effect=fake_signal):
            execute._sigint_received = False
            execute._install_sigint_handler()

        self.assertIsNotNone(handler)

        mock_stdout = io.StringIO()
        with patch("sys.stdout", mock_stdout):
            handler(signal.SIGINT, None)

        self.assertTrue(execute._sigint_received)
        self.assertIn("will stop...", mock_stdout.getvalue())

        # Calling it a second time should not duplicate the output
        mock_stdout.seek(0)
        mock_stdout.truncate(0)
        with patch("sys.stdout", mock_stdout):
            handler(signal.SIGINT, None)

        self.assertEqual(mock_stdout.getvalue(), "")


# ---------------------------------------------------------------------------
# execute_run — timeout_secs (§1.5)
# ---------------------------------------------------------------------------

class TestExecuteRunTimeout(unittest.TestCase):
    """execute_run() kills a hung JVM once timeout_secs elapses (spec §1.5)."""

    def setUp(self):
        # Collapse verify_collectors/stop_collectors' fixed real-time delays
        # (5s + 3s + 2s in production) so this test doesn't pay for them.
        patcher1 = patch.object(execute, "_VERIFY_COLLECTORS_DELAY_SECS", 0)
        patcher2 = patch.object(execute, "_STOP_COLLECTORS_GRACE_SECS", 0)
        patcher3 = patch.object(execute, "_STOP_COLLECTORS_KILL_WAIT_SECS", 0)
        patcher1.start()
        patcher2.start()
        patcher3.start()
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        self.addCleanup(patcher3.stop)

        self._orig_cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="jbr_timeout_test_")

        # Fake "java" binary: answers -version, otherwise sleeps well past any
        # timeout used below. exec'd (not forked), so SIGTERM lands directly
        # on the sleeping process with no intermediate shell to worry about.
        java_home = os.path.join(self.tmp, "fake_jdk")
        os.makedirs(os.path.join(java_home, "bin"), exist_ok=True)
        java_path = os.path.join(java_home, "bin", "java")
        with open(java_path, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/bin/sh\n"
                'if [ "$1" = "-version" ]; then\n'
                '  echo \'openjdk version "21.0.4"\' 1>&2\n'
                "  exit 0\n"
                "fi\n"
                "exec sleep 100\n"
            )
        os.chmod(java_path, 0o755)

        specjbb_dir = os.path.join(self.tmp, "specjbb")
        os.makedirs(os.path.join(specjbb_dir, "config"), exist_ok=True)

        self.reporoot = os.path.join(self.tmp, "reporoot")
        os.makedirs(self.reporoot, exist_ok=True)
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project, exist_ok=True)

        self.run_entry = {
            "runid":      "run-timeout-001",
            "result_dir":  "results/run-timeout-001",
            "benchmark":   "specjbb",
            "tags":        [],
            "heapsize":   512,
            "jvmargs":   [],
            "jdkname":     "fake-jdk",
            "java_home":   java_home,
            "specjbb_dir": specjbb_dir,
            "seq": 1,
            "total": 1,
            "bench_json": "specjbb.bench.toml",
        }

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _result_dir(self):
        return os.path.join(self.project, self.run_entry["result_dir"])

    def test_timeout_kills_jvm_and_marks_failed(self):
        status = execute.execute_run(
            self.run_entry,
            self.reporoot,
            project_dir=self.project,
            sudo_mode=False,
            collector_interval=10,
            batch_id="batch-001",
            verbose=False,
            timeout_secs=1,
        )
        self.assertEqual(status, "FAILED")

        with open(os.path.join(self._result_dir(), "run-info.json"), encoding="utf-8") as fh:
            info = json.load(fh)
        self.assertEqual(info["status"], "FAILED")
        self.assertEqual(info["status_info"], "timeout")
        # Killed shortly after the 1s deadline, nowhere near the fake JVM's 100s sleep.
        self.assertLess(info["duration_secs"], 10)


# ---------------------------------------------------------------------------
# Ramdisk-backed result directories — spec-execute.md §6
# ---------------------------------------------------------------------------

class TestRemoveSymlinkedDir(unittest.TestCase):
    """_remove_symlinked_dir: removes a symlink and its target, if present."""

    def test_removes_symlink_and_existing_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ramdisk = os.path.join(tmpdir, "ramdisk")
            target = os.path.join(ramdisk, "target")
            os.makedirs(target)
            with open(os.path.join(target, "f.txt"), "w") as fh:
                fh.write("data")
            link = os.path.join(tmpdir, "link")
            os.symlink(target, link)

            execute._remove_symlinked_dir(link, ramdisk)

            self.assertFalse(os.path.exists(link))
            self.assertFalse(os.path.islink(link))
            self.assertFalse(os.path.exists(target))

    def test_removes_broken_symlink_without_error(self):
        """A dangling symlink (e.g. the ramdisk was cleared) is just unlinked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ramdisk = os.path.join(tmpdir, "ramdisk")
            target = os.path.join(ramdisk, "gone")
            link = os.path.join(tmpdir, "link")
            os.symlink(target, link)

            execute._remove_symlinked_dir(link, ramdisk)

            self.assertFalse(os.path.islink(link))

    def test_refuses_to_remove_target_outside_ramdisk(self):
        """A symlink repointed outside ramdisk_path is refused, not deleted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ramdisk = os.path.join(tmpdir, "ramdisk")
            os.makedirs(ramdisk)
            outside_target = os.path.join(tmpdir, "outside")
            os.makedirs(outside_target)
            with open(os.path.join(outside_target, "important.txt"), "w") as fh:
                fh.write("do not delete")
            link = os.path.join(tmpdir, "link")
            os.symlink(outside_target, link)

            with self.assertRaises(RuntimeError):
                execute._remove_symlinked_dir(link, ramdisk)

            self.assertTrue(os.path.exists(outside_target))
            self.assertTrue(os.path.exists(os.path.join(outside_target, "important.txt")))

    def test_raises_when_ramdisk_path_missing(self):
        """A symlink target exists but the batch recorded no ramdisk_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "target")
            os.makedirs(target)
            link = os.path.join(tmpdir, "link")
            os.symlink(target, link)

            with self.assertRaises(RuntimeError):
                execute._remove_symlinked_dir(link, None)

            self.assertTrue(os.path.exists(target))


class TestDetectFsType(unittest.TestCase):
    """_detect_fs_type: best-effort, never raises."""

    def test_nonexistent_path_returns_none(self):
        self.assertIsNone(execute._detect_fs_type("/no/such/path/at/all"))

    def test_missing_stat_binary_returns_none(self):
        with patch.object(
            execute.subprocess, "run",
            side_effect=FileNotFoundError("no stat"),
        ):
            self.assertIsNone(execute._detect_fs_type("/tmp"))

    def test_successful_detection_returns_stripped_output(self):
        fake_result = MagicMock(returncode=0, stdout="tmpfs\n")
        with patch.object(execute.subprocess, "run", return_value=fake_result):
            self.assertEqual(execute._detect_fs_type("/dev/shm"), "tmpfs")


class TestCheckRamdiskFsType(unittest.TestCase):
    """_check_ramdisk_fs_type: hard-fails on a non-ramdisk fs, best-effort
    otherwise (spec-execute.md §1.3 step 4b)."""

    def _with_detected_type(self, fs_type):
        fake_result = MagicMock(returncode=0, stdout=f"{fs_type}\n")
        return patch.object(execute.subprocess, "run", return_value=fake_result)

    def test_tmpfs_passes(self):
        with self._with_detected_type("tmpfs"):
            execute._check_ramdisk_fs_type("/dev/shm")  # no raise

    def test_ramfs_passes(self):
        with self._with_detected_type("ramfs"):
            execute._check_ramdisk_fs_type("/mnt/ramdisk")  # no raise

    def test_ext4_raises(self):
        with self._with_detected_type("ext4"):
            with self.assertRaises(RuntimeError):
                execute._check_ramdisk_fs_type("/home/user/ramdisk")

    def test_undetectable_type_warns_and_does_not_raise(self):
        with patch.object(
            execute.subprocess, "run",
            side_effect=FileNotFoundError("no stat"),
        ):
            execute._check_ramdisk_fs_type("/tmp/ramdisk")  # no raise


class TestExecuteRunRamdisk(unittest.TestCase):
    """execute_run() with ramdisk_path set (spec-execute.md §6)."""

    def setUp(self):
        # Collapse verify_collectors/stop_collectors' fixed real-time delays
        # (5s + 3s + 2s in production) so this test doesn't pay for them.
        patcher1 = patch.object(execute, "_VERIFY_COLLECTORS_DELAY_SECS", 0)
        patcher2 = patch.object(execute, "_STOP_COLLECTORS_GRACE_SECS", 0)
        patcher3 = patch.object(execute, "_STOP_COLLECTORS_KILL_WAIT_SECS", 0)
        patcher1.start()
        patcher2.start()
        patcher3.start()
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        self.addCleanup(patcher3.stop)

        self._orig_cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="jbr_ramdisk_test_")

        java_home = os.path.join(self.tmp, "fake_jdk")
        os.makedirs(os.path.join(java_home, "bin"), exist_ok=True)
        java_path = os.path.join(java_home, "bin", "java")
        with open(java_path, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/bin/sh\n"
                'if [ "$1" = "-version" ]; then\n'
                '  echo \'openjdk version "21.0.4"\' 1>&2\n'
                "  exit 0\n"
                "fi\n"
                "echo benchmark output\n"
                "exit 0\n"
            )
        os.chmod(java_path, 0o755)

        specjbb_dir = os.path.join(self.tmp, "specjbb")
        os.makedirs(os.path.join(specjbb_dir, "config"), exist_ok=True)

        self.reporoot = os.path.join(self.tmp, "reporoot")
        os.makedirs(self.reporoot, exist_ok=True)
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project, exist_ok=True)

        self.ramdisk_path = os.path.join(self.tmp, "ramdisk")
        os.makedirs(self.ramdisk_path, exist_ok=True)

        self.run_entry = {
            "runid":      "run-ramdisk-001",
            "result_dir":  "results/run-ramdisk-001",
            "benchmark":   "specjbb",
            "tags":        [],
            "heapsize":   512,
            "jvmargs":   [],
            "jdkname":     "fake-jdk",
            "java_home":   java_home,
            "specjbb_dir": specjbb_dir,
            "seq": 1,
            "total": 1,
            "bench_json": "specjbb.bench.toml",
            "use_ramdisk": True,
        }

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _result_dir(self):
        return os.path.join(self.project, self.run_entry["result_dir"])

    def test_successful_run_ends_with_real_dir_and_no_ramdisk_residue(self):
        status = execute.execute_run(
            self.run_entry,
            self.reporoot,
            project_dir=self.project,
            sudo_mode=False,
            collector_interval=10,
            batch_id="batch-001",
            verbose=False,
            ramdisk_path=self.ramdisk_path,
        )
        self.assertEqual(status, "DONE")

        result_dir = self._result_dir()
        # results/<run_id> must be a real directory now, not a symlink —
        # teardown has copied it back and removed the ramdisk copy.
        self.assertTrue(os.path.isdir(result_dir))
        self.assertFalse(os.path.islink(result_dir))
        self.assertTrue(os.path.exists(os.path.join(result_dir, "info.json")))
        self.assertTrue(os.path.exists(os.path.join(result_dir, "run-info.json")))

        self.assertFalse(
            os.path.exists(os.path.join(self.ramdisk_path, "run-ramdisk-001"))
        )
        self.assertEqual(os.listdir(self.ramdisk_path), [])

        with open(os.path.join(result_dir, "run-info.json"), encoding="utf-8") as fh:
            info = json.load(fh)
        self.assertEqual(info["ramdisk_path"], self.ramdisk_path)
        self.assertIsInstance(info["ramdisk_size_mb"], int)
        self.assertGreater(info["ramdisk_size_mb"], 0)
        # ramdisk_fs_type is best-effort (e.g. BSD stat on macOS doesn't
        # support the flags used) — only check its type when present.
        if "ramdisk_fs_type" in info:
            self.assertIsInstance(info["ramdisk_fs_type"], str)

    def test_use_ramdisk_false_skips_ramdisk_even_when_path_given(self):
        self.run_entry["use_ramdisk"] = False
        status = execute.execute_run(
            self.run_entry,
            self.reporoot,
            project_dir=self.project,
            sudo_mode=False,
            collector_interval=10,
            batch_id="batch-001",
            verbose=False,
            ramdisk_path=self.ramdisk_path,
        )
        self.assertEqual(status, "DONE")

        result_dir = self._result_dir()
        self.assertTrue(os.path.isdir(result_dir))
        self.assertFalse(os.path.islink(result_dir))

        # Ramdisk must never have been touched.
        self.assertEqual(os.listdir(self.ramdisk_path), [])

        with open(os.path.join(result_dir, "run-info.json"), encoding="utf-8") as fh:
            info = json.load(fh)
        self.assertNotIn("ramdisk_path", info)

    def test_use_ramdisk_false_prints_message_when_verbose(self):
        self.run_entry["use_ramdisk"] = False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            execute.execute_run(
                self.run_entry,
                self.reporoot,
                project_dir=self.project,
                sudo_mode=False,
                collector_interval=10,
                batch_id="batch-001",
                verbose=True,
                ramdisk_path=self.ramdisk_path,
            )
        self.assertIn("No Ramdisk will be used - IO jitter can occur", buf.getvalue())

    def test_use_ramdisk_true_does_not_print_no_ramdisk_message(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            execute.execute_run(
                self.run_entry,
                self.reporoot,
                project_dir=self.project,
                sudo_mode=False,
                collector_interval=10,
                batch_id="batch-001",
                verbose=True,
                ramdisk_path=self.ramdisk_path,
            )
        self.assertNotIn("No Ramdisk will be used", buf.getvalue())


class TestCheckPerfCommand(unittest.TestCase):
    """_check_perf_command: one check of the batch's perf command (PRD.md D-86)."""

    def _check(self, perf_events, returncode=0, stdout="1234;;cycles;1000;100.00;;\n"):
        import io
        with (
            patch.object(execute.subprocess, "run",
                         return_value=MagicMock(returncode=returncode, stdout=stdout)) as run,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            execute._check_perf_command(perf_events)
        return run.call_args[0][0]

    def test_checks_collector_command_with_events(self):
        cmd = self._check(["cycles", "cpu/event=0x3c,umask=0/"])
        self.assertEqual(cmd[0], "bash")
        self.assertTrue(cmd[1].endswith("collectors/perf-logger.sh"))
        self.assertTrue(os.path.isfile(cmd[1]))
        self.assertEqual(cmd[2:], ["--check", "--counters", "cycles,cpu/event=0x3c,umask=0/"])

    def test_default_set_checked_without_counters(self):
        self.assertEqual(self._check(None)[2:], ["--check"])

    def test_not_counted_is_fine(self):
        self._check(["cycles"], stdout="<not counted>;;cycles;0;0.00;;\n")

    def test_perf_failure_raises_with_perf_output(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._check(["cyclez"], returncode=129,
                        stdout="event syntax error: 'cyclez'\n  \\___ parser error\n")
        msg = str(ctx.exception)
        self.assertIn("exited with code 129", msg)
        self.assertIn("event syntax error: 'cyclez'", msg)
        self.assertIn("jbr-execute --abort", msg)

    def test_not_supported_event_raises_naming_it(self):
        out = "1234;;cycles;1000;100.00;;\n<not supported>;;LLC-loads;0;100.00;;\n"
        with self.assertRaises(RuntimeError) as ctx:
            self._check(["cycles", "LLC-loads"], stdout=out)
        self.assertIn("does not support event(s) LLC-loads", str(ctx.exception))


class TestPerfCheckAtBatchStart(unittest.TestCase):
    """run_execute checks the perf command once, before the first run."""

    def setUp(self):
        _remove_active_batch_link()
        self.addCleanup(_remove_active_batch_link)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run(self, enabled, check_side_effect=None):
        collectors = dict(_NO_COLLECTORS, enabled=enabled, perf_events=["cycles"])
        with open(os.path.join(self.tmp, "BATCH_IN_PROGRESS.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(_make_batch(runs=[{"seq": 1, "result_dir": "results/r1"},
                                        {"seq": 2, "result_dir": "results/r2"}],
                                  collectors=collectors), fh)
        options = {"abort": False, "shutdown": False, "verbose": False, "cooldown_secs": 0}
        with (
            patch.object(execute, "check_hostname"),
            patch.object(execute, "execute_run", return_value="DONE") as mock_exec,
            patch.object(execute, "_check_perf_command",
                         side_effect=check_side_effect) as mock_check,
            patch.object(execute.platform, "system", return_value="Darwin"),
            patch.object(execute.shutil, "copy2"),
            patch.object(execute.os, "rename"),
        ):
            try:
                execute.run_execute(options, _reporoot=self.tmp, _project_dir=self.tmp)
            except RuntimeError:
                pass
        return mock_check, mock_exec

    def test_checked_once_with_batch_events(self):
        mock_check, mock_exec = self._run(["proc", "perf"])
        mock_check.assert_called_once_with(["cycles"])
        self.assertEqual(mock_exec.call_count, 2)

    def test_not_checked_without_perf(self):
        mock_check, mock_exec = self._run(["proc"])
        mock_check.assert_not_called()
        self.assertEqual(mock_exec.call_count, 2)

    def test_failed_check_stops_batch_before_first_run(self):
        mock_check, mock_exec = self._run(["perf"],
                                          check_side_effect=RuntimeError("perf check failed"))
        mock_check.assert_called_once()
        mock_exec.assert_not_called()


class TestCheckSudoRules(unittest.TestCase):
    """_check_sudo_rules: the sudoers rules are checked at batch start (PRD.md D-87)."""

    def _check(self, stabilize, shutdown, allowed=()):
        def fake_run(cmd, **_kw):
            return MagicMock(returncode=0 if cmd[3] in allowed else 1)
        with patch.object(execute.subprocess, "run", side_effect=fake_run) as run:
            execute._check_sudo_rules(stabilize, shutdown)
        return [c.args[0] for c in run.call_args_list]

    def test_checks_one_command_per_needed_feature(self):
        calls = self._check(True, True, allowed=("/usr/bin/tee", "/usr/sbin/shutdown"))
        self.assertEqual(calls, [["sudo", "-n", "-l"] + execute._SUDO_CHECK_STABILIZE,
                                 ["sudo", "-n", "-l"] + execute._SUDO_CHECK_SHUTDOWN])

    def test_nothing_needed_nothing_checked(self):
        self.assertEqual(self._check(False, False), [])

    def test_missing_rules_raise_naming_features(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._check(True, True, allowed=("/usr/bin/tee",))
        msg = str(ctx.exception)
        self.assertIn("--shutdown", msg)
        self.assertNotIn("machine stabilization", msg)
        self.assertIn("config/jbenchrunner.sudoers", msg)
        self.assertIn("group 'jbenchrunners'", msg)

    def test_no_sudo_ticket_requested(self):
        # No 'sudo -v', no keepalive: only non-interactive checks.
        for cmd in self._check(True, True, allowed=("/usr/bin/tee", "/usr/sbin/shutdown")):
            self.assertEqual(cmd[:3], ["sudo", "-n", "-l"])


class TestStabilizationReminder(unittest.TestCase):
    """After stabilizing, jbr-execute recommends a reboot (PRD.md D-88), however
    the batch ends, unless --shutdown powered the machine off."""

    def setUp(self):
        _remove_active_batch_link()
        self.addCleanup(_remove_active_batch_link)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run(self, stabilize=True, shutdown=False, shutdown_result=True,
             exec_side_effect=None):
        batch = _make_batch(runs=[{"seq": 1, "result_dir": "results/r1"}], sudo_mode=True)
        batch["stabilize_machine"] = stabilize
        with open(os.path.join(self.tmp, "BATCH_IN_PROGRESS.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(batch, fh)
        options = {"abort": False, "shutdown": shutdown, "verbose": False,
                   "cooldown_secs": 0}

        def fake_stabilize():
            execute._machine_stabilized = True

        error = None
        with (
            patch.object(execute, "check_hostname"),
            patch.object(execute, "_check_sudo_rules"),
            patch.object(execute, "_stabilize_machine", side_effect=fake_stabilize),
            patch.object(execute, "_reset_machine"),
            patch.object(execute, "stabilization_warnings", return_value=[]),
            patch.object(execute.platform, "system", return_value="Linux"),
            patch.object(execute, "execute_run", return_value="DONE",
                         side_effect=exec_side_effect),
            patch.object(execute, "_run_shutdown_sequence", return_value=shutdown_result),
            patch.object(execute.shutil, "copy2"),
            patch.object(execute.os, "rename"),
            patch("sys.stdout", new_callable=io.StringIO) as out,
        ):
            try:
                execute.run_execute(options, _reporoot=self.tmp, _project_dir=self.tmp)
            except RuntimeError as exc:
                error = exc
        return out.getvalue(), error

    def test_reminder_after_completed_batch(self):
        out, _ = self._run()
        self.assertIn("weaken security", out)
        self.assertIn("rebooting the machine is recommended", out)
        self.assertTrue(out.rstrip().endswith("recommended."))

    def test_no_reminder_without_stabilization(self):
        out, _ = self._run(stabilize=False)
        self.assertNotIn("reboot", out)

    def test_no_reminder_when_shut_down(self):
        out, _ = self._run(shutdown=True, shutdown_result=True)
        self.assertNotIn("rebooting the machine is recommended", out)

    def test_reminder_when_shutdown_cancelled_or_failed(self):
        out, _ = self._run(shutdown=True, shutdown_result=False)
        self.assertIn("rebooting the machine is recommended", out)

    def test_reminder_when_stopped_by_user(self):
        out, _ = self._run(exec_side_effect=execute._StoppedByUser())
        self.assertIn("Batch stopped by user.", out)
        self.assertIn("rebooting the machine is recommended", out)

    def test_reminder_when_batch_fails(self):
        out, error = self._run(exec_side_effect=RuntimeError("boom"))
        self.assertIsNotNone(error)
        self.assertIn("rebooting the machine is recommended", out)


if __name__ == "__main__":
    unittest.main()
