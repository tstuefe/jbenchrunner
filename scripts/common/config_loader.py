# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Loaders and validators for every configuration file type consumed by jbenchrunner.

Each loader:
  - reads and parses the TOML file,
  - warns via a caller-supplied `warn` callable (default: print) on any
    unrecognised top-level field (decision W-1),
  - raises ValueError with a descriptive message on any schema violation.
"""

import tomllib
import os
import re


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# `jvmargs_<suffix>` / `tags_<suffix>` / `bench_<suffix>` split keys were
# dropped with the switch to TOML (PRD.md D-76), which can spread one array
# over several lines with comments. A leftover split key is a hard error, not
# the usual unknown-field warning: its flags, tags or benchmarks would
# otherwise be silently left out of the batch.
_SPLIT_KEY_RE = re.compile(r"^(jvmargs|tags|bench)_[A-Za-z0-9_]+$")


def _reject_split_keys(value, path: str) -> None:
    """Raise ValueError on a split key anywhere in `value` (any nesting depth)."""
    if isinstance(value, dict):
        for key, v in value.items():
            m = _SPLIT_KEY_RE.match(key)
            if m:
                raise ValueError(
                    f"'{key}' in '{path}': split keys are no longer supported; "
                    f"merge its values into the '{m.group(1)}' array"
                )
            _reject_split_keys(v, path)
    elif isinstance(value, list):
        for v in value:
            _reject_split_keys(v, path)


def _load_toml(path: str) -> dict:
    """Read and parse a TOML config file; raise ValueError on IO or parse errors."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ValueError(f"File not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in '{path}': {exc}")
    _reject_split_keys(data, path)
    return data


def _check_unknown(data: dict, known: set, path: str, warn) -> None:
    """Warn on any key in `data` that is not in `known`."""
    for key in data:
        if key not in known:
            warn(f"WARNING: unknown field '{key}' in '{path}' — ignored")


def _require_string(data: dict, field: str, path: str) -> str:
    """Return data[field] as a non-empty string, or raise ValueError."""
    val = data.get(field)
    if not isinstance(val, str) or not val:
        raise ValueError(f"'{field}' must be a non-empty string in '{path}'")
    return val


def _require_list(data: dict, field: str, path: str) -> list:
    """Return data[field] as a list (may be empty unless caller checks), or raise ValueError."""
    val = data.get(field)
    if not isinstance(val, list):
        raise ValueError(f"'{field}' must be a list in '{path}'")
    return val


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

_PROFILE_JSON_KNOWN = {"flags"}


def load_profile_json(path: str, warn=print) -> dict:
    """
    Load a .profile.toml.  Validates that `flags` is a list of strings.
    """
    data = _load_toml(path)
    _check_unknown(data, _PROFILE_JSON_KNOWN, path, warn)

    flags = _require_list(data, "flags", path)
    for i, f in enumerate(flags):
        if not isinstance(f, str):
            raise ValueError(f"'flags[{i}]' must be a string in '{path}'")

    return {"flags": flags}


# ---------------------------------------------------------------------------

_BENCH_JSON_KNOWN = {
    "benchmark", "sub_benchmark", "datasize", "iterations",
    "jvmargs", "min_java_version", "max_java_version", "minheap",
}

_VALID_BENCHMARKS = {"dacapo", "specjbb"}
_VALID_DATASIZES = {"small", "default", "large", "huge", "vlarge"}


def load_bench_json(path: str, warn=print) -> dict:
    """
    Load a .bench.toml.  Validates required and conditional fields per spec §1.2.
    """
    data = _load_toml(path)
    _check_unknown(data, _BENCH_JSON_KNOWN, path, warn)

    benchmark = _require_string(data, "benchmark", path)
    if benchmark not in _VALID_BENCHMARKS:
        raise ValueError(
            f"'benchmark' must be one of {sorted(_VALID_BENCHMARKS)} in '{path}'"
        )

    result = {"benchmark": benchmark}

    if benchmark == "dacapo":
        result["sub_benchmark"] = _require_string(data, "sub_benchmark", path)

        datasize = data.get("datasize", "default")
        if datasize not in _VALID_DATASIZES:
            raise ValueError(
                f"'datasize' must be one of {sorted(_VALID_DATASIZES)} in '{path}'"
            )
        result["datasize"] = datasize

        iterations = data.get("iterations", 10)
        if not isinstance(iterations, int) or iterations < 1:
            raise ValueError(f"'iterations' must be a positive integer in '{path}'")
        result["iterations"] = iterations

    # jvmargs — optional
    jvmargs = data.get("jvmargs", [])
    if not isinstance(jvmargs, list):
        raise ValueError(f"'jvmargs' must be a list in '{path}'")
    for i, f in enumerate(jvmargs):
        if not isinstance(f, str):
            raise ValueError(f"'jvmargs[{i}]' must be a string in '{path}'")
    result["jvmargs"] = jvmargs

    # version constraints — optional integers
    for vfield in ("min_java_version", "max_java_version"):
        val = data.get(vfield)
        if val is not None:
            if not isinstance(val, int) or val < 1:
                raise ValueError(f"'{vfield}' must be a positive integer in '{path}'")
            result[vfield] = val

    # minheap — optional integer
    minheap = data.get("minheap")
    if minheap is not None:
        if not isinstance(minheap, int) or minheap < 1:
            raise ValueError(f"'minheap' must be a positive integer in '{path}'")
        result["minheap"] = minheap

    return result


# ---------------------------------------------------------------------------

_BENCHGROUP_JSON_KNOWN = {"members"}


def load_benchgroup_json(path: str, warn=print) -> dict:
    """
    Load a .benchgroup.toml.  Validates a non-empty `members` list with no
    duplicate entries per spec §1.3.
    """
    data = _load_toml(path)
    _check_unknown(data, _BENCHGROUP_JSON_KNOWN, path, warn)

    members = _require_list(data, "members", path)
    if not members:
        raise ValueError(f"'members' must not be empty in '{path}'")
    for i, m in enumerate(members):
        if not isinstance(m, str) or not m:
            raise ValueError(f"'members[{i}]' must be a non-empty string in '{path}'")

    # Duplicate detection
    seen = set()
    for m in members:
        if m in seen:
            raise ValueError(f"Duplicate member '{m}' in '{path}'")
        seen.add(m)

    return {"members": members}


# ---------------------------------------------------------------------------

_RUN_JSON_TOPLEVEL_KNOWN = {
    "heapsize_factors", "jvmargs", "tags", "dacapo_iterations", "jvmenv",
    "do_cpu_pinning", "use_ramdisk", "dacapo_args", "bench", "benchgroup", "runs",
    "jdkname", "profiles", "collectors", "collectors_preset", "dynamic_heap",
    "repeat", "perf_events_preset",
}
_RUN_ENTRY_KNOWN = {
    "jdkname", "profiles", "jvmargs", "tags", "jvmenv", "do_cpu_pinning",
    "use_ramdisk", "dacapo_args", "dacapo_iterations",
}

# Batch-level only (PRD.md D-80): in a run entry a hard error, not the usual
# unknown-field warning, so an old per-run override is not silently ignored.
_RUN_ENTRY_FORBIDDEN_COLLECTOR_KEYS = (
    "collectors", "collectors_preset", "perf_events_preset",
)

# Collector short names, as used in the .run.toml "collectors" object, in
# BATCH_IN_PROGRESS.json / info.json "collectors.enabled", and in jbr-ls —
# mapped to the collector config name (config/collectors/<name>.collector.toml).
# Dict order is the canonical order of "collectors.enabled".
COLLECTORS = {
    "cpu_temp":     "cpu-temp-logger",
    "pidstat":      "pidstat-logger",
    "proc":         "proc-logger",
    "perf":         "perf-logger",
    "nmt":          "nmt",
    "system_map":   "system_map",
    "thread_print": "thread_print",
}

# Preset in effect when the .run.toml sets no collector settings.
# Presets themselves are config files (see load_collector_preset).
DEFAULT_COLLECTOR_PRESET = "cheap-10s"

_COLLECTORS_KNOWN = ("interval", "compression", "enabled")


def _parse_collectors(obj, label: str) -> dict:
    """
    Validate a "collectors" structure — the .run.toml "collectors" object
    or a whole .collector-preset.toml — and return
    {"interval", "compression", "enabled"} with "enabled" in canonical
    (COLLECTORS) order. All three keys are required; unknown keys are an
    error, not a warning. `label` is the location for error messages,
    already quoted.
    """
    if not isinstance(obj, dict):
        raise ValueError(f"'collectors' must be an object in {label}")
    for k in obj:
        if k not in _COLLECTORS_KNOWN:
            raise ValueError(
                f"unknown key '{k}' in collectors in {label} "
                f"(known: {', '.join(_COLLECTORS_KNOWN)})"
            )
    for k in _COLLECTORS_KNOWN:
        if k not in obj:
            raise ValueError(f"'{k}' is required in collectors in {label}")
    interval = obj["interval"]
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
        raise ValueError(f"'interval' must be a positive integer in collectors in {label}")
    if not isinstance(obj["compression"], bool):
        raise ValueError(f"'compression' must be a boolean in collectors in {label}")
    enabled = obj["enabled"]
    if not isinstance(enabled, list):
        raise ValueError(f"'enabled' must be a list in collectors in {label}")
    for i, n in enumerate(enabled):
        if n not in COLLECTORS:
            raise ValueError(
                f"'enabled[{i}]' must be one of {', '.join(COLLECTORS)} in collectors in {label}"
            )
    if len(set(enabled)) != len(enabled):
        raise ValueError(f"duplicate collector in 'enabled' in collectors in {label}")
    return {
        "interval": interval,
        "compression": obj["compression"],
        "enabled": [n for n in COLLECTORS if n in enabled],
    }


def _parse_collectors_level(data: dict, label: str) -> dict:
    """
    Parse the mutually exclusive "collectors" / "collectors_preset" keys of a
    .run.toml (batch level only, PRD.md D-80). Returns {"collectors": dict | None,
    "collectors_preset": str | None}; both None when the file sets neither.
    Whether a named preset exists is checked by the caller (prepare.py).
    """
    has_obj = "collectors" in data
    has_preset = "collectors_preset" in data
    if has_obj and has_preset:
        raise ValueError(
            f"At most one of 'collectors' or 'collectors_preset' may be set in {label}"
        )
    preset = None
    if has_preset:
        preset = data["collectors_preset"]
        if not isinstance(preset, str) or not preset:
            raise ValueError(f"'collectors_preset' must be a non-empty string in {label}")
    return {
        "collectors": _parse_collectors(data["collectors"], label) if has_obj else None,
        "collectors_preset": preset,
    }


def collector_preset_path(reporoot: str, name: str) -> str:
    return os.path.join(reporoot, "config", "collector-presets", f"{name}.collector-preset.toml")


def load_collector_preset_json(path: str, warn=print) -> dict:
    """
    Load a .collector-preset.toml — a "collectors" structure (spec-prepare.md
    §1.11) — and return it as parsed by _parse_collectors.
    """
    return _parse_collectors(_load_toml(path), f"'{path}'")


def load_collector_preset(reporoot: str, name: str, warn=print) -> dict:
    """Load config/collector-presets/<name>.collector-preset.toml; a missing file is a hard error."""
    path = collector_preset_path(reporoot, name)
    if not os.path.exists(path):
        raise RuntimeError(f"Collector preset not found for '{name}': '{path}'")
    return load_collector_preset_json(path, warn=warn)


def collectors_preset_needed(batch_level: dict):
    """
    Name of the preset a file's collector settings come from, or None when they
    come from a "collectors" object (see resolve_collectors).
    """
    if batch_level["collectors"] is not None:
        return None
    if batch_level["collectors_preset"] is not None:
        return batch_level["collectors_preset"]
    return DEFAULT_COLLECTOR_PRESET


def resolve_collectors(batch_level: dict, presets: dict) -> dict:
    """
    Return the collector settings of a file's runs per spec-prepare.md §1.4.1:
    the file's "collectors" or "collectors_preset", else DEFAULT_COLLECTOR_PRESET.
    `batch_level` is as returned by _parse_collectors_level; `presets` maps
    preset name -> parsed preset and must hold the one collectors_preset_needed
    names.

    Returns {"interval": int, "compression": bool, "enabled": [short names]}.
    """
    name = collectors_preset_needed(batch_level)
    if name is not None:
        return dict(presets[name])
    return dict(batch_level["collectors"])


_PERF_EVENTS_PRESET_KNOWN = ("events",)


def perf_events_preset_path(reporoot: str, name: str) -> str:
    return os.path.join(reporoot, "config", "perf-events-presets",
                        f"{name}.perf-events-preset.toml")


def load_perf_events_preset_json(path: str) -> list:
    """
    Load a .perf-events-preset.toml (spec-prepare.md §1.12) and return its
    "events": a non-empty list of distinct non-empty strings. Unknown keys are
    an error.
    """
    data = _load_toml(path)
    for k in data:
        if k not in _PERF_EVENTS_PRESET_KNOWN:
            raise ValueError(f"unknown key '{k}' in '{path}' (known: events)")
    events = data.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError(f"'events' must be a non-empty list in '{path}'")
    for i, e in enumerate(events):
        if not isinstance(e, str) or not e:
            raise ValueError(f"'events[{i}]' must be a non-empty string in '{path}'")
    if len(set(events)) != len(events):
        raise ValueError(f"duplicate event in 'events' in '{path}'")
    return list(events)


def load_perf_events_preset(reporoot: str, name: str) -> list:
    """Load config/perf-events-presets/<name>.perf-events-preset.toml; a missing file is a hard error."""
    path = perf_events_preset_path(reporoot, name)
    if not os.path.exists(path):
        raise RuntimeError(f"Perf events preset not found for '{name}': '{path}'")
    return load_perf_events_preset_json(path)


def load_run_json(path: str, warn=print) -> dict:
    """
    Load a .run.toml.  Validates top-level and per-run entry fields per spec §1.4.
    Warns on unknown fields (e.g. `on_failure`, singular `tag`).

    The benchmark selection is batch-level only: the result carries either
    `bench` (ordered list of .bench.toml names; a single name in the file is
    returned as a one-element list)
    or `benchgroup` (one .benchgroup.toml name); run entries carry neither.
    """
    data = _load_toml(path)
    _check_unknown(data, _RUN_JSON_TOPLEVEL_KNOWN, path, warn)

    result = {}

    # heapsize_factors — optional list of positive numbers
    factors = data.get("heapsize_factors")
    if factors is not None:
        if not isinstance(factors, list) or not factors:
            raise ValueError(f"'heapsize_factors' must be a non-empty list in '{path}'")
        for i, f in enumerate(factors):
            if not isinstance(f, (int, float)) or f <= 0:
                raise ValueError(
                    f"'heapsize_factors[{i}]' must be a positive number in '{path}'"
                )
        result["heapsize_factors"] = factors

    # dynamic_heap — optional batch-global boolean; default false. Only meaningful
    # together with 'heapsize_factors' (same file-level-only scope, no per-run-entry
    # override): when true, heap-sweep expansion sets only -Xmx from the swept size;
    # when false (default), it also sets -Xms and -XX:+AlwaysPreTouch (see
    # spec-prepare.md §1.5). It is a validation error to set it without
    # 'heapsize_factors' also being set in the same file.
    dynamic_heap = data.get("dynamic_heap")
    if dynamic_heap is not None:
        if not isinstance(dynamic_heap, bool):
            raise ValueError(f"'dynamic_heap' must be a boolean in '{path}'")
        if dynamic_heap and factors is None:
            raise ValueError(
                f"'dynamic_heap' is set but 'heapsize_factors' is absent in '{path}'"
            )
        result["dynamic_heap"] = dynamic_heap

    # top-level jvmargs — optional
    top_flags = data.get("jvmargs", [])
    if not isinstance(top_flags, list):
        raise ValueError(f"'jvmargs' must be a list in '{path}'")
    for i, f in enumerate(top_flags):
        if not isinstance(f, str):
            raise ValueError(f"'jvmargs[{i}]' must be a string in '{path}'")
    result["jvmargs"] = top_flags

    # top-level tags — optional
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        raise ValueError(f"'tags' must be a list in '{path}'")
    for i, t in enumerate(tags):
        if not isinstance(t, str):
            raise ValueError(f"'tags[{i}]' must be a string in '{path}'")
    result["tags"] = tags

    # dacapo_iterations — optional positive integer, overrides .bench.toml's
    # 'iterations' field for every dacapo run in this batch
    dacapo_iterations = data.get("dacapo_iterations")
    if dacapo_iterations is not None:
        if not isinstance(dacapo_iterations, int) or isinstance(dacapo_iterations, bool) \
                or dacapo_iterations < 1:
            raise ValueError(f"'dacapo_iterations' must be a positive integer in '{path}'")
        result["dacapo_iterations"] = dacapo_iterations

    # repeat — optional positive integer, file-level only: number of times each
    # run entry is repeated (interleaved by round); overridden by --repeat
    repeat = data.get("repeat")
    if repeat is not None:
        if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 1:
            raise ValueError(f"'repeat' must be a positive integer in '{path}'")
        result["repeat"] = repeat

    # top-level jvmenv — optional batch-global env vars for the JVM subprocess
    top_jvm_env = data.get("jvmenv", {})
    if not isinstance(top_jvm_env, dict):
        raise ValueError(f"'jvmenv' must be an object in '{path}'")
    for k, v in top_jvm_env.items():
        if not isinstance(k, str) or not k:
            raise ValueError(f"'jvmenv' keys must be non-empty strings in '{path}'")
        if not isinstance(v, (str, int)) or isinstance(v, bool):
            raise ValueError(f"'jvmenv[{k!r}]' must be a string or integer in '{path}'")
    result["jvmenv"] = {k: str(v) for k, v in top_jvm_env.items()}

    # do_cpu_pinning — optional batch-global boolean; overridden per-run entry
    do_cpu_pinning = data.get("do_cpu_pinning")
    if do_cpu_pinning is not None:
        if not isinstance(do_cpu_pinning, bool):
            raise ValueError(f"'do_cpu_pinning' must be a boolean in '{path}'")
        result["do_cpu_pinning"] = do_cpu_pinning

    # use_ramdisk — optional batch-global boolean; overridden per-run entry
    # (same pattern as do_cpu_pinning). Default: false.
    use_ramdisk = data.get("use_ramdisk")
    if use_ramdisk is not None:
        if not isinstance(use_ramdisk, bool):
            raise ValueError(f"'use_ramdisk' must be a boolean in '{path}'")
        result["use_ramdisk"] = use_ramdisk

    # collectors / collectors_preset — optional, mutually exclusive collector
    # settings for every run of this file (see resolve_collectors).
    result["collectors"] = _parse_collectors_level(data, f"'{path}'")

    # perf_events_preset — optional; absent means perf's default event set
    # (spec-prepare.md §1.4.2). Whether the preset exists is checked by the
    # caller (prepare.py).
    if "perf_events_preset" in data:
        perf_preset = data["perf_events_preset"]
        if not isinstance(perf_preset, str) or not perf_preset:
            raise ValueError(f"'perf_events_preset' must be a non-empty string in '{path}'")
        result["perf_events_preset"] = perf_preset
    else:
        result["perf_events_preset"] = None

    # dacapo_args — optional batch-global extra DaCapo harness args, applied
    # to every dacapo run in the batch; overridden per-run entry
    top_dacapo_args = data.get("dacapo_args")
    if top_dacapo_args is not None:
        if not isinstance(top_dacapo_args, list):
            raise ValueError(f"'dacapo_args' must be a list in '{path}'")
        for i, a in enumerate(top_dacapo_args):
            if not isinstance(a, str):
                raise ValueError(f"'dacapo_args[{i}]' must be a string in '{path}'")
        result["dacapo_args"] = top_dacapo_args

    # jdkname — optional batch-global default; overridden per-run entry.
    # Falls back only when the entry doesn't specify its own jdkname; if
    # neither is set, the entry uses "default".
    if "jdkname" in data:
        result["jdkname"] = _require_string(data, "jdkname", path)

    # profiles — optional batch-global default list of profile names;
    # overridden wholesale per-run entry (same pattern as dacapo_args).
    top_profiles = data.get("profiles", [])
    if not isinstance(top_profiles, list):
        raise ValueError(f"'profiles' must be a list in '{path}'")
    for i, p in enumerate(top_profiles):
        if not isinstance(p, str):
            raise ValueError(f"'profiles[{i}]' must be a string in '{path}'")
    result["profiles"] = top_profiles

    # bench / benchgroup — batch level only (PRD.md D-72); exactly one of the
    # two must be set. `bench` is one name or an array of names; every run
    # entry runs against every bench.
    top_has_bench = "bench" in data
    top_has_group = "benchgroup" in data
    if top_has_bench and top_has_group:
        raise ValueError(f"At most one of 'bench' or 'benchgroup' may be set in '{path}'")
    if top_has_bench:
        benches = data["bench"]
        if isinstance(benches, str):
            benches = [benches]
        if not isinstance(benches, list) or not benches:
            raise ValueError(
                f"'bench' must be a non-empty string or a non-empty array of strings in '{path}'"
            )
        seen = set()
        for i, b in enumerate(benches):
            if not isinstance(b, str) or not b:
                raise ValueError(f"'bench[{i}]' must be a non-empty string in '{path}'")
            if b in seen:
                raise ValueError(f"Duplicate bench '{b}' in 'bench' in '{path}'")
            seen.add(b)
        result["bench"] = benches
    elif top_has_group:
        result["benchgroup"] = _require_string(data, "benchgroup", path)
    else:
        raise ValueError(
            f"Exactly one of 'bench' or 'benchgroup' must be set at the top level "
            f"in '{path}' (run entries may not set them)"
        )

    # runs — required non-empty list
    runs_raw = data.get("runs")
    if not isinstance(runs_raw, list) or not runs_raw:
        raise ValueError(f"'runs' must be a non-empty list in '{path}'")

    runs = []
    for idx, entry in enumerate(runs_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"'runs[{idx}]' must be an object in '{path}'")

        entry_label = f"runs[{idx}] in '{path}'"
        # Hard error, not the usual unknown-field warning: an old config with
        # a per-entry bench would otherwise silently run the wrong benchmark.
        for key in ("bench", "benchgroup"):
            if key in entry:
                raise ValueError(
                    f"'{key}' is not allowed in a run entry ({entry_label}); set "
                    f"'bench'/'benchgroup' at the top level, or use one .run.toml "
                    f"file per benchmark selection"
                )
        for key in _RUN_ENTRY_FORBIDDEN_COLLECTOR_KEYS:
            if key in entry:
                raise ValueError(
                    f"'{key}' is not allowed in a run entry ({entry_label}); set it at "
                    f"the top level, or use one .run.toml file per setting"
                )
        _check_unknown(entry, _RUN_ENTRY_KNOWN, entry_label, warn)

        parsed = {}

        # jdkname — entry value takes precedence; falls back to the
        # batch-global default, then to "default" (config/jdks/default.jdk.toml).
        if "jdkname" in entry:
            parsed["jdkname"] = _require_string(entry, "jdkname", entry_label)
        elif "jdkname" in result:
            parsed["jdkname"] = result["jdkname"]
        else:
            parsed["jdkname"] = "default"

        # profiles — entry value (if present) wholesale-overrides the
        # batch-global default (same pattern as dacapo_args).
        entry_profiles = entry.get("profiles")
        if entry_profiles is not None:
            if not isinstance(entry_profiles, list):
                raise ValueError(f"'profiles' must be a list in {entry_label}")
            for i, p in enumerate(entry_profiles):
                if not isinstance(p, str):
                    raise ValueError(f"'profiles[{i}]' must be a string in {entry_label}")
            parsed["profiles"] = entry_profiles
        else:
            parsed["profiles"] = result["profiles"]

        entry_flags = entry.get("jvmargs", [])
        if not isinstance(entry_flags, list):
            raise ValueError(f"'jvmargs' must be a list in {entry_label}")
        for i, f in enumerate(entry_flags):
            if not isinstance(f, str):
                raise ValueError(f"'jvmargs[{i}]' must be a string in {entry_label}")
        parsed["jvmargs"] = entry_flags

        entry_tags = entry.get("tags", [])
        if not isinstance(entry_tags, list):
            raise ValueError(f"'tags' must be a list in {entry_label}")
        for i, t in enumerate(entry_tags):
            if not isinstance(t, str):
                raise ValueError(f"'tags[{i}]' must be a string in {entry_label}")
        parsed["tags"] = entry_tags

        entry_jvm_env = entry.get("jvmenv", {})
        if not isinstance(entry_jvm_env, dict):
            raise ValueError(f"'jvmenv' must be an object in {entry_label}")
        for k, v in entry_jvm_env.items():
            if not isinstance(k, str) or not k:
                raise ValueError(f"'jvmenv' keys must be non-empty strings in {entry_label}")
            if not isinstance(v, (str, int)) or isinstance(v, bool):
                raise ValueError(f"'jvmenv[{k!r}]' must be a string or integer in {entry_label}")
        parsed["jvmenv"] = {k: str(v) for k, v in entry_jvm_env.items()}

        # do_cpu_pinning — optional per-run override; None (absent) is
        # distinct from an explicit False, so the caller can fall back to
        # the batch-level value only when this entry didn't specify one.
        entry_do_cpu_pinning = entry.get("do_cpu_pinning")
        if entry_do_cpu_pinning is not None and not isinstance(entry_do_cpu_pinning, bool):
            raise ValueError(f"'do_cpu_pinning' must be a boolean in {entry_label}")
        parsed["do_cpu_pinning"] = entry_do_cpu_pinning

        # use_ramdisk — optional per-run override; None (absent) is distinct
        # from an explicit False, so the caller can fall back to the
        # batch-level value only when this entry didn't specify one.
        entry_use_ramdisk = entry.get("use_ramdisk")
        if entry_use_ramdisk is not None and not isinstance(entry_use_ramdisk, bool):
            raise ValueError(f"'use_ramdisk' must be a boolean in {entry_label}")
        parsed["use_ramdisk"] = entry_use_ramdisk

        # dacapo_args — optional per-run override; None (absent) is distinct
        # from an explicit [], so the caller can fall back to the batch-level
        # value only when this entry didn't specify one.
        entry_dacapo_args = entry.get("dacapo_args")
        if entry_dacapo_args is not None:
            if not isinstance(entry_dacapo_args, list):
                raise ValueError(f"'dacapo_args' must be a list in {entry_label}")
            for i, a in enumerate(entry_dacapo_args):
                if not isinstance(a, str):
                    raise ValueError(f"'dacapo_args[{i}]' must be a string in {entry_label}")
        parsed["dacapo_args"] = entry_dacapo_args

        # dacapo_iterations — optional per-run override; None (absent) means
        # fall back through CLI > batch-level > this run's .bench.toml default.
        entry_dacapo_iterations = entry.get("dacapo_iterations")
        if entry_dacapo_iterations is not None:
            if not isinstance(entry_dacapo_iterations, int) or isinstance(entry_dacapo_iterations, bool) \
                    or entry_dacapo_iterations < 1:
                raise ValueError(f"'dacapo_iterations' must be a positive integer in {entry_label}")
        parsed["dacapo_iterations"] = entry_dacapo_iterations

        runs.append(parsed)

    result["runs"] = runs
    return result


# ---------------------------------------------------------------------------

_HOST_JSON_KNOWN = {
    "maxheap", "ramdisk_path", "cpu_list", "cpu_list_harness",
    "dacapo_dir", "specjbb_dir", "test_jdk",
}

# Paths into this machine's benchmark and JDK installations (PRD.md D-83).
_HOST_JSON_PATHS = ("dacapo_dir", "specjbb_dir", "test_jdk")

_CPU_LIST_RE = re.compile(r"\d+(-\d+)?(,\d+(-\d+)?)*")


def load_host_json(path: str, warn=print) -> dict:
    """
    Load a <hostname>.host.toml per spec §1.6.

    `dacapo_dir` and `specjbb_dir` are optional at load time (they are
    required only when running the respective benchmark types; that
    contextual check is the caller's responsibility), as is `test_jdk` (the
    JDK used by the integration tests).  All, when present, must be
    non-empty strings.
    """
    data = _load_toml(path)
    # Hard error, not the unknown-field warning: the events moved to
    # .run.toml (perf_events_preset, PRD.md D-81).
    if "perf_events" in data:
        raise ValueError(
            f"'perf_events' is no longer supported in '{path}'; choose the events "
            f"with 'perf_events_preset' in the .run.toml"
        )
    _check_unknown(data, _HOST_JSON_KNOWN, path, warn)

    result = {}

    ramdisk_path = data.get("ramdisk_path")
    if ramdisk_path is not None:
        if not isinstance(ramdisk_path, str) or not ramdisk_path:
            raise ValueError(f"'ramdisk_path' must be a non-empty string in '{path}'")
        result["ramdisk_path"] = ramdisk_path

    for key in ("cpu_list", "cpu_list_harness"):
        cpu_list = data.get(key)
        if cpu_list is None:
            continue
        if not isinstance(cpu_list, str) or not cpu_list:
            raise ValueError(f"'{key}' must be a non-empty string in '{path}'")
        if not _CPU_LIST_RE.fullmatch(cpu_list):
            raise ValueError(
                f"'{key}' has invalid syntax in '{path}': '{cpu_list}' "
                "(expected taskset --cpu-list syntax, e.g. '0-3' or '1,3,5,7,9')"
            )
        result[key] = cpu_list

    maxheap = data.get("maxheap")
    if maxheap is not None:
        if not isinstance(maxheap, int) or isinstance(maxheap, bool) or maxheap < 1:
            raise ValueError(f"'maxheap' must be a positive integer in '{path}'")
        result["maxheap"] = maxheap

    for field in _HOST_JSON_PATHS:
        value = data.get(field)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise ValueError(f"'{field}' must be a non-empty string in '{path}'")
            result[field] = value


    return result


# ---------------------------------------------------------------------------

_JDK_JSON_KNOWN = {"java_home"}


def load_jdk_json(path: str, warn=print) -> dict:
    """
    Load a .jdk.toml per spec §1.7.
    """
    data = _load_toml(path)
    _check_unknown(data, _JDK_JSON_KNOWN, path, warn)

    java_home = _require_string(data, "java_home", path)
    return {"java_home": java_home}


# ---------------------------------------------------------------------------

_JDKTYPE_JSON_KNOWN = {"gc_log_option", "extra_jvmargs"}


def load_jdktype_json(path: str, warn=print) -> dict:
    """
    Load a .jdktype.toml per spec §1.8.
    """
    data = _load_toml(path)
    _check_unknown(data, _JDKTYPE_JSON_KNOWN, path, warn)

    gc_log_option = _require_string(data, "gc_log_option", path)

    extra_jvmargs = data.get("extra_jvmargs")
    if extra_jvmargs is not None:
        if not isinstance(extra_jvmargs, list):
            raise ValueError(f"'extra_jvmargs' must be a list or null in '{path}'")
        for i, f in enumerate(extra_jvmargs):
            if not isinstance(f, str):
                raise ValueError(
                    f"'extra_jvmargs[{i}]' must be a string in '{path}'"
                )

    return {
        "gc_log_option": gc_log_option,
        "extra_jvmargs": extra_jvmargs if extra_jvmargs is not None else [],
    }


# ---------------------------------------------------------------------------

_COLLECTOR_JSON_KNOWN = {"restrict_to"}
_RESTRICT_TO_ENTRY_KNOWN = {"jdk_type", "min", "max"}
KNOWN_JDK_TYPES = ("openjdk", "openj9")

# Every collector must have a config/collectors/<name>.collector.toml.
KNOWN_COLLECTORS = tuple(COLLECTORS.values())


def collector_json_path(reporoot: str, name: str) -> str:
    return os.path.join(reporoot, "config", "collectors", f"{name}.collector.toml")


def load_collector_json(path: str, warn=print) -> dict:
    """
    Load a .collector.toml: {"restrict_to": [{"jdk_type": ..., "min": N, "max": M}, ...]}.

    `restrict_to` is optional; when absent the collector applies to every JDK
    (returned as {"restrict_to": None}). `min`/`max` are optional inclusive JDK
    major versions. Raises ValueError on an invalid `restrict_to` or unknown jdk_type.
    """
    data = _load_toml(path)
    _check_unknown(data, _COLLECTOR_JSON_KNOWN, path, warn)

    if "restrict_to" not in data:
        return {"restrict_to": None}
    restrict_to = _require_list(data, "restrict_to", path)
    entries = []
    for i, e in enumerate(restrict_to):
        where = f"restrict_to[{i}]"
        if not isinstance(e, dict):
            raise ValueError(f"'{where}' must be an object in '{path}'")
        _check_unknown(e, _RESTRICT_TO_ENTRY_KNOWN, f"{path} ({where})", warn)
        jdk_type = e.get("jdk_type")
        if jdk_type not in KNOWN_JDK_TYPES:
            raise ValueError(
                f"'{where}.jdk_type' must be one of {list(KNOWN_JDK_TYPES)} in '{path}'"
            )
        entry = {"jdk_type": jdk_type}
        for bound in ("min", "max"):
            v = e.get(bound)
            if v is not None:
                if isinstance(v, bool) or not isinstance(v, int):
                    raise ValueError(f"'{where}.{bound}' must be an integer in '{path}'")
                entry[bound] = v
        if "min" in entry and "max" in entry and entry["min"] > entry["max"]:
            raise ValueError(f"'{where}': min > max in '{path}'")
        entries.append(entry)
    return {"restrict_to": entries}


def load_collector_config(reporoot: str, name: str, warn=print) -> dict:
    """Load config/collectors/<name>.collector.toml; a missing file is a hard error."""
    path = collector_json_path(reporoot, name)
    if not os.path.exists(path):
        raise RuntimeError(f"Collector config not found for '{name}': '{path}'")
    return load_collector_json(path, warn=warn)


def collector_applies(config: dict, jdk_type: str, major: int) -> bool:
    """True if the collector has no restriction, or any restrict_to entry matches (jdk_type, major)."""
    if config["restrict_to"] is None:
        return True
    for e in config["restrict_to"]:
        if (e["jdk_type"] == jdk_type
                and major >= e.get("min", 0)
                and major <= e.get("max", float("inf"))):
            return True
    return False
