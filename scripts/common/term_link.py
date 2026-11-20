# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# term_link.py — shared terminal hyperlink (OSC 8) helper.
#
# Used by jbr-status, jbr-ls, and jbr-filt to render RUNIDs (and other
# paths) as clickable terminal hyperlinks when the terminal is known to
# support OSC 8.

import os
import re
import sys
from urllib.parse import quote

# C0 controls, DEL and C1 controls: any of them inside the link (ESC, BEL,
# the 8-bit ST/OSC) could end the escape sequence early and inject others.
_CONTROL_CHARS = re.compile("[\x00-\x1f\x7f-\x9f]")


def terminal_link(target: str, text: str, stream=None) -> str:
    """Return an OSC 8 terminal hyperlink to file://<target> wrapping *text*,
    or *text* unchanged when hyperlinks are not safe/supported.

    Hyperlinks are only emitted when *stream* (defaults to sys.stdout) is
    connected to an interactive terminal -- this keeps piped or redirected
    output (e.g. `jbr-filt | jbr-ls`, or `jbr-ls > file`) free of escape-code
    bytes even when the invoking shell itself is running inside a terminal
    that would otherwise support OSC 8 -- and when that terminal is known to
    support OSC 8 (VTE-based terminals like GNOME Terminal/Tilix, and
    iTerm2), and TERM is not "dumb".

    Names come from the file system (e.g. a run directory), so they may hold
    control characters that would break out of the escape sequence: the
    target is percent-encoded as a URL path, and each control character in
    *text* is shown as '?' (one for one, so column widths stay right).
    Plain output (no hyperlink) is returned unchanged: it is data, e.g.
    RUNIDs piped into the next command.
    """
    if stream is None:
        stream = sys.stdout
    if not stream.isatty():
        return text
    supports_osc8 = os.environ.get("VTE_VERSION") or os.environ.get("TERM_PROGRAM") == "iTerm.app"
    if supports_osc8 and os.environ.get("TERM", "") != "dumb":
        return "\x1b]8;;file://{target}\x1b\\{text}\x1b]8;;\x1b\\".format(
            target=quote(target, safe="/"),
            text=_CONTROL_CHARS.sub("?", text),
        )
    return text
