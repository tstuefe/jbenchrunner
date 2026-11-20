# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Integration tests for scripts/phase-execute/execute.py.

These tests run the real benchmark process against a real JDK and a real DaCapo
installation, taken from this machine's real host config
(config/hosts/<hostname>.host.toml, else config/hosts/default.host.toml):

test_jdk    — absolute path to a JDK installation (java binary at
              <test_jdk>/bin/java)
dacapo_dir  — absolute path to the DaCapo installation directory
              (must contain a dacapo-*-chopin.jar)

Each can be overridden with an environment variable (JBR_TEST_JAVA_HOME,
JBR_TEST_DACAPO_DIR). If either path is unset or does not exist, the entire
class is skipped.

Example
-------
    JBR_TEST_JAVA_HOME=/path/to/jdk \\
    python3 -m unittest discover -s scripts/phase-execute/tests -p 'test_execute_integration.py'

The test constructs a hermetic, throwaway repository tree in a temp directory
so that the real config/ and any real project are never touched. The temp
tree doubles as the project (PRD.md §4).
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import tomllib
import unittest
import uuid
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_EXECUTE_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _PHASE_EXECUTE_DIR not in sys.path:
    sys.path.insert(0, _PHASE_EXECUTE_DIR)

import execute  # noqa: E402
from common.reporoot import find_reporoot  # noqa: E402

# ---------------------------------------------------------------------------
# Path to the batch template fixture. Placeholders __JAVA_HOME__,
# __DACAPO_DIR__, __RUN_ID__ and __HOSTNAME__ are replaced at test setup time.
# ---------------------------------------------------------------------------
_FIXTURE_TEMPLATE = os.path.join(_HERE, "fixtures", "batch_fop_small_template.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _real_host_config() -> dict:
    """This machine's real host config, as jbr-prepare picks it; {} if it
    cannot be read."""
    try:
        hosts_dir = os.path.join(find_reporoot(__file__), "config", "hosts")
        path = os.path.join(hosts_dir, f"{socket.gethostname()}.host.toml")
        if not os.path.isfile(path):
            path = os.path.join(hosts_dir, "default.host.toml")
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError, RuntimeError):
        return {}


def _make_reporoot(tmp_dir: str, java_home: str, dacapo_dir: str) -> tuple:
    """Build a hermetic fake repository root inside *tmp_dir*.

    Creates:
      <tmp_dir>/
        dacapo-timestamp-plugin/
          dacapoExtension.jar       (real jar from the actual repo)
        jbr-project.json            (the temp tree doubles as the project)
        BATCH_IN_PROGRESS.json      (built from the template, run_id filled in)

    Returns (reporoot, run_id) where reporoot == tmp_dir.
    """
    # Locate the real repo root so we can copy the real dacapoExtension.jar.
    real_reporoot = find_reporoot(__file__)

    # dacapo-timestamp-plugin/dacapoExtension.jar — copy the real one.
    plugin_src = os.path.join(real_reporoot, "dacapo-timestamp-plugin", "dacapoExtension.jar")
    plugin_dst_dir = os.path.join(tmp_dir, "dacapo-timestamp-plugin")
    os.makedirs(plugin_dst_dir, exist_ok=True)
    shutil.copy2(plugin_src, os.path.join(plugin_dst_dir, "dacapoExtension.jar"))

    # Project marker
    with open(os.path.join(tmp_dir, "jbr-project.json"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")

    # Build BATCH_IN_PROGRESS.json from the template.
    run_id = str(uuid.uuid4())
    with open(_FIXTURE_TEMPLATE, encoding="utf-8") as fh:
        template_text = fh.read()

    batch_text = (
        template_text
        .replace("__JAVA_HOME__", java_home)
        .replace("__DACAPO_DIR__", dacapo_dir)
        .replace("__RUN_ID__", run_id)
        .replace("__HOSTNAME__", socket.gethostname())
    )
    batch = json.loads(batch_text)
    # The template's collectors (cpu_temp, pidstat, proc) need /sys, sysstat and
    # /proc; elsewhere (e.g. macOS) run the benchmark without collectors.
    if not sys.platform.startswith("linux"):
        batch["collectors"]["enabled"] = []
    batch_path = os.path.join(tmp_dir, "BATCH_IN_PROGRESS.json")
    with open(batch_path, "w", encoding="utf-8") as fh:
        json.dump(batch, fh, indent=2)

    return tmp_dir, run_id


# ---------------------------------------------------------------------------
# Integration test class
# ---------------------------------------------------------------------------

_CONFIG = _real_host_config()
_JAVA_HOME = os.environ.get("JBR_TEST_JAVA_HOME") or _CONFIG.get("test_jdk", "")
_DACAPO_DIR = os.environ.get("JBR_TEST_DACAPO_DIR") or _CONFIG.get("dacapo_dir", "")


@unittest.skipUnless(_JAVA_HOME and os.path.isfile(os.path.join(_JAVA_HOME, "bin", "java")),
                     "no test JDK (test_jdk in the host config or JBR_TEST_JAVA_HOME)")
@unittest.skipUnless(_DACAPO_DIR and os.path.isdir(_DACAPO_DIR),
                     "no DaCapo (dacapo_dir in the host config or JBR_TEST_DACAPO_DIR)")
class TestExecuteFopSmall(unittest.TestCase):
    """End-to-end integration test: DaCapo fop, datasize=small, 1 iteration."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jbr_integration_test_")
        self.reporoot, self.run_id = _make_reporoot(self.tmp, _JAVA_HOME, _DACAPO_DIR)
        # Keep the active-batch link (PRD.md §7) away from the user's real one.
        patcher = patch.dict(os.environ, {"XDG_STATE_HOME": os.path.join(self.tmp, "state")})
        patcher.start()
        self.addCleanup(patcher.stop)
        # run_execute chdirs into the project under self.tmp; restore the cwd
        # before tearDown deletes it, or later tests inherit a deleted cwd.
        self._orig_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fop_small_run_succeeds(self):
        """A single fop/small iteration must complete with status DONE."""
        options = {
            "abort": False,
            "shutdown": False,
            "sudo": False,
            "verbose": True,
        }
        # run_execute writes results under self.reporoot/results/
        execute.run_execute(options, _reporoot=self.reporoot, _project_dir=self.reporoot)

        run_info_path = os.path.join(
            self.reporoot, "results", self.run_id, "run-info.json"
        )
        self.assertTrue(
            os.path.exists(run_info_path),
            f"run-info.json not found at {run_info_path}",
        )
        with open(run_info_path, encoding="utf-8") as fh:
            run_info = json.load(fh)

        self.assertEqual(run_info["status"], "DONE",
                         f"Expected DONE, got {run_info['status']}. "
                         f"Check {self.tmp}/results/{self.run_id}/stdout.log")
        self.assertEqual(run_info["exit_code"], 0)

    def test_fop_small_info_json_written(self):
        """info.json must be present and have the correct benchmark fields."""
        options = {"abort": False, "shutdown": False, "sudo": False, "verbose": False}
        execute.run_execute(options, _reporoot=self.reporoot, _project_dir=self.reporoot)

        info_path = os.path.join(
            self.reporoot, "results", self.run_id, "info.json"
        )
        self.assertTrue(os.path.exists(info_path))
        with open(info_path, encoding="utf-8") as fh:
            info = json.load(fh)

        self.assertEqual(info["benchmark"], "dacapo")
        self.assertEqual(info["dacapo"]["sub_benchmark"], "fop")
        self.assertEqual(info["dacapo"]["datasize"], "small")
        self.assertEqual(info["dacapo"]["iterations"], 1)
        self.assertIn("jdk", info)
        self.assertIn("version", info["jdk"])
