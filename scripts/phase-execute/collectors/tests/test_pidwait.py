#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Tests for scripts/phase-execute/collectors/_pidwait.py"""

import importlib.util
import os
import subprocess
import time
import unittest
from pathlib import Path

_HELPER = Path(__file__).resolve().parent.parent / "_pidwait.py"
_spec = importlib.util.spec_from_file_location("_pidwait", _HELPER)
pidwait = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pidwait)


class TestPidAlive(unittest.TestCase):

    def test_own_pid_alive(self):
        self.assertTrue(pidwait.pid_alive(os.getpid()))

    def test_nonexistent_pid(self):
        self.assertFalse(pidwait.pid_alive(2_000_000_000))

    def test_reaped_child_gone(self):
        p = subprocess.Popen(["true"])
        p.wait()
        self.assertFalse(pidwait.pid_alive(p.pid))


class TestSleepWhileAlive(unittest.TestCase):

    def test_returns_true_after_full_time_when_alive(self):
        start = time.monotonic()
        self.assertTrue(pidwait.sleep_while_alive(os.getpid(), 1.5))
        self.assertGreaterEqual(time.monotonic() - start, 1.5)

    def test_zero_seconds_returns_at_once(self):
        start = time.monotonic()
        self.assertTrue(pidwait.sleep_while_alive(os.getpid(), 0))
        self.assertLess(time.monotonic() - start, 0.5)

    def test_zero_seconds_dead_pid(self):
        self.assertFalse(pidwait.sleep_while_alive(2_000_000_000, 0))

    def test_returns_false_promptly_when_process_dies(self):
        # A long sleep must be cut short, within about one slice of the death.
        target = subprocess.Popen(["sleep", "1"])
        start = time.monotonic()
        try:
            # Reap the child in the background so the pid disappears (not a zombie).
            import threading
            threading.Thread(target=target.wait, daemon=True).start()
            alive = pidwait.sleep_while_alive(target.pid, 300)
        finally:
            target.kill()
            target.wait()
        self.assertFalse(alive)
        self.assertLess(time.monotonic() - start, 5)


if __name__ == "__main__":
    unittest.main()
