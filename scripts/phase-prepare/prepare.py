# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Core prepare-phase logic for jbr-prepare.

Implements run_prepare(run_json_path, cli_options) -> dict and
write_batch(batch_dict, project_dir) for atomic batch-file writing.
The batch is written into the current project (PRD.md §4), together with
frozen copies of the .run.toml files in batches/<BATCHID>/.

Algorithm follows spec-prepare.md §3.1.
"""

import glob as _glob
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Path bootstrap: ensure both scripts/common/ and scripts/phase-prepare/ are
# importable regardless of how this module is loaded (package or sys.path).
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_PHASE_PREPARE_DIR = _HERE

for _p in (_SCRIPTS_DIR, _PHASE_PREPARE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import project as _project      # noqa: E402
from common.collector_probe import unavailable_reason  # noqa: E402
from common.config_loader import (          # noqa: E402
    COLLECTORS,
    collector_applies,
    collectors_preset_needed,
    load_collector_preset,
    load_collector_config,
    load_bench_json,
    load_benchgroup_json,
    load_host_json,
    load_jdk_json,
    load_jdktype_json,
    load_perf_events_preset,
    load_profile_json,
    load_run_json,
    resolve_collectors,
)
from common.jdk_detect import parse_java_version_output  # noqa: E402
from flag_assembly import (                  # noqa: E402
    assemble_flags,
    check_flag_conflicts,
    parse_xmx_mb,
)

# Appended to a run's assembled flags when the NMT collector is enabled for it
# ("collectors" in .run.toml) — HotSpot-only, see the restrict_to gating below.
_NMT_FLAGS = [
    "-XX:NativeMemoryTracking=summary",
    "-XX:+UnlockDiagnosticVMOptions",
    "-XX:+PrintNMTStatistics",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bench_path(reporoot: str, name: str) -> str:
    return os.path.join(reporoot, "config", "bench", f"{name}.bench.toml")


def _benchgroup_path(reporoot: str, name: str) -> str:
    return os.path.join(reporoot, "config", "benchgroup", f"{name}.benchgroup.toml")


def _profile_path(reporoot: str, name: str) -> str:
    return os.path.join(reporoot, "config", "profiles", f"{name}.profile.toml")


def _jdk_path(reporoot: str, jdkname: str) -> str:
    return os.path.join(reporoot, "config", "jdks", f"{jdkname}.jdk.toml")


def _jdktype_path(reporoot: str, jdk_type: str) -> str:
    return os.path.join(reporoot, "config", "jdk-types", f"{jdk_type}.jdktype.toml")


def _host_path(reporoot: str, hostname: str) -> str:
    return os.path.join(reporoot, "config", "hosts", f"{hostname}.host.toml")


def _batch_path(project_dir: str) -> str:
    return _project.batch_file(project_dir)


def _batch_has_started(batch_file: str, project_dir: str) -> bool:
    """
    Return True if the existing BATCH_IN_PROGRESS.json at batch_file has any
    run with a result directory already on disk (i.e. jbr-execute has
    touched it), False if the batch is untouched and safe to overwrite.

    An unreadable or malformed batch file is treated as "started" so it is
    never silently clobbered.
    """
    try:
        with open(batch_file, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
    except (OSError, ValueError):
        return True

    for run in existing.get("runs", []):
        result_dir = run.get("result_dir")
        if result_dir and os.path.exists(os.path.join(project_dir, result_dir)):
            return True
    return False


def _detect_java_version_and_type(java_binary: str, run_java_version=None):
    """
    Run `<java_binary> -version`, parse the integer feature version and JDK type.

    run_java_version is an optional callable(cmd) -> str for testing; defaults to
    running the real binary.

    Returns (feature_version: int, jdk_type: str).
    Raises RuntimeError if the binary is not found or version cannot be parsed.
    """
    cmd = [java_binary, "-version"]
    try:
        if run_java_version is not None:
            output = run_java_version(cmd)
        else:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            output = result.stdout
    except FileNotFoundError:
        raise RuntimeError(
            f"Java binary not found: '{java_binary}'. "
            "Ensure the JDK is installed and java_home is correct."
        )

    try:
        info = parse_java_version_output(output)
    except RuntimeError:
        raise RuntimeError(
            f"Could not parse Java feature version from output of '{java_binary} -version':\n{output}"
        )
    return info["major"], info["type"]


_CPU_RANGE_RE = re.compile(r"^\d+(-\d+)?$")


def _parse_cpu_list_set(cpu_list: str) -> set:
    """Expand a taskset-style CPU list ('0-3,8,10-11') into a set of ints."""
    cpus = set()
    for part in cpu_list.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            cpus.update(range(int(lo), int(hi) + 1))
        else:
            cpus.add(int(part))
    return cpus


def _isolcpus_from_cmdline(cmdline: str):
    """
    Extract the isolated CPU set from a kernel command line string (as found
    in /proc/cmdline), or None if no 'isolcpus=' parameter is present.

    Handles both the plain form ('isolcpus=0-3,8') and the flagged form
    ('isolcpus=managed_irq,domain,0-3,8'), where leading comma-separated
    tokens that aren't a CPU number or range are flags and are ignored.
    """
    isolcpus_value = None
    for token in cmdline.split():
        if token.startswith("isolcpus="):
            isolcpus_value = token[len("isolcpus="):]
    if isolcpus_value is None:
        return None

    cpus = set()
    for part in isolcpus_value.split(","):
        part = part.strip()
        if not part:
            continue
        if _CPU_RANGE_RE.match(part):
            if "-" in part:
                lo, hi = part.split("-", 1)
                cpus.update(range(int(lo), int(hi) + 1))
            else:
                cpus.add(int(part))
        # else: a flag token (e.g. 'managed_irq', 'domain') — ignored
    return cpus


def _check_isolcpus(cpu_list: str, read_cmdline=None):
    """
    Warn (print to stdout; never raises) if the currently booted kernel's
    'isolcpus=' does not exactly match *cpu_list* (the host's cpu_list,
    spec-prepare.md §1.6), or if the kernel was booted without 'isolcpus' at
    all.

    read_cmdline is an optional callable() -> str for testing; defaults to
    reading /proc/cmdline. If /proc/cmdline can't be read (e.g. a non-Linux
    dev machine), the check is silently skipped.
    """
    try:
        if read_cmdline is not None:
            cmdline = read_cmdline()
        else:
            with open("/proc/cmdline", "r", encoding="utf-8") as fh:
                cmdline = fh.read()
    except OSError:
        return

    isolated = _isolcpus_from_cmdline(cmdline)
    expected = _parse_cpu_list_set(cpu_list)

    if isolated is None:
        print(
            f"WARNING: CPU pinning is active (cpu_list='{cpu_list}') but the running "
            f"kernel was booted without 'isolcpus' — pinned CPUs are not isolated "
            f"from the scheduler"
        )
    elif isolated != expected:
        print(
            f"WARNING: CPU pinning is active (cpu_list='{cpu_list}') but the running "
            f"kernel's 'isolcpus' ({sorted(isolated)}) does not match cpu_list "
            f"({sorted(expected)})"
        )


def _check_taskset_supported(cpu_list: str, run_taskset=None) -> bool:
    """
    Probe whether CPU pinning actually works on this host by running
    'taskset --cpu-list <cpu_list> sleep 0' and checking its exit status.
    Covers both 'taskset' being absent and cpu_list being rejected by the
    host (e.g. invalid CPU IDs, restricted affinity). Never raises.

    run_taskset is an optional callable(cmd) -> int (return code) for
    testing; defaults to actually running the command.
    """
    cmd = ["taskset", "--cpu-list", cpu_list, "sleep", "0"]
    try:
        if run_taskset is not None:
            returncode = run_taskset(cmd)
        else:
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            returncode = result.returncode
    except OSError:
        return False
    return returncode == 0


_PERF_EVENT_PARANOID = "/proc/sys/kernel/perf_event_paranoid"

# perf_event_paranoid levels at which unprivileged perf counts nothing at all
# (Debian/Ubuntu kernels), resp. only user space (PRD.md D-85).
_PERF_PARANOID_BLOCKED = 3
_PERF_PARANOID_USER_ONLY = 2


def _read_perf_event_paranoid(paranoid_file: str = _PERF_EVENT_PARANOID):
    """Return this host's kernel.perf_event_paranoid, or None if unreadable."""
    try:
        with open(paranoid_file) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_prepare(run_json_paths, cli_options: dict, reporoot: str = None,
                _run_java_version=None, _hostname=None, _read_cmdline=None,
                _run_taskset=None, project: str = None) -> dict:
    """
    Execute the full preparation pipeline.

    Parameters
    ----------
    run_json_paths : str | list[str] — path(s) to .run.toml file(s). A single
                     string is accepted for backward compatibility. Multiple
                     files are processed in the given order: each file's own
                     batch-level fields (bench/benchgroup, jdkname, profiles,
                     jvmargs, tags, heapsize_factors, jvmenv,
                     do_cpu_pinning, use_ramdisk, dacapo_args, collectors,
                     dacapo_iterations) apply only to
                     that file's own entries (PRD.md D-61); the resulting
                     run lists are concatenated in file-argument order before
                     --repeat and seq numbering are applied.
    cli_options    : dict  — keys: jvmargs (list[str]), tags (list[str]),
                             collector_interval (int | None),
                             dacapo_iterations (int | None), repeat (int | None),
                             sudo_mode (bool), stabilize_machine (bool)

    `collector_interval`, when not None, overrides `collectors.interval` from the
    .run.toml; when both are absent the default is 10.

    `dacapo_iterations`, when not None, overrides the `dacapo_iterations` field
    from the .run.toml (batch-level or per-run-entry); that field, when set,
    overrides the `iterations` field of every dacapo .bench.toml referenced by
    this batch. Non-dacapo runs are unaffected.

    `repeat`, when not None, overrides the file-level `repeat` field of the
    .run.toml file(s); if absent there too, the default is 1. With several
    input files that set different `repeat` values and no CLI `repeat`, the
    batch is rejected.
    reporoot       : str   — repository root; if None, derived from this
                     script's own location
    project        : str   — project directory; if None, found from the
                     current working directory (PRD.md §4)
    _run_java_version : callable | None — injection point for tests
    _hostname      : str | None — override hostname (for tests)
    _read_cmdline  : callable | None — injection point for tests; returns the
                     kernel command line string in place of reading /proc/cmdline
    _run_taskset   : callable | None — injection point for tests; callable(cmd) -> int
                     return code, in place of actually running 'taskset'

    Returns the batch dict (already written to disk).
    Raises ValueError / RuntimeError on any validation error.
    """
    if isinstance(run_json_paths, str):
        run_json_paths = [run_json_paths]
    else:
        run_json_paths = list(run_json_paths)
    if not run_json_paths:
        raise ValueError("At least one .run.toml file must be given")

    if reporoot is None:
        from common.reporoot import find_reporoot
        reporoot = find_reporoot(__file__)

    if project is None:
        project = _project.find_project()
    batch_file = _batch_path(project)

    # -----------------------------------------------------------------------
    # Guard: an active batch in another project blocks preparation (PRD.md
    # §7). Then, if this project's BATCH_IN_PROGRESS.json already exists,
    # only allow overwriting it when it is untouched (no run in it has a
    # result directory on disk).
    # -----------------------------------------------------------------------
    conflict = _project.active_batch_conflict(project)
    if conflict is not None:
        raise RuntimeError(conflict)

    stale_batch_dir = None
    if os.path.exists(batch_file):
        if _batch_has_started(batch_file, project):
            raise RuntimeError(
                "BATCH_IN_PROGRESS.json already exists and has runs "
                "in progress or completed.\n"
                "  Use 'jbr-execute' to run it, or 'jbr-execute --abort' to discard it."
            )
        print(
            "Note: BATCH_IN_PROGRESS.json exists but has not been started; "
            "overwriting it."
        )
        try:
            with open(batch_file, encoding="utf-8") as fh:
                old_batchid = json.load(fh).get("batchid")
        except (OSError, ValueError):
            old_batchid = None
        if old_batchid:
            stale_batch_dir = _project.batch_dir(project, old_batchid)

    # Frozen copies are stored under their base name (spec-prepare.md §3
    # step 3), so two inputs with the same base name would collide.
    base_names = [os.path.basename(p) for p in run_json_paths]
    duplicates = sorted({b for b in base_names if base_names.count(b) > 1})
    if duplicates:
        raise ValueError(
            "Two or more .run.toml files have the same base name: "
            + ", ".join(duplicates)
        )

    # -----------------------------------------------------------------------
    # Step 1a: Load host config (it holds the benchmark dirs, PRD.md D-83;
    #          their checks come after we know which benchmark types are
    #          referenced, so deferred to post-expansion)
    # -----------------------------------------------------------------------
    hostname = _hostname if _hostname is not None else socket.gethostname()
    hpath = _host_path(reporoot, hostname)
    if not os.path.exists(hpath):
        default_hpath = _host_path(reporoot, "default")
        if not os.path.exists(default_hpath):
            raise RuntimeError(
                f"Host config not found for hostname '{hostname}': '{hpath}', "
                f"and no default host config '{default_hpath}' either"
            )
        print(f"Note: no host config for '{hostname}' ('{hpath}'); "
              f"using '{default_hpath}'.")
        hpath = default_hpath
    host_config = load_host_json(hpath)

    # -----------------------------------------------------------------------
    # Step 1b: Load and validate every .run.toml file, in the order given on
    # the command line. Each file's own batch-level fields apply only to
    # that file's own entries — nothing is merged across files (PRD.md D-61).
    # Benchgroup and heap-sweep expansion (i, ii below) therefore also happen
    # per file, before concatenation, since heapsize_factors is one of those
    # per-file batch-level fields.
    # -----------------------------------------------------------------------
    flat_entries = []  # list of dicts, one per resolved bench entry (pre-sweep), all files
    file_repeats = {}  # run_json_path -> file-level 'repeat', for files that set it
    file_collector_settings = {}  # source file base name -> its raw collector settings

    for run_json_path in run_json_paths:
        run_json_path = os.path.abspath(run_json_path)
        run_data = load_run_json(run_json_path)

        file_heapsize_factors = run_data.get("heapsize_factors")  # list[float] or None
        file_dynamic_heap = run_data.get("dynamic_heap", False)
        file_top_flags = run_data.get("jvmargs", [])
        file_tags = run_data.get("tags", [])
        file_jvm_env = run_data.get("jvmenv", {})
        file_do_cpu_pinning = run_data.get("do_cpu_pinning", False)
        file_use_ramdisk = run_data.get("use_ramdisk", False)
        file_dacapo_args = run_data.get("dacapo_args", [])
        # Batch-wide (PRD.md D-84): kept per file only to be resolved and
        # compared once all files are read (step 1c).
        file_collector_settings[os.path.basename(run_json_path)] = {
            "collectors": run_data["collectors"],
            "perf_events_preset": run_data["perf_events_preset"],
        }
        file_dacapo_iterations = run_data.get("dacapo_iterations")
        raw_runs = run_data["runs"]
        if "repeat" in run_data:
            file_repeats[run_json_path] = run_data["repeat"]

        # --- Bench/benchgroup resolution and expansion (step 1b-i) ---
        # bench/benchgroup is batch-level only (PRD.md D-72): resolve this
        # file's bench list once, then run every entry against every bench.
        file_benches = []  # (bench_name, bench_data, bench_group or None)
        if "bench" in run_data:
            for bench_name in run_data["bench"]:
                bpath = _bench_path(reporoot, bench_name)
                if not os.path.exists(bpath):
                    raise ValueError(f"Bench file not found: '{bpath}'")
                file_benches.append((bench_name, load_bench_json(bpath), None))
        else:
            group_name = run_data["benchgroup"]
            gpath = _benchgroup_path(reporoot, group_name)
            if not os.path.exists(gpath):
                raise ValueError(f"Benchgroup file not found: '{gpath}'")
            group_data = load_benchgroup_json(gpath)
            for member in group_data["members"]:
                bpath = _bench_path(reporoot, member)
                if not os.path.exists(bpath):
                    raise ValueError(
                        f"Bench file not found for group member '{member}': '{bpath}'"
                    )
                file_benches.append((member, load_bench_json(bpath), group_name))

        file_entries = []  # this file's own resolved entries, pre-sweep

        for raw in raw_runs:
            common_fields = {
                "jdkname": raw["jdkname"],
                "profiles": raw.get("profiles", []),
                "run_entry_flags": raw.get("jvmargs", []),
                "run_entry_tags": raw.get("tags", []),
                "run_entry_jvm_env": raw.get("jvmenv", {}),
                "run_entry_do_cpu_pinning": raw.get("do_cpu_pinning"),
                "run_entry_use_ramdisk": raw.get("use_ramdisk"),
                "run_entry_dacapo_args": raw.get("dacapo_args"),
                "run_entry_dacapo_iterations": raw.get("dacapo_iterations"),
                "source_file": os.path.basename(run_json_path),
                "batch_top_flags": file_top_flags,
                "batch_tags": file_tags,
                "batch_jvm_env": file_jvm_env,
                "batch_do_cpu_pinning": file_do_cpu_pinning,
                "batch_use_ramdisk": file_use_ramdisk,
                "batch_dacapo_args": file_dacapo_args,
                "batch_dacapo_iterations": file_dacapo_iterations,
            }

            for bench_name, bench_data, group_name in file_benches:
                file_entries.append({
                    **common_fields,
                    "bench_name": bench_name,
                    "bench_data": bench_data,
                    "bench_group": group_name,
                })

        # --- Heap-sweep expansion (step 1b-ii), using this file's own
        # heapsize_factors only ---
        if file_heapsize_factors is not None:
            # Verify all .bench.toml files have minheap
            for entry in file_entries:
                if "minheap" not in entry["bench_data"]:
                    raise ValueError(
                        f"'heapsize_factors' is set in '{run_json_path}' but bench "
                        f"'{entry['bench_name']}' has no 'minheap' field"
                    )

            swept_entries = []
            for entry in file_entries:
                minheap = entry["bench_data"]["minheap"]
                for factor in file_heapsize_factors:
                    heap_mb = round(factor * minheap)
                    if file_dynamic_heap:
                        sweep_flags = [f"-Xmx{heap_mb}m"]
                    else:
                        sweep_flags = [
                            f"-Xms{heap_mb}m",
                            f"-Xmx{heap_mb}m",
                            "-XX:+AlwaysPreTouch",
                        ]
                    new_entry = dict(entry)
                    new_entry["sweep_flags"] = sweep_flags
                    new_entry["heapsize_factor"] = factor
                    swept_entries.append(new_entry)
            file_entries = swept_entries
        else:
            for entry in file_entries:
                entry["sweep_flags"] = None
                entry["heapsize_factor"] = None

        flat_entries.extend(file_entries)

    # Load profile flags now (validate all referenced profiles exist), across
    # every file's entries.
    profile_cache = {}  # name -> list[str]

    def _get_profile_flags(name: str) -> list:
        if name not in profile_cache:
            ppath = _profile_path(reporoot, name)
            if not os.path.exists(ppath):
                raise ValueError(f"Profile file not found: '{ppath}'")
            profile_cache[name] = load_profile_json(ppath)["flags"]
        return profile_cache[name]

    for entry in flat_entries:
        for pname in entry["profiles"]:
            _get_profile_flags(pname)  # validates existence

    # -----------------------------------------------------------------------
    # Step 1a (deferred): Verify benchmark jars exist for referenced types
    # -----------------------------------------------------------------------
    bench_types_used = {e["bench_data"]["benchmark"] for e in flat_entries}

    if "dacapo" in bench_types_used:
        dacapo_dir = host_config.get("dacapo_dir")
        if not dacapo_dir:
            raise ValueError(
                f"'dacapo_dir' is required in the host config '{hpath}' for dacapo benchmarks"
            )
        chopin_jars = _glob.glob(os.path.join(dacapo_dir, "dacapo-*chopin.jar"))
        if not chopin_jars:
            raise ValueError(
                f"No dacapo-*chopin.jar found in dacapo_dir='{dacapo_dir}'"
            )
        plugin_jar = os.path.join(reporoot, "dacapo-timestamp-plugin", "dacapoExtension.jar")
        if not os.path.exists(plugin_jar):
            raise ValueError(
                f"dacapo timestamp plugin not found: '{plugin_jar}'"
            )

    if "specjbb" in bench_types_used:
        specjbb_dir = host_config.get("specjbb_dir")
        if not specjbb_dir:
            raise ValueError(
                f"'specjbb_dir' is required in the host config '{hpath}' for specjbb benchmarks"
            )
        specjbb_jar = os.path.join(specjbb_dir, "specjbb2015.jar")
        if not os.path.exists(specjbb_jar):
            raise ValueError(
                f"specjbb2015.jar not found in specjbb_dir='{specjbb_dir}'"
            )

    # -----------------------------------------------------------------------
    # Step 1c: Host config settings (loaded in step 1a)
    # -----------------------------------------------------------------------
    ramdisk_path = host_config.get("ramdisk_path")
    if ramdisk_path is not None and not os.path.isdir(ramdisk_path):
        raise ValueError(
            f"'ramdisk_path' does not exist or is not a directory: '{ramdisk_path}'"
        )

    maxheap = host_config.get("maxheap")  # may be None
    cpu_list = host_config.get("cpu_list")  # may be None; syntax already validated by load_host_json
    cpu_list_harness = host_config.get("cpu_list_harness")  # ditto

    sudo_mode = bool(cli_options.get("sudo_mode", False))

    # Machine stabilization weakens security until reboot, so it is an explicit
    # opt-in on every jbr-prepare: --stabilize-machine, together with --sudo.
    # There is no host config default (PRD.md D-88).
    stabilize_machine = bool(cli_options.get("stabilize_machine", False))
    if stabilize_machine and not sudo_mode:
        raise ValueError("--stabilize-machine requires --sudo")

    # Resolve effective do_cpu_pinning per entry: per-run override wins over
    # that entry's own file's batch-level default (same pattern as
    # dacapo_iterations/collector_interval).
    for entry in flat_entries:
        override = entry.get("run_entry_do_cpu_pinning")
        entry["do_cpu_pinning"] = override if override is not None else entry["batch_do_cpu_pinning"]

    # Resolve effective use_ramdisk per entry: per-run override wins over
    # that entry's own file's batch-level default (same pattern as
    # do_cpu_pinning). Default: false.
    for entry in flat_entries:
        override = entry.get("run_entry_use_ramdisk")
        entry["use_ramdisk"] = override if override is not None else entry["batch_use_ramdisk"]

    # Resolve effective dacapo_args per entry: per-run override wins over
    # that entry's own file's batch-level default (same pattern as
    # do_cpu_pinning).
    for entry in flat_entries:
        override = entry.get("run_entry_dacapo_args")
        entry["dacapo_args"] = override if override is not None else entry["batch_dacapo_args"]

    # Resolve each input file's collector settings — its "collectors" /
    # "collectors_preset", else the default preset, taken whole
    # (spec-prepare.md §1.4.1), and its perf events preset (§1.4.2). Only the
    # presets actually used are loaded; a missing one is a hard error. The
    # settings are batch-wide (PRD.md D-84): every file must resolve to the
    # same ones, or the batch is rejected. Machine support and restrict_to are
    # checked later (step 1d-collectors), once the JDK types are known.
    preset_names = {collectors_preset_needed(s["collectors"])
                    for s in file_collector_settings.values()} - {None}
    presets = {n: load_collector_preset(reporoot, n) for n in sorted(preset_names)}
    perf_preset_names = {s["perf_events_preset"]
                         for s in file_collector_settings.values()} - {None}
    perf_presets = {n: load_perf_events_preset(reporoot, n) for n in sorted(perf_preset_names)}
    cli_collector_interval = cli_options.get("collector_interval")
    resolved_by_file = {
        name: _resolve_collector_settings(s, presets, perf_presets, cli_collector_interval)
        for name, s in file_collector_settings.items()
    }
    if len(set(resolved_by_file.values())) > 1:
        raise ValueError(
            "the input files' collector settings differ (collectors / collectors_preset, "
            "perf_events_preset); they are batch-wide and must "
            "agree: " + ", ".join(sorted(resolved_by_file)))
    settings = next(iter(resolved_by_file.values()))
    requested_collectors = list(settings.enabled)
    collector_interval = (cli_collector_interval if cli_collector_interval is not None
                          else settings.interval)
    collector_compression = settings.compression
    perf_preset_unused = ("perf" not in requested_collectors
                          and any(s["perf_events_preset"] is not None
                                  for s in file_collector_settings.values()))

    if collector_compression and shutil.which("zstd") is None:
        raise RuntimeError(
            "Collector compression ('collectors.compression') requested but the 'zstd' "
            "binary was not found on PATH"
        )

    # CPU pinning support: an incomplete host config ('cpu_list' or
    # 'cpu_list_harness' missing) or a failing 'taskset' probe all mean the
    # same thing — this host doesn't support pinning right now. None of these
    # abort the batch; instead, warn and disable do_cpu_pinning for the
    # affected runs.
    if any(e["do_cpu_pinning"] for e in flat_entries):
        if cpu_list is None:
            print(
                f"WARNING: one or more runs request CPU pinning ('do_cpu_pinning'), but host "
                f"'{hostname}' has no 'cpu_list' configured in '{hpath}' — disabling"
            )
            for entry in flat_entries:
                entry["do_cpu_pinning"] = False
        elif cpu_list_harness is None:
            print(
                f"WARNING: one or more runs request CPU pinning ('do_cpu_pinning'), but host "
                f"'{hostname}' has no 'cpu_list_harness' configured in '{hpath}' — disabling"
            )
            for entry in flat_entries:
                entry["do_cpu_pinning"] = False
        elif not _check_taskset_supported(cpu_list, run_taskset=_run_taskset):
            print(
                f"WARNING: one or more runs request CPU pinning ('do_cpu_pinning'), but "
                f"'taskset --cpu-list {cpu_list} sleep 0' failed on host '{hostname}' — disabling"
            )
            for entry in flat_entries:
                entry["do_cpu_pinning"] = False

    # Warn — but don't abort — if the running kernel isn't isolating exactly
    # those CPUs via 'isolcpus='. Deactivated for now.
    # if any(e["do_cpu_pinning"] for e in flat_entries):
    #     _check_isolcpus(cpu_list, read_cmdline=_read_cmdline)

    if ramdisk_path is None:
        if any(e["use_ramdisk"] for e in flat_entries):
            print(
                f"WARNING: one or more runs request a ramdisk ('use_ramdisk'), but host "
                f"'{hostname}' has no 'ramdisk_path' configured in '{hpath}' — disabling"
            )
            for entry in flat_entries:
                entry["use_ramdisk"] = False

    # -----------------------------------------------------------------------
    # Step 1d: Load JDK configs, run java -version, detect type
    # -----------------------------------------------------------------------
    jdk_names = list({e["jdkname"] for e in flat_entries})
    jdk_info = {}  # jdkname -> {java_home, feature_version, jdk_type, jdktype_data}

    for jdkname in jdk_names:
        jpath = _jdk_path(reporoot, jdkname)
        if not os.path.exists(jpath):
            raise RuntimeError(
                f"JDK config not found for '{jdkname}': '{jpath}'"
            )
        jdk_data = load_jdk_json(jpath)
        java_home = jdk_data["java_home"]
        java_binary = os.path.join(java_home, "bin", "java")

        feature_version, jdk_type = _detect_java_version_and_type(
            java_binary, run_java_version=_run_java_version
        )

        jdktype_path = _jdktype_path(reporoot, jdk_type)
        if not os.path.exists(jdktype_path):
            raise RuntimeError(
                f"JDK type config not found for type '{jdk_type}': '{jdktype_path}'"
            )
        jdktype_data = load_jdktype_json(jdktype_path)

        jdk_info[jdkname] = {
            "java_home": java_home,
            "feature_version": feature_version,
            "jdk_type": jdk_type,
            "jdktype_data": jdktype_data,
        }

    # -----------------------------------------------------------------------
    # Step 1d-collectors: every collector must have a
    # config/collectors/<name>.collector.toml (missing = hard error). The
    # enabled set is batch-wide (PRD.md D-84): a requested collector this
    # machine cannot run (probed, PRD.md D-82), or whose restrict_to does not
    # cover the JDK of every run, is disabled for the whole batch, with a
    # warning, rather than failing the batch.
    # -----------------------------------------------------------------------
    collector_configs = {n: load_collector_config(reporoot, cfg) for n, cfg in COLLECTORS.items()}

    # perf runs unprivileged (PRD.md D-85); what it can count depends on
    # perf_event_paranoid, which machine stabilization sets to 0 at batch start.
    will_stabilize = bool(stabilize_machine and sudo_mode and platform.system() == "Linux")
    perf_paranoid = None if will_stabilize else _read_perf_event_paranoid()

    collectors_enabled = []
    for cname in requested_collectors:
        reason = unavailable_reason(cname)
        if reason is None and cname == "perf" and perf_paranoid is not None \
                and perf_paranoid >= _PERF_PARANOID_BLOCKED:
            reason = (f"perf_event_paranoid is {perf_paranoid}, perf needs at most "
                      f"{_PERF_PARANOID_BLOCKED - 1} without root; machine stabilization "
                      f"(--stabilize-machine --sudo) would set 0")
        if reason is not None:
            print(f"WARNING: the batch requests collector '{cname}', but it cannot run on "
                  f"this machine ({reason}) — disabling it for the whole batch")
            continue
        excluded = sorted({(jdk_info[e["jdkname"]]["jdk_type"], jdk_info[e["jdkname"]]["feature_version"])
                           for e in flat_entries
                           if not collector_applies(collector_configs[cname],
                                                    jdk_info[e["jdkname"]]["jdk_type"],
                                                    jdk_info[e["jdkname"]]["feature_version"])})
        if excluded:
            jdks = ", ".join(f"'{jt}' version {fv}" for jt, fv in excluded)
            print(f"WARNING: the batch requests collector '{cname}', but it does not apply to "
                  f"JDK type {jdks} used by some runs — disabling it for the whole batch")
            continue
        collectors_enabled.append(cname)

    if "perf" in collectors_enabled and perf_paranoid == _PERF_PARANOID_USER_ONLY:
        print(f"WARNING: perf_event_paranoid is {perf_paranoid} and the machine will not be "
              f"stabilized — perf will count user space only, not kernel events "
              f"(enable with --stabilize-machine --sudo)")

    # System.map raw logs are large; without compression they fill the disk.
    if "system_map" in collectors_enabled and not collector_compression:
        print(
            "WARNING: the batch enables the System.map collector without "
            "'collectors.compression' — the raw system.map.log will be large"
        )

    # Thread.print raw logs grow with thread count and dump frequency; without
    # compression they can fill the disk (same concern as System.map above).
    if "thread_print" in collectors_enabled and not collector_compression:
        print(
            "WARNING: the batch enables the Thread.print collector without "
            "'collectors.compression' — the raw thread-print.log will be large"
        )

    # -----------------------------------------------------------------------
    # Step 1d-perf: resolve the batch's perf events (spec-prepare.md §1.4.2) —
    # None (perf's default set) without a perf_events_preset. They are checked
    # by jbr-execute at batch start, after machine stabilization (PRD.md D-86).
    # -----------------------------------------------------------------------
    if perf_preset_unused:
        print("WARNING: the batch sets 'perf_events_preset', but its collector settings "
              "do not enable 'perf' — ignoring it")
    perf_events = (list(settings.perf_events)
                   if settings.perf_events is not None and "perf" in collectors_enabled
                   else None)

    # -----------------------------------------------------------------------
    # Step 1e: Version filtering
    # -----------------------------------------------------------------------
    filtered_entries = []
    for entry in flat_entries:
        bd = entry["bench_data"]
        jdk_ver = jdk_info[entry["jdkname"]]["feature_version"]
        min_ver = bd.get("min_java_version")
        max_ver = bd.get("max_java_version")

        if min_ver is not None and jdk_ver < min_ver:
            print(
                f"WARNING: skipping bench '{entry['bench_name']}' with jdk '{entry['jdkname']}' "
                f"(JDK version {jdk_ver} < min_java_version {min_ver})"
            )
            continue
        if max_ver is not None and jdk_ver > max_ver:
            print(
                f"WARNING: skipping bench '{entry['bench_name']}' with jdk '{entry['jdkname']}' "
                f"(JDK version {jdk_ver} > max_java_version {max_ver})"
            )
            continue
        filtered_entries.append(entry)

    flat_entries = filtered_entries

    # -----------------------------------------------------------------------
    # Steps 1f–1h: Flag assembly, conflict check, heap_size
    # -----------------------------------------------------------------------
    cli_jvmargs = cli_options.get("jvmargs", [])

    for entry in flat_entries:
        info = jdk_info[entry["jdkname"]]
        jdktype = info["jdktype_data"]
        profile_flags = [_get_profile_flags(p) for p in entry["profiles"]]

        flags = assemble_flags(
            bench_flags=entry["bench_data"].get("jvmargs", []),
            sweep_flags=entry["sweep_flags"],
            profile_flags_list=profile_flags,
            run_top_flags=entry["batch_top_flags"],
            run_entry_flags=entry["run_entry_flags"],
            cli_flags=cli_jvmargs,
            jdktype_extra_flags=jdktype["extra_jvmargs"],
            gc_log_flag=jdktype["gc_log_option"],
        )

        if "nmt" in collectors_enabled:
            flags = flags + _NMT_FLAGS

        check_flag_conflicts(flags)  # step 1g — raises ValueError on conflict

        try:
            heap_size = parse_xmx_mb(flags)  # step 1h
        except ValueError as exc:
            raise ValueError(
                f"Run '{entry['bench_name']}' (jdk '{entry['jdkname']}'): {exc}"
            )

        entry["assembled_flags"] = flags
        entry["heapsize"] = heap_size

    # -----------------------------------------------------------------------
    # Step 1i: Maxheap filter
    # -----------------------------------------------------------------------
    if maxheap is not None:
        kept = []
        for entry in flat_entries:
            if entry["heapsize"] > maxheap:
                print(
                    f"WARNING: skipping bench '{entry['bench_name']}' with jdk '{entry['jdkname']}' "
                    f"(heapsize {entry['heapsize']} MB > maxheap {maxheap} MB)"
                )
            else:
                kept.append(entry)
        flat_entries = kept

    # -----------------------------------------------------------------------
    # Repeat expansion: interleave entries by round, across the full
    # concatenated (all-files) run list — not per file.
    # e.g. [A, B] with repeat=3 -> [A(0), B(0), A(1), B(1), A(2), B(2)]
    # -----------------------------------------------------------------------
    # Effective repeat: CLI wins; else the files' 'repeat' (must agree across
    # files that set it); else 1.
    repeat = cli_options.get("repeat")
    if repeat is None:
        distinct = sorted(set(file_repeats.values()))
        if len(distinct) > 1:
            listing = ", ".join(
                f"'{os.path.basename(p)}': {v}" for p, v in file_repeats.items()
            )
            raise ValueError(
                f"Input files set different 'repeat' values ({listing}); "
                f"make them agree or give --repeat"
            )
        repeat = distinct[0] if distinct else 1
    if repeat > 1:
        repeated_entries = []
        for round_no in range(repeat):
            for entry in flat_entries:
                new_entry = dict(entry)
                new_entry["repeat_no"] = round_no
                repeated_entries.append(new_entry)
        flat_entries = repeated_entries
    else:
        for entry in flat_entries:
            entry["repeat_no"] = 0

    # -----------------------------------------------------------------------
    # Steps 2–3: GUIDs, schema construction, atomic write
    # -----------------------------------------------------------------------
    batch_id = str(uuid.uuid4())
    started_at = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")

    # CLI --tags is global to the whole invocation; the dacapo_iterations CLI
    # override likewise wins over everything, else falls back to the
    # per-run-entry override, else that entry's own file's batch-level default
    # (entry["batch_dacapo_iterations"]). The collector interval was resolved
    # batch-wide in step 1c.
    cli_tags = cli_options.get("tags", [])
    cli_dacapo_iterations = cli_options.get("dacapo_iterations")

    runs_out = []
    for seq, entry in enumerate(flat_entries, start=1):
        run_id = str(uuid.uuid4())
        bd = entry["bench_data"]
        info = jdk_info[entry["jdkname"]]

        # Effective tags: union of cli_tags + this entry's file-level batch
        # tags + run_entry_tags
        effective_tags = list(dict.fromkeys(cli_tags + entry["batch_tags"] + entry["run_entry_tags"]))

        # Effective jvmenv: this entry's file-level batch env merged with
        # per-run, per-run wins on collision
        effective_jvm_env = {**entry["batch_jvm_env"], **entry["run_entry_jvm_env"]}

        run_entry = {
            "runid": run_id,
            "seq": seq,
            "repeat_no": entry["repeat_no"],
            "source_file": entry["source_file"],
            "bench_json": entry["bench_name"],
            "jdkname": entry["jdkname"],
            "java_home": info["java_home"],
            "jdk_type": info["jdk_type"],
            "jdk_major": info["feature_version"],
            "profiles": entry["profiles"],
            "tags": effective_tags,
            "benchmark": bd["benchmark"],
            "heapsize": entry["heapsize"],
            "result_dir": f"{_project.RUNS_DIRNAME}/{run_id}",
            "jvmargs": entry["assembled_flags"],
            "jvmenv": effective_jvm_env,
            "do_cpu_pinning": entry["do_cpu_pinning"],
            "use_ramdisk": entry["use_ramdisk"],
        }

        # Optional fields only for dacapo
        if bd["benchmark"] == "dacapo":
            # Effective dacapo_iterations: CLI wins over everything; else the
            # per-run-entry override; else this entry's file-level batch
            # default; else the .bench.toml's own 'iterations' field.
            effective_batch_dacapo_iterations = (
                cli_dacapo_iterations if cli_dacapo_iterations is not None
                else entry["batch_dacapo_iterations"]
            )
            effective_dacapo_iterations = (
                entry["run_entry_dacapo_iterations"]
                if cli_dacapo_iterations is None and entry["run_entry_dacapo_iterations"] is not None
                else effective_batch_dacapo_iterations
            )
            run_entry["sub_benchmark"] = bd["sub_benchmark"]
            run_entry["datasize"] = bd.get("datasize", "default")
            run_entry["iterations"] = (
                effective_dacapo_iterations if effective_dacapo_iterations is not None
                else bd.get("iterations", 10)
            )
            run_entry["dacapo_dir"] = host_config["dacapo_dir"]
            run_entry["dacapo_args"] = entry["dacapo_args"]

        if bd["benchmark"] == "specjbb":
            run_entry["specjbb_dir"] = host_config["specjbb_dir"]

        # bench_group only when expanded from a group
        if entry["bench_group"] is not None:
            run_entry["bench_group"] = entry["bench_group"]

        # heapsize_factor only for heap-swept runs
        if entry["heapsize_factor"] is not None:
            run_entry["heapsize_factor"] = entry["heapsize_factor"]

        runs_out.append(run_entry)

    batch_dict = {
        "batchid": batch_id,
        "started_at": started_at,
        "hostname": hostname,
        **({"ramdisk_path": ramdisk_path} if ramdisk_path is not None else {}),
        **({"cpu_list": cpu_list} if cpu_list is not None else {}),
        **({"cpu_list_harness": cpu_list_harness} if cpu_list_harness is not None else {}),
        "sudo_mode": sudo_mode,
        "stabilize_machine": stabilize_machine,
        "collectors": {
            "interval_secs": collector_interval,
            "compression": collector_compression,
            "enabled": collectors_enabled,
            "perf_events": perf_events,
        },
        "runs": runs_out,
    }

    # Step 3: frozen copies of the input files, then the batch file.
    if stale_batch_dir is not None and os.path.isdir(stale_batch_dir):
        shutil.rmtree(stale_batch_dir)
    frozen_dir = _project.batch_dir(project, batch_id)
    os.makedirs(frozen_dir, exist_ok=True)
    for run_json_path in run_json_paths:
        shutil.copyfile(run_json_path,
                        os.path.join(frozen_dir, os.path.basename(run_json_path)))
    write_batch(batch_dict, project)

    # Effective collectors, so what gets collected is visible before the batch runs.
    print(f"Collectors: {','.join(collectors_enabled) or '(none)'}  "
          f"interval={collector_interval}s  "
          f"compression={'on' if collector_compression else 'off'}")

    # Machine stabilization, so an unstabilized batch is obvious before it runs.
    if not stabilize_machine:
        print("WARNING: machine will not be stabilized (enable with --stabilize-machine --sudo)")
    elif platform.system() != "Linux":
        print("WARNING: --stabilize-machine is given but this is not Linux "
              "— jbr-execute will skip machine stabilization")
    else:
        print("Machine stabilization: on — ASLR off, perf_event_paranoid=0, swap off, "
              "strict overcommit, services stopped, ...; this weakens security until "
              "reboot")

    # Step 16: Success message
    n = len(runs_out)
    print(f"Preparation complete. {n} run(s) written to {batch_file}.")
    print("Use 'jbr-execute' to execute, or 'jbr-execute --abort' to discard.")

    return batch_dict


class CollectorSettings(NamedTuple):
    """One input file's resolved collector settings (PRD.md D-84). Fields
    without effect are None, so that equal tuples mean equal settings: the
    interval when the CLI overrides it, the perf events when perf is not
    requested."""
    enabled: tuple
    interval: object            # int, or None when --collector-interval is given
    compression: bool
    perf_events: object         # tuple of event names, or None for perf's default set


def _resolve_collector_settings(raw: dict, presets: dict, perf_presets: dict,
                                cli_interval) -> CollectorSettings:
    """Resolve one file's raw collector settings (as kept by run_prepare)."""
    resolved = resolve_collectors(raw["collectors"], presets)
    preset = raw["perf_events_preset"] if "perf" in resolved["enabled"] else None
    events = tuple(perf_presets[preset]) if preset is not None else None
    return CollectorSettings(
        enabled=tuple(resolved["enabled"]),
        interval=resolved["interval"] if cli_interval is None else None,
        compression=resolved["compression"],
        perf_events=events,
    )


def write_batch(batch_dict: dict, project_dir: str) -> None:
    """
    Atomically write batch_dict as BATCH_IN_PROGRESS.json under project_dir.
    Uses temp file + os.replace() per decision W-3.
    """
    os.makedirs(project_dir, exist_ok=True)
    dest = _batch_path(project_dir)
    fd, tmp_path = tempfile.mkstemp(dir=project_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(batch_dict, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, dest)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
