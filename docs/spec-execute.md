# jbenchrunner — Execute Phase Specification

**Part of:** [PRD.md](PRD.md) (see master for goals, target users, repository layout, non-functional requirements, and decisions log)  
**Scope:** `jbr-execute` — benchmark execution against a prepared `BATCH_IN_PROGRESS.json`; `jbr-status` — batch status display; run result artefacts (`info.json`, `run-info.json`); and data sampling.

> **Notation:** `<reporoot>` — the root directory of the jbenchrunner repository, determined at
> runtime from the script's own location; `<project>` — the current project, determined from the
> current working directory (see [PRD.md §4 Conventions](PRD.md)). All relative paths in this
> document (`BATCH_IN_PROGRESS.json`, `batches/`, `results/<RUNID>`) are relative to `<project>`.

> **Design note:** `BATCH_IN_PROGRESS.json` is written once by `jbr-prepare` and is never modified during execution. Per-run runtime state (`status`, `pid`) lives exclusively in `run-info.json` inside each run's result directory. A missing result directory means the run is implicitly `PENDING`.

The sole input to this phase is `BATCH_IN_PROGRESS.json`, produced by `jbr-prepare`
(see [spec-prepare.md](spec-prepare.md)). No config files are re-read during execution.

---

## 1 `jbr-execute`

Executes a prepared batch. Reads `BATCH_IN_PROGRESS.json` and runs all entries that are
not yet completed, in order. Must be run inside a project (PRD.md §4); outside one it aborts.

```
jbr-execute [options]
jbr-execute --abort
```

Options:

| Option | Notes |
|---|---|
| `--timeout-secs N` | Kill a run's JVM if it hasn't exited after `N` seconds of wall-clock time (see §1.5). Per-invocation only — not stored in `BATCH_IN_PROGRESS.json`. Default: disabled. |
| `--shutdown` | On full batch completion only (not on Ctrl-C, not on an abandoned/still-running batch): broadcast a `wall` warning, wait 5 minutes, then `sudo shutdown -h now`. Requires the batch to be in `--sudo` mode (see below); rejected up front otherwise. Per-invocation only — not stored in `BATCH_IN_PROGRESS.json`. Mutually exclusive with `--abort`. Linux only. |
| `--abort` | Abandon the current batch: move `BATCH_IN_PROGRESS.json` to `batches/<batchid>/batch-aborted.json`, and remove the `active-batch` link (§1.1a) if it points to it. Completed run directories and the batch's frozen `.run.toml` copies are kept. Rejected with an error if a batch is currently running (see §1.1 liveness check). Proceeds normally if the batch is orphaned (harness crashed mid-run). If `ramdisk_path` is set (§6), also removes any leftover `<ramdisk_path>/<runid>` directory and dangling `results/<runid>` symlink for a run that never completed teardown. A dangling `active-batch` link (its project was moved or deleted) is removed too; in that case `--abort` succeeds even if the current project has no batch file. Mutually exclusive with `--shutdown`. |

There is no `--sudo` flag on `jbr-execute`. Sudo mode is decided once, at `jbr-prepare
--sudo` time, and stored as `sudo_mode` in `BATCH_IN_PROGRESS.json` (spec-prepare.md §3, §4) —
this guarantees it is applied uniformly across every run in the batch and every resume, which
a per-invocation flag could not (see PRD.md decisions log). `jbr-execute` reads
`sudo_mode` from the batch and, when `true`, may run the root commands of machine
stabilization/reset and `--shutdown` via `sudo -n`. They are granted by the rules of
`config/jbenchrunner.sudoers` (PRD.md D-87), not by a sudo ticket: `jbr-execute` never prompts
for a password. Recorded per-run in `run-info.json` (see §4.2).

### 1.1 Guards

**Active batch in another project** — the `active-batch` link (§1.1a) points to a batch file in
another project, or is dangling: abort with an error naming the linked path, for every
invocation including `--abort` (except for a dangling link, see `--abort`). The other batch must
be completed or abandoned in its own project first (PRD.md §7).

**No batch file** — `BATCH_IN_PROGRESS.json` does not exist:

```
Error: no batch found. Run 'jbr-prepare <file.run.toml>' first.
```

**Batch currently running** — `BATCH_IN_PROGRESS.json` exists and a liveness check
determines that at least one run is actively in progress. The liveness check is:

1. For each run entry, look for `<result_dir>/run-info.json` with `"status": "RUNNING"`.
2. For each such entry, test whether the recorded `pid` is still alive (`kill -0 <pid>`).
3. If any PID is alive: the batch is **genuinely running** → reject fresh start, resume, and
   `--abort` with:

```
Error: a batch is currently running. Do not disturb.
  Progress: 3/8 runs complete
  Started:  2025-06-01 14:32

  Wait for it to finish, or use jbr-status to check progress.
```

4. If all `RUNNING` entries have a dead PID (or there are no `RUNNING` entries): the batch is
   **idle or orphaned** → all invocations including `--abort` are permitted.

> **PID reuse note:** `kill -0` can return true for an unrelated process that inherited the same
> PID after the benchmark died. This window is negligibly small; no special handling is required.

### 1.1a `active-batch` link

`$XDG_STATE_HOME/jbenchrunner/active-batch` (default `~/.local/state/jbenchrunner/active-batch`)
is a symlink to the absolute path of the active batch's `BATCH_IN_PROGRESS.json` (PRD.md §7,
D-73). It carries no other information: batch ID, hostname and liveness are read from the batch
file and the runs' `run-info.json`.

- **Set** by `jbr-execute` before the first run it executes (§1.3 step 4c), creating the
  directory if needed. If the link already points to this project's batch file (a resumed batch),
  it is left as is.
- **Removed** on batch completion (§1.3 step 6) and by `--abort`.
- An interrupted batch keeps the link; it is resumed or abandoned in its own project.


### 1.2 Per-run status

The per-run status is stored in `run-info.json` inside the run's result directory (see §4.2).
`BATCH_IN_PROGRESS.json` does **not** carry run status.

A run's status is determined as follows:

1. `PENDING` *(implicit)*: no result directory exists for this run. Not stored anywhere.
2. `RUNNING`: `run-info.json` exists with `"status": "RUNNING"`. The run has been started; or
   the harness crashed mid-run (orphan). Distinguish the two cases via the liveness check (§1.1).
   On resume, a `RUNNING` entry with a dead PID is retried (same as `STOPPED`).
3. `FAILED`: `run-info.json` exists with `"status": "FAILED"`. The benchmark returned exit code != 0,
   or the run was killed after exceeding `--timeout-secs` (see §1.5; `status_info` is `"timeout"`
   in that case). Skipped on resume — the error is assumed to be tied to the benchmark or its
   settings and thus reproducible.
4. `STOPPED`: `run-info.json` exists with `"status": "STOPPED"`. The run was terminated by SIGINT.
   Retried on resume.
5. `DONE`: `run-info.json` exists with `"status": "DONE"`. The run completed successfully.
   Skipped on resume.


### 1.3 Execution Steps

**Sudo check (only when `sudo_mode` is `true` in `BATCH_IN_PROGRESS.json`):** before any run,
check with `sudo -n -l <command>` that sudo allows, without a password, one root command of each
feature the batch needs: `tee /proc/sys/vm/drop_caches` if machine stabilization is active (see
below), `shutdown -h now` with `--shutdown` (§1.6). If not, abort with an error naming the
features and pointing to `config/jbenchrunner.sudoers`. No sudo ticket is obtained or kept
(PRD.md D-87); an open ticket also passes the check.

**CPU pinning:** When a run entry's effective `do_cpu_pinning` (spec-prepare.md §1.4, §4) is
`true`, `jbr-execute` launches that run's benchmark process wrapped in
`taskset --cpu-list <cpu_list> <command>` (§2 step g), where `cpu_list` is the batch's `cpu_list`
(spec-prepare.md §1.6, §4). Because `taskset` execs into the target process rather than forking,
the resulting PID is unchanged, so no PID-based logic (liveness checks §1.1, SIGINT/timeout
handling §1.4/§1.5) needs any special-casing. An invalid `cpu_list` or missing `taskset` binary
causes the wrapped process to exit almost immediately, which the existing 5-second
collector-startup check (§5.3) already catches as a failed run — no additional error handling is
needed. No sudo is required: setting a process's own CPU affinity is unprivileged.

**Machine stabilization:** When `stabilize_machine` (`jbr-prepare --stabilize-machine`, an
explicit opt-in, spec-prepare.md §3, §4; default `false`) is `true`, `jbr-execute` runs `prepare-for-benchmarks.sh` once, at batch start (step 4c2),
on every invocation including resumes (a reboot in between loses the settings). Like
`reset-machine.sh`, the script runs as the invoking user and uses `sudo -n` only for the commands
that need root (writing sysfs/procfs files, `sysctl`, `systemctl stop`, `swapoff`). The script sets
the CPU governor to `performance`, switches off turbo boost (Intel `intel_pstate/no_turbo` or
`cpufreq/boost`), optionally fixes the CPU frequency, disables ASLR, sets
`perf_event_paranoid=0` (which gives the unprivileged perf collector kernel-level counts, PRD.md
D-85), syncs and drops caches, stops non-essential services, switches swap off
and sets strict overcommit (`vm.overcommit_memory=2`, ratio 100). Nothing is reverted afterwards.
Settings the machine does not support (no cpufreq, no known turbo control, no `systemctl`) are
skipped with a warning by the script; any other failure of the script aborts the batch.

In addition, `jbr-execute` resets machine state (sync, drop caches, restart systemd-journald) via
`reset-machine.sh` (which uses `sudo -n` itself) before every executed run, including the first — see step 5. A failing
reset aborts the batch.

Both require `sudo_mode` (`jbr-prepare` refuses `--stabilize-machine` without `--sudo`; a batch
file that has it without `sudo_mode` gets stabilization and reset skipped with one warning). On
non-Linux they are likewise skipped with a warning.

The settings weaken security (e.g. ASLR off, `perf_event_paranoid=0`) and last until reboot.
Therefore, whenever this `jbr-execute` invocation has started `prepare-for-benchmarks.sh`, it ends
with a warning that recommends rebooting the machine, however the batch ends: completed, stopped
by Ctrl-C, or failed. Only when `--shutdown` has powered the machine off is it left out (PRD.md
D-88).

**Preparation:**

1. Read `BATCH_IN_PROGRESS.json`. All resolved run state is already present — no input
   files are re-read.
2. Check that the current hostname matches the `hostname` field in `BATCH_IN_PROGRESS.json`.
   If they differ, abort with an error:
   ```
   Error: this batch was prepared on '<original-host>' but the current host is '<this-host>'.
   Executing a batch on a different machine is not supported.
   Use 'jbr-execute --abort' to abandon it.
   ```
   Then check that the batch has a top-level `collectors` (spec-prepare.md §4). A batch without
   it was prepared by an older `jbr-prepare`, which stored the collector settings per run; it is
   refused, not read in the old format (PRD.md D-84):
   ```
   Error: this batch was prepared by an older jbr-prepare (collector settings per run instead of batch-wide).
   Prepare it again with 'jbr-prepare', or use 'jbr-execute --abort' to abandon it.
   ```
3. Run the liveness check (§1.1). Abort with the "currently running" error if a live PID is found.
4. If `ramdisk_path` is set in `BATCH_IN_PROGRESS.json` (§6), run `lsof <ramdisk_path>` once, before
   the execution loop starts. This is a best-effort, purely diagnostic check for processes still
   holding files open on the ramdisk from a previous run (e.g. a crashed benchmark JVM) — if `lsof`
   reports any, print a warning listing them to stdout. Never fails the run: a missing `lsof` binary,
   a non-zero exit (no matches), or any other error is silently ignored.
4b. If `ramdisk_path` is set, also verify — once, before the execution loop starts — that it is
    actually backed by a RAM-based filesystem, via `stat -f -c %T <ramdisk_path>` (§6). If the
    filesystem type is determined and is neither `tmpfs` nor `ramfs`, abort with an error: a
    misconfigured `ramdisk_path` silently defeats the point of using one. If the type can't be
    determined (e.g. non-Linux `stat`), this is best-effort: print a warning and continue.
4c. Set the `active-batch` link to this project's `BATCH_IN_PROGRESS.json` (§1.1a).
4c2. If machine stabilization is active (see above), run `prepare-for-benchmarks.sh`. Abort if it
    fails.
4d. Linux only: check, once before the execution loop starts, that the machine looks stabilized,
    and print a warning to stdout for each check that fails. Never fails the run. Currently the
    only check is the CPU governor: every
    `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` must read `performance`; otherwise the
    warning gives the number of affected CPUs and the governors seen. If no such file exists
    (no cpufreq, e.g. in a VM), a warning says the governor could not be verified. If `perf` is
    in `collectors.enabled`, also `perf_event_paranoid` must be 0 or lower (the level
    stabilization sets, PRD.md D-85); otherwise the warning names the level and what perf loses at
    it. An unreadable level is not reported.
4e. If `perf` is in `collectors.enabled`: check the batch's perf command once (PRD.md D-86), with
    `perf-logger.sh --check [--counters <events>]` (§5.1): the collector's own command line,
    counting `true` instead of the JVM. If perf exits non-zero (unknown event, missing
    privileges, ...) or reports an event as `<not supported>`, abort the batch before the first
    run, with perf's output and a hint: fix the host and run `jbr-execute` again, or abandon the
    batch (`--abort`) and prepare it again with other events. Runs on every invocation, including
    resumes.

**Execution:**

5. Execute run entries in order per §2, applying the following filter (status determined per §1.2):
   - `DONE` or `FAILED` → skip.
   - `STOPPED`, or `RUNNING` with a dead PID (orphan) → retry. Remove the old result directory
     first; the run re-uses the same `runid` and `result_dir`.
   - `PENDING` (no result directory) → execute. If machine stabilization is active (see above), run
     `reset-machine.sh` immediately before this run, whether it's the first or a later one.

   > **Note:** a fresh batch (all entries `PENDING`) and a resumed interrupted batch are handled
   > identically by this filter. There is no separate "fresh start" mode.

6. A batch run counts as completed if all runs are either in `DONE` or `FAILED` state. 
   On completion of all entries:
   - rename `BATCH_IN_PROGRESS.json` → `batches/<batchid>/batch.json`
   - remove the `active-batch` link (§1.1a)

7. Print batch summary: total runs, succeeded, failed.
8. If `--shutdown` was given, run the shutdown sequence (§1.6). Otherwise, if the machine was
   stabilized, print the reboot recommendation (see Machine stabilization above); it is also
   printed when the batch ends early.


### 1.4 SIGINT (Ctrl-C) Handling

`jbr-execute` installs a SIGINT handler at startup that applies throughout the entire process
lifetime.

**Between runs** (i.e. during preparation, between two per-run executions, or during the final
rename/summary): stop immediately. No `run-info.json` is written for any not-yet-started run.
Runs without a result directory remain implicitly `PENDING` and the batch can be re-executed by
running `jbr-execute` again.

**During a run** (i.e. while waiting at step j of §2):

1. Send `SIGTERM` to `$jvm_pid`. Wait up to **3 seconds** for it to exit.
2. If still running after 3 seconds, send `SIGKILL`.
3. Proceed with the normal wind-down sequence (steps k–n of §2) with these differences:
   - `run-info.json` final write (step l): `status` is `"STOPPED"` (see §4.2).
   - The batch stops unconditionally after step n.


### 1.5 `--timeout-secs` (Hung-JVM Kill)

Some benchmark configurations — most notably a heap size too small for the workload under a
non-recovering GC algorithm — cause the JVM to hang indefinitely instead of exiting with an
error. `--timeout-secs N` bounds how long `jbr-execute` will wait for a single run before
concluding it is hung and killing it.

**During a run** (i.e. while waiting at step j of §2), on every poll iteration: if wall-clock
time since JVM launch (`time`) has reached `N` seconds:

1. Send `SIGTERM` to `$jvm_pid`. Wait up to **3 seconds** for it to exit.
2. If still running after 3 seconds, send `SIGKILL`. (Same teardown as §1.4 step 1–2.)
3. Proceed with the normal wind-down sequence (steps k–n of §2) with these differences:
   - `run-info.json` final write (step l): `status` is `"FAILED"`, `status_info` is `"timeout"`
     (see §4.2).
   - The run counts as a normal failure for `n` purposes — no special-casing of batch
     continuation, unlike SIGINT.

Unlike SIGINT and the collector hard-failure path (both of which use `"STOPPED"`, retried on
resume), a timeout uses `"FAILED"`, which is **not** retried on resume (§1.2): a hang caused by
a run's own settings (e.g. its heap size) is deterministic and would time out again identically,
so silently retrying it would just waste another `N` seconds.

Only the JVM process itself is signalled — not its process group. This assumes the JVM does not
spawn child processes of its own that would also need killing; revisit if that assumption stops
holding.

### 1.6 `--shutdown` (Post-Batch Power-Off)

Requires the batch to be in `--sudo` mode (`sudo_mode: true` in `BATCH_IN_PROGRESS.json`);
rejected with an error up front, before any run starts, if it is not, or if the sudoers rules
do not allow `shutdown -h now` (§1.3 sudo check).

Fires only after step 7 of §1.3 — i.e. only on a full, successful batch completion. It does
**not** fire when the batch is stopped early: on SIGINT (§1.4), or when the executor exits via
`--abort` or an error before reaching step 6. `--shutdown` and `--abort` are mutually exclusive.

Sequence:

1. Broadcast a warning to all logged-in terminals via `wall`.
2. Wait 5 minutes. Interruptible: Ctrl-C during the wait cancels the shutdown (the batch itself
   has already completed at this point, so nothing else is affected).
3. Run `sudo -n shutdown -h now`.

Linux only (`wall`, `shutdown -h`).

---

## 2 Per-Run Execution Steps

For each run entry to be executed:

a. Create `results/<RUNID>/` — or, if `ramdisk_path` is set and this run's effective `use_ramdisk`
   is `true`, create the real directory under `ramdisk_path` and make `results/<RUNID>` a symlink
   to it (see §6 for the full mechanics, including the live capacity check and `-v`/`--verbose`
   output).
b. Print a progress header.
c. Enter the run's result directory. The rest of the steps happen with the result directory as the
   current directory.
d. (specjbb only) copy `<specjbb_dir>/config` directory to the current directory.
e. **JDK detection** — run `<java_binary> -version 2>&1` and capture the combined output as
   `full_version_string`. From `full_version_string` (parser shared with `jbr-prepare`,
   `scripts/common/jdk_detect.py`):
   - Parse `version`: the numeric prefix of the first quoted version string (`"21.0.4"` ->
     `21.0.4`, `"28-internal"` -> `28`, `"1.8.0_362"` -> `1.8.0`), and `major`: its first
     component, or the second for the legacy `1.x` scheme (Java 8 -> `8`).
   - Detect `jdk.type`: if any line contains the string `Semeru` or `OpenJ9`, the type is
     `openj9`; otherwise the type is `openjdk`.
   - The detected values are authoritative: `info.json` always reflects the JDK the benchmark
     actually ran with, and collector gating (§5.1) uses them. If the run entry carries
     `jdk_type`/`jdk_major` (spec-prepare.md §4) and either disagrees with the detected value,
     the JDK at `java_home` changed since prepare (so the run's flags, version filtering and
     collector selection were decided for a different JDK); a warning is printed and the run
     continues. Entries without these fields (batches prepared before they existed) are not checked.
   If the invocation fails (non-zero exit code, the binary is not found, or no version can be
   parsed), abort the batch immediately.
f. Write `info.json` with all run parameters (see §4.1), populating the `jdk` fields from the
   detection results of step e.
   Written atomically (temp file + rename) before the JVM is launched.
g. Launch the benchmark process as a background process and record its PID (`jvm_pid=$!`);
   stream stdout → `<result-dir>/stdout.log`, stderr → `<result-dir>/stderr.log`,
   GC log → `<result-dir>/gc.log`. The process environment is the ambient environment of
   `jbr-execute` with the run's effective `jvmenv` (see spec-prepare.md §1.4, §4) merged
   in on top. If the run's effective `do_cpu_pinning` is `true` (see spec-prepare.md §1.4, §4),
   the command below is prefixed with `taskset --cpu-list <cpu_list>` (see "CPU pinning" in §1).
   i. For specjbb: Start `[taskset --cpu-list <cpu_list>] <the javavm> <vm options> -jar <specjbb_dir>/specjbb2015.jar -m COMPOSITE -ikv`.
   ii. For dacapo: Search the benchmark jar (`<dacapo_dir>/dacapo-.*-chopin.jar`). Then:
     `[taskset --cpu-list <cpu_list>] <the javavm> <vm options> -cp <dacapo_jar>:<reporoot>/dacapo-timestamp-plugin/dacapoExtension.jar Harness -c stuefe.dacapo.JBRCallback -n <iterations> -s <datasize> --verbose --print-stats <dacapo_args...> <sub_benchmark>`
     where `<dacapo_args...>` is the run entry's effective `dacapo_args` (spec-prepare.md §1.4, §4),
     inserted verbatim before the trailing sub-benchmark name per DaCapo Harness's own
     `Harness [options] <benchmark>` syntax; empty when not configured.

   (the bracketed `taskset` prefix is present only when `do_cpu_pinning` is active for this run)
h. Write an initial `run-info.json` atomically (temp file + rename) to the result directory
   with `status=RUNNING`, `pid=<jvm_pid>`, and `time` set to the JVM launch timestamp
   (see §4.2). This is the liveness sentinel: if the harness crashes after this point, the
   PID-liveness check (§1.1) will detect the orphan.
i. **Start collectors and verify startup** (see §5.3 for full details).
   PID-specific collectors receive `$jvm_pid` as their positional argument.
j. Wait for benchmark end (`wait $jvm_pid`). If `--timeout-secs` is set and exceeded, the JVM is
   killed instead (see §1.5).
k. **Stop collectors** (see §5.3 for full details).
l. Overwrite `run-info.json` atomically (temp file + rename) with the final record: `status`
   set to `DONE`, `FAILED`, or `STOPPED`; `exit_code`; `duration_secs`; all other fields carried
   forward (see §4.2).
m. Print final summary line for the run: run directory, exit code, wall-clock time.
n. On failure: continue to the next run.

---

## 3 `jbr-status`

Displays batch status information of the current project. Read-only — never modifies any file.
Must be run inside a project (PRD.md §4); outside one it aborts.

```
jbr-status [-a|--all] [--list-runids]
```

| Option | Description |
|---|---|
| `-a`, `--all` | Also show the JVM Path and JVM Args columns (§3.1). Default: off. |
| `--list-runids` | Instead of the table, print only the run IDs of the batch, one per line, without header. |

Behaviour depends on which files are present in the project:

| Condition | Display |
|---|---|
| `BATCH_IN_PROGRESS.json` exists | Prints `"Current batch in progress:"` followed by the batch table. |
| Otherwise, `batches/` holds at least one `batch.json` or `batch-aborted.json` | Prints `"Last finished batch:"` (or `"Last aborted batch:"`) followed by the table of the newest one, by `started_at`. |
| Neither | Prints `"No batch found."` and exits 0. |

In addition, if the `active-batch` link (§1.1a) points to a batch file in another project, or is
dangling, a final line `Note: active batch in <linked path>` is printed.

### 3.1 Batch Table Format

One row per run entry. Columns (the last two only with `--all`):

| Column | Source | Notes |
|---|---|---|
| `#` | `seq` | 1-based sequence number. |
| RUN-ID | `runid` | Clickable link (OSC 8, where the terminal supports it) to the run's result directory. |
| Status | derived | Run status (`PENDING`, `RUNNING`, `DONE`, `FAILED`, `STOPPED`), derived per §1.2 by scanning the run's result directory for `run-info.json`. |
| Info | `run-info.json`'s `status_info` | Short free-text tag giving more context on `status` (e.g. `collector` — see §4.2, §5.3); `-` if absent or the run hasn't started. For a `RUNNING` run, its pid instead. |
| Start | `run-info.json`'s `time` | Timestamp the JVM was launched; `-` if absent or the run hasn't started. |
| Duration | `run-info.json`'s `duration_secs` | Wall-clock seconds; `-` until the run has finished. |
| JDK | `jdkname` | JDK name as stored in the batch file. |
| Benchmark | `bench_json` | `.bench.toml` name. |
| Tags | `tags` | Comma-separated; `-` if none. |
| JVM Path | `java_home` | Only with `--all`. |
| JVM Args | `jvmargs` | Only with `--all`. Assembled flag list, space-joined, with harness-injected/operational flags stripped (shared filter, see spec-analyze.md §1.2.4 and `scripts/common/jvm_flag_filter.py`). |

Above the table, a header prints: batch ID, `Prepared:` (the batch file's `started_at`, set by
`jbr-prepare`), hostname, total/done/failed counts.

---

## 4 Run Result Directory

The run result directory contains all files created by a single benchmark run — temporary and
non-temporary data from the benchmark itself, log files, and run-specific JSON data files.
It is also the current directory during the benchmark run.

Path: `<project>/results/<RUNID>/` where `<RUNID>` is the GUID assigned to the run by `jbr-prepare`.
When `ramdisk_path` is configured, `results/<RUNID>` is a symlink to the real directory on the
ramdisk for the duration of the run, transparently to everything in this section — see §6.

Created by `jbr-execute` before the benchmark process is launched.

| File / Dir | Written when | Contents |
|---|---|---|
| `info.json` | After JDK detection, before JVM launch | Static run information (see §4.1). |
| `stdout.log` | During run | Benchmark process stdout. |
| `stderr.log` | During run | Benchmark process stderr. |
| `gc.log` | During run | JVM GC log. The GC log flag is part of the pre-assembled `jvmargs` in `BATCH_IN_PROGRESS.json` — `jbr-execute` passes it verbatim to the JVM without special handling. Compressed to `gc.log.zst` by post-processing (spec-post-process.md §1). |
| `specjbb-result/` | During run (specjbb only) | specjbb native result tree (HTML, txt, raw). |
| `run-info.json` | Twice: once at JVM launch (status=RUNNING, pid), once after exit (final status, exit code, wallclock) | Sole owner of per-run status and PID (see §4.2). Written atomically both times. |
| `result.json` | Post-processing step (successful runs only) | Extracted & derived metrics (see [spec-post-process.md §2](spec-post-process.md#2-resultsjson-schema)). |

### 4.1 `info.json` Schema

```json
{
  "runid":        "<RUNID>",
  "batchid":      "<BATCHID>",
  "repeat_no":    0,
  "name":         "<compound name>",
  "benchmark":    "dacapo|specjbb",
  "hostname":     "<hostname>",
  "arch":         "<uname -m>",
  "os":           "<uname -sr>",
  "tags":         ["<tag1>", "<tag2>"],

  "jdk": {
    "name":                "<jdk-name>",
    "path":                "<absolute path to java binary>",
    "full_version_string": "<output of java -version 2>&1>",
    "version":             "<numerical version, e.g. 21.0.4>",
    "major":               21,
    "type":                "openjdk|openj9"
  },

  "heap": {
    "gc":        "g1gc",
    "heapsize":  16384
  },
  "heapsize_factor": 2.0,
  "jvmargs":  ["-XX:+UseG1GC", "-Xmx16g"],
  "jvmenv":    { "MALLOC_ARENA_MAX": "4" },
  "cpu_list":   "0-3",
  "collectors": { "interval_secs": 10, "compression": false,
                  "enabled": ["cpu_temp", "pidstat", "proc"],
                  "perf_events": null },

  "dacapo": {
    "sub_benchmark": "h2",
    "datasize":      "large",
    "iterations":    10,
    "dacapo_args":   ["-t", "16"]
  },

  "specjbb": {
    "mode": "composite"
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `runid` | string | Pre-assigned RUNID (GUID) of this run, from `BATCH_IN_PROGRESS.json`'s run entry; equals the run's result directory name. |
| `hostname` | string | Network name of the machine the run executed on (`platform.node()`). |
| `arch` | string | Machine architecture as reported by `uname -m` (`platform.machine()`), e.g. `"x86_64"`, `"aarch64"`. |
| `os` | string | Operating system name and release, `"<uname -s> <uname -r>"`, e.g. `"Linux 6.8.0-45-generic"`. |
| `batchid` | string | GUID of the batch (`BATCH_IN_PROGRESS.json`'s `batchid`) this run was created under. |
| `repeat_no` | integer | 0-based repetition index from `BATCH_IN_PROGRESS.json`'s run entry (`jbr-prepare --repeat`, spec-prepare.md §3, §4). Always present; `0` if the run was not repeated, or if the run entry has no `repeat_no` (batch files written before the field existed). |
| `name` | string | Compound human-readable name for the run, always present. Value is `"specjbb"` for specjbb runs, or `"dacapo.<sub_benchmark>"` for dacapo runs (e.g. `"dacapo.h2"`, `"dacapo.tomcat"`). Must be derived from `benchmark` and, where applicable, `dacapo.sub_benchmark`. |
| `benchmark` | string | Top-level type: always `"dacapo"` or `"specjbb"`. |
| *(mutual exclusion)* | — | The `dacapo` and `specjbb` objects are mutually exclusive; the runner omits the key for the benchmark type that is not running. |
| `dacapo.sub_benchmark` | string | The dacapo benchmark name (e.g. `"h2"`, `"tomcat"`); present only in the `dacapo` object. |
| `dacapo.datasize` | string | The dacapo size parameter (e.g. `"large"`); present only in the `dacapo` object. |
| `dacapo.iterations` | integer | Total iteration count; present only in the `dacapo` object. |
| `dacapo.dacapo_args` | array of strings | Effective extra DaCapo Harness args (see spec-prepare.md §1.4); present only in the `dacapo` object. Always an array; empty if none configured. |
| `specjbb.mode` | string | Always `"composite"`; present only in the `specjbb` object. |
| `jdk.name` | string | The `jdkname` value from `BATCH_IN_PROGRESS.json`, i.e. the name of the `.jdk.toml` file (without extension) in `config/jdks/`. |
| `jdk.full_version_string` | string | Raw multi-line output of `java -version 2>&1`; stored for reference but not displayed in table output. |
| `jdk.version` | string | Numerical version string parsed from `full_version_string` (e.g. `"21.0.4"`). |
| `jdk.type` | string | the jdktype (either `openj9` or `openjdk`) |
| `tags` | array | Array of tag strings for this run. Always present; empty array if no tags were assigned. |
| `heap.heapsize` | integer | Effective heap size in integer MB, from `BATCH_IN_PROGRESS.json`. |
| `heap.gc` | string or null | GC collector name, deduced from `jvmargs` by `detect_gc()` (spec-execute.md §4.1): HotSpot `-XX:+Use<Name>GC` flags map to `"<name>gc"` (e.g. `-XX:+UseG1GC` → `"g1gc"`, `-XX:+UseZGC` → `"zgc"`, `-XX:+UseShenandoahGC` → `"shenandoahgc"`); OpenJ9 `-Xgcpolicy:<policy>` flags map to `"<policy>"` (e.g. `"gencon"`, `"metronome"`). If more than one GC flag is present, the last one (in `jvmargs` order) wins. `null` if no GC flag is present — the JVM's own built-in default then applies and is not guessed. |
| `heapsize_factor` | number or null | Heap-sweep factor from `BATCH_IN_PROGRESS.json`'s `heapsize_factor` (see spec-prepare.md §1.5), e.g. `2.0`. Always present; `null` if the run was not part of a heap-size sweep. |
| `jvmargs` | array | The complete flat ordered list of flags passed to the JVM. What you see is what ran. |
| `jvmenv` | object (string values) | Effective `jvmenv` from `BATCH_IN_PROGRESS.json`, merged with the ambient environment and set on the JVM subprocess. Always an object; empty object if none was configured. |
| `cpu_list` | string or null | The batch's `cpu_list` (spec-prepare.md §1.6, §4) if this run's effective `do_cpu_pinning` was `true`; `null` otherwise. Always present (same "always present, null if inactive" convention as `heapsize_factor`). |
| `collectors` | object | Copied verbatim from `BATCH_IN_PROGRESS.json`'s batch-wide `collectors` (spec-prepare.md §4), the same for every run of the batch: `interval_secs` (the `-i` all collectors were started with, §5), `compression` (§5.2), `enabled` (short names of the collectors started for this run — the batch's fully-resolved set, gated by the machine probe and `restrict_to`; spec-prepare.md §1.4.1) and `perf_events` (the events passed to `perf stat -e`, or `null` for perf's default set; spec-prepare.md §1.4.2). If `enabled` contains `nmt`, the run's `jvmargs` include the NMT flags (spec-prepare.md §2 step 6). |

### 4.2 `run-info.json` Schema

`run-info.json` is the sole owner of per-run status. It is written **twice** by `jbr-execute`,
both times atomically (temp file + rename):

1. **At JVM launch** (step h of §2): initial write with `status=RUNNING` and `pid` set.
   `exit_code` and `duration_secs` are absent at this point.
2. **After benchmark exit** (step l of §2): final overwrite with terminal status, `exit_code`,
   and `duration_secs` filled in. The `pid` field is retained unchanged.

Example — initial write (JVM just launched):

```json
{
  "status":    "RUNNING",
  "pid":       98765,
  "time":      "2025-06-01T14:32:00.000+02:00",
  "sudo_mode": false
}
```

Example — initial write, this run's result directory is on a ramdisk (see §6):

```json
{
  "status":          "RUNNING",
  "pid":             98765,
  "time":            "2025-06-01T14:32:00.000+02:00",
  "sudo_mode":       false,
  "ramdisk_path":    "/dev/shm/jbenchrunner",
  "ramdisk_size_mb": 8192,
  "ramdisk_fs_type": "tmpfs"
}
```

Example — final write for a successful run:

```json
{
  "status":        "DONE",
  "pid":           98765,
  "exit_code":     0,
  "duration_secs": 342.7,
  "time":          "2025-06-01T14:32:00.000+02:00",
  "sudo_mode":     false
}
```

Example — final write for a run killed by Ctrl+C:

```json
{
  "status":        "STOPPED",
  "pid":           98765,
  "exit_code":     137,
  "duration_secs": 45.1,
  "time":          "2025-06-01T14:32:00.000+02:00",
  "sudo_mode":     false
}
```

Example — final write for a run killed because an enabled collector failed to
start (see §5.3):

```json
{
  "status":        "STOPPED",
  "pid":           98765,
  "exit_code":     137,
  "duration_secs": 5.2,
  "time":          "2025-06-01T14:32:00.000+02:00",
  "sudo_mode":     false,
  "status_info":   "collector"
}
```

Example — final write for a run killed by `--timeout-secs` (see §1.5):

```json
{
  "status":        "FAILED",
  "pid":           98765,
  "exit_code":     137,
  "duration_secs": 3600.2,
  "time":          "2025-06-01T14:32:00.000+02:00",
  "sudo_mode":     false,
  "status_info":   "timeout"
}
```

| Field | Type | Present | Notes |
|---|---|---|---|
| `status` | string | always | `"RUNNING"` after JVM launch; `"DONE"` if `exit_code == 0` and run completed normally; `"FAILED"` if `exit_code != 0`, or if the run was killed by `--timeout-secs` (see §1.5); `"STOPPED"` if terminated by SIGINT (see §1.4) or by a collector hard-failure (see §5.3) — both are retried on resume (§1.2), unlike a timeout. |
| `pid` | integer | always | OS PID of the JVM benchmark process. Used by the liveness check (§1.1). |
| `exit_code` | integer | final write only | Exit code of the JVM benchmark process. |
| `duration_secs` | number | final write only | Wall-clock duration of the benchmark process in seconds. |
| `time` | string | always | Moment the JVM process was launched, in the collector timestamp format (§5.2): local time with millisecond precision and UTC offset, e.g. `2026-09-23T03:20:24.123+02:00`. This is the run's start time as shown and filtered by `jbr-ls`/`jbr-filt` (`time`, `--since`/`--until`). |
| `sudo_mode` | boolean | always | `true` if the batch's `sudo_mode` (spec-prepare.md §4) is `true`; `false` otherwise. |
| `status_info` | string | optional | Short free-text tag giving more context on `status`. Absent for the ordinary DONE/FAILED/STOPPED-by-Ctrl-C cases. Defined values: `"collector"`, written when an enabled collector no longer applies to the run's JDK (§5.1) or failed to start within the 5-second startup check (§5.3) and the JVM was killed as a result; `"timeout"`, written when the run was killed by `--timeout-secs` (§1.5). For any other FAILED run (JVM exit code != 0), the result directory is classified after exit, first match wins: `"crash"` — killed by SIGABRT and an `hs_err_pid*.log` (OpenJDK) or `javacore*` (OpenJ9) file exists; `"signal <N>"` — killed by signal N (exit code `-N`, or `128+N` if via a shell); `"oom"` — the last 100 lines of `stderr.log` contain `OutOfMemoryError` (not as part of a longer identifier such as a flag name); `"version mismatch"` — `stderr.log` contains `Only Java versions .* are supported` (h2o) or `Java version .* is incompat` (cassandra). If none matches, `status_info` is absent. Only `stderr.log` is scanned, since `stdout.log` holds the `-XX:+PrintFlagsFinal` dump. Implemented in `failure_classifier.py`; `scripts/helpers/migrate-status-info.py` back-fills older runs. |
| `ramdisk_path` | string | optional, always once set | Present only when this run's result directory was created on a ramdisk (§6). Absolute path, copied from `BATCH_IN_PROGRESS.json`. Written at the initial write and carried forward unchanged. |
| `ramdisk_size_mb` | integer | optional, always once set | Present alongside `ramdisk_path`. Live total size of the ramdisk filesystem in MB at the moment this run started, from `shutil.disk_usage` (§6). |
| `ramdisk_fs_type` | string | optional, best-effort | Present alongside `ramdisk_path` when detection succeeds (e.g. `"tmpfs"`, `"ramfs"`), via `stat -f -c %T <ramdisk_path>` (§6). Silently omitted if that command fails — not worth failing a run over. |

---

## 5 Data Sampling

During each benchmark run, `jbr-execute` starts background data collectors immediately after
the JVM benchmark process is launched, and stops them once the benchmark exits.

### 5.1 Collectors

Collectors are scripts in the `collectors/` subdirectory named `*-logger.py` or `*-logger.sh`.
They periodically sample data and write records to stdout. The sampling interval is controlled by
the batch's `collectors.interval_secs` in `BATCH_IN_PROGRESS.json` (default: **10 seconds**), which is
passed to every collector via the mandatory `-i` argument.
`jbr-execute` is responsible for redirecting stdout and stderr to files in the result directory.

| Script | Metric | PID arg | Extra required args | Self-terminates |
|---|---|---|---|---|
| `collectors/cpu-temp-logger.py` | CPU package temperature (via `collectors/read-cpu-temp.sh`) | yes | — | yes — exits when the given PID disappears (the PID is only a liveness anchor; the temperature is machine-wide) |
| `collectors/pidstat-logger.sh` | Process CPU, memory, and I/O (via `pidstat -h -H -rdu`) | yes | — | yes — exits when monitored PID disappears |
| `collectors/proc-logger.py` | raw `/proc/<pid>/stat` and `/proc/<pid>/status` | yes | — | yes — exits when monitored PID disappears |
| `collectors/perf-logger.sh` | Hardware performance counters (via `perf stat`) | yes | `--counters <counter-list>`, optional: absent = perf's default event set | yes — exits when monitored PID disappears |

`perf-logger.sh` runs

```
perf stat -x ';' [-e <counter-list>] --interval-print <interval_ms> -p <pid> [--interval-count <num>]
```

`-x ';'` selects perf's CSV output, with `;` as separator because event names may contain commas
(`cpu/event=0x3c,umask=0/`). Without `--counters` there is no `-e`: perf counts its default set,
which differs between CPUs, kernels and perf versions, and the log records whatever it reports
(PRD.md D-81). perf always runs as the invoking user, never via sudo (PRD.md D-85). What it can
count depends on `perf_event_paranoid`: at 1 or lower (machine stabilization sets 0, §1.3), user
and kernel events; at 2, user space only (perf falls back on its own with a warning, which ends up
in the log and is skipped by post-processing, and the events carry a `:u` modifier, e.g.
`cycles:u`); at 3 or higher, nothing, which `jbr-prepare` catches by dropping `perf` from the
batch (spec-prepare.md §3 step 1d-collectors).

`perf-logger.sh --check [--counters <counter-list>]` runs the same command once on `true`
instead of a process (`perf stat -x ';' [-e <counter-list>] -- true`), prints perf's output
(stdout and stderr) to stdout and exits with perf's exit code. `jbr-execute` uses it for the
check at batch start (§1.3 step 4e); it takes no pid, `--interval` or `--num`.

**Design invariant — every collector takes the JVM PID and exits when it disappears:** every
collector receives the JVM PID as its single required positional argument and terminates on its
own once that process no longer exists, including a collector that measures machine state rather
than the process (`cpu-temp-logger.py`). Collectors are launched detached from the terminal
(§5.3), so if the runner dies before it can stop them, the PID is the only thing that ends them.
A collector with its own sleep loop (`cpu-temp-logger.py`, `proc-logger.py`,
`jcmd-logger.sh`) sleeps in slices of at most 1 second and checks the PID
between slices, so it notices the JVM's death within about a second however large
the interval is (long benchmark runs use large intervals). `waitpid` is not an option:
the JVM is a child of `jbr-execute`, not of the collector. `pidstat-logger.sh` and
`perf-logger.sh` need no such loop: the wrapped tool ends when its target does. The Python
collectors share `collectors/_pidwait.py` for this. See PRD.md D-59.

Every collector (including the jcmd-based ones below) is started only when its short name
(spec-prepare.md §1.4.1) is in the batch's `collectors.enabled` in `BATCH_IN_PROGRESS.json`, and
every run starts all of them: the set is the same for every run (PRD.md D-84). That list is final:
`jbr-prepare` already removed every collector this machine failed to run in its probe
(spec-prepare.md §1.6), whose `restrict_to` does not cover the JDK of every run, or — for `perf`
— when perf could count nothing on this host without stabilization (spec-prepare.md §3 step
1d-collectors). `jbr-execute` does not probe again; it only checks the perf command once at batch
start (§1.3 step 4e).

As a safety net, before starting any collector, `jbr-execute` checks that every enabled
collector's `config/collectors/<name>.collector.toml` `restrict_to` (spec-prepare.md §1.10) covers
the JDK type and major version detected for the run (§2, `info.json` `jdk.type`/`jdk.major`). If
one does not — the JDK at `java_home` changed since `jbr-prepare` — the run would deviate from the
batch's set, so it is a hard failure, handled like a collector that fails to start (§5.3): no
collector is started, the JVM is killed, and the run is `STOPPED` with `status_info="collector"`.
A missing collector config file is a hard error.

**Design invariant — collectors must fail fast:** a collector's first sample (or first
invocation of whatever external tool it wraps) must happen immediately on startup, before its
first loop-internal sleep. This is what lets the 5-second startup check (§5.3) actually catch a
broken external tool, a missing binary, or a permission problem — a collector that only takes
its first sample after the collector interval (which may exceed 5s) would not be caught
by that check. All collectors currently satisfy this by construction, via one of three patterns:
`cpu-temp-logger.py` and `proc-logger.py` sample, print, *then* sleep in a Python loop;
`perf-logger.sh` and `pidstat-logger.sh` instead exec their external tool once, in the
foreground, using its own built-in repeat-interval feature — a broken `perf`/`pidstat` binary or
bad argument fails immediately rather than after waiting out the first interval; `jcmd-logger.sh`
(§5.1b) drives its own loop (jcmd has no repeat-interval feature of its own) but, like the Python
collectors, samples first and sleeps after. Keep one of these three patterns for any future
collector.

#### 5.1b jcmd-based Collectors

A second category of collector, distinct from the four above in what it needs: `jcmd` is part of
the JDK under test, not a host capability, so only `restrict_to` decides whether it applies.
Like the others, each is started only when it is in the batch's `collectors.enabled`. All share one
generic script,
`collectors/jcmd-logger.sh`, parameterized by the jcmd subcommand to run:

| Collector | jcmd subcommand | Short name in `collectors.enabled` |
|---|---|---|
| NMT | `VM.native_memory summary` | `nmt` (HotSpot only, via `restrict_to`, see spec-prepare.md §1.10) |
| System.map | `System.map` | `system_map` (HotSpot only, via `restrict_to`, see spec-prepare.md §1.10) |
| Thread.print | `Thread.print` | `thread_print` (no `restrict_to` — available on both HotSpot and OpenJ9, output format differs) |

`jcmd-logger.sh` is invoked as `jcmd-logger.sh -i <interval_secs> --java-home <run_entry's
java_home> --cmd "<subcommand>" <jvm_pid>` — `--java-home` is needed because `jcmd` must be the
one bundled with the run's own JDK (`<java_home>/bin/jcmd`), not whatever happens to be on
`PATH`. It self-terminates when the monitored PID disappears, same as `pidstat-logger.sh` /
`proc-logger.py` / `perf-logger.sh`. Output: an ISO-8601 timestamp line followed by the raw jcmd
output, repeated each interval (see §5.2).

**System.map** output is large; it is stored raw like NMT's (`system.map.log`) and is one of the
compressed logs (§5.2), so `collectors.compression` should be on for System.map runs (prepare warns
otherwise, spec-prepare.md §3 step 1d-collectors). Parsing into per-category totals happens in the
post-process phase (spec-post-process.md §3b).

**Thread.print** runs `jcmd <pid> Thread.print` (no `-l`) on the interval; output format differs
between HotSpot and OpenJ9, but the same command works on both (verified OpenJ9 26+), so its
collector config has no `restrict_to`. Its raw log (`thread-print.log`) is also large and is one
of the compressed logs (§5.2); prepare warns when it's enabled without `collectors.compression`
(spec-prepare.md §3 step 1d-collectors), same as System.map. HotSpot dumps are parsed into
per-thread data and thread counts in the post-process phase (spec-post-process.md §3c); OpenJ9
dumps are not parsed.

Adding a further jcmd-based collector (e.g. `VM.info`, `VM.metaspace`, or the perturbing `GC.run`
— see PRD.md decisions log) is a one-line addition to `_JCMD_COLLECTOR_DEFS` in `execute.py` plus
a new short name in `COLLECTORS` in `config_loader.py` (spec-prepare.md §1.4.1) plus its
`config/collectors/<name>.collector.toml` — no new script.

### 5.2 Raw Log Files

Each collector's stdout and stderr are redirected to separate files in the result directory:

| File | Source collector | Contents |
|---|---|---|
| `temps.jsonl` | `cpu-temp-logger.py` stdout | JSON Lines; one record per sample, ordered `timestamp`, `temperature` (milli-°C) |
| `temps.err` | `cpu-temp-logger.py` stderr | Diagnostic messages |
| `pidstat.log` | `pidstat-logger.sh` stdout | ISO-8601 start timestamp, "Monitoring PID …" line, then raw `pidstat -h -H -rdu` output (built-in repeat interval, one flattened line per sample) |
| `pidstat.err` | `pidstat-logger.sh` stderr | Diagnostic messages |
| `pidstat.jsonl` | `post-process-pidstat-log.py` stdout | JSON Lines; one record per pidstat interval, ordered `timestamp`, `pid`, then all `pidstat -rdu` fields |
| `proclog.log` | `proc-logger.py` stdout | ISO-8601 timestamp line, then `=== /proc/<pid>/stat ===` and the raw file, then `=== /proc/<pid>/status ===` and the raw file, repeated each interval |
| `proclog.err` | `proc-logger.py` stderr | Diagnostic messages |
| `proclog.jsonl` | `post-process-proc-log.py` stdout | JSON Lines; one record per sample, ordered `timestamp`, `pid`, `proc_pid_stat`, `status` — see spec-post-process.md §2.5 |
| `perflog.log` | `perf-logger.sh` stdout | ISO-8601 start timestamp, "Monitoring PID …" line, then raw `perf stat -x ';' --interval-print` CSV output: one line per event and interval, fields interval time, value, unit, event, run time, enabled pct, metric value, metric unit |
| `perflog.err` | `perf-logger.sh` stderr | Diagnostic messages |
| `perflog.jsonl` | `post-process-perf-log.py` stdout | JSON Lines; one record per perf interval, ordered `timestamp`, `pid`, `counters`. `counters` maps each event (name with every character outside `A-Za-z0-9_` replaced by `_`) to `value` (a number, not always an integer, e.g. `task-clock` in msec; `null` for `<not counted>`/`<not supported>`), `unit` (or `null`) and `multiplex_pct`. perf's derived metric columns are not kept |
| `nmt.log` | `jcmd-logger.sh` (NMT instance, §5.1b) stdout | ISO-8601 timestamp line, then raw `jcmd <pid> VM.native_memory summary` output, repeated each interval |
| `nmt.err` | `jcmd-logger.sh` (NMT instance) stderr | Diagnostic messages |
| `nmt.jsonl` | `post-process-nmt-log.py` stdout | JSON Lines; one record per NMT sample — see spec-post-process.md §3 |
| `system.map.log` | `jcmd-logger.sh` (System.map instance, §5.1b) stdout | ISO-8601 timestamp line, then raw `jcmd <pid> System.map` output, repeated each interval |
| `system.map.err` | `jcmd-logger.sh` (System.map instance) stderr | Diagnostic messages |
| `system.map.jsonl` | `post-process-system-map-log.py` stdout | JSON Lines; one record per System.map sample — see spec-post-process.md §3b |
| `thread-print.log` | `jcmd-logger.sh` (Thread.print instance, §5.1b) stdout | ISO-8601 timestamp line, then raw `jcmd <pid> Thread.print` output, repeated each interval |
| `thread-print.err` | `jcmd-logger.sh` (Thread.print instance) stderr | Diagnostic messages |
| `thread-print.jsonl` | `post-process-thread-print-log.py` stdout | JSON Lines; one record per Thread.print sample (HotSpot only) — see spec-post-process.md §3c |

#### Timestamp format

Every timestamp a collector prints (the per-sample lines of `jcmd-logger.sh` and `proc-logger.py`, the start line of `perf-logger.sh` and `pidstat-logger.sh`, the `timestamp` field of `temps.jsonl`) and `run-info.json`'s `time` (§4.2) have one format: local time with millisecond precision and a colon-separated UTC offset, e.g. `2026-09-22T20:29:03.123+02:00` (bash: `date +%Y-%m-%dT%H:%M:%S.%3N%:z`; Python: `scripts/common/collector_timestamp.py`). The post-processing accepts only this format, and writes the `timestamp` of every derived `*.jsonl` the same way. Logs from before this format are converted with `scripts/helpers/migrate-timestamps.py` (PRD.md D-67).

#### Compressed logs (`collectors.compression`)

When the batch's `collectors.compression` is `true` (spec-prepare.md §1.4.1, §4), the stdout of a
fixed set of collectors is piped through a separate `zstd` process and stored as `<file>.zst`
instead of `<file>`. The set is one constant in `execute.py`; it contains the NMT, System.map,
Thread.print and proclog raw logs: `nmt.log` becomes `nmt.log.zst`, `system.map.log` becomes
`system.map.log.zst`, `thread-print.log` becomes `thread-print.log.zst`, `proclog.log` becomes
`proclog.log.zst`. `.err` files are never compressed. The collector
scripts themselves are unchanged and unaware of the compression.

- The compressor is `zstd -1 -q -T1 -c`: stdin is the collector's stdout pipe, stdout is the
  `.zst` file (`-c`, not `-o`, so zstd never installs its own delete-partial-file interrupt
  handler). It runs with the same `taskset --cpu-list <cpu_list_harness>` as the collectors when
  CPU pinning is active, and at the collectors' own priority: no `nice`, since a starved
  compressor would fill the 64 KB pipe and block its collector on `write()` (priority inversion
  on the shared harness CPUs).
- Streaming, constant memory. The zstd CLI does not flush per sample: it compresses in blocks
  of up to 128 KB and writes at EOF. A clean shutdown loses nothing; a hard kill of `zstd`
  loses what it still buffers and leaves a truncated frame, readable up to the last written
  block. This is an accepted risk (PRD.md D-58).
- Readers (`jbr-pp`, the `plot-*.py` scripts) open a collector file through one shared helper
  in `scripts/common/` that finds `<file>` or, failing that, `<file>.zst`, and reads the latter
  via `zstd -dc`. If both exist, the plain file wins, with a warning. A truncated final frame
  (zstd reports "premature end" or "unexpected end of file") is a warning; what was decoded
  is used. Any other zstd failure on a compressed file is an error.

### 5.3 Collector Lifecycle (`jbr-execute` responsibility)

Steps i, j, and k of the §2 per-run execution steps. Details:

1. **Start and verify** (step i): all collectors are started as background processes immediately
   after the JVM benchmark process is launched (step g), passing the benchmark PID as the PID
   argument to every collector (§5.1 design invariant). Collectors, and any compressor
   (§5.2), are started in their own process group (`process_group=0`): Ctrl-C from the terminal
   therefore reaches only `jbr-execute`, whose SIGINT handling (§1.4) then stops the JVM and
   the collectors through the sequence in point 2 — the single shutdown path. Before that, the
   `restrict_to` safety net (§5.1) runs; a collector that no longer applies is a hard failure
   handled as below, with `"<names> do not apply to <type> <major>, but the batch requires them"`
   instead of "failed to start". Unlike the JVM they are not put in a new session (PRD.md D-59). All collectors receive
   `-i <interval_secs>` where `interval_secs` is the batch's `collectors.interval_secs` in
   `BATCH_IN_PROGRESS.json`. `perf-logger.sh` gets `--counters` with the comma-joined
   `collectors.perf_events` list from `BATCH_IN_PROGRESS.json`
   (e.g. `--counters cycles,instructions,cache-misses`), or no `--counters` when that is `null`
   (perf's default set); for a jcmd-based collector the required
   `--java-home` and `--cmd` arguments come from the run entry's `java_home` and the collector's
   fixed subcommand (§5.1b), respectively.
   stdout and stderr are redirected to the log files listed in §5.2.

   After starting all collectors, `jbr-execute` waits **5 seconds**, then checks:
   - If the **benchmark process has already exited**: print an error
     (`"benchmark process exited within 5 seconds of startup"`) and proceed directly to the stop
     sequence (point 2 below). The run will be recorded as failed.
   - For each **collector (or compressor, §5.2) that is no longer running**: print an error
     (`"<script-name> failed to start"`; for a compressor, its collector's name plus `zstd`).
     This is a **hard failure** for the run — every
     collector started here is in the batch's `collectors.enabled`, so it was requested and
     passed the machine probe in `jbr-prepare` (§5.1), and its dying within the 5-second
     window means something is actually broken, not merely unavailable. The JVM
     is killed the same way a Ctrl-C would (§1.4: SIGTERM, wait up to 3s, then SIGKILL), the
     remaining collectors are stopped (point 2 below), and `run-info.json` is written with
     `status="STOPPED"` and `status_info="collector"` (§4.2) — retried on resume like any
     other `STOPPED` run (§1.2). The batch continues to the next run.

2. **Stop** (step k): invoked after the benchmark process has exited. The shutdown sequence is:
   a. Wait a fixed grace period of **3 seconds** to allow the collectors (all of which
      self-terminate, §5.1 design invariant) to detect that the benchmark PID has gone and exit
      on their own.
   b. SIGTERM every collector that is still running (i.e. one that did not exit within the grace
      period), wait **2 seconds**, then SIGKILL any that remain, and reap them. Compressors
      (§5.2) are **not** sent SIGTERM or SIGKILL with the collectors: each ends on its own,
      flushing its last block, once its collector — and anything the collector spawned, such
      as an in-flight `jcmd` or a `sleep` slice, which briefly keep the pipe open — is gone
      and the pipe reaches EOF. After all collectors are reaped, each compressor is given up
      to **10 seconds** for that; only a compressor still alive after that is SIGKILLed, since
      a kill loses whatever it still buffers (up to 128 KB of input).
   The same shutdown sequence applies on Ctrl-C.

For matching externally sourced names (e.g. perf values) to internal json names, see `docs/addendum-external-name-translation.md`.

---

## 6 Ramdisk-backed Result Directories

When `ramdisk_path` is set in `BATCH_IN_PROGRESS.json` (spec-prepare.md §1.6, §4) *and* a run's
effective `use_ramdisk` (spec-prepare.md §1.4, §4) is `true`, that run's result directory is
physically created under `ramdisk_path` instead of directly under `results/`, to keep filesystem
IO jitter (from GC logs, collector output, stdout/stderr) off the path being measured.
`results/<runid>` becomes a symlink to the ramdisk directory for the duration of the run, so
every existing status/liveness mechanism (§1.1, §1.2, §3) keeps working completely unchanged —
they simply follow the symlink like any other path. There is no `ramdisk_size` config field: an
explicitly declared expected size was judged superfluous once the actual live size is recorded
per-run (below) instead. There is also no live free-space check before a run — sizing the ramdisk
correctly up front is the user's responsibility; see `docs/recommended-ramdisk-sizes.md` for
guidance.

`use_ramdisk` defaults to `false` (spec-prepare.md §1.4): a configured `ramdisk_path` alone no
longer causes the ramdisk to be used — it must also be opted into, at the batch or per-run level.

### 6.1 Per-run creation (replaces plain directory creation, §2 step a)

If `ramdisk_path` is set and this run's effective `use_ramdisk` is `true`:

1. Create `<ramdisk_path>/<runid>/`.
2. Create `results/<runid>` as a symlink to `<ramdisk_path>/<runid>/`.
3. Record `ramdisk_path`, the live `ramdisk_size_mb` (from `shutil.disk_usage`), and a
   best-effort `ramdisk_fs_type` (via `stat -f -c %T <ramdisk_path>`, silently omitted if that
   fails) into `run-info.json` at its initial (`RUNNING`) write (§4.2); carried forward unchanged
   to the final write.
4. With `-v`/`--verbose`: print `Ramdisk dir: <ramdisk_path>/<runid>`.

Otherwise (`ramdisk_path` not set, or this run's effective `use_ramdisk` is `false`),
`results/<runid>/` is created directly, as before. With `-v`/`--verbose`, if `use_ramdisk` is
`false` for this run: print `No Ramdisk will be used - IO jitter can occur`.

### 6.2 Teardown (new step between §2 steps l and m)

Runs unconditionally — for `DONE`, `FAILED`, and `STOPPED` alike — whenever this run's result
directory was created on a ramdisk:

1. With `-v`/`--verbose`: print `Copying result dir back from ramdisk: <ramdisk_path>/<runid> -> results/<runid>`.
2. Copy `<ramdisk_path>/<runid>/` to `results/.tmp-<runid>` (`shutil.copytree`).
3. Remove the `results/<runid>` symlink.
4. Rename `results/.tmp-<runid>` to `results/<runid>`.
5. Delete `<ramdisk_path>/<runid>/`.

This ordering keeps `results/<runid>` absent for only the duration of a single rename, not the
whole copy, and guarantees the copy is fully complete before it replaces the symlink — a crash
mid-copy leaves the still-intact symlink and ramdisk data in place rather than a half-written
result directory.

### 6.3 Resume (§1.3 step 5)

By the time a run reaches a terminal status, teardown (§6.2) has always already run, so
`results/<runid>` is back to being a plain real directory — no ramdisk-specific handling needed
on retry, same as today. The one exception: if `jbr-execute` itself died mid-run, before
reaching teardown (the existing orphan case, §1.1), `results/<runid>` is still a symlink. In
that case, before retrying: delete its ramdisk target (`<ramdisk_path>/<runid>/`) if it still
exists, then remove the symlink itself.

### 6.4 `--abort`

In addition to its existing behaviour (§1.1), `--abort` removes, for any run in the abandoned
batch that never completed teardown, its `<ramdisk_path>/<runid>` directory and `results/<runid>`
symlink if present — otherwise a ramdisk can slowly accumulate debris across abandoned batches.