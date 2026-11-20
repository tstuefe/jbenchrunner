# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Core execute-phase logic for jbr-execute.

Implements the testable core module for the execute phase, following
spec-execute.md §1–§5.  The public entry point run_execute(options) is
called by libexec/jbr-execute.py (run via the bin/jbr-execute wrapper).
"""

import datetime
import json
import os
import platform
import re
import subprocess
import tempfile

from common import collector_timestamp
from common import project as _project
from common.config_loader import collector_applies, load_collector_config
from common.jdk_detect import parse_java_version_output
from failure_classifier import classify_failure
from machine_state import capture_stabilization_state, stabilization_warnings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BATCH_FILENAME = "BATCH_IN_PROGRESS.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _batch_path(project_dir: str) -> str:
    return os.path.join(project_dir, _BATCH_FILENAME)


# ---------------------------------------------------------------------------
# Public API — Sub-Task 2: batch loading and guards
# ---------------------------------------------------------------------------

def load_batch(project_dir: str) -> dict:
    """Read and return the parsed BATCH_IN_PROGRESS.json dict of a project.

    Raises RuntimeError with the spec-mandated message if the file is absent.
    """
    path = _batch_path(project_dir)
    if not os.path.exists(path):
        raise RuntimeError(
            "Error: no batch found. Run 'jbr-prepare <file.run.toml>' first."
        )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check_hostname(batch: dict, current_hostname: str) -> None:
    """Raise RuntimeError if the batch hostname differs from current_hostname.

    Error message is verbatim from spec §1.3 step 2.
    """
    original = batch.get("hostname", "")
    if original != current_hostname:
        raise RuntimeError(
            f"Error: this batch was prepared on '{original}' but the current host is '{current_hostname}'.\n"
            "Executing a batch on a different machine is not supported.\n"
            "Use 'jbr-execute --abort' to abandon it."
        )


def check_batch_format(batch: dict) -> None:
    """Raise RuntimeError if the batch carries its collector settings per run.

    The settings are batch-wide (PRD.md D-84): a batch prepared before that
    has no top-level ``collectors`` and is refused rather than read in the
    old format.
    """
    if "collectors" not in batch:
        raise RuntimeError(
            "Error: this batch was prepared by an older jbr-prepare (collector settings per "
            "run instead of batch-wide).\n"
            "Prepare it again with 'jbr-prepare', or use 'jbr-execute --abort' to abandon it."
        )


def is_pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID appears to be alive.

    Uses os.kill(pid, 0): a ProcessLookupError means the process is gone;
    a PermissionError means the process exists but we lack permission to
    signal it, which is treated as "alive".
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def liveness_check(batch: dict, project_dir: str) -> tuple:
    """Scan run-info.json files and test PIDs to determine whether a run is live.

    Algorithm per spec §1.1:
    1. For each run entry look for <project_dir>/<result_dir>/run-info.json with
       "status": "RUNNING".
    2. For each such entry test whether the recorded pid is still alive via
       os.kill(pid, 0).
    3. Return (is_running, n_complete, started_at).
       is_running is True only when at least one RUNNING-status PID is alive.
       n_complete is the count of runs whose status is DONE or FAILED.
       started_at comes from the batch-level field.
    """
    n_complete = 0
    is_running = False

    for run_entry in batch.get("runs", []):
        result_dir = run_entry.get("result_dir", "")
        run_info_path = os.path.join(project_dir, result_dir, "run-info.json")

        if not os.path.exists(run_info_path):
            # PENDING — no result directory yet
            continue

        try:
            with open(run_info_path, encoding="utf-8") as fh:
                run_info = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue

        status = run_info.get("status", "")

        if status in ("DONE", "FAILED"):
            n_complete += 1
            continue

        if status == "RUNNING":
            pid = run_info.get("pid")
            if pid is not None and is_pid_alive(pid):
                is_running = True

    started_at = batch.get("started_at", "")

    return (is_running, n_complete, started_at)


# ---------------------------------------------------------------------------
# Public API — Sub-Task 3: per-run status derivation
# ---------------------------------------------------------------------------

def get_run_status(run_entry: dict, project_dir: str) -> str:
    """Derive the status of a single run entry per spec §1.2.

    Returns one of: "PENDING", "RUNNING", "DONE", "FAILED", "STOPPED".

    The result directory path is constructed by joining project_dir (the
    project root) with the run entry's result_dir field (e.g. "results/<UUID>").

    PENDING is implicit: if the result directory does not exist, the run has
    never been started.  Otherwise, run-info.json is read and its "status"
    field is returned.
    """
    result_dir = run_entry.get("result_dir", "")
    run_dir = os.path.join(project_dir, result_dir)

    if not os.path.isdir(run_dir):
        return "PENDING"

    run_info_path = os.path.join(run_dir, "run-info.json")
    with open(run_info_path, encoding="utf-8") as fh:
        run_info = json.load(fh)
    return run_info["status"]


# ---------------------------------------------------------------------------
# Internal helpers — Sub-Task 4: atomic JSON writer
# ---------------------------------------------------------------------------

def _atomic_write_json(path: str, data: dict) -> None:
    """Write *data* as JSON to *path* atomically (temp file + os.replace).

    The temporary file is created in the same directory as *path* so that the
    rename is guaranteed to be atomic on POSIX (same filesystem).
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file if anything goes wrong before the rename.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API — Sub-Task 4: JDK detection and JSON writers
# ---------------------------------------------------------------------------

def detect_jdk(java_binary: str) -> dict:
    """Run ``<java_binary> -version`` and return a dict with JDK metadata.

    The combined stdout+stderr output is captured as ``full_version_string``.
    Parsed fields ``version``, ``major`` and ``type`` come from
    ``common.jdk_detect.parse_java_version_output`` (shared with jbr-prepare).

    Raises RuntimeError on non-zero exit code, unparseable output, or if the binary cannot be
    executed (FileNotFoundError / PermissionError), which causes the batch to
    abort immediately per spec §2 step e.
    """
    try:
        result = subprocess.run(
            [java_binary, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise RuntimeError(
            f"JDK detection failed: could not execute '{java_binary}': {exc}"
        ) from exc

    full_version_string = result.stdout

    if result.returncode != 0:
        raise RuntimeError(
            f"JDK detection failed: '{java_binary} -version' exited with code "
            f"{result.returncode}.\nOutput:\n{full_version_string}"
        )

    try:
        parsed = parse_java_version_output(full_version_string)
    except RuntimeError as exc:
        raise RuntimeError(f"JDK detection failed for '{java_binary}': {exc}") from exc

    return {"full_version_string": full_version_string, **parsed}


def check_jdk_against_prepared(jdk_info: dict, run_entry: dict) -> None:
    """Warn if the just-detected JDK type/major differs from what jbr-prepare recorded.

    The detected values are authoritative — info.json always reflects the JDK
    the benchmark actually ran with (spec-execute.md §2 step e). A mismatch
    means the JDK at ``java_home`` changed since prepare, so the run's flags,
    version filtering and collector selection were decided for a different JDK.
    Run entries from batches prepared before these fields existed are not checked.
    """
    for field, key in (("jdk_type", "type"), ("jdk_major", "major")):
        if field in run_entry and run_entry[field] != jdk_info[key]:
            print(
                f"WARNING: JDK {key} mismatch for '{run_entry['java_home']}': prepared as "
                f"'{run_entry[field]}', but java -version now reports '{jdk_info[key]}' — "
                f"the JDK changed since jbr-prepare ran",
                flush=True,
            )


_GC_HOTSPOT_RE = re.compile(r"^-XX:\+Use(?P<name>[A-Za-z0-9]+)GC$")
_GC_OPENJ9_RE = re.compile(r"^-Xgcpolicy:(?P<policy>\S+)$")


def detect_gc(jvmargs: list) -> str:
    """Deduce the GC collector name from the assembled JVM flags.

    Recognizes HotSpot-style ``-XX:+Use<Name>GC`` flags generically (e.g.
    ``-XX:+UseG1GC`` -> ``"g1gc"``, ``-XX:+UseZGC`` -> ``"zgc"``,
    ``-XX:+UseShenandoahGC`` -> ``"shenandoahgc"``) and OpenJ9-style
    ``-Xgcpolicy:<policy>`` flags (e.g. ``-Xgcpolicy:gencon`` -> ``"gencon"``).

    If more than one GC flag is present, the last one in flag order wins
    (mirrors flag_assembly.parse_xmx_mb's last-flag-wins convention).

    Returns None if no GC flag is present in *jvmargs* — the JVM's own
    built-in default then applies, which varies by JDK build/version and is
    deliberately not guessed here.
    """
    detected = None
    for flag in jvmargs:
        match = _GC_HOTSPOT_RE.match(flag)
        if match:
            detected = match.group("name").lower() + "gc"
            continue
        match = _GC_OPENJ9_RE.match(flag)
        if match:
            detected = match.group("policy").lower()
    return detected


def write_info_json(
    run_entry: dict, jdk_info: dict, batch_id: str, result_dir: str, cpu_list: str = None,
    cpu_list_harness: str = None,
) -> None:
    """Write info.json to *result_dir* per spec §4.1.

    Written atomically using _atomic_write_json.

    The *result_dir* argument is the absolute path to the run's result
    directory (already created by the caller).

    Fields sourced from *run_entry*:
      runid, repeat_no, benchmark, sub_benchmark, datasize, iterations,
      dacapo_args, tags, heapsize, heapsize_factor, jvmargs, jvmenv, jdkname,
      java_home, do_cpu_pinning, collectors

    "repeat_no" defaults to 0 if absent (BATCH_IN_PROGRESS.json written
    before the field was introduced).

    The "heap" sub-object is derived, not sourced verbatim: "heapsize" is
    run_entry's heapsize (moved under "heap" rather than top-level), and
    "gc" is deduced from jvmargs by detect_gc().

    Fields sourced from *jdk_info*:
      full_version_string, version, major, type

    *batch_id* is the UUID of the batch (BATCH_IN_PROGRESS.json's "batchid")
    this run was created under.

    *cpu_list* is the batch's cpu_list (BATCH_IN_PROGRESS.json's top-level
    "cpu_list"); recorded as info.json's "cpu_list" only when this run's
    "do_cpu_pinning" is true, else recorded as null (mirrors the
    heapsize_factor null-when-inactive convention).

    System fields (hostname, arch, os, time) are derived at
    call time.
    """
    benchmark = run_entry["benchmark"]

    # Compound name: "specjbb" or "dacapo.<sub_benchmark>"
    if benchmark == "dacapo":
        name = f"dacapo.{run_entry['sub_benchmark']}"
    else:
        name = "specjbb"

    # Absolute path to the java binary
    java_path = os.path.join(run_entry["java_home"], "bin", "java")

    data = {
        "runid":        run_entry["runid"],
        "batchid":      batch_id,
        "repeat_no":    run_entry.get("repeat_no", 0),
        "name":         name,
        "benchmark":    benchmark,
        "hostname":     platform.node(),
        "arch":         platform.machine(),
        "os":           f"{platform.system()} {platform.release()}",
        "tags":         run_entry.get("tags", []),
        "jdk": {
            "name":                run_entry["jdkname"],
            "path":                java_path,
            "full_version_string": jdk_info["full_version_string"],
            "version":             jdk_info["version"],
            "major":               jdk_info["major"],
            "type":                jdk_info["type"],
        },
        "heap": {
            "gc":        detect_gc(run_entry.get("jvmargs", [])),
            "heapsize":  run_entry["heapsize"],
        },
        "heapsize_factor": run_entry.get("heapsize_factor"),
        "jvmargs":         run_entry.get("jvmargs", []),
        "jvmenv":          run_entry.get("jvmenv", {}),
        "cpu_list":        cpu_list if run_entry.get("do_cpu_pinning") else None,
        "cpu_list_harness": cpu_list_harness if run_entry.get("do_cpu_pinning") else None,
        "collectors":      run_entry.get("collectors"),
    }

    # Mutually exclusive benchmark-specific sub-objects
    if benchmark == "dacapo":
        data["dacapo"] = {
            "sub_benchmark": run_entry["sub_benchmark"],
            "datasize":      run_entry["datasize"],
            "iterations":    run_entry["iterations"],
            "dacapo_args":   run_entry.get("dacapo_args", []),
        }
    else:
        data["specjbb"] = {
            "mode": "composite",
        }

    _atomic_write_json(os.path.join(result_dir, "info.json"), data)


def write_run_info_json(
    result_dir: str,
    *,
    status: str,
    pid: int,
    start_time: str,
    sudo_mode: bool,
    exit_code: int = None,
    duration_secs: float = None,
    status_info: str = None,
    stabilization: dict = None,
    ramdisk_path: str = None,
    ramdisk_size_mb: int = None,
    ramdisk_fs_type: str = None,
) -> None:
    """Write run-info.json to *result_dir* per spec §4.2.

    Written atomically using _atomic_write_json.

    ``exit_code`` and ``duration_secs`` are omitted from the dict when they are
    ``None`` (initial write at JVM launch).  Both are present on the final
    write after benchmark exit.  ``status_info`` is a short free-text tag
    giving more context on ``status``; omitted when ``None``.  ``stabilization``
    is the machine-state snapshot from machine_state.capture_stabilization_state,
    taken once at run start; omitted when ``None``.  ``ramdisk_path``,
    ``ramdisk_size_mb``, and ``ramdisk_fs_type`` are present only when this
    run's result directory was created on a ramdisk (spec-execute.md §6);
    each write of this file is a full overwrite, not a merge, so the caller
    must pass them again on every write within the same run to keep them.
    """
    data = {
        "status":    status,
        "pid":       pid,
        "time":      start_time,
        "sudo_mode": sudo_mode,
    }
    if exit_code is not None:
        data["exit_code"] = exit_code
    if duration_secs is not None:
        data["duration_secs"] = duration_secs
    if status_info is not None:
        data["status_info"] = status_info
    if stabilization is not None:
        data["stabilization"] = stabilization
    if ramdisk_path is not None:
        data["ramdisk_path"] = ramdisk_path
    if ramdisk_size_mb is not None:
        data["ramdisk_size_mb"] = ramdisk_size_mb
    if ramdisk_fs_type is not None:
        data["ramdisk_fs_type"] = ramdisk_fs_type

    _atomic_write_json(os.path.join(result_dir, "run-info.json"), data)

# ---------------------------------------------------------------------------
# Constants — Sub-Task 5: collector definitions
# ---------------------------------------------------------------------------

# Each entry: (name, script_filename, invoke_prefix, needs_pid, log_stem)
# invoke_prefix is the leading command ("python3" or "bash") preceding the script path.
_COLLECTOR_DEFS = [
    # (name,               script,                  cmd,       needs_pid, log_stem)
    ("cpu-temp-logger.py", "cpu-temp-logger.py",    "python3", True,      "temps"),
    ("pidstat-logger.sh",  "pidstat-logger.sh",     "bash",    True,      "pidstat"),
    ("proc-logger.py",     "proc-logger.py",        "python3", True,      "proclog"),
    ("perf-logger.sh",     "perf-logger.sh",        "bash",    True,      "perflog"),
]

# Collectors whose stdout is JSON Lines (<log_stem>.jsonl); all others write raw <log_stem>.log.
_JSONL_COLLECTORS = {"cpu-temp-logger.py"}

_COLLECTORS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collectors")

# Delay between successive collector starts when CPU pinning is active
# (spec-execute.md §5.3) — avoids all collectors hitting the harness CPUs at once.
_COLLECTOR_START_STAGGER_SECS = 0.05

# Collector logs written zstd-compressed (<file>.zst) when the run's
# collectors.compression is true (spec-execute.md §5.2), keyed by log_stem.
# The raw logs: NMT, System.map, Thread.print and proclog.
_COMPRESSED_LOG_STEMS = {"nmt", "system.map", "thread-print", "proclog"}

# Compressor process: reads the collector's stdout pipe, writes the .zst file to its
# own stdout. It ends on EOF, never on SIGTERM (see stop_collectors), and runs at the
# collectors' priority — no nice (PRD.md D-58).
_COMPRESSOR_CMD = ["zstd", "-1", "-q", "-T1", "-c"]

# Suffix of the compressor's name in the list start_collectors returns.
_COMPRESSOR_SUFFIX = "+zstd"

# Longest stop_collectors waits, after all collectors are gone, for a compressor to
# see EOF and finish its last block before killing it.
_COMPRESSOR_DRAIN_SECS = 10

# jcmd-based collectors. All share the single generic jcmd-logger.sh script,
# parameterized by the jcmd subcommand. Like _COLLECTOR_DEFS above, each is
# started only when its short name is in the run entry's "collectors.enabled"
# list (resolved at prepare time — spec-prepare.md §1.4.1).
# (name, jcmd subcommand tokens, log_stem)
_JCMD_COLLECTOR_DEFS = [
    ("nmt", ["VM.native_memory", "summary"], "nmt"),
    ("system_map", ["System.map"], "system.map"),
    ("thread_print", ["Thread.print"], "thread-print"),
]

_JCMD_LOGGER_SCRIPT = "jcmd-logger.sh"

# verify_collectors: delay after starting collectors before checking health
# (spec-execute.md §5.3) — gives the benchmark and collectors time to fail fast.
_VERIFY_COLLECTORS_DELAY_SECS = 5

# stop_collectors: grace period for self-terminating collectors to exit on
# their own before SIGTERM is sent (spec-execute.md §5.3).
_STOP_COLLECTORS_GRACE_SECS = 3

# stop_collectors: time after SIGTERM before an unresponsive collector is
# SIGKILLed (spec-execute.md §5.3).
_STOP_COLLECTORS_KILL_WAIT_SECS = 2


# ---------------------------------------------------------------------------
# Public API — Sub-Task 5: collector lifecycle management
# ---------------------------------------------------------------------------

# Short name (as in "collectors.enabled") of each _COLLECTOR_DEFS entry.
_COLLECTOR_SHORT_NAMES = {
    "cpu-temp-logger.py": "cpu_temp",
    "pidstat-logger.sh":  "pidstat",
    "proc-logger.py":     "proc",
    "perf-logger.sh":     "perf",
}


def run_perf_events(run_entry: dict):
    """
    The events perf-logger.sh counts for this run: ``collectors.perf_events``
    of the batch (spec-prepare.md §4, handed in as the run entry's
    ``collectors``), ``None`` meaning perf's default event set.
    """
    return (run_entry.get("collectors") or {}).get("perf_events")


class CollectorsNotApplicable(Exception):
    """Enabled collectors whose ``restrict_to`` does not cover the run's JDK."""

    def __init__(self, names: list):
        super().__init__(", ".join(names))
        self.names = names


def start_collectors(
    run_entry: dict,
    jvm_pid: int,
    result_dir: str,
    collector_interval: int,
    perf_events: list,
    verbose: bool = False,
    jdk_info: dict = None,
    reporoot: str = None,
    cpu_list_harness: str = None,
    collector_compression: bool = False,
) -> list:
    """Start all applicable background collector processes.

    Hard-codes the known collectors per spec §5.1.  A collector is started
    only when its short name (``_COLLECTOR_SHORT_NAMES``, or the jcmd
    collector's name) is in ``run_entry["collectors"]["enabled"]`` — the
    batch's collector set, resolved at prepare time from the .run.toml, the
    machine probe and ``restrict_to`` (spec-prepare.md §1.4.1). Every run
    starts exactly that set (PRD.md D-84).

    Each collector receives ``-i <collector_interval>``.  PID-specific
    collectors also receive ``<jvm_pid>`` as their positional argument.
    ``perf-logger.sh`` additionally receives ``--counters <comma-joined events>``
    (none when *perf_events* is ``None``: perf's default event set). No
    collector runs via sudo (PRD.md D-85).

    stdout/stderr for each collector are redirected to the log files specified
    in spec §5.2, opened inside *result_dir*.

    jcmd-based collectors (``_JCMD_COLLECTOR_DEFS``, e.g. NMT) are started via
    the single generic ``jcmd-logger.sh`` script. Each receives
    ``--java-home <run_entry["java_home"]>`` and ``--cmd <jcmd subcommand>`` in
    addition to ``-i`` and the pid.

    When *jdk_info* (as returned by ``detect_jdk``) and *reporoot* are given,
    every enabled collector's ``config/collectors/<name>.collector.toml``
    ``restrict_to`` must match the detected JDK type/major; otherwise
    ``CollectorsNotApplicable`` is raised before anything is started, since the
    run would deviate from the batch's collector set. A missing config file is
    a hard error (RuntimeError).

    When *cpu_list_harness* is given (this run's effective ``do_cpu_pinning``
    is true), every collector command is prefixed with
    ``taskset --cpu-list <cpu_list_harness>`` (child processes inherit the
    affinity), and each collector start after the first is delayed by
    ``_COLLECTOR_START_STAGGER_SECS`` to avoid a startup stampede.

    Every collector is started in its own process group (``process_group=0``):
    a terminal Ctrl-C then reaches only this process, and ``stop_collectors``
    is the single shutdown path (spec-execute.md §5.3). Unlike the JVM it is
    not put in a new session (PRD.md D-59).

    When *collector_compression* is true, a collector whose log_stem is in
    ``_COMPRESSED_LOG_STEMS`` has its stdout piped through a separate
    ``_COMPRESSOR_CMD`` process that writes ``<log_stem><ext>.zst``
    (spec-execute.md §5.2). The compressor is started first, so a missing
    ``zstd`` fails before its collector exists, and is returned as an extra
    ``(<name>+zstd, proc)`` entry right after its collector.

    Returns a list of (name, proc) tuples where proc is a subprocess.Popen
    instance.
    """
    procs = []
    enabled = set((run_entry.get("collectors") or {}).get("enabled", []))

    def _pin_and_stagger(args: list) -> list:
        if cpu_list_harness is None:
            return args
        if procs:
            time.sleep(_COLLECTOR_START_STAGGER_SECS)
        return ["taskset", "--cpu-list", cpu_list_harness] + args

    # Safety net (PRD.md D-84): jbr-prepare only enables collectors that apply
    # to every run's JDK; one that does not apply now means the JDK changed
    # since then. Check all before starting any.
    if jdk_info is not None and reporoot is not None:
        configs = ([os.path.splitext(d[0])[0] for d in _COLLECTOR_DEFS
                    if _COLLECTOR_SHORT_NAMES[d[0]] in enabled]
                   + [d[0] for d in _JCMD_COLLECTOR_DEFS if d[0] in enabled])
        not_applicable = [n for n in configs
                          if not collector_applies(load_collector_config(reporoot, n),
                                                   jdk_info["type"], jdk_info["major"])]
        if not_applicable:
            print(f"Error: collector(s) {', '.join(not_applicable)} do not apply to "
                  f"{jdk_info['type']} {jdk_info['major']}, but the batch requires them",
                  flush=True)
            raise CollectorsNotApplicable(not_applicable)

    def _spawn(name: str, args: list, log_stem: str, out_ext: str) -> None:
        """Start one collector, plain or through the compressor, and record it."""
        import shlex
        args = _pin_and_stagger(args)
        compress = collector_compression and log_stem in _COMPRESSED_LOG_STEMS

        stdout_path = os.path.join(result_dir, log_stem + out_ext)
        stderr_path = os.path.join(result_dir, log_stem + ".err")
        if compress:
            stdout_path += ".zst"

        # kept open for the proc lifetime
        stdout_fh = open(stdout_path, "wb" if compress else "w")   # noqa: SIM115
        stderr_fh = open(stderr_path, "w")   # noqa: SIM115

        if verbose:
            print(f"Collector command line: {shlex.join(args)}", flush=True)

        if compress:
            zargs = list(_COMPRESSOR_CMD)
            if cpu_list_harness is not None:
                zargs = ["taskset", "--cpu-list", cpu_list_harness] + zargs
            if verbose:
                print(f"Compressor command line: {shlex.join(zargs)}", flush=True)
            zproc = subprocess.Popen(zargs, stdin=subprocess.PIPE, stdout=stdout_fh,
                                     stderr=stderr_fh, process_group=0)
            proc = subprocess.Popen(args, stdout=zproc.stdin, stderr=stderr_fh,
                                    stdin=subprocess.DEVNULL, process_group=0)
            # The collector now holds the only write end: the compressor sees EOF
            # exactly when the collector (and anything it spawned) is gone.
            zproc.stdin.close()
            procs.append((name, proc))
            procs.append((name + _COMPRESSOR_SUFFIX, zproc))
            if verbose:
                print(f"Compressor pid ({name}): {zproc.pid}", flush=True)
        else:
            proc = subprocess.Popen(args, stdout=stdout_fh, stderr=stderr_fh,
                                    stdin=subprocess.DEVNULL, process_group=0)
            procs.append((name, proc))
        if verbose:
            print(f"Collector pid ({name}): {proc.pid}", flush=True)

    for name, script_filename, cmd, needs_pid, log_stem in _COLLECTOR_DEFS:
        if _COLLECTOR_SHORT_NAMES[name] not in enabled:
            continue

        script_path = os.path.join(_COLLECTORS_DIR, script_filename)

        args = [cmd, script_path, "-i", str(collector_interval)]

        if needs_pid:
            args.append(str(jvm_pid))

        if name == "perf-logger.sh":
            if perf_events is not None:
                args += ["--counters", ",".join(perf_events)]

        # Determine log file extension: .jsonl for collectors that emit JSON Lines
        # themselves, .log for everything else (raw output, post-processed later)
        out_ext = ".jsonl" if name in _JSONL_COLLECTORS else ".log"

        _spawn(name, args, log_stem, out_ext)

    for name, subcommand, log_stem in _JCMD_COLLECTOR_DEFS:
        if name not in enabled:
            continue

        script_path = os.path.join(_COLLECTORS_DIR, _JCMD_LOGGER_SCRIPT)
        java_home = run_entry["java_home"]

        args = [
            "bash", script_path, "-i", str(collector_interval),
            "--java-home", java_home, "--cmd", " ".join(subcommand),
            str(jvm_pid),
        ]

        _spawn(name, args, log_stem, ".log")

    return procs


def verify_collectors(collector_procs: list, benchmark_proc) -> tuple:
    """Wait 5 seconds then check collector and benchmark process health.

    Called immediately after start_collectors.  Waits 5 seconds per spec §5.3,
    then:

    - If ``benchmark_proc`` has already exited: prints an error message and
      returns ``(True, [])`` (caller should proceed straight to stop sequence).
    - For each collector that is no longer running: prints an error and adds
      its name to the returned dead-collector list. An enabled
      collector dying here is now a hard failure for the run (caller kills
      the JVM), not just a warning.

    Returns ``(benchmark_already_exited, dead_collector_names)``.
    """
    import time
    time.sleep(_VERIFY_COLLECTORS_DELAY_SECS)

    if benchmark_proc.poll() is not None:
        print("benchmark process exited within 5 seconds of startup", flush=True)
        return True, []

    dead = []
    for name, proc in collector_procs:
        if proc.poll() is not None:
            print(f"{name} failed to start", flush=True)
            dead.append(name)

    return False, dead


def stop_collectors(collector_procs: list) -> None:
    """Stop all background collector processes.

    Waits a 3-second grace period to allow self-terminating collectors to exit
    on their own, then terminates any that are still running.  After sending
    SIGTERM, waits 2 seconds; if a process is still alive it receives SIGKILL.
    Finally, ``proc.wait()`` is called on every collector to reap it.

    Compressors (names ending in ``_COMPRESSOR_SUFFIX``) are never sent SIGTERM
    or SIGKILL along with the collectors: each ends by itself, flushing its last
    block, once its collector (and anything the collector spawned, e.g. an
    in-flight jcmd) is gone and the pipe reaches EOF. After all collectors are
    reaped, each compressor gets up to ``_COMPRESSOR_DRAIN_SECS`` for that, and is
    only then killed — a SIGKILL loses its buffered tail (spec-execute.md §5.3).
    """
    import time
    time.sleep(_STOP_COLLECTORS_GRACE_SECS)

    collectors = [(n, p) for n, p in collector_procs if not n.endswith(_COMPRESSOR_SUFFIX)]
    compressors = [(n, p) for n, p in collector_procs if n.endswith(_COMPRESSOR_SUFFIX)]

    for _name, proc in collectors:
        if proc.poll() is None:
            proc.terminate()

    time.sleep(_STOP_COLLECTORS_KILL_WAIT_SECS)

    for _name, proc in collectors:
        if proc.poll() is None:
            proc.kill()

    for _name, proc in collectors:
        proc.wait()

    for _name, proc in compressors:
        try:
            proc.wait(timeout=_COMPRESSOR_DRAIN_SECS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

# ---------------------------------------------------------------------------
# Sub-Task 6 — Per-run execution orchestration
# ---------------------------------------------------------------------------

import glob as _glob
import shutil
import signal
import time


class _StoppedByUser(Exception):
    """Raised when the user interrupts a run via SIGINT (Ctrl-C)."""


# ---------------------------------------------------------------------------
# Ramdisk-backed result directories — spec-execute.md §6
# ---------------------------------------------------------------------------

def _detect_fs_type(path: str) -> str | None:
    """Best-effort filesystem type of *path* (e.g. "tmpfs", "ramfs").

    Uses ``stat -f -c %T``. Returns None on any failure (missing binary,
    non-Linux stat, non-zero exit, ...) — this is purely informational and
    must never fail a run.
    """
    try:
        result = subprocess.run(
            ["stat", "-f", "-c", "%T", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    fs_type = result.stdout.strip()
    return fs_type or None


_RAMDISK_FS_TYPES = {"tmpfs", "ramfs"}


def _check_ramdisk_fs_type(ramdisk_path: str) -> None:
    """Verify *ramdisk_path* is actually backed by a RAM-based filesystem
    (spec-execute.md §1.3 step 4b).

    Raises RuntimeError if the filesystem type can be determined and is
    neither ``tmpfs`` nor ``ramfs`` — a misconfigured ramdisk_path silently
    defeats the whole point of using one. If the type can't be determined
    (e.g. non-Linux ``stat``), this is a best-effort check: print a warning
    and continue rather than failing the batch.
    """
    fs_type = _detect_fs_type(ramdisk_path)
    if fs_type is None:
        print(
            f"Warning: could not determine filesystem type of ramdisk "
            f"'{ramdisk_path}' (stat unavailable or unsupported on this "
            f"platform); skipping ramdisk filesystem check.",
            flush=True,
        )
        return
    if fs_type not in _RAMDISK_FS_TYPES:
        raise RuntimeError(
            f"'ramdisk_path' ('{ramdisk_path}') is not a RAM-based "
            f"filesystem (detected type: '{fs_type}', expected tmpfs or "
            f"ramfs). Configure a ramdisk_path that is actually backed by "
            f"RAM, or remove 'ramdisk_path' from the host config to "
            f"disable this feature."
        )


def _check_ramdisk_lingering_processes(ramdisk_path: str) -> None:
    """Best-effort ``lsof`` check for processes still holding files open on
    *ramdisk_path*, run once at batch start. Purely diagnostic — prints a
    warning listing any lingering processes, never fails the run.
    """
    try:
        result = subprocess.run(
            ["lsof", ramdisk_path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if result.returncode == 0 and result.stdout.strip():
        print(
            f"Warning: lingering processes hold files open on ramdisk '{ramdisk_path}':\n"
            f"{result.stdout.strip()}",
            flush=True,
        )


def _remove_symlinked_dir(path: str, ramdisk_path: str) -> None:
    """Remove the symlink at *path* and, if its target still exists, delete it too.

    Shared by orphaned-ramdisk-run retry (§6.3) and --abort cleanup (§6.4).
    Handles a broken symlink (e.g. the ramdisk was cleared by a reboot)
    gracefully — the target check simply finds nothing to remove.

    *ramdisk_path* is the batch's configured ramdisk root. The resolved
    symlink target must lie under it — *path* lives in the project's results/
    tree, so between symlink creation and this cleanup running, something
    else with write access to that tree could in principle have repointed
    it elsewhere. Refusing to rmtree anything outside ramdisk_path turns
    that into a hard error instead of a silent deletion of an arbitrary
    directory.
    """
    target = os.readlink(path)
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(path), target)
    if os.path.exists(target):
        if not ramdisk_path:
            raise RuntimeError(
                f"'{path}' is a symlink but the batch has no ramdisk_path "
                f"recorded; refusing to remove its target '{target}'"
            )
        real_target = os.path.realpath(target)
        real_ramdisk = os.path.realpath(ramdisk_path)
        if os.path.commonpath([real_target, real_ramdisk]) != real_ramdisk:
            raise RuntimeError(
                f"refusing to remove '{real_target}': symlink '{path}' "
                f"resolves outside the configured ramdisk '{real_ramdisk}'"
            )
        shutil.rmtree(target)
    os.remove(path)


def _ramdisk_teardown(ramdisk_run_dir: str, result_dir: str, project_dir: str,
                       verbose: bool = False) -> None:
    """Copy a ramdisk-backed result dir back into results/ and clean up.

    Runs unconditionally after a run's final run-info.json write, regardless
    of outcome (DONE, FAILED, or STOPPED) — see spec-execute.md §6.2.

    *result_dir* is still, at this point, the symlink created in step a of
    execute_run(); *ramdisk_run_dir* is the real directory it points to.

    The process's cwd is inside *ramdisk_run_dir* (execute_run chdir'd into
    the symlink) — moved back to *project_dir* first so nothing here operates
    relative to a directory about to be deleted.

    Copies to a temp name and renames into place so a crash mid-copy leaves
    the still-intact symlink and ramdisk data behind rather than a
    half-written result directory.
    """
    if verbose:
        print(
            f"Copying result dir back from ramdisk: {ramdisk_run_dir} -> {result_dir}",
            flush=True,
        )
    os.chdir(project_dir)
    tmp_dir = os.path.join(os.path.dirname(result_dir), ".tmp-" + os.path.basename(result_dir))
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    shutil.copytree(ramdisk_run_dir, tmp_dir)
    os.remove(result_dir)  # removes the symlink only, not its target
    os.rename(tmp_dir, result_dir)
    shutil.rmtree(ramdisk_run_dir)


# Module-level SIGINT state -------------------------------------------------

_sigint_received: bool = False
_current_jvm_pid: int | None = None


def _install_sigint_handler() -> None:
    """Install a SIGINT handler that sets _sigint_received and stores the JVM PID.

    The wait loop in execute_run polls _sigint_received each iteration so it
    can react to Ctrl-C without blocking on wait().
    """
    def _handler(signum, frame):  # noqa: ANN001
        global _sigint_received  # noqa: PLW0603
        if not _sigint_received:
            print("will stop...", flush=True)
        _sigint_received = True
    signal.signal(signal.SIGINT, _handler)


# Command-line builder -------------------------------------------------------

def _build_jvm_cmdline(run_entry: dict, reporoot: str, cpu_list: str = None) -> list:
    """Return the JVM command-line list for *run_entry*.

    For specjbb:
        <java> <flags> -jar <specjbb_dir>/specjbb2015.jar -m COMPOSITE -ikv

    For dacapo:
        <java> <flags> -cp <dacapo_jar>:<reporoot>/dacapo-timestamp-plugin/dacapoExtension.jar
               Harness -c stuefe.dacapo.JBRCallback -n <iterations> -s <datasize>
               --verbose --print-stats <dacapo_args...>

    If the run entry's effective ``do_cpu_pinning`` is true (spec-prepare.md
    §1.4, §4) and *cpu_list* is given, the command is wrapped in
    ``taskset --cpu-list <cpu_list> <command>`` (spec-execute.md §1, §2 step
    g). Wrapping the launch — rather than pinning after the fact via
    ``taskset -p`` — sets affinity before ``exec``, covering every JVM thread
    from birth with no race window; since ``taskset`` execs into the target
    rather than forking, the resulting PID is unaffected.

    Raises RuntimeError if the dacapo jar cannot be found.
    """
    java_binary = os.path.join(run_entry["java_home"], "bin", "java")
    jvmargs = run_entry.get("jvmargs", [])
    benchmark = run_entry["benchmark"]

    if benchmark == "specjbb":
        specjbb_dir = run_entry["specjbb_dir"]
        cmdline = (
            [java_binary]
            + jvmargs
            + ["-jar", os.path.join(specjbb_dir, "specjbb2015.jar"),
               "-m", "COMPOSITE", "-ikv"]
        )
    else:
        # dacapo
        dacapo_dir = run_entry["dacapo_dir"]
        pattern = os.path.join(dacapo_dir, "dacapo-*-chopin.jar")
        matches = _glob.glob(pattern)
        if not matches:
            raise RuntimeError(
                f"dacapo jar not found: no file matching '{pattern}'"
            )
        dacapo_jar = matches[0]

        extension_jar = os.path.join(
            reporoot, "dacapo-timestamp-plugin", "dacapoExtension.jar"
        )
        classpath = f"{dacapo_jar}:{extension_jar}"

        cmdline = (
            [java_binary]
            + jvmargs
            + [
                "-cp", classpath,
                "Harness",
                "-c", "stuefe.dacapo.JBRCallback",
                "-n", str(run_entry["iterations"]),
                "-s", run_entry["datasize"],
                "--verbose",
                "--print-stats",
            ]
            + run_entry.get("dacapo_args", [])
            + [run_entry["sub_benchmark"]]
        )

    if run_entry.get("do_cpu_pinning") and cpu_list:
        cmdline = ["taskset", "--cpu-list", cpu_list] + cmdline

    return cmdline


# Per-run orchestration ------------------------------------------------------

def _terminate_jvm(jvm_proc, jvm_pid: int) -> int:
    """Terminate the JVM process: SIGTERM, wait up to 3s, then SIGKILL.

    Shared by the SIGINT path and the collector-hard-fail path — both need
    the exact same "graceful then forceful" teardown sequence.

    Returns the process's exit code once it has actually exited.
    """
    try:
        os.kill(jvm_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # already gone

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if jvm_proc.poll() is not None:
            break
        time.sleep(0.1)

    if jvm_proc.poll() is None:
        try:
            os.kill(jvm_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        jvm_proc.wait()

    return jvm_proc.returncode


def execute_run(
    run_entry: dict,
    reporoot: str,
    sudo_mode: bool,
    collector_interval: int,
    batch_id: str = None,
    verbose: bool = False,
    timeout_secs: int = None,
    ramdisk_path: str = None,
    cpu_list: str = None,
    cpu_list_harness: str = None,
    total_runs: int = None,
    *,
    project_dir: str,
) -> str:
    """Execute a single benchmark run end-to-end per spec §2 steps a–n.

    The run's ``result_dir`` is relative to *project_dir*; *reporoot* is
    used for the repository's own files (config, plugin jar, scripts).

    Returns ``"DONE"``, ``"FAILED"``, or ``"STOPPED"`` (the latter also when a
    collector from collectors.enabled fails to start — see §5.3 — in which
    case ``status_info="collector"`` is recorded in run-info.json). On
    SIGINT raises ``_StoppedByUser`` after writing run-info.json with
    ``status="STOPPED"``.

    If ``timeout_secs`` is given and the JVM has not exited after that many
    seconds of wall-clock time, it is killed the same way a Ctrl-C would be
    (SIGTERM, then SIGKILL if still alive after 3s) and the run is recorded
    as ``"FAILED"`` with ``status_info="timeout"``.

    ``ramdisk_path`` is only actually used when the run entry's effective
    ``use_ramdisk`` (spec-prepare.md §1.4, §4) is true; otherwise the result
    directory is created directly under ``results/`` even if the host has a
    ``ramdisk_path`` configured.

    The SIGINT handler is installed inside this function for now; sub-task 7
    will move the installation call to the batch-loop startup.
    """
    global _sigint_received, _current_jvm_pid  # noqa: PLW0603

    # Install SIGINT handler (idempotent; re-installing is harmless).
    _install_sigint_handler()
    _sigint_received = False

    # ------------------------------------------------------------------
    # a. Create result directory — directly, or via a ramdisk + symlink
    #    (spec-execute.md §6.1)
    # ------------------------------------------------------------------
    result_dir_rel = run_entry["result_dir"]          # e.g. "results/<UUID>"
    result_dir = os.path.join(project_dir, result_dir_rel)

    ramdisk_run_dir = None
    ramdisk_size_mb = None
    ramdisk_fs_type = None

    if ramdisk_path and run_entry.get("use_ramdisk"):
        usage = shutil.disk_usage(ramdisk_path)
        ramdisk_size_mb = usage.total // (1024 * 1024)
        ramdisk_fs_type = _detect_fs_type(ramdisk_path)

        ramdisk_run_dir = os.path.join(ramdisk_path, run_entry["runid"])
        os.makedirs(ramdisk_run_dir, exist_ok=True)
        os.makedirs(os.path.dirname(result_dir), exist_ok=True)
        os.symlink(ramdisk_run_dir, result_dir)
        if verbose:
            print(f"Ramdisk dir: {ramdisk_run_dir}", flush=True)
    else:
        os.makedirs(result_dir, exist_ok=True)
        if verbose and not run_entry.get("use_ramdisk"):
            print("No Ramdisk will be used - IO jitter can occur", flush=True)

    # ------------------------------------------------------------------
    # b. Progress header
    # ------------------------------------------------------------------
    seq       = run_entry.get("seq", "?")
    total     = total_runs if total_runs is not None else run_entry.get("total", "?")
    bench_json = run_entry.get("bench_json", "")
    jdkname   = run_entry.get("jdkname", "")
    heap_size = run_entry.get("heapsize", "")
    tags      = run_entry.get("tags", [])
    tags_suffix = f"  tags={','.join(tags)}" if tags else ""
    print(
        f"--- Run {seq}/{total}  {bench_json}  {jdkname}  heap={heap_size}MB{tags_suffix} ---",
        flush=True,
    )

    # ------------------------------------------------------------------
    # c. Enter result directory
    # ------------------------------------------------------------------
    os.chdir(result_dir)

    # ------------------------------------------------------------------
    # d. specjbb: copy config directory
    # ------------------------------------------------------------------
    if run_entry["benchmark"] == "specjbb":
        specjbb_config_src = os.path.join(run_entry["specjbb_dir"], "config")
        specjbb_config_dst = os.path.join(result_dir, "config")
        if not os.path.exists(specjbb_config_dst):
            shutil.copytree(specjbb_config_src, specjbb_config_dst)

    # ------------------------------------------------------------------
    # e. JDK detection
    # ------------------------------------------------------------------
    java_binary = os.path.join(run_entry["java_home"], "bin", "java")
    try:
        jdk_info = detect_jdk(java_binary)
    except RuntimeError as exc:
        print(f"Error: JDK detection failed: {exc}", flush=True)
        raise
    check_jdk_against_prepared(jdk_info, run_entry)

    # ------------------------------------------------------------------
    # f. Write info.json (atomically, before JVM launch)
    # ------------------------------------------------------------------
    write_info_json(run_entry, jdk_info, batch_id, result_dir, cpu_list=cpu_list,
                    cpu_list_harness=cpu_list_harness)

    # Snapshot machine stabilization state once, before JVM launch, and
    # record it on every run-info.json write below (see machine_state.py).
    stabilization = capture_stabilization_state()

    # ------------------------------------------------------------------
    # g. Build command line and launch JVM
    # ------------------------------------------------------------------
    cmdline = _build_jvm_cmdline(run_entry, reporoot, cpu_list=cpu_list)

    stdout_log = open(os.path.join(result_dir, "stdout.log"), "w")
    stderr_log = open(os.path.join(result_dir, "stderr.log"), "w")

    start_dt = datetime.datetime.now().astimezone()
    # Millisecond precision: post-processing derives dacapo startup_ms from it.
    start_str = collector_timestamp.to_string(start_dt)

    if verbose:
        import shlex
        print(f"Benchmark command line: {shlex.join(cmdline)}", flush=True)

    jvmenv = {**os.environ, **run_entry.get("jvmenv", {})}
    jvm_proc = subprocess.Popen(cmdline, stdout=stdout_log, stderr=stderr_log,
                                stdin=subprocess.DEVNULL, start_new_session=True,
                                env=jvmenv)
    jvm_pid = jvm_proc.pid
    _current_jvm_pid = jvm_pid
    if verbose:
        print(f"Benchmark pid: {jvm_pid}", flush=True)

    # ------------------------------------------------------------------
    # h. Write initial run-info.json  (status=RUNNING, pid, time)
    # ------------------------------------------------------------------
    write_run_info_json(
        result_dir,
        status="RUNNING",
        pid=jvm_pid,
        start_time=start_str,
        sudo_mode=sudo_mode,
        stabilization=stabilization,
        ramdisk_path=ramdisk_path if ramdisk_run_dir else None,
        ramdisk_size_mb=ramdisk_size_mb,
        ramdisk_fs_type=ramdisk_fs_type,
    )

    # ------------------------------------------------------------------
    # i. Start and verify collectors
    # ------------------------------------------------------------------
    perf_events = run_perf_events(run_entry)

    try:
        collector_procs = start_collectors(
            run_entry, jvm_pid, result_dir, collector_interval, perf_events,
            verbose=verbose,
            jdk_info=jdk_info, reporoot=reporoot,
            cpu_list_harness=cpu_list_harness if run_entry.get("do_cpu_pinning") else None,
            collector_compression=(run_entry.get("collectors") or {}).get("compression", False),
        )
    except CollectorsNotApplicable as exc:
        collector_procs, benchmark_already_exited, dead_collectors = [], False, exc.names
    else:
        benchmark_already_exited, dead_collectors = verify_collectors(collector_procs, jvm_proc)

    if not benchmark_already_exited and dead_collectors:
        # An enabled collector (it passed jbr-prepare's probe) failed to start,
        # or no longer applies to the run's JDK (D-84) — this is
        # now a hard failure for the run: kill the JVM the same way a
        # Ctrl-C would, and record why via status_info (see §5.3).
        exit_code = _terminate_jvm(jvm_proc, jvm_pid)
        duration_secs = (
            datetime.datetime.now().astimezone() - start_dt
        ).total_seconds()

        stop_collectors(collector_procs)

        write_run_info_json(
            result_dir,
            status="STOPPED",
            pid=jvm_pid,
            start_time=start_str,
            sudo_mode=sudo_mode,
            exit_code=exit_code,
            duration_secs=round(duration_secs, 1),
            status_info="collector",
            stabilization=stabilization,
            ramdisk_path=ramdisk_path if ramdisk_run_dir else None,
            ramdisk_size_mb=ramdisk_size_mb,
            ramdisk_fs_type=ramdisk_fs_type,
        )

        if verbose:
            print("Benchmark status: STOPPED (collector)", flush=True)

        print(
            f"  -> {result_dir_rel}  exit={exit_code}  duration={duration_secs:.1f}s  "
            f"[STOPPED: {', '.join(dead_collectors)} failed to start]",
            flush=True,
        )
        stdout_log.close()
        stderr_log.close()
        if ramdisk_run_dir:
            _ramdisk_teardown(ramdisk_run_dir, result_dir, project_dir, verbose=verbose)
        return "STOPPED"

    # ------------------------------------------------------------------
    # j. Wait for benchmark (poll loop checking SIGINT flag)
    # ------------------------------------------------------------------
    if not benchmark_already_exited:
        while True:
            if _sigint_received:
                # SIGINT received — terminate JVM gracefully
                exit_code = _terminate_jvm(jvm_proc, jvm_pid)
                duration_secs = (
                    datetime.datetime.now().astimezone() - start_dt
                ).total_seconds()

                # k (SIGINT path). Stop collectors
                stop_collectors(collector_procs)

                # l (SIGINT path). Write STOPPED run-info.json
                write_run_info_json(
                    result_dir,
                    status="STOPPED",
                    pid=jvm_pid,
                    start_time=start_str,
                    sudo_mode=sudo_mode,
                    exit_code=exit_code,
                    duration_secs=round(duration_secs, 1),
                    stabilization=stabilization,
                    ramdisk_path=ramdisk_path if ramdisk_run_dir else None,
                    ramdisk_size_mb=ramdisk_size_mb,
                    ramdisk_fs_type=ramdisk_fs_type,
                )

                if verbose:
                    print("Benchmark status: STOPPED", flush=True)

                print(
                    f"  -> {result_dir_rel}  exit={exit_code}  "
                    f"duration={duration_secs:.1f}s  [STOPPED]",
                    flush=True,
                )
                stdout_log.close()
                stderr_log.close()
                if ramdisk_run_dir:
                    _ramdisk_teardown(ramdisk_run_dir, result_dir, project_dir, verbose=verbose)
                raise _StoppedByUser

            if timeout_secs is not None:
                elapsed = (
                    datetime.datetime.now().astimezone() - start_dt
                ).total_seconds()
                if elapsed >= timeout_secs:
                    # Hung JVM (e.g. a non-recovering GC under a too-low heap
                    # size) — kill it the same way a Ctrl-C would, but record
                    # it as FAILED (not STOPPED): a timeout is a deterministic
                    # property of this run's settings, not a transient event,
                    # so it must not be silently retried on batch resume.
                    exit_code = _terminate_jvm(jvm_proc, jvm_pid)
                    duration_secs = (
                        datetime.datetime.now().astimezone() - start_dt
                    ).total_seconds()

                    stop_collectors(collector_procs)

                    write_run_info_json(
                        result_dir,
                        status="FAILED",
                        pid=jvm_pid,
                        start_time=start_str,
                        sudo_mode=sudo_mode,
                        exit_code=exit_code,
                        duration_secs=round(duration_secs, 1),
                        status_info="timeout",
                        stabilization=stabilization,
                        ramdisk_path=ramdisk_path if ramdisk_run_dir else None,
                        ramdisk_size_mb=ramdisk_size_mb,
                        ramdisk_fs_type=ramdisk_fs_type,
                    )

                    if verbose:
                        print("Benchmark status: FAILED (timeout)", flush=True)

                    print(
                        f"  -> {result_dir_rel}  exit={exit_code}  "
                        f"duration={duration_secs:.1f}s  [FAILED: timed out after "
                        f"{timeout_secs}s]",
                        flush=True,
                    )
                    stdout_log.close()
                    stderr_log.close()
                    if ramdisk_run_dir:
                        _ramdisk_teardown(ramdisk_run_dir, result_dir, project_dir, verbose=verbose)
                    return "FAILED"

            try:
                jvm_proc.wait(timeout=0.5)
                break  # process exited normally
            except subprocess.TimeoutExpired:
                pass  # still running; loop again

    # ------------------------------------------------------------------
    # k. Stop collectors
    # ------------------------------------------------------------------
    stop_collectors(collector_procs)

    # ------------------------------------------------------------------
    # l. Write final run-info.json
    # ------------------------------------------------------------------
    exit_code = jvm_proc.returncode
    duration_secs = (
        datetime.datetime.now().astimezone() - start_dt
    ).total_seconds()

    final_status = "DONE" if exit_code == 0 else "FAILED"
    status_info = classify_failure(result_dir, exit_code) if exit_code != 0 else None

    write_run_info_json(
        result_dir,
        status=final_status,
        pid=jvm_pid,
        start_time=start_str,
        sudo_mode=sudo_mode,
        exit_code=exit_code,
        duration_secs=round(duration_secs, 1),
        status_info=status_info,
        stabilization=stabilization,
        ramdisk_path=ramdisk_path if ramdisk_run_dir else None,
        ramdisk_size_mb=ramdisk_size_mb,
        ramdisk_fs_type=ramdisk_fs_type,
    )

    if verbose:
        suffix = f" ({status_info})" if status_info else ""
        print(f"Benchmark status: {final_status}{suffix}", flush=True)

    # ------------------------------------------------------------------
    # m. Print final summary
    # ------------------------------------------------------------------
    print(
        f"  -> {result_dir_rel}  exit={exit_code}  duration={duration_secs:.1f}s",
        flush=True,
    )

    stdout_log.close()
    stderr_log.close()

    if ramdisk_run_dir:
        _ramdisk_teardown(ramdisk_run_dir, result_dir, project_dir, verbose=verbose)

    # n. Return status
    return final_status


# ---------------------------------------------------------------------------
# Sub-Task 7 — Batch orchestration and run_execute entry point
# ---------------------------------------------------------------------------

import socket


# Root commands are granted by the rules of config/jbenchrunner.sudoers, not by
# a sudo ticket (PRD.md D-87). At batch start, one command per feature that
# needs root is checked with 'sudo -n -l' (spec §1.3 sudo check), using the
# paths of the sudoers rules; scripts/phase-execute/tests/test_sudoers_template.py
# keeps these, _SHUTDOWN_CMD and the scripts in line with the template.
_SUDO_CHECK_STABILIZE = ["/usr/bin/tee", "/proc/sys/vm/drop_caches"]
_SUDO_CHECK_SHUTDOWN = ["/usr/sbin/shutdown", "-h", "now"]
_SHUTDOWN_CMD = ["shutdown", "-h", "now"]


def _check_sudo_rules(stabilize_machine: bool, shutdown: bool) -> None:
    """Check that sudo allows what this batch will run as root, without a
    password: machine stabilization/reset and/or ``--shutdown``.

    Raises RuntimeError naming the missing features, so a batch without the
    sudoers rules fails at start instead of at its first reset or after its
    last run. Passes with an open sudo ticket, too.
    """
    needed = []
    if stabilize_machine:
        needed.append(("machine stabilization", _SUDO_CHECK_STABILIZE))
    if shutdown:
        needed.append(("--shutdown", _SUDO_CHECK_SHUTDOWN))
    missing = [
        what for what, cmd in needed
        if subprocess.run(["sudo", "-n", "-l"] + cmd, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode != 0
    ]
    if missing:
        raise RuntimeError(
            f"sudo does not allow the root commands needed for {' and '.join(missing)} "
            f"without a password (checked: sudo -n -l). Install the rules of "
            f"config/jbenchrunner.sudoers (see its header), and run as a member of "
            f"group 'jbenchrunners' (membership takes effect at the next login)."
        )


def _reset_machine(reporoot: str) -> None:
    """Run reset-machine.sh (sync, drop_caches, restart systemd-journald).

    Needs the rules of config/jbenchrunner.sudoers; raises RuntimeError
    if the script fails so the batch aborts rather than proceeding on an
    unreset machine.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reset-machine.sh")
    result = subprocess.run([script])
    if result.returncode != 0:
        raise RuntimeError(
            f"reset-machine.sh failed (exit {result.returncode}); aborting batch"
        )


def _stabilize_machine() -> None:
    """Run prepare-for-benchmarks.sh once at batch start.

    The script runs as the invoking user and uses ``sudo -n`` only for the
    commands that need root. Needs the rules of config/jbenchrunner.sudoers;
    raises RuntimeError if the script fails so the batch aborts rather than proceeding on a
    half-stabilized machine. Settings the machine does not support are only
    warned about by the script itself.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "prepare-for-benchmarks.sh")
    global _machine_stabilized  # noqa: PLW0603
    print("Stabilizing machine...", flush=True)
    # Set before running: a script that fails halfway has changed settings too.
    _machine_stabilized = True
    result = subprocess.run([script])
    if result.returncode != 0:
        raise RuntimeError(
            f"prepare-for-benchmarks.sh failed (exit {result.returncode}); aborting batch"
        )


def _check_perf_command(perf_events) -> None:
    """Check the batch's perf command once, at batch start (PRD.md D-86).

    Runs ``perf-logger.sh --check``: the collector's own perf command line
    (*perf_events*, or perf's default set for ``None``), counting ``true``
    instead of the JVM. Raises RuntimeError, with perf's output, if perf
    exits non-zero (unknown event, missing privileges, ...) or reports an
    event as ``<not supported>``, so the batch stops before the first run
    instead of every run failing or recording nothing for an event.
    """
    script = os.path.join(_COLLECTORS_DIR, "perf-logger.sh")
    args = ["bash", script, "--check"]
    if perf_events is not None:
        args += ["--counters", ",".join(perf_events)]
    print("Checking perf command...", flush=True)
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    output = result.stdout.strip()
    hint = ("Fix the host (e.g. perf_event_paranoid) and run jbr-execute again, or "
            "abandon the batch (jbr-execute --abort) and prepare it again with other "
            "perf events.")
    if result.returncode != 0:
        raise RuntimeError(
            f"perf check failed: perf exited with code {result.returncode}. "
            f"perf said:\n{output}\n{hint}"
        )
    # CSV (-x ';') without an interval column: value;unit;event;...
    unsupported = []
    for line in output.splitlines():
        fields = line.split(";")
        if len(fields) > 2 and fields[0].strip() == "<not supported>":
            unsupported.append(fields[2].strip())
    if unsupported:
        raise RuntimeError(
            f"perf check failed: perf does not support event(s) "
            f"{', '.join(unsupported)} on this host. perf said:\n{output}\n{hint}"
        )


def _abort(project_dir: str) -> None:
    """Implement --abort: abandon the project's batch unless a live run exists.

    Per spec §1 --abort option:
      - A dangling active-batch link is removed first; if the project has
        no batch file, that alone is a successful abort.
      - Run liveness check; reject if a live PID is found.
      - Sweep stray ramdisk data for any run that never completed teardown
        (spec-execute.md §6.4).
      - Move BATCH_IN_PROGRESS.json to batches/<batchid>/batch-aborted.json
        and remove the active-batch link if it points to it.
      - Print "Batch aborted." on success.

    Raises:
        RuntimeError: if the batch is currently running, the active-batch
                      link belongs to another project, or there is neither
                      a batch file nor a dangling link.
    """
    target = _project.read_active_batch()
    removed_dangling = False
    if target is not None and not os.path.exists(target):
        _project.clear_active_batch(project_dir)
        print(f"Removed dangling active-batch link (pointed to {target}).", flush=True)
        removed_dangling = True
    if removed_dangling and not os.path.exists(_batch_path(project_dir)):
        return

    conflict = _project.active_batch_conflict(project_dir)
    if conflict is not None:
        raise RuntimeError(conflict)

    # Load batch first (raises RuntimeError if file is missing).
    batch = load_batch(project_dir)

    is_running, n_complete, started_at = liveness_check(batch, project_dir)
    if is_running:
        total = len(batch.get("runs", []))
        started_fmt = started_at[:16].replace("T", " ") if started_at else "unknown"
        print(
            f"Error: a batch is currently running. Do not disturb.\n"
            f"  Progress: {n_complete}/{total} runs complete\n"
            f"  Started:  {started_fmt}\n\n"
            f"  Wait for it to finish, or use jbr-status to check progress.",
            flush=True,
        )
        raise RuntimeError("batch is currently running")

    ramdisk_path = batch.get("ramdisk_path")
    for run_entry in batch.get("runs", []):
        result_dir = os.path.join(project_dir, run_entry.get("result_dir", ""))
        if os.path.islink(result_dir):
            _remove_symlinked_dir(result_dir, ramdisk_path)

    _project.clear_active_batch(project_dir)
    _move_batch_file(project_dir, batch, _project.ABORTED_MANIFEST)
    print("Batch aborted.", flush=True)


def _move_batch_file(project_dir: str, batch: dict, manifest_name: str) -> None:
    """Move the project's BATCH_IN_PROGRESS.json, unchanged, to
    batches/<batchid>/<manifest_name>."""
    dest_dir = _project.batch_dir(project_dir, batch.get("batchid", "unknown"))
    os.makedirs(dest_dir, exist_ok=True)
    os.replace(_batch_path(project_dir), os.path.join(dest_dir, manifest_name))


_SHUTDOWN_DELAY_SECS = 5 * 60


def _run_shutdown_sequence() -> bool:
    """Implement --shutdown: warn logged-in users, wait, then power off.

    Called only on full batch completion (never on Ctrl-C or a still-running
    batch). Needs the sudoers rule for shutdown, which the caller checked at
    batch start (_check_sudo_rules). The wait is interruptible via Ctrl-C,
    like the inter-run cooldown.

    Linux only (wall, shutdown -h). Returns True if the shutdown command was
    issued successfully, False if it was cancelled or failed.
    """
    message = "jbr-execute: batch complete, shutting down in 5 minutes"
    subprocess.run(["wall", message])
    print(message, flush=True)

    deadline = time.monotonic() + _SHUTDOWN_DELAY_SECS
    while time.monotonic() < deadline:
        if _sigint_received:
            break
        time.sleep(min(0.5, deadline - time.monotonic()))
    if _sigint_received:
        print("Shutdown cancelled by user.", flush=True)
        return False

    return subprocess.run(["sudo", "-n"] + _SHUTDOWN_CMD).returncode == 0


# Set by _stabilize_machine once prepare-for-benchmarks.sh has been started in
# this process; run_execute then reminds the user to reboot (spec §1.3 step 8).
_machine_stabilized: bool = False

_STABILIZATION_REMINDER = (
    "WARNING: this machine was stabilized for benchmarking (ASLR off, "
    "perf_event_paranoid=0, swap off, strict overcommit, services stopped, ...). "
    "These settings weaken security and stay in effect until reboot; "
    "rebooting the machine is recommended."
)


def run_execute(options: dict, _reporoot: str = None, _project_dir: str = None) -> None:
    """Public entry point for jbr-execute.

    Args:
        options:   dict with keys:
            "abort"         (bool) — run --abort path
            "shutdown"      (bool) — on full batch completion, warn logged-in
                                     users, wait 5 minutes, then shut the
                                     machine down. Requires
                                     batch["sudo_mode"]. Not fired on Ctrl-C.
            "cooldown_secs" (int)  — sleep this many seconds between runs
                                     (not before the first, not after the
                                     last), to let CPU temperatures settle
            "timeout_secs"  (int)  — kill a run's JVM if it hasn't exited
                                     after this many seconds, recording it as
                                     FAILED with status_info="timeout".
                                     None (default) disables the timeout.
        _reporoot: override the repository root (for testing only).
        _project_dir: override the project directory (for testing only);
                   otherwise found from the current working directory.

    Sudo mode is not a CLI option here — it's read from ``batch["sudo_mode"]``,
    set once at ``jbr-prepare --sudo`` time, so it can never vary across
    resumes of the same batch.

    ``batch["stabilize_machine"]`` (``jbr-prepare --stabilize-machine``,
    explicit opt-in, default off) runs prepare-for-benchmarks.sh
    once at batch start, on every invocation including resumes, and
    reset-machine.sh (sync, drop_caches, restart systemd-journald) before
    every executed run. Both are skipped with a warning without ``--sudo``
    mode or on non-Linux. Whenever this process stabilized the machine, it
    ends with a warning recommending a reboot (the settings weaken security
    and last until reboot), however the batch ends — completed, stopped by
    Ctrl-C or failed — unless --shutdown powered the machine off.

    Per spec §1.3 execution steps.
    """
    global _machine_stabilized  # noqa: PLW0603
    _machine_stabilized = False
    shut_down = False
    try:
        shut_down = _run_execute(options, _reporoot, _project_dir)
    finally:
        if _machine_stabilized and not shut_down:
            print(_STABILIZATION_REMINDER, flush=True)


def _run_execute(options: dict, _reporoot: str, _project_dir: str) -> bool:
    """run_execute without the reboot reminder. Returns True only if
    --shutdown powered the machine off."""
    if _reporoot is None:
        from common.reporoot import find_reporoot  # type: ignore[import]
        reporoot = find_reporoot(__file__)
    else:
        reporoot = _reporoot
    project_dir = _project_dir if _project_dir is not None else _project.find_project()

    # ------------------------------------------------------------------
    # --abort path
    # ------------------------------------------------------------------
    if options.get("abort"):
        _abort(project_dir)
        return

    # An active batch in another project blocks execution (PRD.md §7).
    conflict = _project.active_batch_conflict(project_dir)
    if conflict is not None:
        raise RuntimeError(conflict)

    # ------------------------------------------------------------------
    # Preparation (spec §1.3 steps 1–3)
    # ------------------------------------------------------------------
    batch = load_batch(project_dir)
    check_hostname(batch, socket.gethostname())
    check_batch_format(batch)

    is_running, n_complete, started_at = liveness_check(batch, project_dir)
    if is_running:
        total = len(batch.get("runs", []))
        started_fmt = started_at[:16].replace("T", " ") if started_at else "unknown"
        print(
            f"Error: a batch is currently running. Do not disturb.\n"
            f"  Progress: {n_complete}/{total} runs complete\n"
            f"  Started:  {started_fmt}\n\n"
            f"  Wait for it to finish, or use jbr-status to check progress.",
            flush=True,
        )
        raise RuntimeError("batch is currently running")

    sudo_mode = batch.get("sudo_mode", False)

    if options.get("shutdown") and not sudo_mode:
        raise RuntimeError(
            "--shutdown requires the batch to be running in --sudo mode "
            "(see jbr-prepare --sudo)"
        )

    stabilize_machine = batch.get("stabilize_machine", False)
    if stabilize_machine and not sudo_mode:
        print(
            "WARNING: stabilize_machine is enabled but the batch is not in "
            "--sudo mode (see jbr-prepare --sudo); skipping machine "
            "stabilization and per-run machine reset.",
            flush=True,
        )
        stabilize_machine = False
    if stabilize_machine and platform.system() != "Linux":
        print(
            "WARNING: stabilize_machine is enabled but this is not Linux; "
            "skipping machine stabilization and per-run machine reset.",
            flush=True,
        )
        stabilize_machine = False

    # ------------------------------------------------------------------
    # Sudo check (spec §1.3 sudo check): the sudoers rules, no ticket
    # ------------------------------------------------------------------
    if sudo_mode:
        _check_sudo_rules(stabilize_machine, bool(options.get("shutdown")))

    # ------------------------------------------------------------------
    # Install SIGINT handler
    # ------------------------------------------------------------------
    global _sigint_received  # noqa: PLW0603
    _sigint_received = False
    _install_sigint_handler()

    # ------------------------------------------------------------------
    # Execution loop (spec §1.3 step 5)
    # ------------------------------------------------------------------
    succeeded = 0
    failed = 0

    cooldown_secs = options.get("cooldown_secs", 0)
    timeout_secs = options.get("timeout_secs")
    runs = batch.get("runs", [])

    batch_ramdisk_path = batch.get("ramdisk_path")
    if batch_ramdisk_path is not None:
        # spec §1.3 step 4
        _check_ramdisk_lingering_processes(batch_ramdisk_path)
        # spec §1.3 step 4b
        _check_ramdisk_fs_type(batch_ramdisk_path)

    # spec §1.3 step 4c: from here on the batch is active.
    _project.set_active_batch(project_dir)

    # spec §1.3 step 4c2: stabilize the machine (aborts the batch on failure).
    if stabilize_machine:
        _stabilize_machine()

    # spec §1.3 step 4d: warn (never fail) if the machine looks unstabilized.
    perf_enabled = "perf" in batch["collectors"]["enabled"]
    if platform.system() == "Linux":
        for warning in stabilization_warnings(perf_enabled=perf_enabled):
            print(warning, flush=True)

    # spec §1.3 step 4e: check the batch's perf command once (aborts the batch
    # if perf rejects it), after stabilization has set perf_event_paranoid.
    if perf_enabled:
        _check_perf_command(batch["collectors"]["perf_events"])
    if batch_ramdisk_path is not None and any(r.get("use_ramdisk") for r in runs):
        print(f"Using ramdisk ({batch_ramdisk_path})", flush=True)

    batch_cpu_list = batch.get("cpu_list")
    batch_cpu_list_harness = batch.get("cpu_list_harness")
    if batch_cpu_list is not None and any(r.get("do_cpu_pinning") for r in runs):
        print(f"Using CPU-pinning (JVM CPUs: {batch_cpu_list}, "
              f"collector CPUs: {batch_cpu_list_harness})", flush=True)

    for run_index, run_entry in enumerate(runs):
        seq = run_entry.get("seq", "?")

        # Check for SIGINT between runs
        if _sigint_received:
            print("Batch stopped by user.", flush=True)
            return

        status = get_run_status(run_entry, project_dir)

        if status in ("DONE", "FAILED"):
            print(f"  Skipping run {seq} ({status})", flush=True)
            if status == "DONE":
                succeeded += 1
            else:
                failed += 1
            continue

        if status in ("STOPPED", "RUNNING"):
            # RUNNING with live PID was already rejected above; arriving here
            # means the PID is dead (orphan).  Both STOPPED and orphaned RUNNING
            # are retried by removing the old result directory.
            #
            # A STOPPED run has always already completed ramdisk teardown
            # (§6.2), so old_result_dir is a plain directory here, same as a
            # non-ramdisk run. The one exception is an orphaned RUNNING run
            # whose harness process died before reaching teardown (§6.3) —
            # old_result_dir is then still a symlink onto the ramdisk, whose
            # target must be cleaned up too (it may also be a broken symlink
            # if the ramdisk itself was cleared, e.g. by a reboot).
            result_dir_rel = run_entry["result_dir"]
            old_result_dir = os.path.join(project_dir, result_dir_rel)
            # The path comes from the batch file; refuse to delete anything
            # that is not a direct child of the project's results/ dir.
            if (os.path.dirname(os.path.normpath(old_result_dir))
                    != os.path.normpath(_project.runs_dir(project_dir))):
                raise RuntimeError(
                    f"refusing to remove result_dir {result_dir_rel!r} of run "
                    f"{seq}: not a directory directly under "
                    f"{_project.runs_dir(project_dir)}"
                )
            if os.path.islink(old_result_dir):
                _remove_symlinked_dir(old_result_dir, batch_ramdisk_path)
            elif os.path.exists(old_result_dir):
                shutil.rmtree(old_result_dir)
            print(f"  Retrying run {seq}", flush=True)

        # PENDING (or just cleared STOPPED/orphan RUNNING): execute
        if stabilize_machine:
            print("  Resetting machine state...", flush=True)
            _reset_machine(reporoot)

        try:
            # The batch's collector settings apply to every run (PRD.md D-84);
            # the run sees them as its own (and records them in info.json).
            run_status = execute_run(
                {**run_entry, "collectors": batch["collectors"]},
                reporoot,
                sudo_mode,
                batch_id=batch.get("batchid", ""),
                collector_interval=batch["collectors"]["interval_secs"],
                verbose=options.get("verbose", False),
                timeout_secs=timeout_secs,
                ramdisk_path=batch_ramdisk_path,
                cpu_list=batch_cpu_list,
                cpu_list_harness=batch_cpu_list_harness,
                total_runs=len(runs),
                project_dir=project_dir,
            )
        except _StoppedByUser:
            print("Batch stopped by user.", flush=True)
            return

        if run_status == "DONE":
            succeeded += 1
        else:
            failed += 1

        # Cooldown: let CPU temperatures settle before the next run. Skipped
        # after the last run and interruptible via Ctrl-C.
        if cooldown_secs > 0 and run_index < len(runs) - 1:
            print(f"Cooling down for {cooldown_secs}s...", flush=True)
            deadline = time.monotonic() + cooldown_secs
            while time.monotonic() < deadline:
                if _sigint_received:
                    break
                time.sleep(min(0.5, deadline - time.monotonic()))
            if _sigint_received:
                print("Batch stopped by user.", flush=True)
                return

    # ------------------------------------------------------------------
    # Batch finalisation (spec §1.3 step 6)
    # ------------------------------------------------------------------
    _project.clear_active_batch(project_dir)
    _move_batch_file(project_dir, batch, _project.DONE_MANIFEST)

    # ------------------------------------------------------------------
    # Summary (spec §1.3 step 7)
    # ------------------------------------------------------------------
    total = succeeded + failed
    print(
        f"Batch complete: {total} runs, {succeeded} succeeded, {failed} failed.",
        flush=True,
    )

    # ------------------------------------------------------------------
    # Shutdown (--shutdown), only on full batch completion
    # ------------------------------------------------------------------
    if options.get("shutdown"):
        return _run_shutdown_sequence()
    return False
