# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# filter.py — core logic for jbr-filt.
#
# Provides:
#   parse_jdk_major(val)         — parse a --jdkmajor argument like ">=21" or "21"
#   parse_repeat_no(val)         — parse a --repeat_no argument like ">=1" or "0"
#   parse_duration(val)          — parse a --duration argument like ">=120" or "120"
#   parse_startup_ms(val)        — parse a --startup_ms argument like "<2000" or "1500"
#   build_filter(parsed_args)    — compose all active filters from argparse Namespace
#   run_jq_filter(expr, merged)  — evaluate a raw jq boolean expression on a merged dict
#
# Usage:
#   from filter import build_filter
#   matches = build_filter(args)
#   for merged in run_dicts:
#       if matches(merged):
#           print(run_id)
#
# Every convenience flag also accepts the reserved value "null" (case-
# insensitive) to mean "this field is missing or JSON null" instead of the
# flag's normal comparison, e.g. --gc null matches runs with no heap.gc.

import datetime
import fnmatch
import json
import operator
import re
import shutil
import subprocess

# ---------------------------------------------------------------------------
# Supported operators for --jdkmajor
# ---------------------------------------------------------------------------
_OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    ">":  operator.gt,
    "<":  operator.lt,
}

# Ordered longest-first so that ">=" is tried before ">" etc.
_OP_TOKENS = [">=", "<=", "==", ">", "<"]

# Reserved sentinel value accepted by every convenience flag, meaning "this
# field is missing or JSON null" instead of the flag's normal comparison.
_NULL_SENTINEL = "null"


def _is_null_sentinel(val: str) -> bool:
    """Return True if *val* is the reserved 'null' sentinel (case-insensitive)."""
    return val.strip().lower() == _NULL_SENTINEL


def _parse_int_cmp(val: str, flag: str) -> tuple:
    """Parse an integer comparison argument for *flag* such as ``>=21``,
    ``<=11``, ``>17``, ``<25``, ``==21``, or bare ``21`` (defaults to ``==``).

    Returns
    -------
    (op_str, n) : tuple[str, int]
        The operator string (e.g. ``">="`` ) and the integer threshold.

    Raises
    ------
    ValueError
        If the string does not conform to the expected format.
    """
    val = val.strip()
    for token in _OP_TOKENS:
        if val.startswith(token):
            rest = val[len(token):]
            if not rest:
                raise ValueError(
                    f"{flag} '{val}': missing integer after operator '{token}'"
                )
            if not rest.isdigit():
                raise ValueError(
                    f"{flag} '{val}': '{rest}' is not a valid integer"
                )
            return (token, int(rest))

    # Bare integer — default to ==
    if val.isdigit():
        return ("==", int(val))

    raise ValueError(
        f"{flag} '{val}': unrecognised format; expected [op]N where op is "
        "one of >, <, >=, <=, == and N is a non-negative integer"
    )


def parse_jdk_major(val: str) -> tuple:
    """Parse a --jdkmajor argument such as ``>=21`` or bare ``21``; see
    _parse_int_cmp."""
    return _parse_int_cmp(val, "--jdkmajor")


def parse_repeat_no(val: str) -> tuple:
    """Parse a --repeat_no argument such as ``>=1`` or bare ``0``; see
    _parse_int_cmp."""
    return _parse_int_cmp(val, "--repeat_no")


def parse_heapszf(val: str) -> tuple:
    """Parse a --heapszf argument such as ``>=2``, ``<=1.5``, ``==4``,
    or bare ``2`` (defaults to ``==``).

    Returns
    -------
    (op_str, n) : tuple[str, float]
        The operator string (e.g. ``">="`` ) and the float threshold.

    Raises
    ------
    ValueError
        If the string does not conform to the expected format.
    """
    val = val.strip()
    for token in _OP_TOKENS:
        if val.startswith(token):
            rest = val[len(token):]
            if not rest:
                raise ValueError(
                    f"--heapszf '{val}': missing number after operator '{token}'"
                )
            try:
                return (token, float(rest))
            except ValueError:
                raise ValueError(
                    f"--heapszf '{val}': '{rest}' is not a valid number"
                )

    # Bare number — default to ==
    try:
        return ("==", float(val))
    except ValueError:
        pass

    raise ValueError(
        f"--heapszf '{val}': unrecognised format; expected [op]N where op is "
        "one of >, <, >=, <=, == and N is a number"
    )


def parse_heapsz(val: str) -> tuple:
    """Parse a --heapsz argument such as ``>=8192``, ``<=16384``, ``==8192``,
    or bare ``8192`` (defaults to ``==``). Values are in MB.

    Returns
    -------
    (op_str, n) : tuple[str, float]
        The operator string (e.g. ``">="`` ) and the numeric threshold (MB).

    Raises
    ------
    ValueError
        If the string does not conform to the expected format.
    """
    val = val.strip()
    for token in _OP_TOKENS:
        if val.startswith(token):
            rest = val[len(token):]
            if not rest:
                raise ValueError(
                    f"--heapsz '{val}': missing number after operator '{token}'"
                )
            try:
                return (token, float(rest))
            except ValueError:
                raise ValueError(
                    f"--heapsz '{val}': '{rest}' is not a valid number"
                )

    # Bare number — default to ==
    try:
        return ("==", float(val))
    except ValueError:
        pass

    raise ValueError(
        f"--heapsz '{val}': unrecognised format; expected [op]N where op is "
        "one of >, <, >=, <=, == and N is a number (MB)"
    )


def parse_duration(val: str) -> tuple:
    """Parse a --duration argument such as ``>=120``, ``<=60.5``, ``==300``,
    or bare ``120`` (defaults to ``==``). Values are in seconds.

    Returns
    -------
    (op_str, n) : tuple[str, float]
        The operator string (e.g. ``">="`` ) and the numeric threshold (seconds).

    Raises
    ------
    ValueError
        If the string does not conform to the expected format.
    """
    val = val.strip()
    for token in _OP_TOKENS:
        if val.startswith(token):
            rest = val[len(token):]
            if not rest:
                raise ValueError(
                    f"--duration '{val}': missing number after operator '{token}'"
                )
            try:
                return (token, float(rest))
            except ValueError:
                raise ValueError(
                    f"--duration '{val}': '{rest}' is not a valid number"
                )

    # Bare number — default to ==
    try:
        return ("==", float(val))
    except ValueError:
        pass

    raise ValueError(
        f"--duration '{val}': unrecognised format; expected [op]N where op is "
        "one of >, <, >=, <=, == and N is a number (seconds)"
    )


def parse_startup_ms(val: str) -> tuple:
    """Parse a --startup_ms argument such as ``<2000``, ``>=1500.5``,
    ``==1800``, or bare ``1800`` (defaults to ``==``). Values are in ms.

    Returns
    -------
    (op_str, n) : tuple[str, float]
        The operator string (e.g. ``"<"`` ) and the numeric threshold (ms).

    Raises
    ------
    ValueError
        If the string does not conform to the expected format.
    """
    val = val.strip()
    for token in _OP_TOKENS:
        if val.startswith(token):
            rest = val[len(token):]
            if not rest:
                raise ValueError(
                    f"--startup_ms '{val}': missing number after operator '{token}'"
                )
            try:
                return (token, float(rest))
            except ValueError:
                raise ValueError(
                    f"--startup_ms '{val}': '{rest}' is not a valid number"
                )

    # Bare number — default to ==
    try:
        return ("==", float(val))
    except ValueError:
        pass

    raise ValueError(
        f"--startup_ms '{val}': unrecognised format; expected [op]N where op is "
        "one of >, <, >=, <=, == and N is a number (ms)"
    )

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


def parse_datetime_spec(val: str) -> str:
    """Parse a --since/--until argument such as ``2026-09-22T20:39:46``.

    Accepts a date and time separated by a literal ``T`` (matching the
    ``time`` field / jbr-ls's ``time`` column); either side may be
    omitted. A missing seconds component defaults to ``:00``, a missing
    time defaults to ``00:00:00``, and a missing date defaults to today.

    Returns
    -------
    str
        A normalised ``YYYY-MM-DDTHH:MM:SS`` string, directly comparable
        (lexicographically) against the truncated ``time`` field of a
        merged run object.

    Raises
    ------
    ValueError
        If the string does not conform to the expected format.
    """
    raw = val.strip()
    if not raw:
        raise ValueError("empty date/time specification")

    parts = raw.split("T", 1)
    date_str = time_str = None
    if len(parts) == 2:
        date_str, time_str = parts
    elif _DATE_RE.match(parts[0]):
        date_str = parts[0]
    elif _TIME_RE.match(parts[0]):
        time_str = parts[0]
    else:
        raise ValueError(
            f"'{val}': unrecognised date/time format; expected "
            "'YYYY-MM-DD[THH:MM[:SS]]' or bare 'HH:MM[:SS]'"
        )

    if date_str is None:
        date_obj = datetime.date.today()
    else:
        if not _DATE_RE.match(date_str):
            raise ValueError(
                f"'{val}': invalid date '{date_str}'; expected YYYY-MM-DD"
            )
        try:
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"'{val}': invalid date '{date_str}': {exc}")

    if time_str is None:
        hour, minute, second = 0, 0, 0
    else:
        if not _TIME_RE.match(time_str):
            raise ValueError(
                f"'{val}': invalid time '{time_str}'; expected HH:MM[:SS]"
            )
        fields = time_str.split(":")
        if len(fields) == 2:
            fields.append("00")
        hour, minute, second = (int(f) for f in fields)
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise ValueError(f"'{val}': time '{time_str}' out of range")

    dt = datetime.datetime(
        date_obj.year, date_obj.month, date_obj.day, hour, minute, second
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _time_key(merged: dict):
    """Return the merged run's time truncated to second precision, or
    None if absent/malformed. Comparable lexicographically with the output
    of parse_datetime_spec()."""
    ts = merged.get("time")
    if not isinstance(ts, str) or len(ts) < 19:
        return None
    return ts[:19]


def _since_until_predicate(threshold: str, op):
    def _pred(m):
        key = _time_key(m)
        return key is not None and op(key, threshold)
    return _pred


# ---------------------------------------------------------------------------
# jq integration
# ---------------------------------------------------------------------------

def run_jq_filter(expr: str, merged: dict) -> bool:
    """Evaluate a raw jq boolean expression against a merged run dict.

    Serialises *merged* to JSON, pipes it to ``jq -e '<expr>'``, and returns
    ``True`` if ``jq`` exits with code 0 (expression is truthy).

    Raises
    ------
    RuntimeError
        If ``jq`` is not found on PATH.
    """
    if shutil.which("jq") is None:
        raise RuntimeError(
            "jq is not available on PATH; required for --where filters"
        )
    json_bytes = json.dumps(merged).encode()
    result = subprocess.run(
        ["jq", "-e", expr],
        input=json_bytes,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Filter builder
# ---------------------------------------------------------------------------

def build_filter(parsed_args):
    """Compose all active filters from a parsed argparse.Namespace into a
    single callable ``fn(merged: dict) -> bool``.

    Each convenience flag maps to a Python predicate over the merged run dict.
    Multiple occurrences of the same flag each produce a separate predicate.
    All predicates are AND-ed (default) or OR-ed (if ``parsed_args.or_`` is
    True).  The combined result is negated when ``parsed_args.invert`` is True.

    If no filters are active the returned callable always returns True.
    """
    predicates = []

    # --benchmark
    for val in (parsed_args.benchmark or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("benchmark") is None)
            continue
        v = val
        predicates.append(lambda m, _v=v: m.get("benchmark") == _v)

    # --sub
    for val in (parsed_args.sub or []):
        if _is_null_sentinel(val):
            predicates.append(
                lambda m: m.get("dacapo", {}).get("sub_benchmark") is None
            )
            continue
        v = val
        predicates.append(
            lambda m, _v=v: m.get("dacapo", {}).get("sub_benchmark") == _v
        )

    # --datasize
    for val in (parsed_args.datasize or []):
        if _is_null_sentinel(val):
            predicates.append(
                lambda m: m.get("dacapo", {}).get("datasize") is None
            )
            continue
        v = val
        predicates.append(
            lambda m, _v=v: m.get("dacapo", {}).get("datasize") == _v
        )

    # --jdkmajor  (parsing already validated before build_filter is called)
    for val in (parsed_args.jdkmajor or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("jdk", {}).get("major") is None)
            continue
        op_str, n = parse_jdk_major(val)
        op_fn = _OPS[op_str]
        predicates.append(
            lambda m, _op=op_fn, _n=n: (
                isinstance(m.get("jdk", {}).get("major"), int)
                and _op(m["jdk"]["major"], _n)
            )
        )

    # --repeat_no  (parsing already validated before build_filter is called)
    for val in (parsed_args.repeat_no or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("repeat_no") is None)
            continue
        op_str, n = parse_repeat_no(val)
        op_fn = _OPS[op_str]
        predicates.append(
            lambda m, _op=op_fn, _n=n: (
                isinstance(m.get("repeat_no"), int)
                and _op(m["repeat_no"], _n)
            )
        )

    # --heapszf  (parsing already validated before build_filter is called)
    for val in (parsed_args.heapszf or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("heapsize_factor") is None)
            continue
        op_str, n = parse_heapszf(val)
        op_fn = _OPS[op_str]
        predicates.append(
            lambda m, _op=op_fn, _n=n: (
                isinstance(m.get("heapsize_factor"), (int, float))
                and _op(m["heapsize_factor"], _n)
            )
        )

    # --heapsz  (parsing already validated before build_filter is called)
    for val in (parsed_args.heapsz or []):
        if _is_null_sentinel(val):
            predicates.append(
                lambda m: m.get("heap", {}).get("heapsize") is None
            )
            continue
        op_str, n = parse_heapsz(val)
        op_fn = _OPS[op_str]
        predicates.append(
            lambda m, _op=op_fn, _n=n: (
                isinstance(m.get("heap", {}).get("heapsize"), (int, float))
                and _op(m["heap"]["heapsize"], _n)
            )
        )

    # --duration  (parsing already validated before build_filter is called)
    for val in (parsed_args.duration or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("duration_secs") is None)
            continue
        op_str, n = parse_duration(val)
        op_fn = _OPS[op_str]
        predicates.append(
            lambda m, _op=op_fn, _n=n: (
                isinstance(m.get("duration_secs"), (int, float))
                and _op(m["duration_secs"], _n)
            )
        )

    # --startup_ms  (parsing already validated before build_filter is called)
    for val in (parsed_args.startup_ms or []):
        if _is_null_sentinel(val):
            predicates.append(
                lambda m: (m.get("dacapo_result") or {}).get("startup_ms") is None
            )
            continue
        op_str, n = parse_startup_ms(val)
        op_fn = _OPS[op_str]
        predicates.append(
            lambda m, _op=op_fn, _n=n: (
                isinstance((m.get("dacapo_result") or {}).get("startup_ms"), (int, float))
                and _op(m["dacapo_result"]["startup_ms"], _n)
            )
        )

    # --gc  (exact match on heap.gc)
    for val in (parsed_args.gc or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("heap", {}).get("gc") is None)
            continue
        v = val
        predicates.append(
            lambda m, _v=v: m.get("heap", {}).get("gc") == _v
        )

    # --jdktype
    for val in (parsed_args.jdktype or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("jdk", {}).get("type") is None)
            continue
        v = val
        predicates.append(
            lambda m, _v=v: m.get("jdk", {}).get("type") == _v
        )

    # --jdkname  (substring in jdk.name)
    for val in (parsed_args.jdkname or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("jdk", {}).get("name") is None)
            continue
        v = val
        predicates.append(
            lambda m, _v=v: _v in (m.get("jdk", {}).get("name") or "")
        )

    # --jdkversion  (substring in jdk.version)
    for val in (parsed_args.jdkversion or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("jdk", {}).get("version") is None)
            continue
        v = val
        predicates.append(
            lambda m, _v=v: _v in (m.get("jdk", {}).get("version") or "")
        )

    # --tags  (glob match against any element of tags; a literal value without
    # glob characters therefore matches exactly; case-sensitive)
    for val in (parsed_args.tags or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("tags") is None)
            continue
        v = val
        predicates.append(
            lambda m, _v=v: any(fnmatch.fnmatchcase(t, _v)
                                for t in (m.get("tags") or []))
        )

    # --name  (glob match against the compound name, e.g. "dacapo.h2"; a
    # literal value without glob characters therefore matches exactly;
    # case-sensitive)
    for val in (parsed_args.name or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("name") is None)
            continue
        v = val
        predicates.append(
            lambda m, _v=v: isinstance(m.get("name"), str)
            and fnmatch.fnmatchcase(m["name"], _v)
        )

    # --hostname
    for val in (parsed_args.hostname or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("hostname") is None)
            continue
        v = val
        predicates.append(lambda m, _v=v: m.get("hostname") == _v)

    # --batchid
    for val in (parsed_args.batchid or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("batchid") is None)
            continue
        v = val
        predicates.append(lambda m, _v=v: m.get("batchid") == _v)

    # --arch
    for val in (parsed_args.arch or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("arch") is None)
            continue
        v = val
        predicates.append(lambda m, _v=v: m.get("arch") == _v)

    # --os  (substring in os)
    for val in (parsed_args.os or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("os") is None)
            continue
        v = val
        predicates.append(lambda m, _v=v: _v in (m.get("os") or ""))

    # --jvmargs  (substring in any element of jvmargs)
    for val in (parsed_args.jvmargs or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("jvmargs") is None)
            continue
        v = val
        predicates.append(
            lambda m, _v=v: any(_v in f for f in (m.get("jvmargs") or []))
        )

    # --since  (parsing already validated before build_filter is called)
    for val in (parsed_args.since or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("time") is None)
            continue
        threshold = parse_datetime_spec(val)
        predicates.append(_since_until_predicate(threshold, operator.ge))

    # --until  (parsing already validated before build_filter is called)
    for val in (parsed_args.until or []):
        if _is_null_sentinel(val):
            predicates.append(lambda m: m.get("time") is None)
            continue
        threshold = parse_datetime_spec(val)
        predicates.append(_since_until_predicate(threshold, operator.le))

    # --where  (raw jq expression; delegated to jq subprocess)
    for expr in (parsed_args.where or []):
        e = expr
        predicates.append(lambda m, _e=e: run_jq_filter(_e, m))

    if not predicates:
        return lambda m: True

    use_or = getattr(parsed_args, "or_", False)
    invert = getattr(parsed_args, "invert", False)

    def _combined(merged):
        if use_or:
            result = any(p(merged) for p in predicates)
        else:
            result = all(p(merged) for p in predicates)
        return not result if invert else result

    return _combined
