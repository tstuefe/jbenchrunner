# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/common/config_loader.py

Tests cover:
  - valid inputs produce expected dicts
  - missing required fields raise ValueError
  - unknown fields trigger a warning (but do not raise)
  - a selection of invalid field types/values raise ValueError
"""

import os
import tempfile
import unittest

from scripts.common.toml_writer import dumps as toml_dumps

from scripts.common.config_loader import (
    load_bench_json,
    load_benchgroup_json,
    load_host_json,
    load_jdk_json,
    load_jdktype_json,
    load_collector_json,
    load_collector_config,
    collector_applies,
    load_profile_json,
    load_run_json,
    load_collector_preset,
    load_collector_preset_json,
    collectors_preset_needed,
    load_perf_events_preset,
    load_perf_events_preset_json,
    resolve_collectors,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(data: dict) -> str:
    """Write `data` as TOML to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, "w") as fh:
        fh.write(toml_dumps(data))
    return path


def _write_raw(text: str) -> str:
    """Write raw TOML `text` to a temp file and return the path.

    Needed for duplicate-key tests: a Python dict literal can't hold a
    repeated key, so those cases must be written as raw TOML text.
    """
    fd, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


def _collecting_warn():
    """Return (warn_callable, messages_list) pair."""
    messages = []
    return messages.append, messages


# ---------------------------------------------------------------------------
# load_profile_json
# ---------------------------------------------------------------------------

class TestLoadProfileJson(unittest.TestCase):

    def test_valid(self):
        path = _write({"flags": ["-XX:+UseG1GC", "-Xss512k"]})
        result = load_profile_json(path)
        self.assertEqual(result["flags"], ["-XX:+UseG1GC", "-Xss512k"])

    def test_empty_flags_list(self):
        path = _write({"flags": []})
        result = load_profile_json(path)
        self.assertEqual(result["flags"], [])

    def test_unknown_field_warns(self):
        warn, msgs = _collecting_warn()
        path = _write({"flags": [], "unknown_key": 1})
        load_profile_json(path, warn=warn)
        self.assertEqual(len(msgs), 1)

    def test_flags_not_list_raises(self):
        path = _write({"flags": "-Xmx1g"})
        with self.assertRaises(ValueError):
            load_profile_json(path)

    def test_missing_flags_raises(self):
        path = _write({})
        with self.assertRaises(ValueError):
            load_profile_json(path)

    def test_non_string_flag_raises(self):
        path = _write({"flags": ["-Xmx1g", 42]})
        with self.assertRaises(ValueError):
            load_profile_json(path)


# ---------------------------------------------------------------------------
# load_bench_json
# ---------------------------------------------------------------------------

class TestLoadBenchJson(unittest.TestCase):

    def _dacapo_minimal(self):
        return {
            "benchmark": "dacapo",
            "sub_benchmark": "h2",
        }

    def test_valid_dacapo_minimal(self):
        path = _write(self._dacapo_minimal())
        result = load_bench_json(path)
        self.assertEqual(result["benchmark"], "dacapo")
        self.assertEqual(result["sub_benchmark"], "h2")
        self.assertEqual(result["datasize"], "default")
        self.assertEqual(result["iterations"], 10)
        self.assertEqual(result["jvmargs"], [])

    def test_valid_dacapo_full(self):
        data = {
            "benchmark": "dacapo",
            "sub_benchmark": "cassandra",
            "datasize": "large",
            "iterations": 5,
            "minheap": 2048,
            "max_java_version": 21,
            "jvmargs": ["-Djava.security.manager=allow"],
        }
        path = _write(data)
        result = load_bench_json(path)
        self.assertEqual(result["datasize"], "large")
        self.assertEqual(result["iterations"], 5)
        self.assertEqual(result["minheap"], 2048)
        self.assertEqual(result["max_java_version"], 21)
        self.assertNotIn("min_java_version", result)

    def test_valid_specjbb(self):
        data = {"benchmark": "specjbb", "minheap": 8192}
        path = _write(data)
        result = load_bench_json(path)
        self.assertEqual(result["benchmark"], "specjbb")
        self.assertEqual(result["minheap"], 8192)
        # dacapo-only fields must not appear
        self.assertNotIn("sub_benchmark", result)

    def test_unknown_field_warns(self):
        warn, msgs = _collecting_warn()
        data = dict(self._dacapo_minimal())
        data["mystery"] = "value"
        path = _write(data)
        load_bench_json(path, warn=warn)
        self.assertEqual(len(msgs), 1)
        self.assertIn("mystery", msgs[0])

    def test_missing_benchmark_raises(self):
        path = _write({"sub_benchmark": "h2"})
        with self.assertRaises(ValueError):
            load_bench_json(path)

    def test_invalid_benchmark_value_raises(self):
        path = _write({"benchmark": "jmh", "sub_benchmark": "x"})
        with self.assertRaises(ValueError):
            load_bench_json(path)

    def test_invalid_datasize_raises(self):
        path = _write({"benchmark": "dacapo", "sub_benchmark": "h2", "datasize": "XXL"})
        with self.assertRaises(ValueError):
            load_bench_json(path)

    def test_dacapo_missing_sub_benchmark_raises(self):
        path = _write({"benchmark": "dacapo"})
        with self.assertRaises(ValueError):
            load_bench_json(path)

    def test_invalid_iterations_raises(self):
        path = _write({"benchmark": "dacapo", "sub_benchmark": "h2", "iterations": 0})
        with self.assertRaises(ValueError):
            load_bench_json(path)

    def test_negative_minheap_raises(self):
        path = _write({"benchmark": "dacapo", "sub_benchmark": "h2", "minheap": -1})
        with self.assertRaises(ValueError):
            load_bench_json(path)

    def test_min_java_version_stored(self):
        data = dict(self._dacapo_minimal())
        data["min_java_version"] = 17
        path = _write(data)
        result = load_bench_json(path)
        self.assertEqual(result["min_java_version"], 17)

    def test_duplicate_jvmargs_raises(self):
        path = _write_raw("""
        benchmark = "dacapo"
        sub_benchmark = "h2"
        jvmargs = ["-XX:+FlagA"]
        jvmargs = ["-XX:+FlagB"]
        """)
        with self.assertRaises(ValueError):
            load_bench_json(path)

    def test_split_key_raises(self):
        # Split keys were dropped (PRD.md D-76); a leftover one must not be
        # silently ignored, or its flags would be missing from the run.
        path = _write({"benchmark": "dacapo", "sub_benchmark": "h2",
                       "jvmargs": ["-XX:+FlagA"], "jvmargs_b": ["-XX:+FlagB"]})
        with self.assertRaisesRegex(ValueError, "'jvmargs_b'.*merge its values into the 'jvmargs' array"):
            load_bench_json(path)


# ---------------------------------------------------------------------------
# load_benchgroup_json
# ---------------------------------------------------------------------------

class TestLoadBenchgroupJson(unittest.TestCase):

    def test_valid(self):
        path = _write({"members": ["bench-a", "bench-b"]})
        result = load_benchgroup_json(path)
        self.assertEqual(result["members"], ["bench-a", "bench-b"])

    def test_unknown_field_warns(self):
        warn, msgs = _collecting_warn()
        path = _write({"members": ["a"], "extra": 1})
        load_benchgroup_json(path, warn=warn)
        self.assertEqual(len(msgs), 1)

    def test_empty_members_raises(self):
        path = _write({"members": []})
        with self.assertRaises(ValueError):
            load_benchgroup_json(path)

    def test_missing_members_raises(self):
        path = _write({})
        with self.assertRaises(ValueError):
            load_benchgroup_json(path)

    def test_duplicate_member_raises(self):
        path = _write({"members": ["bench-a", "bench-b", "bench-a"]})
        with self.assertRaises(ValueError):
            load_benchgroup_json(path)

    def test_non_string_member_raises(self):
        path = _write({"members": ["bench-a", 42]})
        with self.assertRaises(ValueError):
            load_benchgroup_json(path)


# ---------------------------------------------------------------------------
# load_run_json
# ---------------------------------------------------------------------------

class TestLoadRunJson(unittest.TestCase):

    def _minimal_run(self):
        return {
            "bench": "dacapo-h2-large",
            "runs": [
                {"jdkname": "jdk-21"}
            ]
        }

    def test_valid_minimal(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["bench"], ["dacapo-h2-large"])
        self.assertNotIn("bench", result["runs"][0])
        self.assertEqual(result["runs"][0]["jdkname"], "jdk-21")
        self.assertEqual(result["runs"][0]["profiles"], [])
        self.assertEqual(result["runs"][0]["jvmargs"], [])
        self.assertEqual(result["runs"][0]["tags"], [])

    def test_valid_full(self):
        data = {
            "tags": ["gc-compare"],
            "heapsize_factors": [1.0, 2.0],
            "jvmargs": ["-XX:+UseCompactObjectHeaders"],
            "benchgroup": "dacapo-all-large",
            "runs": [
                {
                    "jdkname": "jdk-25",
                    "profiles": ["g1-baseline"],
                    "jvmargs": ["-Xss2m"],
                    "tags": ["group-run"],
                }
            ],
        }
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["heapsize_factors"], [1.0, 2.0])
        self.assertEqual(result["jvmargs"], ["-XX:+UseCompactObjectHeaders"])
        self.assertEqual(result["tags"], ["gc-compare"])
        self.assertEqual(result["benchgroup"], "dacapo-all-large")
        entry = result["runs"][0]
        self.assertNotIn("benchgroup", entry)
        self.assertEqual(entry["profiles"], ["g1-baseline"])

    def test_unknown_toplevel_field_warns(self):
        warn, msgs = _collecting_warn()
        data = dict(self._minimal_run())
        data["on_failure"] = "abort"
        path = _write(data)
        load_run_json(path, warn=warn)
        self.assertEqual(len(msgs), 1)
        self.assertIn("on_failure", msgs[0])

    def test_unknown_entry_field_warns(self):
        warn, msgs = _collecting_warn()
        data = {
            "bench": "b",
            "runs": [
                {"jdkname": "jdk-21", "tag": "oops"}
            ]
        }
        path = _write(data)
        load_run_json(path, warn=warn)
        self.assertEqual(len(msgs), 1)
        self.assertIn("tag", msgs[0])

    def test_missing_runs_raises(self):
        path = _write({})
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_empty_runs_raises(self):
        path = _write({"runs": []})
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_neither_bench_nor_benchgroup_raises(self):
        data = {"runs": [{"jdkname": "jdk-21"}]}
        path = _write(data)
        with self.assertRaisesRegex(ValueError, "Exactly one of 'bench' or 'benchgroup'"):
            load_run_json(path)

    def test_entry_only_bench_raises_with_hint(self):
        # An old-style config: bench only in the run entries.
        data = {"runs": [{"bench": "b", "jdkname": "jdk-21"}]}
        path = _write(data)
        with self.assertRaisesRegex(ValueError, "run entries may not set them"):
            load_run_json(path)

    def test_batch_global_bench_is_a_list(self):
        data = {
            "bench": "dacapo-h2-large",
            "runs": [{"jdkname": "jdk-21"}],
        }
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["bench"], ["dacapo-h2-large"])
        self.assertNotIn("benchgroup", result)
        self.assertNotIn("bench", result["runs"][0])

    def test_batch_global_benchgroup(self):
        data = {
            "benchgroup": "dacapo-all-large",
            "runs": [{"jdkname": "jdk-21"}],
        }
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["benchgroup"], "dacapo-all-large")
        self.assertNotIn("bench", result)
        self.assertNotIn("benchgroup", result["runs"][0])

    def test_batch_global_both_bench_and_benchgroup_raises(self):
        data = {
            "bench": "b",
            "benchgroup": "g",
            "runs": [{"jdkname": "jdk-21"}],
        }
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_bench_array_and_benchgroup_raises(self):
        data = {"bench": ["b1", "b2"], "benchgroup": "g", "runs": [{"jdkname": "jdk-21"}]}
        with self.assertRaisesRegex(ValueError, "At most one of 'bench' or 'benchgroup'"):
            load_run_json(_write(data))

    def test_entry_bench_raises(self):
        data = {
            "bench": "b",
            "runs": [{"bench": "c", "jdkname": "jdk-21"}],
        }
        path = _write(data)
        with self.assertRaisesRegex(ValueError, "'bench' is not allowed in a run entry"):
            load_run_json(path)

    def test_entry_benchgroup_raises(self):
        data = {"benchgroup": "g", "runs": [{"benchgroup": "g", "jdkname": "jdk-21"}]}
        path = _write(data)
        with self.assertRaisesRegex(ValueError, "'benchgroup' is not allowed in a run entry"):
            load_run_json(path)

    def test_entry_split_bench_raises(self):
        data = {"bench": "b", "runs": [{"bench_2": "c", "jdkname": "jdk-21"}]}
        with self.assertRaisesRegex(ValueError, "'bench_2'.*split keys are no longer supported"):
            load_run_json(_write(data))

    def test_bench_array_keeps_order(self):
        warn, messages = _collecting_warn()
        data = {"bench": ["b2", "b1", "b3"], "runs": [{"jdkname": "jdk-21"}]}
        result = load_run_json(_write(data), warn=warn)
        self.assertEqual(result["bench"], ["b2", "b1", "b3"])
        self.assertEqual(messages, [])

    def test_bench_string_becomes_one_element_list(self):
        result = load_run_json(_write({"bench": "b1", "runs": [{"jdkname": "jdk-21"}]}))
        self.assertEqual(result["bench"], ["b1"])

    def test_bench_empty_array_raises(self):
        with self.assertRaisesRegex(ValueError, "'bench' must be a non-empty string or a non-empty array"):
            load_run_json(_write({"bench": [], "runs": [{"jdkname": "jdk-21"}]}))

    def test_bench_array_non_string_raises(self):
        for bad in (["b1", 3], ["b1", ""]):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, r"'bench\[1\]' must be a non-empty string"):
                    load_run_json(_write({"bench": bad, "runs": [{"jdkname": "jdk-21"}]}))

    def test_bench_duplicate_name_raises(self):
        data = {"bench": ["b1", "b2", "b1"], "runs": [{"jdkname": "jdk-21"}]}
        with self.assertRaisesRegex(ValueError, "Duplicate bench 'b1'"):
            load_run_json(_write(data))

    def test_missing_jdkname_uses_default(self):
        data = {"bench": "b", "runs": [{}]}
        result = load_run_json(_write(data))
        self.assertEqual(result["runs"][0]["jdkname"], "default")

    def test_batch_global_jdkname_used_when_entry_omits_it(self):
        data = {
            "jdkname": "jdk-21",
            "bench": "b",
            "runs": [{}],
        }
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["runs"][0]["jdkname"], "jdk-21")

    def test_run_local_jdkname_overrides_batch_global(self):
        data = {
            "jdkname": "jdk-21",
            "bench": "b",
            "runs": [{"jdkname": "jdk-25"}],
        }
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["runs"][0]["jdkname"], "jdk-25")

    def test_batch_global_profiles_absent_defaults_empty(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertEqual(result["profiles"], [])
        self.assertEqual(result["runs"][0]["profiles"], [])

    def test_batch_global_profiles_used_when_entry_omits_it(self):
        data = dict(self._minimal_run())
        data["profiles"] = ["g1-baseline"]
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["runs"][0]["profiles"], ["g1-baseline"])

    def test_run_local_profiles_overrides_batch_global_wholesale(self):
        data = dict(self._minimal_run())
        data["profiles"] = ["g1-baseline"]
        data["runs"][0]["profiles"] = ["numa-pinned"]
        path = _write(data)
        result = load_run_json(path)
        # Wholesale override (same pattern as dacapo_args), not a merge.
        self.assertEqual(result["runs"][0]["profiles"], ["numa-pinned"])

    def test_run_local_profiles_explicit_empty_overrides_batch_global(self):
        data = dict(self._minimal_run())
        data["profiles"] = ["g1-baseline"]
        data["runs"][0]["profiles"] = []
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["runs"][0]["profiles"], [])

    def test_profiles_toplevel_not_list_raises(self):
        data = dict(self._minimal_run())
        data["profiles"] = "g1-baseline"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_profiles_entry_non_string_element_raises(self):
        data = dict(self._minimal_run())
        data["runs"][0]["profiles"] = [1]
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_invalid_heapsize_factor_raises(self):
        data = dict(self._minimal_run())
        data["heapsize_factors"] = [1.0, -0.5]
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_duplicate_toplevel_jvmargs_raises(self):
        path = _write_raw("""
        jvmargs = ["-XX:+FlagA"]
        jvmargs = ["-XX:+FlagB"]
        bench = "b"
        [[runs]]
        jdkname = "jdk-21"
        """)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_duplicate_run_entry_jvmargs_raises(self):
        path = _write_raw("""
        bench = "b"
        [[runs]]
        jdkname = "jdk-21"
        jvmargs = ["-XX:+FlagA"]
        jvmargs = ["-XX:+FlagB"]
        """)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_duplicate_toplevel_tags_raises(self):
        path = _write_raw("""
        tags = ["a"]
        tags = ["b"]
        bench = "b"
        [[runs]]
        jdkname = "jdk-21"
        """)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_split_keys_raise_at_any_level(self):
        cases = {
            "top-level jvmargs": ({"jvmargs_diag": ["-XX:+A"]}, {}),
            "top-level tags": ({"tags_2": ["t"]}, {}),
            "top-level bench": ({"bench_2": "b2"}, {}),
            "run-entry jvmargs": ({}, {"jvmargs_x": ["-XX:+A"]}),
            "run-entry tags": ({}, {"tags_2": ["t"]}),
            "nested table": ({"jvmenv": {"A": "1"}, "collectors": {"interval": 1, "compression": False,
                              "enabled": [], "tags_x": ["t"]}}, {}),
        }
        for label, (top, entry) in cases.items():
            with self.subTest(label):
                data = {"bench": "b", **top, "runs": [{"jdkname": "jdk-21", **entry}]}
                with self.assertRaisesRegex(ValueError, "split keys are no longer supported"):
                    load_run_json(_write(data))

    def test_keys_merely_starting_with_bench_are_not_split_keys(self):
        # `benchgroup` must not be mistaken for a `bench_<suffix>` key.
        result = load_run_json(_write({"benchgroup": "g", "runs": [{"jdkname": "jdk-21"}]}))
        self.assertEqual(result["benchgroup"], "g")

    def test_duplicate_scalar_field_raises(self):
        path = _write_raw("""
        bench = "b"
        [[runs]]
        jdkname = "jdk-21"
        jdkname = "jdk-25"
        """)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_empty_heapsize_factors_raises(self):
        data = dict(self._minimal_run())
        data["heapsize_factors"] = []
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dynamic_heap_stored(self):
        data = dict(self._minimal_run())
        data["heapsize_factors"] = [1.0]
        data["dynamic_heap"] = True
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["dynamic_heap"], True)

    def test_dynamic_heap_absent_omitted(self):
        data = dict(self._minimal_run())
        data["heapsize_factors"] = [1.0]
        path = _write(data)
        result = load_run_json(path)
        self.assertNotIn("dynamic_heap", result)

    def test_dynamic_heap_not_bool_raises(self):
        data = dict(self._minimal_run())
        data["heapsize_factors"] = [1.0]
        data["dynamic_heap"] = "true"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dynamic_heap_without_heapsize_factors_raises(self):
        data = dict(self._minimal_run())
        data["dynamic_heap"] = True
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dynamic_heap_false_without_heapsize_factors_ok(self):
        data = dict(self._minimal_run())
        data["dynamic_heap"] = False
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["dynamic_heap"], False)

    def test_dacapo_iterations_stored(self):
        data = dict(self._minimal_run())
        data["dacapo_iterations"] = 3
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["dacapo_iterations"], 3)

    def test_dacapo_iterations_absent_omitted(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertNotIn("dacapo_iterations", result)

    def test_dacapo_iterations_zero_raises(self):
        data = dict(self._minimal_run())
        data["dacapo_iterations"] = 0
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dacapo_iterations_negative_raises(self):
        data = dict(self._minimal_run())
        data["dacapo_iterations"] = -5
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dacapo_iterations_not_int_raises(self):
        data = dict(self._minimal_run())
        data["dacapo_iterations"] = "10"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_repeat_stored(self):
        data = dict(self._minimal_run())
        data["repeat"] = 3
        path = _write(data)
        self.assertEqual(load_run_json(path)["repeat"], 3)

    def test_repeat_absent_omitted(self):
        path = _write(self._minimal_run())
        self.assertNotIn("repeat", load_run_json(path))

    def test_repeat_invalid_raises(self):
        for bad in (0, -1, "2", True, 1.5):
            data = dict(self._minimal_run())
            data["repeat"] = bad
            path = _write(data)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                load_run_json(path)

    def test_repeat_in_run_entry_warns_unknown(self):
        # repeat is file-level only; in a run entry it is an unknown field
        data = dict(self._minimal_run())
        data["runs"] = [dict(data["runs"][0], repeat=2)]
        path = _write(data)
        warnings = []
        result = load_run_json(path, warn=warnings.append)
        self.assertTrue(any("'repeat'" in w for w in warnings))
        self.assertNotIn("repeat", result["runs"][0])

    def test_dacapo_iterations_entry_absent_is_none(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertIsNone(result["runs"][0]["dacapo_iterations"])

    def test_dacapo_iterations_entry_stored(self):
        data = dict(self._minimal_run())
        data["dacapo_iterations"] = 10
        data["runs"][0]["dacapo_iterations"] = 3
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["runs"][0]["dacapo_iterations"], 3)
        self.assertEqual(result["dacapo_iterations"], 10)

    def test_dacapo_iterations_entry_zero_raises(self):
        data = dict(self._minimal_run())
        data["runs"][0]["dacapo_iterations"] = 0
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dacapo_iterations_entry_not_int_raises(self):
        data = dict(self._minimal_run())
        data["runs"][0]["dacapo_iterations"] = "3"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_jvm_env_absent_defaults_empty(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertEqual(result["jvmenv"], {})
        self.assertEqual(result["runs"][0]["jvmenv"], {})

    def test_jvm_env_toplevel_stored_and_coerced_to_str(self):
        data = dict(self._minimal_run())
        data["jvmenv"] = {"MALLOC_ARENA_MAX": 4}
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["jvmenv"], {"MALLOC_ARENA_MAX": "4"})

    def test_jvm_env_entry_stored(self):
        data = {
            "bench": "b",
            "runs": [
                {"jdkname": "jdk-21", "jvmenv": {"FOO": "bar"}}
            ]
        }
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["runs"][0]["jvmenv"], {"FOO": "bar"})

    def test_jvm_env_toplevel_not_dict_raises(self):
        data = dict(self._minimal_run())
        data["jvmenv"] = ["MALLOC_ARENA_MAX=4"]
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_jvm_env_entry_not_dict_raises(self):
        data = {
            "bench": "b",
            "runs": [{"jdkname": "jdk-21", "jvmenv": "oops"}]
        }
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_jvm_env_non_string_key_raises(self):
        data = dict(self._minimal_run())
        data["jvmenv"] = {"": "4"}
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_jvm_env_invalid_value_type_raises(self):
        data = dict(self._minimal_run())
        data["jvmenv"] = {"MALLOC_ARENA_MAX": [4]}
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_jvm_env_bool_value_raises(self):
        data = dict(self._minimal_run())
        data["jvmenv"] = {"FOO": True}
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_do_cpu_pinning_absent_omitted(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertNotIn("do_cpu_pinning", result)

    def test_do_cpu_pinning_toplevel_stored(self):
        data = dict(self._minimal_run())
        data["do_cpu_pinning"] = True
        path = _write(data)
        result = load_run_json(path)
        self.assertTrue(result["do_cpu_pinning"])

    def test_do_cpu_pinning_toplevel_not_bool_raises(self):
        data = dict(self._minimal_run())
        data["do_cpu_pinning"] = "true"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_do_cpu_pinning_entry_absent_is_none(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertIsNone(result["runs"][0]["do_cpu_pinning"])

    def test_do_cpu_pinning_entry_explicit_false_stored(self):
        data = {
            "do_cpu_pinning": True,
            "bench": "dacapo-h2-large",
            "runs": [
                {"jdkname": "jdk-21", "do_cpu_pinning": False}
            ],
        }
        path = _write(data)
        result = load_run_json(path)
        # Explicit False must be distinguishable from "not specified" (None)
        # so a per-run override can turn batch-level pinning back off.
        self.assertIs(result["runs"][0]["do_cpu_pinning"], False)

    def test_do_cpu_pinning_entry_not_bool_raises(self):
        data = dict(self._minimal_run())
        data["runs"][0]["do_cpu_pinning"] = "yes"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    _FULL = {"interval": 5, "compression": True, "enabled": ["nmt", "cpu_temp"]}

    def test_collectors_absent_is_none(self):
        result = load_run_json(_write(self._minimal_run()))
        self.assertEqual(result["collectors"], {"collectors": None, "collectors_preset": None})
        self.assertNotIn("collectors", result["runs"][0])

    def test_collectors_object_stored_in_canonical_order(self):
        data = dict(self._minimal_run())
        data["collectors"] = dict(self._FULL)
        result = load_run_json(_write(data))
        self.assertEqual(result["collectors"]["collectors"],
                         {"interval": 5, "compression": True, "enabled": ["cpu_temp", "nmt"]})

    def test_collectors_preset_stored(self):
        data = dict(self._minimal_run())
        data["collectors_preset"] = "all"
        result = load_run_json(_write(data))
        self.assertEqual(result["collectors"]["collectors_preset"], "all")

    def test_collectors_and_preset_mutex(self):
        data = dict(self._minimal_run())
        data.update({"collectors": dict(self._FULL), "collectors_preset": "cheap"})
        with self.assertRaises(ValueError):
            load_run_json(_write(data))

    def test_collector_keys_in_run_entry_raise(self):
        # Batch level only (PRD.md D-80): a hard error, not an ignored unknown field.
        for key, value in (("collectors", dict(self._FULL)), ("collectors_preset", "cheap"),
                           ("perf_events_preset", "basic")):
            with self.subTest(key=key):
                data = dict(self._minimal_run())
                data["runs"] = [dict(data["runs"][0], **{key: value})]
                with self.assertRaisesRegex(ValueError, f"'{key}' is not allowed in a run entry"):
                    load_run_json(_write(data))

    def test_perf_events_preset_defaults(self):
        result = load_run_json(_write(self._minimal_run()))
        self.assertIsNone(result["perf_events_preset"])

    def test_perf_events_preset_stored(self):
        data = dict(self._minimal_run(), perf_events_preset="basic")
        result = load_run_json(_write(data))
        self.assertEqual(result["perf_events_preset"], "basic")

    def test_perf_events_preset_invalid_raises(self):
        for key, bad in (("perf_events_preset", ""), ("perf_events_preset", ["basic"])):
            with self.subTest(key=key, value=bad):
                data = dict(self._minimal_run(), **{key: bad})
                with self.assertRaises(ValueError):
                    load_run_json(_write(data))

    def test_collectors_invalid_raise(self):
        bad_cases = [
            ["nmt"],
            {"interval": 5, "compression": True},
            {"interval": 5, "enabled": []},
            {"compression": True, "enabled": []},
            dict(self._FULL, interval=0), dict(self._FULL, interval="10"), dict(self._FULL, interval=True),
            dict(self._FULL, compression="true"),
            dict(self._FULL, enabled="nmt"), dict(self._FULL, enabled=["pidsat"]),
            dict(self._FULL, enabled=["nmt", "nmt"]),
            dict(self._FULL, nmt=True), dict(self._FULL, preset="all"),
        ]
        for bad in bad_cases:
            with self.subTest(value=bad):
                data = dict(self._minimal_run())
                data["collectors"] = bad
                with self.assertRaises(ValueError):
                    load_run_json(_write(data))

    def test_collectors_preset_invalid_raises(self):
        for bad in ("", True, ["cheap"]):
            with self.subTest(value=bad):
                data = dict(self._minimal_run())
                data["collectors_preset"] = bad
                with self.assertRaises(ValueError):
                    load_run_json(_write(data))

    def test_collectors_comment_is_unknown_key(self):
        data = dict(self._minimal_run())
        data["collectors"] = dict(self._FULL, COMMENT="x")
        with self.assertRaisesRegex(ValueError, "unknown key 'COMMENT'"):
            load_run_json(_write(data))

    def test_old_collector_keys_are_unknown(self):
        warn, msgs = _collecting_warn()
        data = dict(self._minimal_run())
        data["nmt_tracking"] = True
        data["collector_interval"] = 5
        load_run_json(_write(data), warn=warn)
        self.assertEqual(len(msgs), 2)

    def test_use_ramdisk_absent_omitted(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertNotIn("use_ramdisk", result)

    def test_use_ramdisk_toplevel_stored(self):
        data = dict(self._minimal_run())
        data["use_ramdisk"] = True
        path = _write(data)
        result = load_run_json(path)
        self.assertTrue(result["use_ramdisk"])

    def test_use_ramdisk_toplevel_not_bool_raises(self):
        data = dict(self._minimal_run())
        data["use_ramdisk"] = "true"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_use_ramdisk_entry_absent_is_none(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertIsNone(result["runs"][0]["use_ramdisk"])

    def test_use_ramdisk_entry_explicit_false_stored(self):
        data = {
            "use_ramdisk": True,
            "bench": "dacapo-h2-large",
            "runs": [
                {"jdkname": "jdk-21", "use_ramdisk": False}
            ],
        }
        path = _write(data)
        result = load_run_json(path)
        # Explicit False must be distinguishable from "not specified" (None)
        # so a per-run override can turn batch-level ramdisk use back off.
        self.assertIs(result["runs"][0]["use_ramdisk"], False)

    def test_use_ramdisk_entry_not_bool_raises(self):
        data = dict(self._minimal_run())
        data["runs"][0]["use_ramdisk"] = "yes"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dacapo_args_absent_omitted(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertNotIn("dacapo_args", result)

    def test_dacapo_args_toplevel_stored(self):
        data = dict(self._minimal_run())
        data["dacapo_args"] = ["-t", "16"]
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["dacapo_args"], ["-t", "16"])

    def test_dacapo_args_toplevel_not_list_raises(self):
        data = dict(self._minimal_run())
        data["dacapo_args"] = "-t 16"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dacapo_args_toplevel_non_string_element_raises(self):
        data = dict(self._minimal_run())
        data["dacapo_args"] = ["-t", 16]
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dacapo_args_entry_absent_is_none(self):
        path = _write(self._minimal_run())
        result = load_run_json(path)
        self.assertIsNone(result["runs"][0]["dacapo_args"])

    def test_dacapo_args_entry_explicit_empty_stored(self):
        data = {
            "dacapo_args": ["-t", "16"],
            "bench": "dacapo-h2-large",
            "runs": [
                {"jdkname": "jdk-21", "dacapo_args": []}
            ],
        }
        path = _write(data)
        result = load_run_json(path)
        # Explicit [] must be distinguishable from "not specified" (None) so
        # a per-run override can turn off the batch-level args entirely.
        self.assertEqual(result["runs"][0]["dacapo_args"], [])

    def test_dacapo_args_entry_stored(self):
        data = dict(self._minimal_run())
        data["runs"][0]["dacapo_args"] = ["-t", "4"]
        path = _write(data)
        result = load_run_json(path)
        self.assertEqual(result["runs"][0]["dacapo_args"], ["-t", "4"])

    def test_dacapo_args_entry_not_list_raises(self):
        data = dict(self._minimal_run())
        data["runs"][0]["dacapo_args"] = "-t 4"
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)

    def test_dacapo_args_entry_non_string_element_raises(self):
        data = dict(self._minimal_run())
        data["runs"][0]["dacapo_args"] = [4]
        path = _write(data)
        with self.assertRaises(ValueError):
            load_run_json(path)


# ---------------------------------------------------------------------------
# load_host_json
# ---------------------------------------------------------------------------

_PRESETS = {
    "cheap-10s": {"interval": 10, "compression": False, "enabled": ["cpu_temp", "pidstat", "proc"]},
    "all-10s": {"interval": 10, "compression": True,
            "enabled": ["cpu_temp", "pidstat", "proc", "perf", "nmt", "system_map",
                        "thread_print"]},
}
_NONE = {"collectors": None, "collectors_preset": None}
_OWN = {"interval": 2, "compression": False, "enabled": ["nmt"]}


def _level(collectors=None, preset=None):
    return {"collectors": collectors, "collectors_preset": preset}


class TestResolveCollectors(unittest.TestCase):

    def test_default_is_cheap_preset(self):
        self.assertEqual(resolve_collectors(_NONE, _PRESETS), _PRESETS["cheap-10s"])
        self.assertEqual(collectors_preset_needed(_NONE), "cheap-10s")

    def test_file_settings_apply(self):
        self.assertEqual(resolve_collectors(_level(preset="all-10s"), _PRESETS),
                         _PRESETS["all-10s"])
        self.assertEqual(resolve_collectors(_level(_OWN), _PRESETS), _OWN)

    def test_preset_needed(self):
        self.assertIsNone(collectors_preset_needed(_level(_OWN)))
        self.assertEqual(collectors_preset_needed(_level(preset="all-10s")), "all-10s")

    def test_result_is_a_copy(self):
        r = resolve_collectors(_NONE, _PRESETS)
        r["interval"] = 99
        self.assertEqual(_PRESETS["cheap-10s"]["interval"], 10)

class TestLoadHostJson(unittest.TestCase):

    def test_valid_full(self):
        data = {
            "maxheap": 65536,
        }
        path = _write(data)
        result = load_host_json(path)
        self.assertEqual(result["maxheap"], 65536)

    def test_valid_empty(self):
        path = _write({})
        result = load_host_json(path)
        self.assertNotIn("maxheap", result)
        self.assertNotIn("perf_events", result)

    def test_comment_key_is_an_unknown_field(self):
        # No COMMENT convention any more (PRD.md D-76): use # comments.
        warn, msgs = _collecting_warn()
        load_host_json(_write({"COMMENT": "doc text", "maxheap": 1024}), warn=warn)
        self.assertEqual(len(msgs), 1)
        self.assertIn("COMMENT", msgs[0])

    def test_unknown_field_warns(self):
        warn, msgs = _collecting_warn()
        path = _write({"maxheap": 1024, "numa_nodes": 4})
        load_host_json(path, warn=warn)
        self.assertEqual(len(msgs), 1)
        self.assertIn("numa_nodes", msgs[0])

    def test_invalid_maxheap_raises(self):
        path = _write({"maxheap": 0})
        with self.assertRaises(ValueError):
            load_host_json(path)

    def test_perf_events_is_an_error(self):
        # Moved to .run.toml perf_events_preset (PRD.md D-81).
        for value in (["cycles"], [], "cycles"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "perf_events_preset"):
                    load_host_json(_write({"perf_events": value}))

    def test_paths_valid(self):
        # Benchmark and test JDK paths live in the host config (PRD.md D-83).
        warn, msgs = _collecting_warn()
        data = {"dacapo_dir": "/bench/dacapo", "specjbb_dir": "/bench/specjbb",
                "test_jdk": "/jdks/test"}
        result = load_host_json(_write(data), warn=warn)
        for field, value in data.items():
            self.assertEqual(result[field], value)
        self.assertEqual(msgs, [])

    def test_paths_absent_omitted(self):
        result = load_host_json(_write({}))
        for field in ("dacapo_dir", "specjbb_dir", "test_jdk"):
            self.assertNotIn(field, result)

    def test_invalid_paths_raise(self):
        for field in ("dacapo_dir", "specjbb_dir", "test_jdk"):
            for value in ("", 123):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        load_host_json(_write({field: value}))

    def test_has_flags_are_unknown_fields(self):
        # Host-level collector opt-outs were removed (PRD.md D-82).
        warn, msgs = _collecting_warn()
        result = load_host_json(_write({"has_perf": False}), warn=warn)
        self.assertNotIn("has_perf", result)
        self.assertEqual(len(msgs), 1)
        self.assertIn("has_perf", msgs[0])

    def test_maxheap_bool_raises(self):
        # bool is a subclass of int in Python — must be rejected explicitly.
        path = _write({"maxheap": True})
        with self.assertRaises(ValueError):
            load_host_json(path)

    def test_valid_ramdisk_path(self):
        path = _write({"ramdisk_path": "/dev/shm/jbenchrunner"})
        result = load_host_json(path)
        self.assertEqual(result["ramdisk_path"], "/dev/shm/jbenchrunner")

    def test_ramdisk_path_absent_by_default(self):
        path = _write({})
        result = load_host_json(path)
        self.assertNotIn("ramdisk_path", result)

    def test_ramdisk_path_empty_string_raises(self):
        path = _write({"ramdisk_path": ""})
        with self.assertRaises(ValueError):
            load_host_json(path)

    def test_valid_cpu_list_range(self):
        path = _write({"cpu_list": "0-3"})
        result = load_host_json(path)
        self.assertEqual(result["cpu_list"], "0-3")

    def test_valid_cpu_list_comma_separated(self):
        path = _write({"cpu_list": "1,3,5,7,9"})
        result = load_host_json(path)
        self.assertEqual(result["cpu_list"], "1,3,5,7,9")

    def test_valid_cpu_list_mixed(self):
        path = _write({"cpu_list": "0-3,8,10-11"})
        result = load_host_json(path)
        self.assertEqual(result["cpu_list"], "0-3,8,10-11")

    def test_valid_cpu_list_harness(self):
        path = _write({"cpu_list_harness": "0-1"})
        result = load_host_json(path)
        self.assertEqual(result["cpu_list_harness"], "0-1")

    def test_cpu_list_harness_absent_by_default(self):
        path = _write({})
        self.assertNotIn("cpu_list_harness", load_host_json(path))

    def test_cpu_list_harness_invalid_syntax_raises(self):
        path = _write({"cpu_list_harness": "0-"})
        with self.assertRaises(ValueError):
            load_host_json(path)

    def test_cpu_list_absent_by_default(self):
        path = _write({})
        result = load_host_json(path)
        self.assertNotIn("cpu_list", result)

    def test_cpu_list_invalid_syntax_raises(self):
        path = _write({"cpu_list": "abc"})
        with self.assertRaises(ValueError):
            load_host_json(path)

    def test_cpu_list_empty_string_raises(self):
        path = _write({"cpu_list": ""})
        with self.assertRaises(ValueError):
            load_host_json(path)

    def test_cpu_list_trailing_comma_raises(self):
        path = _write({"cpu_list": "0-3,"})
        with self.assertRaises(ValueError):
            load_host_json(path)

    def test_reset_machine_between_runs_is_unknown_key(self):
        """Replaced by stabilize_machine (PRD.md D-78), itself gone since D-88:
        only warned about."""
        warnings = []
        path = _write({"reset_machine_between_runs": True})
        result = load_host_json(path, warn=warnings.append)
        self.assertNotIn("reset_machine_between_runs", result)
        self.assertTrue(any("reset_machine_between_runs" in w for w in warnings))

    def test_stabilize_machine_is_unknown_key(self):
        """An explicit jbr-prepare opt-in since PRD.md D-88: only warned about."""
        warnings = []
        path = _write({"stabilize_machine": True})
        result = load_host_json(path, warn=warnings.append)
        self.assertNotIn("stabilize_machine", result)
        self.assertTrue(any("stabilize_machine" in w for w in warnings))

    def test_collector_compression_no_longer_a_host_field(self):
        # It is a .run.toml parameter now; a host file still carrying it gets the
        # ordinary unknown-field warning.
        warn, msgs = _collecting_warn()
        path = _write({"collector_compression": True})
        result = load_host_json(path, warn=warn)
        self.assertNotIn("collector_compression", result)
        self.assertEqual(len(msgs), 1)
        self.assertIn("collector_compression", msgs[0])


# ---------------------------------------------------------------------------
# load_jdk_json
# ---------------------------------------------------------------------------

class TestLoadJdkJson(unittest.TestCase):

    def test_valid(self):
        path = _write({"java_home": "/opt/jdk-21"})
        result = load_jdk_json(path)
        self.assertEqual(result["java_home"], "/opt/jdk-21")

    def test_unknown_field_warns(self):
        warn, msgs = _collecting_warn()
        path = _write({"java_home": "/opt/jdk-21", "vendor": "sap"})
        load_jdk_json(path, warn=warn)
        self.assertEqual(len(msgs), 1)

    def test_missing_java_home_raises(self):
        path = _write({})
        with self.assertRaises(ValueError):
            load_jdk_json(path)

    def test_empty_java_home_raises(self):
        path = _write({"java_home": ""})
        with self.assertRaises(ValueError):
            load_jdk_json(path)


# ---------------------------------------------------------------------------
# load_jdktype_json
# ---------------------------------------------------------------------------

class TestLoadJdktypeJson(unittest.TestCase):

    def test_valid_with_extra_flags(self):
        data = {
            "gc_log_option": "-Xlog:gc*:file=gc.log::filecount=0",
            "extra_jvmargs": ["-XX:+PrintFlagsFinal"],
        }
        path = _write(data)
        result = load_jdktype_json(path)
        self.assertEqual(result["gc_log_option"], "-Xlog:gc*:file=gc.log::filecount=0")
        self.assertEqual(result["extra_jvmargs"], ["-XX:+PrintFlagsFinal"])

    def test_valid_null_extra_flags(self):
        data = {"gc_log_option": "-Xverbosegclog:gc.log", "extra_jvmargs": None}
        path = _write(data)
        result = load_jdktype_json(path)
        self.assertEqual(result["extra_jvmargs"], [])

    def test_valid_omitted_extra_flags(self):
        data = {"gc_log_option": "-Xlog:gc*"}
        path = _write(data)
        result = load_jdktype_json(path)
        self.assertEqual(result["extra_jvmargs"], [])

    def test_unknown_field_warns(self):
        warn, msgs = _collecting_warn()
        path = _write({"gc_log_option": "-Xlog:gc*", "debug": True})
        load_jdktype_json(path, warn=warn)
        self.assertEqual(len(msgs), 1)
        self.assertIn("debug", msgs[0])

    def test_missing_gc_log_option_raises(self):
        path = _write({"extra_jvmargs": []})
        with self.assertRaises(ValueError):
            load_jdktype_json(path)

    def test_extra_flags_not_list_raises(self):
        path = _write({"gc_log_option": "-Xlog:gc*", "extra_jvmargs": "-XX:+PrintFlagsFinal"})
        with self.assertRaises(ValueError):
            load_jdktype_json(path)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# load_collector_json / collector_applies
# ---------------------------------------------------------------------------

class TestLoadCollectorJson(unittest.TestCase):

    def test_valid(self):
        path = _write({"restrict_to": [{"jdk_type": "openjdk", "min": 11},
                                      {"jdk_type": "openj9", "max": 17}]})
        result = load_collector_json(path)
        self.assertEqual(result["restrict_to"], [{"jdk_type": "openjdk", "min": 11},
                                                {"jdk_type": "openj9", "max": 17}])

    def test_empty_list_ok(self):
        self.assertEqual(load_collector_json(_write({"restrict_to": []}))["restrict_to"], [])

    def test_missing_restrict_to_means_unrestricted(self):
        cfg = load_collector_json(_write({}))
        self.assertIsNone(cfg["restrict_to"])
        self.assertTrue(collector_applies(cfg, "openj9", 8))
        self.assertTrue(collector_applies(cfg, "openjdk", 99))

    def test_restrict_to_not_list_raises(self):
        with self.assertRaises(ValueError):
            load_collector_json(_write({"restrict_to": {"jdk_type": "openjdk"}}))

    def test_unknown_jdk_type_raises(self):
        with self.assertRaises(ValueError):
            load_collector_json(_write({"restrict_to": [{"jdk_type": "hotspot"}]}))

    def test_non_int_bound_raises(self):
        with self.assertRaises(ValueError):
            load_collector_json(_write({"restrict_to": [{"jdk_type": "openjdk", "min": "11"}]}))

    def test_min_greater_than_max_raises(self):
        with self.assertRaises(ValueError):
            load_collector_json(_write({"restrict_to": [{"jdk_type": "openjdk", "min": 17, "max": 11}]}))

    def test_unknown_field_warns(self):
        warn, msgs = _collecting_warn()
        load_collector_json(_write({"restrict_to": [], "x": 1}), warn=warn)
        self.assertEqual(len(msgs), 1)

    def test_missing_config_file_raises(self):
        with self.assertRaises(RuntimeError):
            load_collector_config("/nonexistent-root", "nmt")


class TestCollectorApplies(unittest.TestCase):

    def test_matching(self):
        cfg = {"restrict_to": [{"jdk_type": "openjdk", "min": 11, "max": 21}]}
        self.assertTrue(collector_applies(cfg, "openjdk", 11))
        self.assertTrue(collector_applies(cfg, "openjdk", 21))
        self.assertFalse(collector_applies(cfg, "openjdk", 8))
        self.assertFalse(collector_applies(cfg, "openjdk", 22))
        self.assertFalse(collector_applies(cfg, "openj9", 17))

    def test_open_ended_and_empty(self):
        self.assertTrue(collector_applies({"restrict_to": [{"jdk_type": "openj9"}]}, "openj9", 99))
        self.assertFalse(collector_applies({"restrict_to": []}, "openjdk", 21))


class TestLoadPerfEventsPreset(unittest.TestCase):

    def test_valid(self):
        path = _write({"events": ["cycles", "cpu/event=0x3c,umask=0/"]})
        self.assertEqual(load_perf_events_preset_json(path),
                         ["cycles", "cpu/event=0x3c,umask=0/"])

    def test_invalid_raises(self):
        for bad in ({}, {"events": []}, {"events": "cycles"}, {"events": ["cycles", ""]},
                    {"events": ["cycles", 1]}, {"events": ["cycles", "cycles"]},
                    {"events": ["cycles"], "interval": 1}):
            with self.subTest(data=bad):
                with self.assertRaises(ValueError):
                    load_perf_events_preset_json(_write(bad))

    def test_missing_file_is_hard_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                load_perf_events_preset(tmp, "basic")

    def test_shipped_presets_valid(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        self.assertIn("cycles", load_perf_events_preset(root, "basic"))
        self.assertIn("dTLB-load-misses", load_perf_events_preset(root, "l1-tlb"))


class TestLoadCollectorPreset(unittest.TestCase):

    def test_valid(self):
        path = _write({"interval": 3, "compression": False,
                "enabled": ["nmt", "cpu_temp"]})
        self.assertEqual(load_collector_preset_json(path),
                         {"interval": 3, "compression": False, "enabled": ["cpu_temp", "nmt"]})

    def test_invalid_raises(self):
        full = {"interval": 3, "compression": False, "enabled": []}
        for bad in ({}, dict(full, enabled="nmt"), dict(full, enabled=["pidsat"]),
                    dict(full, enabled=["nmt", "nmt"]), {"interval": 3, "enabled": []},
                    dict(full, collectors_preset="cheap")):
            with self.subTest(data=bad):
                with self.assertRaises(ValueError):
                    load_collector_preset_json(_write(bad))

    def test_missing_file_is_hard_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                load_collector_preset(tmp, "cheap")

    def test_shipped_presets_valid(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        self.assertEqual(load_collector_preset(root, "none"),
                         {"interval": 10, "compression": False, "enabled": []})
        self.assertEqual(load_collector_preset(root, "cheap-10s"),
                         {"interval": 10, "compression": True,
                          "enabled": ["cpu_temp", "pidstat", "proc"]})
        self.assertEqual(load_collector_preset(root, "all-10s")["enabled"],
                         ["cpu_temp", "pidstat", "proc", "perf", "nmt", "system_map",
                          "thread_print"])
