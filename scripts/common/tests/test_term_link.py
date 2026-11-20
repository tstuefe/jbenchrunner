# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# test_term_link.py — unit tests for scripts/common/term_link.py
#
# Run with:
#   python3 -m unittest discover scripts

import io
import os
import sys
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMMON = os.path.dirname(_HERE)
if _COMMON not in sys.path:
    sys.path.insert(0, _COMMON)

from term_link import terminal_link  # noqa: E402


class _FakeStream(io.StringIO):
    def __init__(self, is_a_tty):
        super().__init__()
        self._is_a_tty = is_a_tty

    def isatty(self):
        return self._is_a_tty


class TestTerminalLink(unittest.TestCase):

    def test_plain_text_when_stream_not_a_tty(self):
        stream = _FakeStream(False)
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=False):
            result = terminal_link("/results/abc", "abc", stream=stream)
        self.assertEqual(result, "abc")

    def test_plain_text_when_terminal_unsupported(self):
        stream = _FakeStream(True)
        with patch.dict(os.environ, {}, clear=True):
            result = terminal_link("/results/abc", "abc", stream=stream)
        self.assertEqual(result, "abc")

    def test_plain_text_when_term_is_dumb(self):
        stream = _FakeStream(True)
        with patch.dict(os.environ, {"VTE_VERSION": "1", "TERM": "dumb"}, clear=False):
            result = terminal_link("/results/abc", "abc", stream=stream)
        self.assertEqual(result, "abc")

    def test_link_emitted_for_vte(self):
        stream = _FakeStream(True)
        with patch.dict(os.environ, {"VTE_VERSION": "6003"}, clear=False):
            os.environ.pop("TERM", None)
            result = terminal_link("/results/abc", "abc", stream=stream)
        self.assertEqual(result, "\x1b]8;;file:///results/abc\x1b\\abc\x1b]8;;\x1b\\")

    def test_link_emitted_for_iterm(self):
        stream = _FakeStream(True)
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=False):
            os.environ.pop("VTE_VERSION", None)
            os.environ.pop("TERM", None)
            result = terminal_link("/results/abc", "abc", stream=stream)
        self.assertEqual(result, "\x1b]8;;file:///results/abc\x1b\\abc\x1b]8;;\x1b\\")

    def _link(self, target, text):
        stream = _FakeStream(True)
        with patch.dict(os.environ, {"VTE_VERSION": "6003"}, clear=False):
            os.environ.pop("TERM", None)
            return terminal_link(target, text, stream=stream)

    def test_control_chars_cannot_break_out_of_the_sequence(self):
        # A name with ESC / BEL would otherwise end the OSC 8 sequence early
        # and inject further escape codes.
        evil = "abc\x1b]0;pwned\x07\x1b[2J\x9b"
        result = self._link("/results/" + evil, evil)
        inner = result[len("\x1b]8;;"):-len("\x1b]8;;\x1b\\")]
        target, text = inner.split("\x1b\\", 1)
        self.assertEqual(target, "file:///results/abc%1B%5D0%3Bpwned%07%1B%5B2J%C2%9B")
        self.assertEqual(text, "abc?]0;pwned??[2J?")
        self.assertEqual(len(text), len(evil))
        self.assertEqual(result.count("\x1b"), 4)  # only the link's own sequences

    def test_target_is_url_encoded(self):
        result = self._link("/my results/a#b", "a#b")
        self.assertIn("file:///my%20results/a%23b\x1b\\a#b", result)

    def test_plain_text_unchanged_when_not_a_tty(self):
        # Piped output is data (RUNIDs for the next command): left as is.
        stream = _FakeStream(False)
        with patch.dict(os.environ, {"VTE_VERSION": "6003"}, clear=False):
            self.assertEqual(terminal_link("/r/a\x1bb", "a\x1bb", stream=stream), "a\x1bb")

    def test_defaults_to_stdout_when_stream_omitted(self):
        with patch.object(sys.stdout, "isatty", return_value=False):
            result = terminal_link("/results/abc", "abc")
        self.assertEqual(result, "abc")


if __name__ == "__main__":
    unittest.main()
