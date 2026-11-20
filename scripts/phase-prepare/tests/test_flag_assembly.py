#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/phase-prepare/flag_assembly.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(__file__)
_PHASE_PREPARE_DIR = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _PHASE_PREPARE_DIR)

from flag_assembly import assemble_flags, parse_xmx_mb, check_flag_conflicts


class TestAssembleFlags(unittest.TestCase):

    def test_assembly_order(self):
        # 1. Bench + sweep
        # 2. Profiles (flattened)
        # 3. Run top + entry
        # 4. CLI
        # 5. JDK type extra + GC log
        bench_flags = ["-Dbench=true"]
        sweep_flags = ["-Xmx512m", "-Xms512m"]
        profile_flags_list = [["-XX:+UseG1GC"], ["-XX:G1HeapRegionSize=16m"]]
        run_top_flags = ["-Dtop=true"]
        run_entry_flags = ["-Dentry=true"]
        cli_flags = ["--add-opens=java.base/java.lang=ALL-UNNAMED"]
        jdktype_extra_flags = ["-XX:+PrintFlagsFinal"]
        gc_log_flag = "-Xlog:gc*"

        result = assemble_flags(
            bench_flags=bench_flags,
            sweep_flags=sweep_flags,
            profile_flags_list=profile_flags_list,
            run_top_flags=run_top_flags,
            run_entry_flags=run_entry_flags,
            cli_flags=cli_flags,
            jdktype_extra_flags=jdktype_extra_flags,
            gc_log_flag=gc_log_flag,
        )

        expected = [
            "-Dbench=true",
            "-Xmx512m",
            "-Xms512m",
            "-XX:+UseG1GC",
            "-XX:G1HeapRegionSize=16m",
            "-Dtop=true",
            "-Dentry=true",
            "--add-opens=java.base/java.lang=ALL-UNNAMED",
            "-XX:+PrintFlagsFinal",
            "-Xlog:gc*",
        ]
        self.assertEqual(result, expected)

    def test_assembly_with_none_and_empty(self):
        result = assemble_flags(
            bench_flags=None,
            sweep_flags=[],
            profile_flags_list=None,
            run_top_flags=[],
            run_entry_flags=None,
            cli_flags=None,
            jdktype_extra_flags=None,
            gc_log_flag=None,
        )
        self.assertEqual(result, [])

    def test_assembly_trims_whitespace(self):
        # Leading/trailing whitespace on flags from any source must be stripped.
        result = assemble_flags(
            bench_flags=[" -Dbench=true"],
            sweep_flags=["-Xmx512m "],
            profile_flags_list=[[" -XX:+UseG1GC "]],
            run_top_flags=["-Dtop=true\t"],
            run_entry_flags=["\n-Dentry=true"],
            cli_flags=[" --add-opens=java.base/java.lang=ALL-UNNAMED "],
            jdktype_extra_flags=["  -XX:+PrintFlagsFinal"],
            gc_log_flag=" -Xlog:gc* ",
        )
        expected = [
            "-Dbench=true",
            "-Xmx512m",
            "-XX:+UseG1GC",
            "-Dtop=true",
            "-Dentry=true",
            "--add-opens=java.base/java.lang=ALL-UNNAMED",
            "-XX:+PrintFlagsFinal",
            "-Xlog:gc*",
        ]
        self.assertEqual(result, expected)

    def test_profile_flags_list_handling(self):
        # Profile list containing flat list of strings
        profile_flags_list_flat = ["-XX:+UseParallelGC", "-XX:ParallelGCThreads=4"]
        result1 = assemble_flags(profile_flags_list=profile_flags_list_flat)
        self.assertEqual(result1, ["-XX:+UseParallelGC", "-XX:ParallelGCThreads=4"])

        # Profile list containing nested list of strings
        profile_flags_list_nested = [
            ["-XX:+UseParallelGC"],
            ["-XX:ParallelGCThreads=4"],
        ]
        result2 = assemble_flags(profile_flags_list=profile_flags_list_nested)
        self.assertEqual(result2, ["-XX:+UseParallelGC", "-XX:ParallelGCThreads=4"])


class TestParseXmxMB(unittest.TestCase):

    def test_suffixes_conversion(self):
        # Kilobytes (k/K)
        self.assertEqual(parse_xmx_mb(["-Xmx1024k"]), 1)
        self.assertEqual(parse_xmx_mb(["-Xmx2048K"]), 2)
        self.assertEqual(parse_xmx_mb(["-Xmx512k"]), 0)

        # Megabytes (m/M)
        self.assertEqual(parse_xmx_mb(["-Xmx256m"]), 256)
        self.assertEqual(parse_xmx_mb(["-Xmx4096M"]), 4096)

        # Gigabytes (g/G)
        self.assertEqual(parse_xmx_mb(["-Xmx1g"]), 1024)
        self.assertEqual(parse_xmx_mb(["-Xmx4G"]), 4096)

        # Terabytes (t/T)
        self.assertEqual(parse_xmx_mb(["-Xmx1t"]), 1048576)
        self.assertEqual(parse_xmx_mb(["-Xmx2T"]), 2097152)

        # No suffix (bytes)
        self.assertEqual(parse_xmx_mb(["-Xmx1048576"]), 1)
        self.assertEqual(parse_xmx_mb(["-Xmx2097152"]), 2)

    def test_last_wins(self):
        flags = ["-Xmx512m", "-DsomeOption", "-Xmx1g"]
        self.assertEqual(parse_xmx_mb(flags), 1024)

        flags_same = ["-Xmx1024m", "-Xmx1g"]
        self.assertEqual(parse_xmx_mb(flags_same), 1024)

    def test_missing_xmx_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            parse_xmx_mb(["-Xms512m", "-XX:+UseG1GC"])
        self.assertIn("Missing -Xmx flag", str(ctx.exception))

    def test_malformed_xmx_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_xmx_mb(["-Xmx"])
        with self.assertRaises(ValueError):
            parse_xmx_mb(["-Xmxabc"])
        with self.assertRaises(ValueError):
            parse_xmx_mb(["-Xmx100x"])


class TestCheckFlagConflicts(unittest.TestCase):

    def test_same_value_accepted(self):
        # -Xmx same value (different representation)
        check_flag_conflicts(["-Xmx1g", "-Xmx1024m"])
        check_flag_conflicts(["-Xmx2048k", "-Xmx2m"])
        check_flag_conflicts(["-Xmx1048576", "-Xmx1024k", "-Xmx1m"])

        # -Xms same value (different representation)
        check_flag_conflicts(["-Xms1g", "-Xms1024m"])
        check_flag_conflicts(["-Xms2048k", "-Xms2m"])

    def test_conflicting_xmx_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            check_flag_conflicts(["-Xmx1g", "-Xmx2g"])
        self.assertIn("Conflicting -Xmx values", str(ctx.exception))

    def test_conflicting_xms_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            check_flag_conflicts(["-Xms1g", "-Xms512m"])
        self.assertIn("Conflicting -Xms values", str(ctx.exception))

    def test_malformed_flags_raise_value_error(self):
        with self.assertRaises(ValueError):
            check_flag_conflicts(["-Xmxabc"])
        with self.assertRaises(ValueError):
            check_flag_conflicts(["-Xmsabc"])


if __name__ == "__main__":
    unittest.main()
