# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Tests for common/jdk_detect.py — parsing of `java -version` output.

Run from repo root:
    python3 -m unittest scripts/common/tests/test_jdk_detect.py
"""

import os
import sys
import unittest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.jdk_detect import parse_java_version_output  # noqa: E402


class TestParseJavaVersionOutput(unittest.TestCase):

    def _p(self, output):
        return parse_java_version_output(output)

    def test_openjdk_dotted(self):
        info = self._p('openjdk version "21.0.4" 2024-07-16\n'
                       'OpenJDK 64-Bit Server VM (build 21.0.4+7, mixed mode)\n')
        self.assertEqual(info, {"version": "21.0.4", "major": 21, "type": "openjdk"})

    def test_four_component_version(self):
        info = self._p('openjdk version "26.0.2.1" 2026-01-20\n')
        self.assertEqual(info["version"], "26.0.2.1")
        self.assertEqual(info["major"], 26)

    def test_ga_version_without_dot(self):
        """GA releases print just the feature number, e.g. "27"."""
        info = self._p('openjdk version "27" 2026-09-15\n')
        self.assertEqual(info["version"], "27")
        self.assertEqual(info["major"], 27)

    def test_internal_build(self):
        """Self-built JDKs print e.g. "28-internal"."""
        info = self._p('openjdk version "28-internal" 2027-03-23\n'
                       'OpenJDK Runtime Environment (build 28-internal-adhoc.x.source)\n')
        self.assertEqual(info["version"], "28")
        self.assertEqual(info["major"], 28)

    def test_java8_legacy_scheme(self):
        info = self._p('java version "1.8.0_362"\n'
                       'Java(TM) SE Runtime Environment (build 1.8.0_362-b09)\n')
        self.assertEqual(info["version"], "1.8.0")
        self.assertEqual(info["major"], 8)

    def test_openj9(self):
        info = self._p('openjdk version "11.0.15" 2022-04-19\n'
                       'Eclipse OpenJ9 VM AdoptOpenJDK (build openj9-0.32.0)\n')
        self.assertEqual(info["type"], "openj9")
        self.assertEqual(info["major"], 11)

    def test_semeru(self):
        info = self._p('openjdk version "17.0.6" 2023-01-17\n'
                       'IBM Semeru Runtime Open Edition 17.0.6.0 (build 17.0.6+10)\n')
        self.assertEqual(info["type"], "openj9")

    def test_leading_noise_lines_ignored(self):
        """JVMs may print e.g. 'Picked up JAVA_TOOL_OPTIONS' before the version."""
        info = self._p('Picked up JAVA_TOOL_OPTIONS: -Xss2m\n'
                       'openjdk version "25.0.1" 2025-10-21\n')
        self.assertEqual(info["major"], 25)

    def test_unparseable_raises(self):
        with self.assertRaises(RuntimeError):
            self._p("Error: could not create the Java Virtual Machine.\n")


if __name__ == "__main__":
    unittest.main()
