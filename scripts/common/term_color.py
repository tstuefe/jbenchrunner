# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# term_color.py — shared terminal color (ANSI SGR) helper.
#
# Used by jbr-ls to color --compare percentage deltas when --color is given
# and the terminal is known to support ANSI color codes: green/red for
# good/bad changes, orange/blue for "hotter"/"cooler" (value-neutral) ones.

import os
import sys

_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_BLUE = "\x1b[34m"
_YELLOW = "\x1b[33m"
_ORANGE_256 = "\x1b[38;5;215m"
_RESET = "\x1b[0m"


def supports_color(stream=None) -> bool:
    """Return True when *stream* (defaults to sys.stdout) is safe to write
    ANSI color codes to.

    Mirrors the capability checks in term_link.terminal_link: color is
    suppressed for piped/redirected output (so `jbr-ls --color > file` or
    `jbr-ls --color | less` stays free of escape-code bytes), when NO_COLOR
    is set (see https://no-color.org), and when TERM is "dumb".
    """
    if stream is None:
        stream = sys.stdout
    if not stream.isatty():
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return True


def supports_256_colors() -> bool:
    """Return True when the terminal advertises 256-color (or truecolor)
    support via TERM (e.g. "xterm-256color") or COLORTERM ("truecolor"/
    "24bit")."""
    if "256color" in os.environ.get("TERM", ""):
        return True
    return os.environ.get("COLORTERM", "") in ("truecolor", "24bit")


def colorize(text: str, tone: str, stream=None) -> str:
    """Wrap *text* in the ANSI color for *tone*, or return it unchanged when
    color is not enabled/supported (see ``supports_color``).

    *tone* is one of "good" (green), "bad" (red), "hot" (orange; yellow on
    terminals without 256-color support, see ``supports_256_colors``) or
    "cold" (blue).
    """
    if not supports_color(stream):
        return text
    if tone == "hot":
        color = _ORANGE_256 if supports_256_colors() else _YELLOW
    else:
        color = {"good": _GREEN, "bad": _RED, "cold": _BLUE}[tone]
    return f"{color}{text}{_RESET}"
