#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-prepare/prepare.py

Tests use a self-contained fixture tree under tests/fixtures/ — no real JDK
paths or benchmark installations are required.  java -version invocation is
monkey-patched via the _run_java_version injection point on run_prepare().

Fixture reporoot layout (tests/fixtures/):
  config/bench/        bench-h2, bench-tomcat, bench-cassandra,
                       bench-minver25, bench-nominheap
  config/benchgroup/   group-two (members: bench-h2, bench-tomcat)
  config/profiles/     g1
  config/hosts/        testhost.host.toml  (maxheap=8192); every host file
                       gets dacapo_dir / specjbb_dir added by _make_reporoot
  config/jdks/         jdk-21.jdk.toml
  config/collectors/   <name>.collector.toml
  config/jdk-types/    openjdk.jdktype.toml
  config/run/          plain, group, sweep, version-filter-max,
                       version-filter-min, maxheap-skip, conflict-xmx,
                       no-xmx, unknown-field, sweep-no-minheap,
                       sweep-dynamic-heap, multi-bench
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Make phase-prepare importable directly
_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_PREPARE_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _PHASE_PREPARE_DIR not in sys.path:
    sys.path.insert(0, _PHASE_PREPARE_DIR)

from prepare import run_prepare, write_batch   # noqa: E402
# Unpatched; setUpModule replaces prepare._read_perf_event_paranoid.
from prepare import _read_perf_event_paranoid as _real_read_perf_event_paranoid  # noqa: E402

_COMMON_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "common"))
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)

import tomllib                                 # noqa: E402
from toml_writer import dumps as _toml_dumps   # noqa: E402


def _load_toml(f) -> dict:
    """Parse a config file opened in text mode."""
    return tomllib.loads(f.read())


def _dump_toml(data: dict, f) -> None:
    """Write `data` as TOML to a config file opened for writing."""
    f.write(_toml_dumps(data))

_STATE_TMP = None
_SAVED_XDG_STATE_HOME = None
_PROBE_PATCHER = None


def setUpModule():
    """Point the active-batch link (PRD.md §7) into a temp dir, so tests never
    see or touch the user's real ~/.local/state/jbenchrunner. Also pretend the
    machine can run every collector (PRD.md D-82), so results do not depend on
    the test machine; tests of the probing patch it themselves."""
    global _STATE_TMP, _SAVED_XDG_STATE_HOME, _PROBE_PATCHER
    _STATE_TMP = tempfile.mkdtemp()
    _SAVED_XDG_STATE_HOME = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = _STATE_TMP
    _PROBE_PATCHER = patch("prepare.unavailable_reason", return_value=None)
    _PROBE_PATCHER.start()
    # Same for the host's perf_event_paranoid (PRD.md D-85): unknown, so perf
    # is neither dropped nor warned about; tests of it patch it themselves.
    _PARANOID_PATCHER.start()


_PARANOID_PATCHER = patch("prepare._read_perf_event_paranoid", return_value=None)


def tearDownModule():
    import shutil
    _PARANOID_PATCHER.stop()
    _PROBE_PATCHER.stop()
    if _SAVED_XDG_STATE_HOME is None:
        os.environ.pop("XDG_STATE_HOME", None)
    else:
        os.environ["XDG_STATE_HOME"] = _SAVED_XDG_STATE_HOME
    shutil.rmtree(_STATE_TMP, ignore_errors=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FIXTURES = os.path.join(_HERE, "fixtures")

# Default java -version output for a plain openjdk 21
_JDK21_OUTPUT = 'openjdk version "21.0.2" 2024-01-16\nOpenJDK Runtime Environment\n'

# Fake dacapo chopin jar presence is patched via os.path — see _make_reporoot()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _java_version_stub(output: str):
    """Return a callable that ignores the command and returns `output`."""
    def _stub(cmd):
        return output
    return _stub


def _make_reporoot(tmp_dir: str) -> str:
    """
    Copy the fixture config tree into tmp_dir, then add fake dacapo / specjbb
    jars inside tmp_dir so jar-existence checks pass without writing to /.
    Returns tmp_dir (used as reporoot).
    """
    import shutil

    src = os.path.join(_FIXTURES, "config")
    dst = os.path.join(tmp_dir, "config")
    shutil.copytree(src, dst)

    # Fake benchmark directories inside the temp tree
    dacapo_dir = os.path.join(tmp_dir, "fake_dacapo")
    specjbb_dir = os.path.join(tmp_dir, "fake_specjbb")
    os.makedirs(dacapo_dir, exist_ok=True)
    os.makedirs(specjbb_dir, exist_ok=True)

    open(os.path.join(dacapo_dir, "dacapo-23.11-chopin.jar"), "w").close()
    open(os.path.join(specjbb_dir, "specjbb2015.jar"), "w").close()

    # Point every host config at the tmp-local benchmark dirs (PRD.md D-83)
    hosts_dir = os.path.join(dst, "hosts")
    for name in os.listdir(hosts_dir):
        with open(os.path.join(hosts_dir, name), "a") as f:
            f.write("\n" + _toml_dumps({"dacapo_dir": dacapo_dir, "specjbb_dir": specjbb_dir}))

    # Fake dacapo plugin jar
    plugin_dir = os.path.join(tmp_dir, "dacapo-timestamp-plugin")
    os.makedirs(plugin_dir, exist_ok=True)
    open(os.path.join(plugin_dir, "dacapoExtension.jar"), "w").close()

    # The temp tree doubles as the project (PRD.md §4): mark it as one.
    with open(os.path.join(tmp_dir, "jbr-project.json"), "w") as f:
        f.write("{}\n")

    return tmp_dir


def _run_json(name: str) -> str:
    return os.path.join(_FIXTURES, "config", "run", f"{name}.run.toml")


def _default_opts(**overrides):
    opts = {
        "jvmargs": [],
        "tags": [],
        "collector_interval": None,
        "dacapo_iterations": None,
        "sudo_mode": False,
    }
    opts.update(overrides)
    return opts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunPrepareHappyPath(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plain_bench_writes_batch(self):
        """Basic happy path: single bench run, no sweep."""
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )

        self.assertIn("batchid", batch)
        self.assertEqual(len(batch["runs"]), 1)
        run = batch["runs"][0]
        self.assertEqual(run["seq"], 1)
        self.assertEqual(run["bench_json"], "bench-h2")
        self.assertEqual(run["benchmark"], "dacapo")
        self.assertEqual(run["sub_benchmark"], "h2")
        self.assertEqual(run["jdkname"], "jdk-21")
        self.assertEqual(run["java_home"], "/fake/jdk21")
        self.assertEqual(run["jdk_type"], "openjdk")
        self.assertEqual(run["jdk_major"], 21)
        self.assertIn("-XX:+UseG1GC", run["jvmargs"])
        self.assertIn("-Xmx2048m", run["jvmargs"])
        self.assertIn("-XX:+PrintFlagsFinal", run["jvmargs"])
        self.assertEqual(run["heapsize"], 2048)
        self.assertNotIn("heapsize_factor", run)
        self.assertIn("smoke", run["tags"])
        # result_dir uses run_id
        self.assertTrue(run["result_dir"].startswith("results/"))
        # BATCH_IN_PROGRESS.json was written
        batch_file = os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")
        self.assertTrue(os.path.exists(batch_file))

    def test_batch_json_is_valid(self):
        """The written BATCH_IN_PROGRESS.json must be valid JSON."""
        run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        batch_file = os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")
        with open(batch_file) as f:
            data = json.load(f)
        self.assertIn("runs", data)

    def test_perf_events_default_null(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertIsNone(batch["collectors"]["perf_events"])
        self.assertNotIn("perf_events", batch["runs"][0])

    def test_collector_interval_stored(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(collector_interval=5),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["collectors"]["interval_secs"], 5)

    def test_collector_interval_default_when_unset_everywhere(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["collectors"]["interval_secs"], 10)

    def test_collector_interval_from_run_json_used_when_cli_absent(self):
        batch = run_prepare(
            _run_json("collector-interval"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["collectors"]["interval_secs"], 3)

    def test_collector_interval_cli_overrides_run_json(self):
        batch = run_prepare(
            _run_json("collector-interval"),
            _default_opts(collector_interval=30),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["collectors"]["interval_secs"], 30)

    def test_dacapo_iterations_default_from_bench_json_when_unset_everywhere(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["iterations"], 5)

    def test_dacapo_iterations_from_run_json_used_when_cli_absent(self):
        batch = run_prepare(
            _run_json("dacapo-iterations"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["iterations"], 2)

    def test_dacapo_iterations_cli_overrides_run_json(self):
        batch = run_prepare(
            _run_json("dacapo-iterations"),
            _default_opts(dacapo_iterations=7),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["iterations"], 7)

    def test_dacapo_iterations_entry_overrides_batch_global(self):
        batch = run_prepare(
            _run_json("dacapo-iterations-entry-override"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        # Per-run override (4) wins over the batch-level value (2).
        self.assertEqual(batch["runs"][0]["iterations"], 4)

    def test_dacapo_iterations_cli_overrides_entry(self):
        batch = run_prepare(
            _run_json("dacapo-iterations-entry-override"),
            _default_opts(dacapo_iterations=7),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        # CLI wins over everything, including a per-run-entry override.
        self.assertEqual(batch["runs"][0]["iterations"], 7)

    def test_jdkname_batch_global_used_when_entry_omits_it(self):
        batch = run_prepare(
            _run_json("jdkname-batch-global"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["jdkname"], "jdk-21")

    def test_profiles_batch_global_used_when_entry_omits_it(self):
        batch = run_prepare(
            _run_json("profiles-batch-global"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["profiles"], ["g1"])

    def test_sudo_mode_default_false(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertIs(batch["sudo_mode"], False)

    def test_sudo_mode_from_cli(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(sudo_mode=True),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertIs(batch["sudo_mode"], True)

    def test_cheap_collectors_by_default_when_host_omits_flags(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["collectors"],
                         {"interval_secs": 10, "compression": False,
                          "enabled": ["cpu_temp", "pidstat", "proc"], "perf_events": None})
        self.assertNotIn("reset_machine_between_runs", batch)

    def _prepare_stabilize(self, hostname, **opts):
        return run_prepare(
            _run_json("plain"),
            _default_opts(**opts),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname=hostname,
        )

    def test_stabilize_machine_off_by_default(self):
        batch = self._prepare_stabilize("testhost")
        self.assertIs(batch["stabilize_machine"], False)

    def test_host_config_key_no_longer_enables_stabilization(self):
        # Explicit opt-in only (PRD.md D-88): a leftover host config key has no
        # effect, even with --sudo, and is only warned about as unknown.
        out = self._stabilize_output("testhost-stabilize-machine-true", sudo_mode=True)
        self.assertIn("WARNING: machine will not be stabilized", out)
        self.assertIn("stabilize_machine", out.replace("machine will not be stabilized", ""))
        batch = self._prepare_stabilize("testhost-stabilize-machine-true", sudo_mode=True)
        self.assertIs(batch["stabilize_machine"], False)

    def test_stabilize_machine_cli(self):
        batch = self._prepare_stabilize("testhost", sudo_mode=True,
                                        stabilize_machine=True)
        self.assertIs(batch["stabilize_machine"], True)

    def test_stabilize_machine_cli_without_sudo_raises(self):
        with self.assertRaises(ValueError):
            self._prepare_stabilize("testhost", stabilize_machine=True)

    def _stabilize_output(self, hostname, system="Linux", **opts):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("platform.system", return_value=system):
            self._prepare_stabilize(hostname, **opts)
        return buf.getvalue()

    def test_stabilize_machine_off_warns(self):
        out = self._stabilize_output("testhost")
        self.assertIn("WARNING: machine will not be stabilized", out)

    def test_stabilize_machine_on_reported_with_security_note(self):
        out = self._stabilize_output("testhost", sudo_mode=True, stabilize_machine=True)
        self.assertIn("Machine stabilization: on", out)
        self.assertIn("weakens security until reboot", out)
        self.assertNotIn("stabiliz", out.replace("Machine stabilization: on", ""))

    def test_stabilize_machine_not_linux_warns(self):
        out = self._stabilize_output("testhost", system="Darwin",
                                     sudo_mode=True, stabilize_machine=True)
        self.assertIn("this is not Linux", out)

    def test_benchmark_dir_from_host_config(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["dacapo_dir"],
                         os.path.join(self.reporoot, "fake_dacapo"))

    def test_benchmark_dir_missing_in_host_config_raises(self):
        host = os.path.join(self.reporoot, "config", "hosts", "testhost.host.toml")
        with open(host, "w") as f:
            f.write("maxheap = 8192\n")
        with self.assertRaisesRegex(ValueError, "'dacapo_dir' is required in the host config"):
            run_prepare(
                _run_json("plain"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )

    def test_host_perf_events_key_raises(self):
        # perf_events moved from the host config to .run.toml (PRD.md D-81).
        host = os.path.join(self.reporoot, "config", "hosts", "testhost.host.toml")
        with open(host, "a") as f:
            f.write('perf_events = ["cycles"]\n')
        with self.assertRaisesRegex(ValueError, "perf_events_preset"):
            run_prepare(
                _run_json("plain"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )

    def test_flag_assembly_order(self):
        """bench flags → sweep → profile → run-top → run-entry → cli → jdktype → gc_log"""
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(jvmargs=["-Xcli"]),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        flags = batch["runs"][0]["jvmargs"]
        # bench flag is first
        self.assertEqual(flags[0], "-Dfoo=bar")
        # cli flag comes before jdktype extra flags
        cli_idx = flags.index("-Xcli")
        pff_idx = flags.index("-XX:+PrintFlagsFinal")
        gc_idx = flags.index("-Xlog:gc:file=gc.log::filecount=0")
        self.assertLess(cli_idx, pff_idx)
        self.assertLess(pff_idx, gc_idx)


class TestBenchgroupExpansion(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_benchgroup_expands_to_two_entries(self):
        batch = run_prepare(
            _run_json("group"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 2)

    def test_expanded_entries_have_correct_bench_names(self):
        batch = run_prepare(
            _run_json("group"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        names = [r["bench_json"] for r in batch["runs"]]
        self.assertIn("bench-h2", names)
        self.assertIn("bench-tomcat", names)

    def test_expanded_entries_inherit_jdkname(self):
        batch = run_prepare(
            _run_json("group"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        for r in batch["runs"]:
            self.assertEqual(r["jdkname"], "jdk-21")

    def test_expanded_entries_have_bench_group_field(self):
        batch = run_prepare(
            _run_json("group"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        for r in batch["runs"]:
            self.assertEqual(r["bench_group"], "group-two")

    def test_plain_bench_has_no_bench_group_field(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertNotIn("bench_group", batch["runs"][0])

    def test_seq_is_1_based_sequential(self):
        batch = run_prepare(
            _run_json("group"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        seqs = [r["seq"] for r in batch["runs"]]
        self.assertEqual(seqs, [1, 2])

    def test_batch_global_bench_used_when_entry_omits_both(self):
        batch = run_prepare(
            _run_json("batch-global-bench"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 1)
        self.assertEqual(batch["runs"][0]["bench_json"], "bench-h2")

    def test_bench_array_runs_every_entry_against_every_bench(self):
        batch = run_prepare(
            _run_json("multi-bench"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        # 2 entries x 2 benches, entry-major (same order as a benchgroup).
        got = [("first" if "first" in r["tags"] else "second", r["bench_json"])
               for r in batch["runs"]]
        self.assertEqual(got, [
            ("first", "bench-h2"), ("first", "bench-tomcat"),
            ("second", "bench-h2"), ("second", "bench-tomcat"),
        ])
        # No named group behind a bench array.
        for r in batch["runs"]:
            self.assertNotIn("bench_group", r)


class TestHeapSweepExpansion(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sweep_expands_to_two_entries(self):
        # sweep.run.toml has 2 factors × 1 bench = 2 runs
        batch = run_prepare(
            _run_json("sweep"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 2)

    def test_sweep_injects_xmx_xms_and_pretouch(self):
        batch = run_prepare(
            _run_json("sweep"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        for r in batch["runs"]:
            flags = r["jvmargs"]
            self.assertTrue(any(f.startswith("-Xms") for f in flags))
            self.assertTrue(any(f.startswith("-Xmx") for f in flags))
            self.assertIn("-XX:+AlwaysPreTouch", flags)

    def test_sweep_heap_sizes_correct(self):
        # bench-h2 minheap=1024; factors 1.0 and 2.0
        batch = run_prepare(
            _run_json("sweep"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        heap_sizes = sorted(r["heapsize"] for r in batch["runs"])
        self.assertEqual(heap_sizes, [1024, 2048])

    def test_sweep_heapsize_factor_stored(self):
        # bench-h2 minheap=1024; factors 1.0 and 2.0
        batch = run_prepare(
            _run_json("sweep"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        factors = sorted(r["heapsize_factor"] for r in batch["runs"])
        self.assertEqual(factors, [1.0, 2.0])

    def test_sweep_heapsize_factor_matches_heap_size(self):
        batch = run_prepare(
            _run_json("sweep"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        for r in batch["runs"]:
            self.assertEqual(r["heapsize"], round(r["heapsize_factor"] * 1024))

    def test_sweep_xmx_is_before_profile_flags(self):
        """Sweep flags follow bench flags (position 1), before profile flags (position 2)."""
        batch = run_prepare(
            _run_json("sweep"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        flags = batch["runs"][0]["jvmargs"]
        xmx_idx = next(i for i, f in enumerate(flags) if f.startswith("-Xmx"))
        g1_idx = flags.index("-XX:+UseG1GC")
        self.assertLess(xmx_idx, g1_idx)

    def test_sweep_missing_minheap_aborts(self):
        with self.assertRaises((ValueError, RuntimeError)):
            run_prepare(
                _run_json("sweep-no-minheap"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )

    def test_dynamic_heap_injects_only_xmx(self):
        batch = run_prepare(
            _run_json("sweep-dynamic-heap"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        for r in batch["runs"]:
            flags = r["jvmargs"]
            self.assertTrue(any(f.startswith("-Xmx") for f in flags))
            self.assertFalse(any(f.startswith("-Xms") for f in flags))
            self.assertNotIn("-XX:+AlwaysPreTouch", flags)

    def test_dynamic_heap_heap_size_still_correct(self):
        # bench-h2 minheap=1024; factors 1.0 and 2.0
        batch = run_prepare(
            _run_json("sweep-dynamic-heap"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        heap_sizes = sorted(r["heapsize"] for r in batch["runs"])
        self.assertEqual(heap_sizes, [1024, 2048])


class TestVersionFiltering(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_max_version_filter_removes_entry(self):
        """bench-cassandra max_java_version=21; JDK 25 must be skipped."""
        jdk25_output = 'openjdk version "25.0.1" 2025-01-01\nOpenJDK Runtime Environment\n'
        batch = run_prepare(
            _run_json("version-filter-max"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(jdk25_output),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 0)

    def test_max_version_filter_keeps_entry_at_boundary(self):
        """bench-cassandra max_java_version=21; JDK 21 must be kept."""
        batch = run_prepare(
            _run_json("version-filter-max"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 1)

    def test_min_version_filter_removes_entry(self):
        """bench-minver25 min_java_version=25; JDK 21 must be skipped."""
        batch = run_prepare(
            _run_json("version-filter-min"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 0)


class TestMaxheapFiltering(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_entry_exceeding_maxheap_is_skipped(self):
        """maxheap-skip.run.toml uses -Xmx16384m; host maxheap=8192 → 0 runs."""
        batch = run_prepare(
            _run_json("maxheap-skip"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 0)

    def test_entry_within_maxheap_is_kept(self):
        """plain.run.toml uses -Xmx2048m; host maxheap=8192 → kept."""
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 1)


class TestRamdiskConfig(unittest.TestCase):
    """ramdisk_path passthrough from config/hosts/<hostname>.host.toml into BATCH_IN_PROGRESS.json."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_config_ramdisk_path(self, value):
        host_path = os.path.join(self.reporoot, "config", "hosts", "testhost.host.toml")
        with open(host_path) as f:
            cfg = _load_toml(f)
        cfg["ramdisk_path"] = value
        with open(host_path, "w") as f:
            _dump_toml(cfg, f)

    def test_ramdisk_path_absent_by_default(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertNotIn("ramdisk_path", batch)

    def test_ramdisk_path_copied_into_batch_when_set(self):
        ramdisk_dir = os.path.join(self.tmp, "fake_ramdisk")
        os.makedirs(ramdisk_dir, exist_ok=True)
        self._set_config_ramdisk_path(ramdisk_dir)

        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["ramdisk_path"], ramdisk_dir)

    def test_ramdisk_path_nonexistent_directory_raises(self):
        self._set_config_ramdisk_path(os.path.join(self.tmp, "no_such_ramdisk"))

        with self.assertRaises(ValueError) as ctx:
            run_prepare(
                _run_json("plain"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertIn("ramdisk_path", str(ctx.exception))


class TestCpuPinningConfig(unittest.TestCase):
    """cpu_list passthrough and do_cpu_pinning resolution/validation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_host_cpu_list(self, value):
        host_path = os.path.join(self.reporoot, "config", "hosts", "testhost.host.toml")
        with open(host_path) as f:
            cfg = _load_toml(f)
        cfg["cpu_list"] = value
        cfg["cpu_list_harness"] = "4-5"
        with open(host_path, "w") as f:
            _dump_toml(cfg, f)

    def _write_run_json(self, name, data):
        path = os.path.join(self.reporoot, "config", "run", f"{name}.run.toml")
        with open(path, "w") as f:
            _dump_toml(data, f)
        return path

    def test_do_cpu_pinning_and_cpu_list_absent_by_default(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertNotIn("cpu_list", batch)
        self.assertFalse(batch["runs"][0]["do_cpu_pinning"])

    def test_cpu_list_copied_into_batch_when_set(self):
        self._set_host_cpu_list("0-3")
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["cpu_list"], "0-3")
        # host has a cpu_list, but nothing in .run.toml requested pinning
        self.assertFalse(batch["runs"][0]["do_cpu_pinning"])

    def test_do_cpu_pinning_without_host_cpu_list_warns_and_disables(self):
        run_path = self._write_run_json("cpu-pin-no-list", {
            "do_cpu_pinning": True,
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]}
            ],
        })
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                run_path,
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertFalse(batch["runs"][0]["do_cpu_pinning"])
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("cpu_list", buf.getvalue())

    def test_cpu_list_harness_copied_into_batch_when_set(self):
        self._set_host_cpu_list("0-3")
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["cpu_list_harness"], "4-5")

    def test_do_cpu_pinning_without_host_cpu_list_harness_warns_and_disables(self):
        self._set_host_cpu_list("0-3")
        host_path = os.path.join(self.reporoot, "config", "hosts", "testhost.host.toml")
        with open(host_path) as f:
            cfg = _load_toml(f)
        del cfg["cpu_list_harness"]
        with open(host_path, "w") as f:
            _dump_toml(cfg, f)
        run_path = self._write_run_json("cpu-pin-no-harness", {
            "do_cpu_pinning": True,
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]}
            ],
        })
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                run_path,
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertFalse(batch["runs"][0]["do_cpu_pinning"])
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("cpu_list_harness", buf.getvalue())

    def test_batch_level_do_cpu_pinning_applies_to_all_runs(self):
        self._set_host_cpu_list("0-3")
        run_path = self._write_run_json("cpu-pin-batch", {
            "do_cpu_pinning": True,
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
            _run_taskset=lambda cmd: 0,
        )
        self.assertTrue(all(r["do_cpu_pinning"] for r in batch["runs"]))

    def test_per_run_do_cpu_pinning_overrides_batch_level(self):
        self._set_host_cpu_list("0-3")
        run_path = self._write_run_json("cpu-pin-override", {
            "do_cpu_pinning": True,
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"], "do_cpu_pinning": False},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
            _run_taskset=lambda cmd: 0,
        )
        pinned = [r["do_cpu_pinning"] for r in batch["runs"]]
        self.assertTrue(pinned[0])
        self.assertFalse(pinned[1])

    def test_per_run_do_cpu_pinning_enables_when_batch_level_absent(self):
        self._set_host_cpu_list("0-3")
        run_path = self._write_run_json("cpu-pin-per-run-only", {
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"], "do_cpu_pinning": True},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
            _run_taskset=lambda cmd: 0,
        )
        pinned = [r["do_cpu_pinning"] for r in batch["runs"]]
        self.assertFalse(pinned[0])
        self.assertTrue(pinned[1])


class TestTasksetSupportProbe(unittest.TestCase):
    """
    Warn (but don't abort) and disable do_cpu_pinning for the affected runs
    when a 'taskset --cpu-list <cpu_list> sleep 0' probe fails — covering
    both an absent 'taskset' binary and a host-rejected cpu_list.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_host_cpu_list(self, value):
        host_path = os.path.join(self.reporoot, "config", "hosts", "testhost.host.toml")
        with open(host_path) as f:
            cfg = _load_toml(f)
        cfg["cpu_list"] = value
        cfg["cpu_list_harness"] = "4-5"
        with open(host_path, "w") as f:
            _dump_toml(cfg, f)

    def _write_pinned_run_json(self, name):
        path = os.path.join(self.reporoot, "config", "run", f"{name}.run.toml")
        with open(path, "w") as f:
            _dump_toml({
                "do_cpu_pinning": True,
                "bench": "bench-h2",
                "runs": [
                    {"jdkname": "jdk-21", "profiles": ["g1"],
                     "jvmargs": ["-Xmx2048m"]},
                    {"jdkname": "jdk-21", "profiles": ["g1"],
                     "jvmargs": ["-Xmx2048m"]},
                ],
            }, f)
        return path

    def _run_prepare(self, run_path, run_taskset):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                run_path,
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
                _run_taskset=run_taskset,
            )
        return batch, buf.getvalue()

    def test_working_taskset_keeps_pinning_enabled(self):
        self._set_host_cpu_list("0-3")
        run_path = self._write_pinned_run_json("taskset-ok")
        batch, output = self._run_prepare(run_path, lambda cmd: 0)
        self.assertTrue(all(r["do_cpu_pinning"] for r in batch["runs"]))
        self.assertNotIn("CPU pinning", output)

    def test_missing_taskset_binary_disables_pinning(self):
        """taskset not on PATH -> subprocess raises FileNotFoundError (OSError)."""
        self._set_host_cpu_list("0-3")
        run_path = self._write_pinned_run_json("taskset-missing")

        def _raise(cmd):
            raise FileNotFoundError("taskset not found")

        batch, output = self._run_prepare(run_path, _raise)
        self.assertFalse(any(r["do_cpu_pinning"] for r in batch["runs"]))
        self.assertIn("WARNING", output)
        self.assertIn("taskset", output)

    def test_rejected_cpu_list_disables_pinning(self):
        """taskset present but the probe exits non-zero (e.g. invalid cpu_list)."""
        self._set_host_cpu_list("0-3")
        run_path = self._write_pinned_run_json("taskset-rejected")
        batch, output = self._run_prepare(run_path, lambda cmd: 1)
        self.assertFalse(any(r["do_cpu_pinning"] for r in batch["runs"]))
        self.assertIn("WARNING", output)

    def test_not_probed_when_pinning_inactive(self):
        self._set_host_cpu_list("0-3")
        calls = []

        def _run_taskset(cmd):
            calls.append(cmd)
            return 0

        run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
            _run_taskset=_run_taskset,
        )
        self.assertEqual(calls, [])


class TestIsolcpusCheck(unittest.TestCase):
    """
    Warn (but don't abort) when CPU pinning is active and the running
    kernel's 'isolcpus=' is absent or doesn't match the host's cpu_list.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_host_cpu_list(self, value):
        host_path = os.path.join(self.reporoot, "config", "hosts", "testhost.host.toml")
        with open(host_path) as f:
            cfg = _load_toml(f)
        cfg["cpu_list"] = value
        cfg["cpu_list_harness"] = "4-5"
        with open(host_path, "w") as f:
            _dump_toml(cfg, f)

    def _write_pinned_run_json(self, name):
        path = os.path.join(self.reporoot, "config", "run", f"{name}.run.toml")
        with open(path, "w") as f:
            _dump_toml({
                "do_cpu_pinning": True,
                "bench": "bench-h2",
                "runs": [
                    {"jdkname": "jdk-21", "profiles": ["g1"],
                     "jvmargs": ["-Xmx2048m"]}
                ],
            }, f)
        return path

    def _run_prepare(self, run_path, cmdline):
        """Run run_prepare with a stubbed /proc/cmdline, capturing stdout."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                run_path,
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
                _read_cmdline=lambda: cmdline,
                _run_taskset=lambda cmd: 0,
            )
        return batch, buf.getvalue()

    def test_matching_isolcpus_no_warning(self):
        self._set_host_cpu_list("0-3")
        run_path = self._write_pinned_run_json("isolcpus-match")
        _batch, output = self._run_prepare(
            run_path, "BOOT_IMAGE=/vmlinuz root=/dev/sda1 isolcpus=0-3 quiet"
        )
        self.assertNotIn("CPU pinning", output)

    @unittest.skip("isolcpus warning is temporarily deactivated in prepare.py")
    def test_missing_isolcpus_warns(self):
        self._set_host_cpu_list("0-3")
        run_path = self._write_pinned_run_json("isolcpus-missing")
        _batch, output = self._run_prepare(
            run_path, "BOOT_IMAGE=/vmlinuz root=/dev/sda1 quiet"
        )
        self.assertIn("WARNING", output)
        self.assertIn("isolcpus", output)

    @unittest.skip("isolcpus warning is temporarily deactivated in prepare.py")
    def test_mismatched_isolcpus_warns(self):
        self._set_host_cpu_list("0-3")
        run_path = self._write_pinned_run_json("isolcpus-mismatch")
        _batch, output = self._run_prepare(
            run_path, "BOOT_IMAGE=/vmlinuz root=/dev/sda1 isolcpus=0-2 quiet"
        )
        self.assertIn("WARNING", output)
        self.assertIn("isolcpus", output)

    def test_flagged_isolcpus_syntax_matches(self):
        """Newer isolcpus=managed_irq,domain,<list> syntax is parsed correctly."""
        self._set_host_cpu_list("0-3")
        run_path = self._write_pinned_run_json("isolcpus-flagged")
        _batch, output = self._run_prepare(
            run_path, "BOOT_IMAGE=/vmlinuz isolcpus=managed_irq,domain,0-3 quiet"
        )
        self.assertNotIn("CPU pinning", output)

    def test_not_checked_when_pinning_inactive(self):
        """No do_cpu_pinning requested → the cmdline is never even consulted."""
        self._set_host_cpu_list("0-3")
        calls = []

        def _read_cmdline():
            calls.append(1)
            return "BOOT_IMAGE=/vmlinuz quiet"

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_prepare(
                _run_json("plain"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
                _read_cmdline=_read_cmdline,
            )
        self.assertEqual(calls, [])
        self.assertNotIn("CPU pinning", buf.getvalue())


def _coll(interval=10, compression=False, enabled=("cpu_temp", "pidstat", "proc")):
    """A full .run.toml "collectors" object."""
    return {"interval": interval, "compression": compression, "enabled": list(enabled)}


class TestCollectorCompressionConfig(unittest.TestCase):
    """collectors.compression: batch-wide setting, zstd check."""

    _RUNS = [
        {"jdkname": "jdk-21", "profiles": ["g1"], "jvmargs": ["-Xmx2048m"]},
        {"jdkname": "jdk-21", "profiles": ["g1"], "jvmargs": ["-Xmx2048m"]},
    ]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prepare(self, data, zstd="/usr/bin/zstd"):
        path = os.path.join(self.reporoot, "config", "run", "compression.run.toml")
        with open(path, "w") as f:
            _dump_toml({"bench": "bench-h2", **data}, f)
        with patch("shutil.which", return_value=zstd):
            return run_prepare(
                path, _default_opts(), reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT), _hostname="testhost",
            )

    def test_defaults_false_and_zstd_not_needed(self):
        batch = self._prepare({"runs": [dict(r) for r in self._RUNS]}, zstd=None)
        self.assertIs(batch["collectors"]["compression"], False)

    def test_batch_level_field_not_per_run(self):
        # The collector settings are batch-wide (PRD.md D-84).
        batch = self._prepare({"collectors": _coll(compression=True), "runs": [dict(r) for r in self._RUNS]})
        self.assertIs(batch["collectors"]["compression"], True)
        for run in batch["runs"]:
            self.assertNotIn("collectors", run)

    def test_requested_without_zstd_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._prepare({"collectors": _coll(compression=True), "runs": [dict(r) for r in self._RUNS]},
                          zstd=None)
        self.assertIn("zstd", str(ctx.exception))


class TestCollectorsConfig(unittest.TestCase):
    """collectors: presets, per-collector booleans, host and restrict_to gating."""

    _RUN = {"jdkname": "jdk-21", "profiles": ["g1"], "jvmargs": ["-Xmx2048m"]}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prepare(self, data, hostname="testhost", **opts):
        import io
        from contextlib import redirect_stdout
        path = os.path.join(self.reporoot, "config", "run", "collectors.run.toml")
        with open(path, "w") as f:
            _dump_toml({"bench": "bench-h2", **data}, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                path, _default_opts(**opts), reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT), _hostname=hostname,
            )
        return batch, buf.getvalue()

    def _enabled(self, batch):
        return batch["collectors"]["enabled"]

    def test_preset_all(self):
        batch, _out = self._prepare({"collectors_preset": "all-10s", "runs": [dict(self._RUN)]})
        self.assertEqual(batch["collectors"],
                         {"interval_secs": 10, "compression": True,
                          "enabled": ["cpu_temp", "pidstat", "proc", "perf", "nmt",
                                      "system_map", "thread_print"],
                          "perf_events": None})
        self.assertIn("-XX:NativeMemoryTracking=summary", batch["runs"][0]["jvmargs"])

    def test_preset_none(self):
        batch, _out = self._prepare({"collectors_preset": "none", "runs": [dict(self._RUN)]})
        self.assertEqual(self._enabled(batch), [])

    def test_default_is_cheap(self):
        batch, _out = self._prepare({"runs": [dict(self._RUN)]})
        self.assertEqual(batch["collectors"],
                         {"interval_secs": 10, "compression": False,
                          "enabled": ["cpu_temp", "pidstat", "proc"], "perf_events": None})

    def test_run_level_collectors_rejected(self):
        # Batch level only (PRD.md D-80).
        for key, value in (("collectors", _coll(interval=2, enabled=["nmt"])),
                           ("collectors_preset", "cheap-10s")):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "not allowed in a run entry"):
                    self._prepare({"collectors_preset": "all-10s",
                                   "runs": [dict(self._RUN, **{key: value})]})

    def test_cli_interval_wins(self):
        runs = [dict(self._RUN), dict(self._RUN)]
        batch, _out = self._prepare({"collectors": _coll(interval=2), "runs": runs},
                                    collector_interval=30)
        self.assertEqual(batch["collectors"]["interval_secs"], 30)

    def test_machine_unavailable_disabled_with_warning(self):
        reasons = {"cpu_temp": "no sensor", "pidstat": "'pidstat' not found on PATH"}
        with patch("prepare.unavailable_reason", side_effect=reasons.get) as probe:
            batch, out = self._prepare({"collectors_preset": "all-10s",
                                        "runs": [dict(self._RUN), dict(self._RUN)]})
        self.assertEqual(self._enabled(batch),
                         ["proc", "perf", "nmt", "system_map", "thread_print"])
        self.assertIn("requests collector 'cpu_temp', but it cannot run on this machine "
                      "(no sensor) — disabling it for the whole batch", out)
        self.assertIn("requests collector 'pidstat'", out)
        self.assertEqual(out.count("cannot run on this machine"), 2)
        # probed once per requested collector, not per run
        self.assertEqual(sorted(c.args[0] for c in probe.call_args_list),
                         sorted(["cpu_temp", "pidstat", "proc", "perf", "nmt",
                                 "system_map", "thread_print"]))

    def test_unrequested_collector_not_probed(self):
        with patch("prepare.unavailable_reason", return_value=None) as probe:
            self._prepare({"collectors_preset": "none", "runs": [dict(self._RUN)]})
        probe.assert_not_called()

    def test_machine_without_perf_skips_event_check(self):
        with patch("prepare.unavailable_reason",
                   side_effect=lambda c: "no perf" if c == "perf" else None):
            batch, out = self._prepare(
                {"collectors": _coll(enabled=["proc", "perf"]), "runs": [dict(self._RUN)]})
        self.assertEqual(self._enabled(batch), ["proc"])
        self.assertIn("requests collector 'perf'", out)

    _PERF_DATA = {"collectors": _coll(enabled=["proc", "perf"])}

    def _prepare_paranoid(self, level, system="Linux", **opts):
        with patch("prepare._read_perf_event_paranoid", return_value=level), \
             patch("platform.system", return_value=system):
            return self._prepare(dict(self._PERF_DATA, runs=[dict(self._RUN)]), **opts)

    def test_perf_dropped_when_paranoid_blocks_unprivileged_perf(self):
        # PRD.md D-85: perf never runs via sudo; at level >= 3 it can't run at all.
        for level in (3, 4):
            with self.subTest(level=level):
                batch, out = self._prepare_paranoid(level)
                self.assertEqual(self._enabled(batch), ["proc"])
                self.assertIn(f"requests collector 'perf', but it cannot run on this "
                              f"machine (perf_event_paranoid is {level}", out)

    def test_perf_kept_when_stabilization_will_lower_paranoid(self):
        # Stabilization sets perf_event_paranoid=0 at batch start, so the
        # current level is irrelevant (and not even read).
        with patch("prepare._read_perf_event_paranoid", return_value=4) as read, \
             patch("platform.system", return_value="Linux"):
            batch, out = self._prepare(dict(self._PERF_DATA, runs=[dict(self._RUN)]),
                                       sudo_mode=True, stabilize_machine=True)
        self.assertEqual(self._enabled(batch), ["proc", "perf"])
        self.assertNotIn("perf_event_paranoid is", out)
        read.assert_not_called()

    def test_perf_dropped_when_stabilization_not_effective(self):
        # stabilize_machine without Linux is skipped by jbr-execute.
        batch, out = self._prepare_paranoid(3, system="Darwin",
                                            sudo_mode=True, stabilize_machine=True)
        self.assertEqual(self._enabled(batch), ["proc"])

    def test_perf_user_space_only_warning(self):
        batch, out = self._prepare_paranoid(2)
        self.assertEqual(self._enabled(batch), ["proc", "perf"])
        self.assertIn("perf will count user space only", out)

    def test_perf_no_paranoid_warning_at_low_levels(self):
        for level in (-1, 0, 1):
            with self.subTest(level=level):
                batch, out = self._prepare_paranoid(level)
                self.assertEqual(self._enabled(batch), ["proc", "perf"])
                self.assertNotIn("perf_event_paranoid", out)

    def test_perf_without_preset_enabled_with_default_set(self):
        batch, out = self._prepare(
            {"collectors": _coll(enabled=["proc", "perf"]), "runs": [dict(self._RUN)]})
        self.assertEqual(self._enabled(batch), ["proc", "perf"])
        self.assertIsNone(batch["collectors"]["perf_events"])
        self.assertNotIn("requests collector", out)

    def test_custom_preset_file(self):
        path = os.path.join(self.reporoot, "config", "collector-presets",
                            "memory.collector-preset.toml")
        with open(path, "w") as f:
            _dump_toml(_coll(interval=3, enabled=["proc", "nmt"]), f)
        batch, _out = self._prepare({"collectors_preset": "memory", "runs": [dict(self._RUN)]})
        self.assertEqual(batch["collectors"],
                         {"interval_secs": 3, "compression": False, "enabled": ["proc", "nmt"],
                          "perf_events": None})

    def test_unknown_preset_is_hard_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._prepare({"collectors_preset": "nope", "runs": [dict(self._RUN)]})
        self.assertIn("nope.collector-preset.toml", str(ctx.exception))

    def test_default_preset_read_from_file(self):
        path = os.path.join(self.reporoot, "config", "collector-presets",
                            "cheap-10s.collector-preset.toml")
        with open(path, "w") as f:
            _dump_toml(_coll(interval=7, enabled=["pidstat"]), f)
        batch, _out = self._prepare({"runs": [dict(self._RUN)]})
        self.assertEqual(batch["collectors"],
                         {"interval_secs": 7, "compression": False, "enabled": ["pidstat"],
                          "perf_events": None})

    def test_unused_preset_file_not_needed(self):
        os.remove(os.path.join(self.reporoot, "config", "collector-presets",
                               "cheap-10s.collector-preset.toml"))
        batch, _out = self._prepare({"collectors": _coll(enabled=["proc"]),
                                     "runs": [dict(self._RUN)]})
        self.assertEqual(self._enabled(batch), ["proc"])

    def test_effective_collectors_printed(self):
        runs = [dict(self._RUN), dict(self._RUN)]
        _batch, out = self._prepare({"collectors": _coll(interval=5), "runs": runs})
        self.assertIn("Collectors: cpu_temp,pidstat,proc  interval=5s  compression=off", out)


class TestPerfEvents(unittest.TestCase):
    """perf_events_preset resolution (spec-prepare.md §1.4.2). The events are
    checked by jbr-execute, not here (PRD.md D-86)."""

    _RUN = {"jdkname": "jdk-21", "profiles": ["g1"], "jvmargs": ["-Xmx2048m"]}
    _PERF = {"interval": 10, "compression": False, "enabled": ["proc", "perf"]}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_preset(self, name, events):
        d = os.path.join(self.reporoot, "config", "perf-events-presets")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{name}.perf-events-preset.toml"), "w") as f:
            _dump_toml({"events": events}, f)

    def _write_run(self, name, data):
        path = os.path.join(self.reporoot, "config", "run", f"{name}.run.toml")
        with open(path, "w") as f:
            _dump_toml({"bench": "bench-h2", "collectors": self._PERF,
                        "runs": [dict(self._RUN)], **data}, f)
        return path

    def _prepare(self, paths, hostname="testhost", **opts):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                paths, _default_opts(**opts), reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT), _hostname=hostname,
            )
        return batch, buf.getvalue()

    def _collectors(self, batch):
        return batch["collectors"]

    def test_preset_events_recorded_not_name(self):
        self._write_preset("p", ["cycles", "instructions"])
        batch, _out = self._prepare(self._write_run("a", {"perf_events_preset": "p"}))
        self.assertEqual(self._collectors(batch)["perf_events"], ["cycles", "instructions"])
        self.assertNotIn("perf_events_preset", json.dumps(batch))

    def test_events_not_checked_by_prepare(self):
        # Even a name perf would reject is recorded as is; jbr-execute checks
        # the perf command at batch start (PRD.md D-86). perf is never run.
        self._write_preset("p", ["cycles", "cyclez"])
        with patch("prepare.subprocess.run") as run, \
             patch("prepare.subprocess.Popen") as popen:
            batch, _out = self._prepare(self._write_run("a", {"perf_events_preset": "p"}))
        self.assertEqual(self._collectors(batch)["perf_events"], ["cycles", "cyclez"])
        for call in run.call_args_list + popen.call_args_list:
            self.assertNotEqual(call.args[0][0], "perf")

    def test_no_preset_means_default_set(self):
        batch, _out = self._prepare(self._write_run("a", {}))
        self.assertIsNone(self._collectors(batch)["perf_events"])

    def test_missing_preset_is_hard_error(self):
        with self.assertRaisesRegex(RuntimeError, "nope.perf-events-preset.toml"):
            self._prepare(self._write_run("a", {"perf_events_preset": "nope"}))

    def test_differing_presets_with_same_events_accepted(self):
        self._write_preset("p", ["cycles"])
        self._write_preset("q", ["cycles"])
        batch, _out = self._prepare([self._write_run("a", {"perf_events_preset": "p"}),
                                     self._write_run("b", {"perf_events_preset": "q"})])
        self.assertEqual(self._collectors(batch)["perf_events"], ["cycles"])

    def test_differing_events_across_files_rejected(self):
        # Batch-wide settings must agree across input files (PRD.md D-84).
        self._write_preset("p", ["cycles"])
        self._write_preset("q", ["instructions"])
        with self.assertRaisesRegex(ValueError, "collector settings differ.*a.run.toml, b.run.toml"):
            self._prepare([self._write_run("a", {"perf_events_preset": "p"}),
                           self._write_run("b", {"perf_events_preset": "q"})])

    def test_preset_without_perf_collector_ignored_with_warning(self):
        self._write_preset("p", ["cycles"])
        batch, out = self._prepare(self._write_run(
            "a", {"perf_events_preset": "p",
                  "collectors": {"interval": 10, "compression": False, "enabled": ["proc"]}}))
        self.assertIsNone(self._collectors(batch)["perf_events"])
        self.assertIn("do not enable 'perf'", out)


class TestReadPerfEventParanoid(unittest.TestCase):

    def test_read_perf_event_paranoid(self):
        read = _real_read_perf_event_paranoid
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("-1\n")
        try:
            self.assertEqual(read(f.name), -1)
            with open(f.name, "w") as g:
                g.write("garbage\n")
            self.assertIsNone(read(f.name))
            self.assertIsNone(read("/nonexistent/paranoid"))
        finally:
            os.unlink(f.name)


class TestUseRamdiskConfig(unittest.TestCase):
    """ramdisk_path passthrough and use_ramdisk resolution/validation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_host_ramdisk_path(self, value):
        host_path = os.path.join(self.reporoot, "config", "hosts", "testhost.host.toml")
        with open(host_path) as f:
            cfg = _load_toml(f)
        cfg["ramdisk_path"] = value
        with open(host_path, "w") as f:
            _dump_toml(cfg, f)

    def _write_run_json(self, name, data):
        path = os.path.join(self.reporoot, "config", "run", f"{name}.run.toml")
        with open(path, "w") as f:
            _dump_toml(data, f)
        return path

    def test_use_ramdisk_and_ramdisk_path_absent_by_default(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertNotIn("ramdisk_path", batch)
        self.assertFalse(batch["runs"][0]["use_ramdisk"])

    def test_ramdisk_path_set_but_use_ramdisk_absent_stays_off(self):
        ramdisk_dir = os.path.join(self.tmp, "fake_ramdisk")
        os.makedirs(ramdisk_dir, exist_ok=True)
        self._set_host_ramdisk_path(ramdisk_dir)
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["ramdisk_path"], ramdisk_dir)
        # host has a ramdisk_path, but nothing in .run.toml opted in
        self.assertFalse(batch["runs"][0]["use_ramdisk"])

    def test_use_ramdisk_without_host_ramdisk_path_warns_and_disables(self):
        run_path = self._write_run_json("use-ramdisk-no-path", {
            "use_ramdisk": True,
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]}
            ],
        })
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                run_path,
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertFalse(batch["runs"][0]["use_ramdisk"])
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("ramdisk_path", buf.getvalue())

    def test_batch_level_use_ramdisk_applies_to_all_runs(self):
        ramdisk_dir = os.path.join(self.tmp, "fake_ramdisk")
        os.makedirs(ramdisk_dir, exist_ok=True)
        self._set_host_ramdisk_path(ramdisk_dir)
        run_path = self._write_run_json("use-ramdisk-batch", {
            "use_ramdisk": True,
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertTrue(all(r["use_ramdisk"] for r in batch["runs"]))

    def test_per_run_use_ramdisk_overrides_batch_level(self):
        ramdisk_dir = os.path.join(self.tmp, "fake_ramdisk")
        os.makedirs(ramdisk_dir, exist_ok=True)
        self._set_host_ramdisk_path(ramdisk_dir)
        run_path = self._write_run_json("use-ramdisk-override", {
            "use_ramdisk": True,
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"], "use_ramdisk": False},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        using = [r["use_ramdisk"] for r in batch["runs"]]
        self.assertTrue(using[0])
        self.assertFalse(using[1])

    def test_per_run_use_ramdisk_enables_when_batch_level_absent(self):
        ramdisk_dir = os.path.join(self.tmp, "fake_ramdisk")
        os.makedirs(ramdisk_dir, exist_ok=True)
        self._set_host_ramdisk_path(ramdisk_dir)
        run_path = self._write_run_json("use-ramdisk-per-run-only", {
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"], "use_ramdisk": True},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        using = [r["use_ramdisk"] for r in batch["runs"]]
        self.assertFalse(using[0])
        self.assertTrue(using[1])


class TestDacapoArgsConfig(unittest.TestCase):
    """dacapo_args resolution: batch-global default, per-run override."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_run_json(self, name, data):
        path = os.path.join(self.reporoot, "config", "run", f"{name}.run.toml")
        with open(path, "w") as f:
            _dump_toml(data, f)
        return path

    def test_dacapo_args_absent_by_default(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["dacapo_args"], [])

    def test_batch_level_dacapo_args_applies_to_all_runs(self):
        run_path = self._write_run_json("dacapo-args-batch", {
            "dacapo_args": ["-t", "16"],
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertTrue(all(r["dacapo_args"] == ["-t", "16"] for r in batch["runs"]))

    def test_per_run_dacapo_args_overrides_batch_level(self):
        run_path = self._write_run_json("dacapo-args-override", {
            "dacapo_args": ["-t", "16"],
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"]},
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"], "dacapo_args": ["-t", "4"]},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        args = [r["dacapo_args"] for r in batch["runs"]]
        self.assertEqual(args[0], ["-t", "16"])
        self.assertEqual(args[1], ["-t", "4"])

    def test_per_run_dacapo_args_explicit_empty_overrides_batch_level(self):
        run_path = self._write_run_json("dacapo-args-empty-override", {
            "dacapo_args": ["-t", "16"],
            "bench": "bench-h2",
            "runs": [
                {"jdkname": "jdk-21", "profiles": ["g1"],
                 "jvmargs": ["-Xmx2048m"], "dacapo_args": []},
            ],
        })
        batch = run_prepare(
            run_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["dacapo_args"], [])


class TestGuardAndErrors(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guard_existing_started_batch_file_raises(self):
        """If BATCH_IN_PROGRESS.json exists and a run's result_dir is present, raise."""
        batch_file = os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")
        run_id = "started-run-id"
        result_dir = f"results/{run_id}"
        os.makedirs(os.path.join(self.reporoot, result_dir))
        with open(batch_file, "w") as f:
            json.dump(
                {"batchid": "existing", "runs": [{"runid": run_id, "result_dir": result_dir}]},
                f,
            )

        with self.assertRaises(RuntimeError) as ctx:
            run_prepare(
                _run_json("plain"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertIn("BATCH_IN_PROGRESS.json already exists", str(ctx.exception))

    def test_guard_existing_unstarted_batch_file_is_overwritten(self):
        """If BATCH_IN_PROGRESS.json exists but no run's result_dir is present, overwrite it."""
        batch_file = os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")
        with open(batch_file, "w") as f:
            json.dump(
                {"batchid": "existing", "runs": [{"runid": "never-started", "result_dir": "results/never-started"}]},
                f,
            )

        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertNotEqual(batch["batchid"], "existing")
        with open(batch_file) as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["batchid"], batch["batchid"])

    def test_guard_existing_malformed_batch_file_raises(self):
        """If BATCH_IN_PROGRESS.json exists but is not valid JSON, raise."""
        batch_file = os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")
        with open(batch_file, "w") as f:
            f.write("{not valid json")

        with self.assertRaises(RuntimeError) as ctx:
            run_prepare(
                _run_json("plain"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertIn("BATCH_IN_PROGRESS.json already exists", str(ctx.exception))

    def test_missing_xmx_raises(self):
        """no-xmx.run.toml has no -Xmx anywhere → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            run_prepare(
                _run_json("no-xmx"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertIn("Xmx", str(ctx.exception))

    def test_conflicting_xmx_raises(self):
        """conflict-xmx.run.toml has -Xmx2048m and -Xmx4096m → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            run_prepare(
                _run_json("conflict-xmx"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertIn("Xmx", str(ctx.exception))

    def test_missing_host_config_raises(self):
        """Unknown hostname → RuntimeError about missing host config."""
        with self.assertRaises(RuntimeError) as ctx:
            run_prepare(
                _run_json("plain"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="no-such-host",
            )
        self.assertIn("Host config not found", str(ctx.exception))

    def test_missing_host_config_falls_back_to_default(self):
        """Unknown hostname with config/hosts/default.host.toml present → uses it."""
        import io
        import shutil as _shutil
        from contextlib import redirect_stdout
        hosts = os.path.join(self.reporoot, "config", "hosts")
        _shutil.copyfile(os.path.join(hosts, "testhost.host.toml"),
                         os.path.join(hosts, "default.host.toml"))
        out = io.StringIO()
        with redirect_stdout(out):
            batch = run_prepare(
                _run_json("plain"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="no-such-host",
            )
        self.assertEqual(batch["hostname"], "no-such-host")
        self.assertIn("default.host.toml", out.getvalue())

    def test_missing_jdk_config_raises(self):
        """Reference to undefined jdkname → RuntimeError."""
        # Write a run.json referencing a jdk that doesn't exist
        bad_run = {
            "bench": "bench-h2",
            "runs": [{"jdkname": "no-such-jdk",
                       "jvmargs": ["-Xmx1g"]}]
        }
        bad_path = os.path.join(
            self.reporoot, "config", "run", "bad-jdk.run.toml"
        )
        with open(bad_path, "w") as f:
            _dump_toml(bad_run, f)
        with self.assertRaises(RuntimeError) as ctx:
            run_prepare(
                bad_path,
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertIn("no-such-jdk", str(ctx.exception))

    def test_unknown_run_json_field_warns_not_aborts(self):
        """unknown-field.run.toml has 'tag' (unknown) → warning, no exception."""
        import io
        import sys as _sys

        captured = io.StringIO()
        old_stdout = _sys.stdout
        _sys.stdout = captured
        try:
            batch = run_prepare(
                _run_json("unknown-field"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        finally:
            _sys.stdout = old_stdout

        # Should succeed (return a batch dict)
        self.assertIn("runs", batch)
        # Should have emitted a warning about 'tag'
        output = captured.getvalue()
        self.assertIn("tag", output)
        self.assertIn("WARNING", output)


class TestWriteBatch(unittest.TestCase):

    def test_write_batch_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = {"batchid": "test-id", "runs": []}
            write_batch(batch, tmp)
            dest = os.path.join(tmp, "BATCH_IN_PROGRESS.json")
            self.assertTrue(os.path.exists(dest))

    def test_write_batch_content_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = {"batchid": "test-id", "runs": [{"seq": 1}]}
            write_batch(batch, tmp)
            dest = os.path.join(tmp, "BATCH_IN_PROGRESS.json")
            with open(dest) as f:
                loaded = json.load(f)
            self.assertEqual(loaded["batchid"], "test-id")
            self.assertEqual(loaded["runs"][0]["seq"], 1)

    def test_write_batch_creates_results_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = os.path.join(tmp, "results")
            batch = {"batchid": "x", "runs": []}
            write_batch(batch, results)
            self.assertTrue(os.path.exists(results))


class TestTagMerging(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tags_merged_from_all_sources(self):
        """CLI + batch-level + run-entry tags are unioned; duplicates removed."""
        batch = run_prepare(
            _run_json("group"),          # run entry has tags=["group-run"]
            _default_opts(tags=["cli-tag"]),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        for r in batch["runs"]:
            self.assertIn("cli-tag", r["tags"])
            self.assertIn("group-run", r["tags"])

    def test_tag_deduplication(self):
        """Duplicate tags across sources appear only once."""
        batch = run_prepare(
            _run_json("plain"),          # batch-level tags=["smoke"]
            _default_opts(tags=["smoke"]),  # same tag from CLI
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        tags = batch["runs"][0]["tags"]
        self.assertEqual(tags.count("smoke"), 1)


class TestJvmEnvMerging(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_jvm_env_merged_batch_and_per_run(self):
        """Batch-global jvmenv applies to every run; per-run wins on key collision."""
        batch = run_prepare(
            _run_json("jvm-env"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        no_override, with_override = batch["runs"]
        self.assertEqual(
            no_override["jvmenv"], {"MALLOC_ARENA_MAX": "1", "GLOBAL_ONLY": "g"}
        )
        self.assertEqual(
            with_override["jvmenv"], {"MALLOC_ARENA_MAX": "4", "GLOBAL_ONLY": "g"}
        )


class TestJdkTypeDetection(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)
        # Add openj9 jdktype fixture
        openj9_type = {
            "gc_log_option": "-Xverbosegclog:gc.log",
            "extra_jvmargs": []
        }
        with open(
            os.path.join(self.reporoot, "config", "jdk-types", "openj9.jdktype.toml"),
            "w",
        ) as f:
            _dump_toml(openj9_type, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_openj9_detected_from_semeru(self):
        """If java -version mentions 'Semeru', gc_log_option is openj9's."""
        semeru_output = (
            'openjdk version "21.0.3" 2024-04-16\n'
            'IBM Semeru Runtime Open Edition 21.0.3.0 (build 21.0.3+9)\n'
            'Eclipse OpenJ9 VM (build openj9-0.44.0)\n'
        )
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(semeru_output),
            _hostname="testhost",
        )
        flags = batch["runs"][0]["jvmargs"]
        self.assertIn("-Xverbosegclog:gc.log", flags)
        self.assertEqual(batch["runs"][0]["jdk_type"], "openj9")

    def test_openjdk_gc_log_used_for_hotspot(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        flags = batch["runs"][0]["jvmargs"]
        self.assertIn("-Xlog:gc:file=gc.log::filecount=0", flags)

    def test_java8_version_parsed_correctly(self):
        """Legacy '1.8.x' scheme → feature version 8."""
        java8_output = (
            'java version "1.8.0_362"\n'
            'Java(TM) SE Runtime Environment (build 1.8.0_362-b09)\n'
        )
        # Add a jdk-8 fixture
        with open(
            os.path.join(self.reporoot, "config", "jdks", "jdk-8.jdk.toml"), "w"
        ) as f:
            _dump_toml({"java_home": "/fake/jdk8"}, f)

        # bench-minver25 has min_java_version=25 — JDK 8 should be filtered out
        run8 = {
            "bench": "bench-minver25",
            "runs": [
                {"jdkname": "jdk-8", "jvmargs": ["-Xmx512m"]}
            ]
        }
        run8_path = os.path.join(self.reporoot, "config", "run", "run8.run.toml")
        with open(run8_path, "w") as f:
            _dump_toml(run8, f)

        batch = run_prepare(
            run8_path,
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(java8_output),
            _hostname="testhost",
        )
        # min_java_version=25 → JDK 8 filtered out
        self.assertEqual(len(batch["runs"]), 0)


class TestNmtTracking(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)
        openj9_type = {
            "gc_log_option": "-Xverbosegclog:gc.log",
            "extra_jvmargs": []
        }
        with open(
            os.path.join(self.reporoot, "config", "jdk-types", "openj9.jdktype.toml"),
            "w",
        ) as f:
            _dump_toml(openj9_type, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_by_default(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        run = batch["runs"][0]
        self.assertNotIn("nmt", batch["collectors"]["enabled"])
        for flag in ("-XX:NativeMemoryTracking=summary", "-XX:+PrintNMTStatistics"):
            self.assertNotIn(flag, run["jvmargs"])

    def test_batch_level_enables_and_injects_flags(self):
        batch = run_prepare(
            _run_json("nmt-tracking"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        h2, = batch["runs"]
        self.assertIn("nmt", batch["collectors"]["enabled"])
        for flag in (
            "-XX:NativeMemoryTracking=summary",
            "-XX:+UnlockDiagnosticVMOptions",
            "-XX:+PrintNMTStatistics",
        ):
            self.assertIn(flag, h2["jvmargs"])

    def test_unsupported_on_openj9_disables_with_warning(self):
        semeru_output = (
            'openjdk version "21.0.3" 2024-04-16\n'
            'IBM Semeru Runtime Open Edition 21.0.3.0 (build 21.0.3+9)\n'
            'Eclipse OpenJ9 VM (build openj9-0.44.0)\n'
        )
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                _run_json("nmt-tracking"),
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(semeru_output),
                _hostname="testhost",
            )
        h2, = batch["runs"]
        self.assertNotIn("nmt", batch["collectors"]["enabled"])
        self.assertNotIn("-XX:NativeMemoryTracking=summary", h2["jvmargs"])
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("'nmt'", buf.getvalue())


    def test_mixed_jdk_batch_drops_nmt_for_all_runs(self):
        # One OpenJ9 run excludes nmt (restrict_to openjdk): it is dropped for
        # the whole batch, OpenJDK runs included (PRD.md D-84).
        import io
        from contextlib import redirect_stdout
        with open(os.path.join(self.reporoot, "config", "jdks", "j9-21.jdk.toml"), "w") as f:
            _dump_toml({"java_home": "/fake/j9-21"}, f)
        path = os.path.join(self.reporoot, "config", "run", "mixed.run.toml")
        with open(path, "w") as f:
            _dump_toml({"bench": "bench-h2",
                        "collectors": _coll(enabled=["proc", "nmt"]),
                        "runs": [{"jdkname": "jdk-21", "profiles": ["g1"], "jvmargs": ["-Xmx2048m"]},
                                 {"jdkname": "j9-21", "profiles": ["g1"], "jvmargs": ["-Xmx2048m"]}]}, f)
        semeru_output = (
            'openjdk version "21.0.3" 2024-04-16\n'
            'IBM Semeru Runtime Open Edition 21.0.3.0 (build 21.0.3+9)\n'
            'Eclipse OpenJ9 VM (build openj9-0.44.0)\n'
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            batch = run_prepare(
                path, _default_opts(), reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=lambda cmd: semeru_output if "j9" in cmd[0] else _JDK21_OUTPUT,
                _hostname="testhost",
            )
        self.assertEqual([r["jdk_type"] for r in batch["runs"]], ["openjdk", "openj9"])
        self.assertEqual(batch["collectors"]["enabled"], ["proc"])
        for run in batch["runs"]:
            self.assertNotIn("-XX:NativeMemoryTracking=summary", run["jvmargs"])
        self.assertIn("collector 'nmt', but it does not apply to JDK type 'openj9' version 21 "
                      "used by some runs — disabling it for the whole batch", buf.getvalue())


class TestThreadPrintTracking(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)
        openj9_type = {
            "gc_log_option": "-Xverbosegclog:gc.log",
            "extra_jvmargs": []
        }
        with open(
            os.path.join(self.reporoot, "config", "jdk-types", "openj9.jdktype.toml"),
            "w",
        ) as f:
            _dump_toml(openj9_type, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_by_default(self):
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        run = batch["runs"][0]
        self.assertNotIn("thread_print", batch["collectors"]["enabled"])

    def test_batch_level_enables_no_flags_injected(self):
        batch = run_prepare(
            _run_json("thread-print-tracking"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        h2, = batch["runs"]
        self.assertIn("thread_print", batch["collectors"]["enabled"])
        # Unlike NMT, Thread.print needs no extra JVM flags.
        self.assertEqual(h2["jvmargs"].count("-Xmx2048m"), 1)

    def test_applies_on_openj9_too(self):
        # Unlike NMT/System.map, this collector's config has no restrict_to.
        semeru_output = (
            'openjdk version "21.0.3" 2024-04-16\n'
            'IBM Semeru Runtime Open Edition 21.0.3.0 (build 21.0.3+9)\n'
            'Eclipse OpenJ9 VM (build openj9-0.44.0)\n'
        )
        batch = run_prepare(
            _run_json("thread-print-tracking"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(semeru_output),
            _hostname="testhost",
        )
        h2, = batch["runs"]
        self.assertIn("thread_print", batch["collectors"]["enabled"])


class TestRepeatExpansion(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **opts):
        return run_prepare(
            _run_json("repeat"),
            _default_opts(**opts),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )

    def test_no_repeat_produces_two_runs(self):
        # repeat.run.toml has 2 benches (a bench array); default repeat=1
        batch = self._run()
        self.assertEqual(len(batch["runs"]), 2)

    def test_no_repeat_repeat_no_is_zero(self):
        batch = self._run()
        for r in batch["runs"]:
            self.assertEqual(r["repeat_no"], 0)

    def test_repeat_3_produces_six_runs(self):
        # 2 benches × 3 repeats = 6 runs
        batch = self._run(repeat=3)
        self.assertEqual(len(batch["runs"]), 6)

    def test_repeat_interleaved_order(self):
        # Expected order: h2(0), tomcat(0), h2(1), tomcat(1), h2(2), tomcat(2)
        batch = self._run(repeat=3)
        runs = batch["runs"]
        bench_names = [r["bench_json"] for r in runs]
        repeat_nos  = [r["repeat_no"]  for r in runs]
        self.assertEqual(bench_names, [
            "bench-h2", "bench-tomcat",
            "bench-h2", "bench-tomcat",
            "bench-h2", "bench-tomcat",
        ])
        self.assertEqual(repeat_nos, [0, 0, 1, 1, 2, 2])

    def test_repeat_seq_is_sequential(self):
        batch = self._run(repeat=3)
        seqs = [r["seq"] for r in batch["runs"]]
        self.assertEqual(seqs, list(range(1, 7)))

    def test_repeat_each_run_has_unique_run_id(self):
        batch = self._run(repeat=3)
        run_ids = [r["runid"] for r in batch["runs"]]
        self.assertEqual(len(run_ids), len(set(run_ids)))

    def test_repeat_1_same_as_default(self):
        batch_default = self._run()
        # Fresh reporoot for second call (guard check)
        import shutil
        tmp2 = tempfile.mkdtemp()
        reporoot2 = _make_reporoot(tmp2)
        try:
            batch_explicit = run_prepare(
                _run_json("repeat"),
                _default_opts(repeat=1),
                reporoot=reporoot2, project=reporoot2,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
        self.assertEqual(len(batch_default["runs"]), len(batch_explicit["runs"]))
        for r in batch_explicit["runs"]:
            self.assertEqual(r["repeat_no"], 0)


class TestRepeatFromRunFile(unittest.TestCase):
    """File-level 'repeat' key; --repeat (cli_options['repeat']) overrides it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, names, **opts):
        return run_prepare(
            [_run_json(n) for n in names],
            _default_opts(**opts),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )

    def test_file_repeat_applied(self):
        batch = self._run(["repeat-3"])
        self.assertEqual([r["repeat_no"] for r in batch["runs"]], [0, 1, 2])

    def test_cli_repeat_overrides_file(self):
        batch = self._run(["repeat-3"], repeat=2)
        self.assertEqual([r["repeat_no"] for r in batch["runs"]], [0, 1])

    def test_cli_repeat_1_overrides_file(self):
        batch = self._run(["repeat-3"], repeat=1)
        self.assertEqual([r["repeat_no"] for r in batch["runs"]], [0])

    def test_file_repeat_applies_to_files_without_it(self):
        # plain sets no repeat; repeat-2 does -> whole combined list repeated
        batch = self._run(["plain", "repeat-2"])
        runs = batch["runs"]
        self.assertEqual([r["source_file"] for r in runs], [
            "plain.run.toml", "repeat-2.run.toml",
            "plain.run.toml", "repeat-2.run.toml",
        ])
        self.assertEqual([r["repeat_no"] for r in runs], [0, 0, 1, 1])

    def test_conflicting_file_repeats_raise(self):
        with self.assertRaises(ValueError) as cm:
            self._run(["repeat-2", "repeat-3"])
        self.assertIn("repeat", str(cm.exception))
        self.assertFalse(os.path.exists(
            os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")))

    def test_conflicting_file_repeats_resolved_by_cli(self):
        batch = self._run(["repeat-2", "repeat-3"], repeat=1)
        self.assertEqual(len(batch["runs"]), 2)


class TestMultiFile(unittest.TestCase):
    """Multiple .run.toml files in one jbr-prepare invocation (PRD.md D-61, D-62)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_file_still_accepted_as_bare_string(self):
        """Backward compatibility: a bare string path still works."""
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(len(batch["runs"]), 1)

    def test_source_file_set_for_single_file_batch(self):
        """source_file is always present, even for a single-file batch (D-62)."""
        batch = run_prepare(
            _run_json("plain"),
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["runs"][0]["source_file"], "plain.run.toml")

    def test_entries_concatenated_in_file_order_with_contiguous_seq(self):
        batch = run_prepare(
            [_run_json("plain"), _run_json("jdkname-batch-global")],
            _default_opts(),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        runs = batch["runs"]
        self.assertEqual(len(runs), 2)
        self.assertEqual([r["seq"] for r in runs], [1, 2])
        self.assertEqual(runs[0]["source_file"], "plain.run.toml")
        self.assertEqual(runs[1]["source_file"], "jdkname-batch-global.run.toml")

    def test_differing_collector_settings_rejected(self):
        """collector-interval.run.toml sets collectors.interval=3, plain.run.toml
        leaves the default (10): the settings are batch-wide (PRD.md D-84), so
        the files must agree; nothing is written."""
        with self.assertRaisesRegex(ValueError, "collector settings differ"):
            run_prepare(
                [_run_json("plain"), _run_json("collector-interval")],
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        self.assertFalse(os.path.exists(os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")))

    def test_cli_interval_resolves_differing_file_intervals(self):
        """--collector-interval (CLI) wins over every file's value, so files
        differing only in the interval are accepted."""
        batch = run_prepare(
            [_run_json("plain"), _run_json("collector-interval")],
            _default_opts(collector_interval=30),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        self.assertEqual(batch["collectors"]["interval_secs"], 30)

    def test_repeat_interleaves_across_concatenated_multi_file_list(self):
        batch = run_prepare(
            [_run_json("plain"), _run_json("jdkname-batch-global")],
            _default_opts(repeat=2),
            reporoot=self.reporoot, project=self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )
        runs = batch["runs"]
        self.assertEqual(len(runs), 4)
        self.assertEqual([r["bench_json"] for r in runs],
                          ["bench-h2", "bench-h2", "bench-h2", "bench-h2"])
        self.assertEqual([r["source_file"] for r in runs], [
            "plain.run.toml",
            "jdkname-batch-global.run.toml",
            "plain.run.toml",
            "jdkname-batch-global.run.toml",
        ])
        self.assertEqual([r["repeat_no"] for r in runs], [0, 0, 1, 1])
        self.assertEqual([r["seq"] for r in runs], [1, 2, 3, 4])

    def test_error_in_second_file_aborts_whole_multi_file_batch(self):
        """no-xmx.run.toml has no -Xmx anywhere -> ValueError; nothing must be
        written, including entries already resolved from the first (valid) file."""
        with self.assertRaises(ValueError):
            run_prepare(
                [_run_json("plain"), _run_json("no-xmx")],
                _default_opts(),
                reporoot=self.reporoot, project=self.reporoot,
                _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                _hostname="testhost",
            )
        batch_file = os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")
        self.assertFalse(os.path.exists(batch_file))


class TestProjectLayout(unittest.TestCase):
    """Batch files, frozen copies and the active-batch guard (PRD.md §4, §7, D-73)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reporoot = _make_reporoot(self.tmp)
        self.link = os.path.join(os.environ["XDG_STATE_HOME"], "jbenchrunner", "active-batch")

    def tearDown(self):
        import shutil
        if os.path.lexists(self.link):
            os.remove(self.link)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prepare(self, paths, project=None):
        return run_prepare(
            paths,
            _default_opts(),
            reporoot=self.reporoot, project=project or self.reporoot,
            _run_java_version=_java_version_stub(_JDK21_OUTPUT),
            _hostname="testhost",
        )

    def _set_link(self, target):
        os.makedirs(os.path.dirname(self.link), exist_ok=True)
        os.symlink(target, self.link)

    def test_frozen_copies_written_into_batch_dir(self):
        batch = self._prepare([_run_json("plain"), _run_json("jdkname-batch-global")])
        frozen = os.path.join(self.reporoot, "batches", batch["batchid"])
        for name in ("plain", "jdkname-batch-global"):
            with open(os.path.join(frozen, f"{name}.run.toml"), "rb") as f:
                copy = f.read()
            with open(_run_json(name), "rb") as f:
                self.assertEqual(copy, f.read())
        self.assertTrue(os.path.isfile(os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")))

    def test_result_dir_relative_to_project(self):
        batch = self._prepare(_run_json("plain"))
        run = batch["runs"][0]
        self.assertEqual(run["result_dir"], f"results/{run['runid']}")

    def test_duplicate_base_names_rejected(self):
        other = os.path.join(self.tmp, "elsewhere")
        os.makedirs(other)
        import shutil
        dup = os.path.join(other, "plain.run.toml")
        shutil.copyfile(_run_json("plain"), dup)
        with self.assertRaises(ValueError) as ctx:
            self._prepare([_run_json("plain"), dup])
        self.assertIn("plain.run.toml", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.reporoot, "batches")))

    def test_overwriting_unstarted_batch_removes_its_batch_dir(self):
        first = self._prepare(_run_json("plain"))
        old_dir = os.path.join(self.reporoot, "batches", first["batchid"])
        self.assertTrue(os.path.isdir(old_dir))
        second = self._prepare(_run_json("plain"))
        self.assertFalse(os.path.exists(old_dir))
        self.assertTrue(os.path.isdir(os.path.join(self.reporoot, "batches", second["batchid"])))

    def test_active_batch_in_other_project_blocks(self):
        other = os.path.join(self.tmp, "other-project")
        os.makedirs(other)
        other_batch = os.path.join(other, "BATCH_IN_PROGRESS.json")
        with open(other_batch, "w") as f:
            f.write("{}")
        self._set_link(other_batch)
        with self.assertRaises(RuntimeError) as ctx:
            self._prepare(_run_json("plain"))
        self.assertIn(other_batch, str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.reporoot, "BATCH_IN_PROGRESS.json")))

    def test_dangling_active_batch_link_blocks(self):
        self._set_link(os.path.join(self.tmp, "gone", "BATCH_IN_PROGRESS.json"))
        with self.assertRaises(RuntimeError) as ctx:
            self._prepare(_run_json("plain"))
        self.assertIn("no longer exists", str(ctx.exception))

    def test_outside_a_project_fails(self):
        os.remove(os.path.join(self.reporoot, "jbr-project.json"))
        from common.project import ProjectError
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            with self.assertRaises(ProjectError):
                run_prepare(
                    _run_json("plain"), _default_opts(), reporoot=self.reporoot,
                    _run_java_version=_java_version_stub(_JDK21_OUTPUT),
                    _hostname="testhost",
                )
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
