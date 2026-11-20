# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Unit tests for scripts/common/toml_writer.py: everything written must read
back with tomllib to the same data (minus None values), and the layout must
stay hand-editable.
"""

import tomllib
import unittest

from scripts.common.toml_writer import dumps


class TestTomlWriter(unittest.TestCase):

    def _roundtrip(self, data, expected=None):
        text = dumps(data)
        self.assertEqual(tomllib.loads(text), data if expected is None else expected, text)
        return text

    def test_scalars(self):
        self._roundtrip({"s": "x", "i": 3, "f": 1.5, "whole": 2.0, "t": True, "fa": False})

    def test_float_stays_float(self):
        result = tomllib.loads(dumps({"heapsize_factors": [1.0, 2.0]}))
        self.assertIsInstance(result["heapsize_factors"][0], float)

    def test_string_escapes(self):
        self._roundtrip({"s": 'a "quoted" \\ back\nnew\ttab \x01 \x7f ü'})

    def test_quoted_keys(self):
        self._roundtrip({"a.b": 1, "with space": "x", "plain_key-1": 2})

    def test_none_dropped(self):
        self._roundtrip({"a": None, "b": {"c": None, "d": 1}}, {"b": {"d": 1}})

    def test_short_list_inline(self):
        text = self._roundtrip({"flags": ["-XX:+UseZGC"]})
        self.assertEqual(text, 'flags = ["-XX:+UseZGC"]\n')

    def test_long_list_one_item_per_line(self):
        members = [f"dacapo-bench-number-{i}-default" for i in range(10)]
        text = self._roundtrip({"members": members})
        self.assertIn('    "dacapo-bench-number-0-default",\n', text)

    def test_array_of_tables_after_plain_keys(self):
        data = {"runs": [{"jdkname": "a"}, {}], "bench": "b"}
        text = self._roundtrip(data)
        self.assertLess(text.index("bench ="), text.index("[[runs]]"))
        self.assertEqual(text.count("[[runs]]"), 2)

    def test_nested_tables(self):
        self._roundtrip({
            "jvmenv": {"MALLOC_ARENA_MAX": "4"},
            "runs": [
                {"jdkname": "a", "collectors": {"interval": 2, "enabled": ["x", "y"]}},
                {"collectors": {"enabled": [f"long-collector-name-{i}" for i in range(8)]}},
            ],
        })

    def test_empty_values(self):
        self._roundtrip({"l": [], "d": {}, "runs": [{}]})

    def test_rejects_non_dict(self):
        with self.assertRaises(TypeError):
            dumps(["a"])


if __name__ == "__main__":
    unittest.main()
