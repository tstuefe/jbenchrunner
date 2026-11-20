# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Minimal TOML writer for jbenchrunner config files (stdlib has only a reader,
tomllib).

Usage:
    from toml_writer import dumps
    text = dumps({"flags": ["-XX:+UseZGC"]})

Covers exactly what the config files use: strings, booleans, integers,
floats, lists, and nested dicts. Output is meant to stay hand-editable:

  - plain keys first, in their original order (TOML requires them before
    any table header),
  - a list of dicts becomes an array of tables (`[[runs]]`), a dict that
    does not fit on one line becomes a sub-table (`[runs.collectors]`),
    everything else is written inline,
  - long lists are written one item per line with a trailing comma,
  - `None` values are dropped (TOML has no null; every config loader treats
    a missing key like null).

Used by scripts/helpers/convert-config-to-toml.py and by the tests to write
config fixtures.
"""

import math
import re

_MAX_LINE = 100
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE_KEY_RE.match(k) else _string(k)


def _string(s: str) -> str:
    out = []
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return _string(v)
    raise TypeError(f"cannot write {type(v).__name__} value to TOML: {v!r}")


def _inline(v) -> str:
    """Render any value on a single line."""
    if isinstance(v, dict):
        items = [f"{_key(k)} = {_inline(x)}" for k, x in v.items() if x is not None]
        return "{ " + ", ".join(items) + " }" if items else "{}"
    if isinstance(v, list):
        return "[" + ", ".join(_inline(x) for x in v) + "]"
    return _scalar(v)


def _value(key: str, v) -> str:
    """Render `key = value`; long lists are split one item per line."""
    line = f"{_key(key)} = {_inline(v)}"
    if len(line) <= _MAX_LINE or not isinstance(v, list) or not v:
        return line
    body = "".join(f"    {_inline(x)},\n" for x in v)
    return f"{_key(key)} = [\n{body}]"


def _is_table_array(v) -> bool:
    return isinstance(v, list) and len(v) > 0 and all(isinstance(x, dict) for x in v)


def _emit_table(lines: list, path: list, d: dict) -> None:
    plain, tables, arrays = [], [], []
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            line = f"{_key(k)} = {_inline(v)}"
            if len(line) <= _MAX_LINE:
                plain.append(line)
            else:
                tables.append((k, v))
        elif _is_table_array(v):
            arrays.append((k, v))
        else:
            plain.append(_value(k, v))
    lines.extend(plain)
    for k, v in tables:
        sub = path + [k]
        lines.append("")
        lines.append("[" + ".".join(_key(p) for p in sub) + "]")
        _emit_table(lines, sub, v)
    for k, v in arrays:
        sub = path + [k]
        for elem in v:
            lines.append("")
            lines.append("[[" + ".".join(_key(p) for p in sub) + "]]")
            _emit_table(lines, sub, elem)


def dumps(data: dict) -> str:
    """Return `data` as TOML text (ending in a newline)."""
    if not isinstance(data, dict):
        raise TypeError("TOML document must be a dict")
    lines = []
    _emit_table(lines, [], data)
    return "\n".join(lines) + "\n"
