# jbenchrunner — Post-Process Phase Specification

**Part of:** [PRD.md](PRD.md) (see master for goals, target users, repository layout, non-functional requirements, and decisions log)
**Scope:** The `jbr-pp` tool and the `result.json` output schema. External name translation tables used by data collectors are in [addendum-external-name-translation.md](addendum-external-name-translation.md).

---

## 1 Post-processing

`bin/jbr-pp` (implemented in `libexec/jbr-pp.py`) is invoked manually at any time:

```
bin/jbr-pp [-h] <run-directory>
```

| Option | Notes |
|---|---|
| `-h`, `--help` | Show usage and exit. |

Post-processing reads `run-info.json` first. A missing `run-info.json`, `info.json` or `stdout.log`, or any status but `DONE`
 results in an error — `result.json` is only produced for successful runs. Otherwise it processes the various log files produced
during a benchmark to produce a condensed result dataset, `result.json` (see §2).

If a collector log file is absent, empty, or cannot be parsed, the corresponding section is
omitted from `result.json` and a warning is printed to stderr. The benchmark result sections
(`dacapo_result`, `specjbb_result`) are derived from `stdout.log`; if that file is absent or
unparseable, `bin/jbr-pp` exits with an error.

Safe to re-run on successful runs; overwrites any previous `result.json`.

For dacapo runs, `bin/jbr-pp` also deletes the `scratch/` directory DaCapo
extracts benchmark data into during the run (its working directory at run
time is the run directory itself), since it is no longer needed once
post-processing has read `stdout.log` and can grow to tens of MB.

If the run has a GC log (`gc.log`, or `gc-all.log`), `bin/jbr-pp` extracts the heap
sizes from it into `heapsizes.txt` and plots them as `heapsizes.svg`. Afterwards the GC
log and `heapsizes.txt` are zstd-compressed in place (`gc.log.zst`, `heapsizes.txt.zst`;
`zstd -1`, as for collector logs, PRD.md D-58), whether or not extraction and plotting
succeeded. A re-run reads the compressed GC log and regenerates both files. Compression is
best-effort: if `zstd` is missing or fails, a warning is printed and the files stay
uncompressed. `scripts/helpers/compress-files.py` compresses the same files (and
`arena.log`, see below) in run directories post-processed before this was introduced,
without re-running post-processing.

If the run has an arena memory manager log (`arena.log`, or `arena.log.zst`), `bin/jbr-pp`
runs the standalone tool `bin/tools/arena-log-parser.py` on it, which writes per block size and
combined time series (cached / in-use / in-use last tick peak / dirty, plus per-tick cache and
slab alloc/dealloc counts) and plots into `arena-report/` in the run directory, plus
`arena-report/arena-summary.json`: the time-weighted averages (trapezoidal, as for `rssavg`) in
bytes of the in-use peak, dirty and cached series, combined and per block size; the combined
section also has the maxima of these series (`<series>_bytes_max`) and, if the log has an
`OOMs per block size` section, `oom_end`: block size -> OOM count at the last tick.
`arena-report/combined-all.svg` stacks the three combined plots (`combined-bytes.svg`,
`combined-bytes-by-block-size.svg`, `combined-allocs.svg`) top to bottom in one file; they share
the same x range and plot area width, so their x axes line up. (`combined-bytes.svg` was called
`combined.svg` before; a re-run removes a stale `combined.svg`.) The combined
maxima and averages also go into `result.json` (§2.9). Its plots get
the same second title line (`--header`) as all other plots `bin/jbr-pp` generates.
Afterwards `arena.log` is zstd-compressed in place together with the GC log (see above); a
re-run reads `arena.log.zst`.

All plots generated for a run have a two-line title: the plot's own title, then
`<dir> - <name> - <tags>`, i.e. the run directory's name, the run's `name` and its
comma-separated `tags` from `info.json` (e.g.
`3846532a-75d5-48bb-a73f-da3aa8af45be - dacapo.spring - arena2,v2.2,1 jit thread`). Empty
parts are left out together with their ` - `; without a readable `info.json` the second line
is omitted. Best-effort: a failure (e.g. an `arena.log` without statistics ticks) is logged
as a warning; nothing is added to `result.json`.

---

## 2 `result.json` Schema

Contains benchmark results and derived metrics. Top-level keys: `dacapo_result` (dacapo runs),
`specjbb_result` (specjbb runs), `temperatures`, `memory`, `malloc`, `threads`, `page_faults`, `arena`. These keys are disjoint from the
top-level keys of `info.json` and `run-info.json` (PRD §7 key-disjointness requirement).

### 2.1 `dacapo_result` section (dacapo runs only)

Contains a condensed view onto the dacapo results. 

| Field | Type | Notes |
|---|---|---|
| `iterations` | array | One object per iteration in run order; iterations 1..N-1 are warmups, iteration N is the measured run |
| `iterations[].n` | integer | 1-based sequence number matching DaCapo output |
| `iterations[].warmup` | boolean | `true` for warmup iterations (1..N-1), `false` for the final measured iteration (N) |
| `iterations[].start_ts` | string | Optional ISO-8601 string timestamp marking iteration start (generated by `dacapo-timestamp-plugin`); absent if plugin not enabled |
| `iterations[].time_ms` | integer | Iteration wall time in milliseconds, as printed by DaCapo |
| `iterations[].latency_usec` | object | Present only for benchmarks that report tail latency (e.g. `tomcat`); absent otherwise |
| `result_ms` | integer | `time_ms` of the last iteration (the single measured run) |
| `first_iteration_ts` | string | `start_ts` of the first iteration, copied verbatim; absent if that iteration has no `start_ts` |
| `final_iteration_ts` | string | `start_ts` of the last (measured) iteration, copied verbatim; absent if that iteration has no `start_ts` |
| `startup_ms` | integer | Milliseconds from JVM launch (`run-info.json` `time`) to `first_iteration_ts`; a trailing Java zone-id suffix (`[Europe/Berlin]`) on the latter is ignored. Absent if `first_iteration_ts` or `time` is missing or unparseable |
| `stddev_ms` | number | Sample standard deviation of `time_ms` over the stability tail: the last N iterations (including the final one), where N is 10 if the run has >= 40 iterations, 7 if >= 30, 5 if >= 20; present only when the run has >= 20 iterations |
| `stderr_ms` | number | Standard error of the mean over the same tail (`stddev_ms / sqrt(N)`); present only when the run has >= 20 iterations |
| `stderr_pct` | number | Relative standard error in percent (`stderr_ms / mean(time_ms) * 100`, mean over the same tail); present only when the run has >= 20 iterations |
| `p50`, `p99`, `p100` | integer | Tail latency summary in usec: `p50`, `p99` and `max` of the final iteration's `latency_usec.simple`; present only if the final iteration has that variant |
| `p50_smooth`, `p99_smooth`, `p100_smooth` | integer | Same, from the final iteration's `latency_usec.smooth_100ms` |
| `p99_stderr` | number | Standard error (usec) of `latency_usec.simple.p99` over the same tail as `stderr_ms` (sample stddev / sqrt(N)); present only when the run has >= 20 iterations and each tail iteration has that variant |
| `p99_stderr_pct` | number | Relative standard error in percent (`p99_stderr / mean(p99) * 100`, mean over the same tail); same presence rule as `p99_stderr` |
| `p99_smooth_stderr`, `p99_smooth_stderr_pct` | number | Same as `p99_stderr` / `p99_stderr_pct`, for `latency_usec.smooth_100ms.p99` |

### 2.2 `dacapo_result.iterations[].latency_usec` sub-object

Three smoothing variants, each with the same fields:

| Key | Smoothing variant | DaCapo output label |
|---|---|---|
| `simple` | No smoothing | `tail latency, simple` |
| `smooth_100ms` | 100 ms metered | `tail latency, metered 100ms smoothing` |
| `smooth_full` | Full metered | `tail latency, metered full smoothing` |

Each variant contains: `p50`, `p90`, `p99`, `p999`, `p9999`, `max` (all integers, microseconds), `num_events` (integer).

### 2.3 `specjbb_result` section (specjbb runs only)

Parsed from the single `RUN RESULT:` line in `stdout.log`:

```
RUN RESULT: hbIR (max attempted) = 220, hbIR (settled) = 200, max-jOPS = 136, critical-jOPS = 0
```

| Field | Type | Notes |
|---|---|---|
| `hbirmax` | integer | `hbIR (max attempted)` |
| `hbirset` | integer | `hbIR (settled)` |
| `maxjops` | integer | `max-jOPS` |
| `critjops` | integer | `critical-jOPS` |

### 2.4 `temperatures` section

Accumulated from `temps.jsonl`. All temperatures in milligrade (e.g. 55000 = 55 degrees C)

| Field | Type | Notes |
|---|---|---|
| `tempin` | integer | temp at start of benchmark (first sample) |
| `tempout` | integer | temp at end of benchmark (last sample) |
| `tempmax` | integer | Max temp during benchmark (highest sample) |


### 2.5 `memory` section

Accumulated from `vm_rss` in `proclog.jsonl`. All memory units in MB.

`proclog.jsonl` is produced first by `post-process-proc-log.py` from `proclog.log` (or
`proclog.log.zst`, identical output), which the collector writes as raw `/proc/<pid>/stat` and
`/proc/<pid>/status` dumps, each preceded by a `=== /proc/<pid>/{stat,status} ===` delimiter line
after the sample's timestamp line (spec-execute.md §5.2). Runs from before the raw-log change have
only a `proclog.jsonl` written by the old collector; when no `proclog.log[.zst]` exists the file is
left untouched. Status keys are translated to snake_case field names; unknown keys are dropped with
one warning per key. A sample lacking either section, or a last sample cut off mid-write, is
skipped with a warning; no valid sample at all -> no `proclog.jsonl` written (warning).

| Field | Type | Notes |
|---|---|---|
| `rssmax` | integer | Max process rss |
| `rssavg` | integer | RSS, time-weighted average; omitted if no sample carries a valid timestamp |

### 2.6 `malloc` section

Accumulated from `total_malloc` in `nmt.jsonl` (§3) — present only for runs with the `nmt`
collector enabled (PRD D-53), i.e. only when `nmt.jsonl` was produced. All memory units in MB. Unlike the
rest of `nmt.jsonl`, which stays a raw time series (§3), this is the one aggregate rolled up into
`result.json` from it.

| Field | Type | Notes |
|---|---|---|
| `mallocmax` | number | Max total malloc'd memory (MB, 2 decimal places) |
| `mallocavg` | number | Time-weighted average total malloc'd memory (MB, 2 decimal places); omitted if no sample carries a valid timestamp |

### 2.7 `threads` section

`osthreads_*` are accumulated from `status.threads` (OS thread count) in `proclog.jsonl`; the
per-category objects from the per-sample category objects in `thread-print.jsonl` (§3c). Either
group is absent if its source is (e.g. the category objects for runs without the `thread_print`
collector, or OpenJ9 runs).

```json
"threads": {
  "osthreads_max": 29,
  "osthreads_avg": 27,
  "c2":      {"num_threads_max": 2,  "num_threads_avg": 2.0,  "cpu_pct_avg": 4.1,   "cpu_pct_max": 61.3},
  "c1":      {"num_threads_max": 1,  "num_threads_avg": 1.0,  "cpu_pct_avg": 1.9,   "cpu_pct_max": 91.5},
  "gc":      {"num_threads_max": 11, "num_threads_avg": 11.0, "cpu_pct_avg": 22.7,  "cpu_pct_max": 180.4},
  "mutator": {"num_threads_max": 3,  "num_threads_avg": 1.2,  "cpu_pct_avg": 108.2, "cpu_pct_max": 131.0},
  "other":   {"num_threads_max": 12, "num_threads_avg": 11.4, "cpu_pct_avg": 0.6,   "cpu_pct_max": 3.2},
  "unaccounted": {"cpu_pct_avg": 1.4, "cpu_pct_max": 38.0}
}
```

| Field | Type | Notes |
|---|---|---|
| `osthreads_max` | integer | Max number of OS threads during the run |
| `osthreads_avg` | integer | Average number of OS threads during the run |
| `<c>` | object | One per thread category `<c>`: `c2`, `c1`, `gc`, `mutator`, `other` (§3c) |
| `<c>.num_threads_max` | integer | Max thread count of the category over all thread dumps |
| `<c>.num_threads_avg` | number | Time-weighted average thread count (1 decimal place); omitted if no sample carries a valid timestamp |
| `<c>.cpu_pct_avg` | number | Average CPU usage of the category in IRIX notation (100 = one core fully busy), 1 decimal place, over the span from the first to the last thread dump: the category's CPU time in that span divided by its length, i.e. the per-sample `cpu_pct` weighted by `interval_secs`. CPU time before the first dump (JVM startup) is not included. Omitted if fewer than two usable samples |
| `<c>.cpu_pct_max` | number | Max per-sample `cpu_pct` of the category (1 decimal place); omitted like `cpu_pct_avg` |
| `unaccounted` | object | CPU time no dumped thread accounts for (threads that ended between two dumps, or lived only between them; §3c.1): `cpu_pct_avg` and `cpu_pct_max`, computed like the categories'. No thread counts. Absent without the `proc` collector |

### 2.8 `page_faults` section

Page fault totals of the JVM process, from `proc_pid_stat.minflt` / `.majflt` in the **last**
sample of `proclog.jsonl`. These counters are cumulative since process start, so the totals
include startup (e.g. heap pretouch with `-XX:+AlwaysPreTouch`), also the part before the
collectors attached. Faults after the last sample (up to one collector interval, shutdown) are
not included. Children's faults (`cminflt`/`cmajflt`) are not counted. Absent without the
`proc` collector.

```json
"page_faults": { "minflt": 4922269, "majflt": 72 }
```

| Field | Type | Notes |
|---|---|---|
| `minflt` | integer | Total minor page faults: faults served without I/O (first touch of anonymous memory, file pages already in the page cache) |
| `majflt` | integer | Total major page faults: faults that required I/O (file pages not in the page cache, swap-in). Typically well below 200 per run, almost all during startup; clearly higher values hint at a run disturbed by swapping or a cold page cache, or at an application that maps many files |

No per-second average is recorded (PRD.md D-69).

`jbr-pp` also plots the fault rates as `pagefaults.svg` in the run directory
(`plot-pagefaults.py`): two panels over elapsed time, minor faults/s at the top, major faults/s
at the bottom. Each rate is the counter difference between two consecutive proclog samples
divided by the time between them, drawn as a step over that interval. Faults before the first
sample have no interval and are not drawn (they are included in the totals). Best-effort like
the other plots — a plotting failure (e.g. gnuplot missing) is a warning only.

### 2.9 `arena` section

The combined (all block sizes) maxima and time-weighted averages from
`arena-report/arena-summary.json` (§1), converted from bytes to MB (2 decimal places). Present
only for runs with an `arena.log` that `arena-log-parser.py` could parse. A field is omitted if
the summary lacks the value; in particular the `used_mb_*` fields need the log's
`blocks in use (last tick peak)` lines.

```json
"arena": { "used_mb_max": 812.5, "used_mb_avg": 402.13, "dirty_mb_max": 950.0,
           "dirty_mb_avg": 610.4, "cached_mb_max": 64.0, "cached_mb_avg": 21.77 }
```

| Field | Type | Notes |
|---|---|---|
| `used_mb_max`, `used_mb_avg` | number | In-use memory, last tick peak (includes blocks held in the block caches); summed over block sizes per tick, so an upper bound (the per block size peaks are not simultaneous) |
| `dirty_mb_max`, `dirty_mb_avg` | number | Dirty memory |
| `cached_mb_max`, `cached_mb_avg` | number | Memory held in the per-CPU block caches |

## 3 `nmt.jsonl` — Native Memory Tracking time series

Produced by `post-process-nmt-log.py` from `nmt.log`, or from `nmt.log.zst` when the run has
`collectors.compression` on (spec-execute.md §5.2; the output is identical) (present only for runs with
the `nmt` collector enabled, PRD D-53) as part of the same `jbr-pp` invocation that writes
`result.json`. Unlike `temperatures`/`memory`/`threads`, this file itself is **not** a
`result.json` section: it is a raw, full-resolution time series written as its own file,
`nmt.jsonl`, in the run directory — one JSON object per `jcmd VM.native_memory summary`
sample, in the same JSON-Lines style as `perflog.jsonl`/`pidstat.jsonl` (spec-execute.md
§5.2). The one aggregate rolled up from it into `result.json` is `malloc` (§2.6), from this
file's `total_malloc` field; a fuller scalar summary (peak native memory usage per category
etc.) remains a deliberately separate, future step.

| Field | Type | Notes |
|---|---|---|
| `timestamp` | string | ISO-8601, as printed by `jcmd-logger.sh` before each sample |
| `pid` | integer | Monitored process ID |
| `total` | integer | Total committed memory, KB (`Total: ... committed=`) |
| `total_malloc` | integer | Total current malloc'd memory, KB |
| `total_malloc_peak` | integer | Total peak malloc'd memory, KB |
| `total_mmap_committed` | integer | Total committed mmap'd memory, KB |
| `<category>` | object | One key per NMT category present in that sample (e.g. `java_heap`, `class`, `thread`, `gc`, `compiler`, `code`, `internal`, `symbol`, `arena_chunk`, ...). Categories are resolved dynamically from the log — they vary across JDK releases and can appear/disappear between samples of the same run (e.g. `class` only shows up once loaded) — so a category absent from a given sample simply has no key there, rather than being zero-filled. |
| `<category>.mmap_committed` | integer | Committed mmap'd memory for this category, KB (0 if the category reports no mmap) |
| `<category>.malloc` | integer | Current malloc'd memory for this category, KB (0 if the category reports no malloc) |
| `<category>.malloc_peak` | integer | Peak malloc'd memory for this category, KB (equal to `malloc` when NMT prints `(at peak)` instead of a separate `peak=` value) |

Category names are sanitized to lowercase with any run of non-alphanumeric characters
(spaces, hyphens, ...) collapsed to a single `_` — e.g. `Java Heap` -> `java_heap`,
`Arena Chunk` -> `arena_chunk`. Names already written as one word are lowercased whole,
without guessing at internal word boundaries — e.g. `GCCardSet` -> `gccardset`, not
`gc_card_set`.

`arena=`/`arena peak=` figures reported inline for some categories (`GC`, `Compiler`,
`Thread`, `Code`, `Arena Chunk`) are **not** captured per-category — the `Arena Chunk`
category's own totals are the intended signal for arena memory.

Parsing errors are handled at three levels, each logged to stderr and never aborting the
rest of the file:
- a single category's block fails to parse -> that category is omitted from that sample.
- a sample's PID line or `Total:` block fails to parse (e.g. truncated because the JVM was
  killed mid-dump) -> the whole sample is skipped.
- the file has no parseable samples at all -> `post-process-nmt-log.py` exits with an error
  and no `nmt.jsonl` is written (same "absent/empty/unparseable -> omit + warn" contract as
  §1 for the other collector logs).

## 3b `system.map.jsonl` — System.map time series

Produced by `post-process-system-map-log.py` from `system.map.log` (or `system.map.log.zst`,
identical output) for runs with the `system_map` collector enabled, in the same `jbr-pp`
invocation, before the System.map plot. Runs from before the raw-log change have only a
`system.map.jsonl` written by the old collector; when no `system.map.log[.zst]` exists the file
is left untouched. Like `nmt.jsonl`, not a `result.json` section.

One JSON object per sample: `{"timestamp": ..., "pid": ..., "total": {"vsize": <bytes>,
"rss": <bytes>}, "<category>": {"vsize": <bytes>, "rss": <bytes>}, ...}`, one key per category
present in that sample (not zero-filled), plus a `total` key holding the sum of `vsize`/`rss`
across all categories in that sample. Scanning:
skip to the `====` separator line, read table rows until the first blank line. Each row has 9
whitespace-separated fields (`from-to` is one token; the `file` field may contain spaces):
`vsize` = field 2, `prot` = field 3, `rss` = field 4, `info` = field 8, `file` = field 9. Both
sizes are summed per category, chosen from `info` (first matching rule wins):

| `info` value | Category key |
|---|---|
| `-` and `file` ends in `.so` or `.so.<N>...` | `sharedlib_text` if `prot` is `r-xp`, else `sharedlib_data` |
| `-` | `unknown` |
| contains a `,` (e.g. `CODE,GC`) | `combined` |
| starts with `STACK-` (e.g. `STACK-12350-VM-Thread`) | `stacks` |
| anything else | a leading `mt` is stripped when followed by an uppercase letter (e.g. `mtChunkMmap`), camelCase boundaries get `_` inserted, then the result is lowercased with non-alphanumeric runs collapsed to `_` (`mtChunkMmap` -> `chunk_mmap`, `JAVAHEAP` -> `javaheap`) |

Errors, each logged to stderr, never aborting the rest of the file:
- an unparseable table row -> row skipped.
- a sample without a table, or cut off before the summary section that follows the table
  (`Number of mappings:` / `vsize:`) — JVM killed mid-dump, truncated compressed tail, failed
  jcmd call -> whole sample skipped (a partial table would silently understate the totals).
- no valid sample at all -> exit with an error, no `system.map.jsonl` written.

## 3c `thread-print.jsonl` — thread dump time series

Produced by `post-process-thread-print-log.py` from `thread-print.log` (or `thread-print.log.zst`,
identical output) for runs with the `thread_print` collector enabled, in the same `jbr-pp`
invocation. Like `nmt.jsonl`, not a `result.json` section itself, but its category objects are
accumulated into the `threads` section (§2.7). Only HotSpot dumps are parsed.

One JSON object per sample (one line in the file; wrapped here):

```json
{"timestamp": "2026-09-22T20:29:05.214+02:00",
 "interval_secs": 1.13,
 "num_threads": 25,
 "c2":      {"num_threads": 2,  "cpu_pct": 5.4},
 "c1":      {"num_threads": 1,  "cpu_pct": 9.1},
 "gc":      {"num_threads": 11, "cpu_pct": 12.6},
 "mutator": {"num_threads": 1,  "cpu_pct": 64.9},
 "other":   {"num_threads": 10, "cpu_pct": 0.1},
 "unaccounted": {"cpu_pct": 1.2},
 "threads": [{"name": "main", "jlt_state": "RUNNABLE", "cpu_since_start_ms": 882.0,
              "elapsed_s": 1.48, "cpu_pct": 64.9, "os_tid": 1376533}, ...]}
```

| Field | Notes |
|---|---|
| `timestamp` | The collector's timestamp line (spec-execute.md §5.2), not jcmd's local-time line |
| `interval_secs` | Length of the interval since the previous usable sample, from the threads' `elapsed=` values (§3c.1); `null` in the first sample |
| `num_threads` | Number of threads in the dump; the sum of the categories' `num_threads` |
| `<c>.num_threads` | Number of threads in category `<c>` (`c2`, `c1`, `gc`, `mutator`, `other`; rules below) |
| `<c>.cpu_pct` | CPU usage of the category in the interval, IRIX notation (100 = one core), 1 decimal place: the sum of its threads' `cpu_pct`. `null` in the first sample |
| `unaccounted.cpu_pct` | CPU usage in the interval that no dumped thread accounts for (§3c.1), same notation; `null` in the first sample and when proclog does not cover the interval. The `unaccounted` object is present only when `proclog.jsonl` was available |
| `threads` | One entry per thread header line (`"<name>" ...`), in dump order; see below |

Per thread:

| Field | Source |
|---|---|
| `name` | the quoted thread name |
| `jlt_state` | the `java.lang.Thread.State:` enum value, without the parenthesized detail (`WAITING (on object monitor)` -> `WAITING`); `null` for native VM threads, which have no such line |
| `cpu_since_start_ms` | `cpu=<N>ms`; `null` if absent |
| `elapsed_s` | `elapsed=<N>s` (time since the thread started, 10 ms resolution); `null` if absent |
| `cpu_pct` | CPU usage of the thread in the interval, IRIX notation, 1 decimal place (§3c.1); `null` in the first sample and when it cannot be computed |
| `os_tid` | `nid=` (decimal, or `0x` hex in older JDKs). Taken from `nid` rather than the `[<tid>]` bracket because only Java threads print the bracket; on Linux both hold the kernel tid |

Each thread belongs to exactly one category (first matching rule wins):

| Rule | Category |
|---|---|
| name starts with `C2 CompilerThread` | `c2` |
| name starts with `C1 CompilerThread` | `c1` |
| no `jlt_state` (native VM thread) and name starts with `GC Thread`, `G1 `, `Shenandoah `, or `Z` followed by an uppercase letter (ZGC: `ZDirector`, `ZWorkerYoung#0`, ...) | `gc` |
| has `jlt_state`, at least one `at ...` frame line, and name is not `Reference Handler`, `Finalizer` or `Common-Cleaner` | `mutator` |
| anything else | `other` |

A thread's category is decided anew in every sample; its CPU time for an interval is booked to
the category it has in the sample that ends the interval.

Only tab-indented `at ...` lines are frames — `- locked ...`, `- waiting on ...`,
`No compile task`, `Compiling: ...` are not.

Errors, each logged to stderr, never aborting the rest of the file:
- a sample without a `Full thread dump` line (failed jcmd call, OpenJ9 format), or cut off before
  its `JNI global refs:` trailer (JVM killed mid-dump, truncated compressed tail) -> whole sample
  skipped.
- no valid sample at all -> the script exits with an error and no `thread-print.jsonl` is written.
  Unlike the other collector logs this is only a warning in `jbr-pp`, so OpenJ9 runs with
  the `thread_print` collector post-process normally.

### 3c.1 CPU usage

Computed per sample from the sample and the previous usable one (skipped samples, see above, do
not count; the interval then simply spans them):

1. **Matching.** A thread is the same thread as in the previous sample if it has the same `os_tid`
   and its `elapsed_s` did not decrease (a decrease means the kernel reused the tid for a new
   thread). Threads without `os_tid`, `cpu_since_start_ms` or `elapsed_s` get `cpu_pct: null` and
   contribute nothing.
2. **Interval.** `interval_secs` is the median of `elapsed_s - previous elapsed_s` over the matched
   threads. Unlike the collector timestamps, `elapsed=` is read inside the JVM at dump time, so it
   is not distorted by the time jcmd takes to attach, which varies between samples. If no thread
   matches, `interval_secs` and all `cpu_pct` values of the sample are `null`.
3. **Per thread.** CPU time in the interval is `cpu_since_start_ms - previous cpu_since_start_ms`
   for a matched thread, and all of `cpu_since_start_ms` for a thread that started during the
   interval (not in the previous sample). `cpu_pct = cpu time / interval_secs` (as %). CPU time of
   threads that ended during the interval is lost; so is all CPU time of threads that lived only
   between two samples.
4. **Per category.** The sum of the category's thread `cpu_pct` values.
5. **Unaccounted CPU.** If `proclog.jsonl` exists (`proc` collector), the
   process CPU time in the interval is taken from it: `proc_pid_stat.utime + stime`, in clock ticks
   of 1/100 s (the Linux default; the benchmark host's value is not recorded), interpolated
   linearly to the two samples' timestamps. The process CPU in % of the timestamp span minus the
   sum of all categories' `cpu_pct` is the unaccounted CPU (threads that ended or lived only between
   samples), recorded as `unaccounted.cpu_pct`. With much thread churn it can be large (dacapo
   tomcat: about 30 % of the process CPU), and it is mostly CPU of short-lived application
   threads. It is not clamped: the collector timestamps are taken
   before jcmd attaches (spec-execute.md §5.2), so per sample the value is only approximate and can
   be slightly negative; over the run these errors cancel out. A sample whose interval is not
   covered by proclog samples at both ends gets `null`. `jbr-pp` passes
   `proclog.jsonl` to `post-process-thread-print-log.py` as `--proclog <path>`.

### 3c.2 Plots

After `thread-print.jsonl` is written, `plot-thread-print.py` renders two SVGs in the run
directory, each with one stacked band per category over elapsed time, bottom to top GC, Other,
C1, C2, Mutator (steady categories at the base, volatile ones on top so their swings don't shift
the bands below):

- `thread-print.svg`: the thread counts; the top edge is `num_threads`.
- `thread-print-cpu.svg`: the categories' `cpu_pct` (y axis: CPU %, 100 = one core), plus, if
  present, `unaccounted.cpu_pct` as a band on top labelled "Unaccounted/Short-lived"; with it,
  the top edge is the process's total CPU usage. Each value covers the interval ending at its
  sample, so the bands are drawn as steps over that interval, not as lines between samples. A
  negative or `null` unaccounted value is drawn as 0. The first sample (no values) is not drawn.

Best-effort like the NMT and System.map plots — a plotting failure (e.g. gnuplot missing) is a
warning only.

---

## 4 `result.json` Examples

**DaCapo — plain benchmark (`pmd`, 5 iterations: 4 warmups + 1 measured):**

```json
{
  "dacapo_result": {
    "iterations": [
      { "n": 1, "warmup": true, "start_ts": "2026-03-29T10:00:00.123456", "time_ms": 18839 },
      { "n": 2, "warmup": true, "start_ts": "2026-03-29T10:00:19.012345", "time_ms": 15365 },
      { "n": 3, "warmup": true, "start_ts": "2026-03-29T10:00:34.456789", "time_ms": 15034 },
      { "n": 4, "warmup": true, "start_ts": "2026-03-29T10:00:49.567890", "time_ms": 15160 },
      { "n": 5, "warmup": false, "start_ts": "2026-03-29T10:01:04.890123", "time_ms": 14303 }
    ],
    "result_ms": 14303,
    "first_iteration_ts": "2026-03-29T10:00:00.123456",
    "final_iteration_ts": "2026-03-29T10:01:04.890123",
    "startup_ms": 1123
  },
  "temperatures": {
    "tempin": 65000,
    "tempout": 65000,
    "tempmax": 65000
  },
  "memory": {
    "rssmax": 2560,
    "rssavg": 1300
  },
  "malloc": {
    "mallocmax": 48.83,
    "mallocavg": 42.5
  },
  "threads": {
    "osthreads_max": 24,
    "osthreads_avg": 18
  },
  "page_faults": {
    "minflt": 923963,
    "majflt": 99
  }
}
```

**DaCapo — latency benchmark (`tomcat`, 5 iterations: 4 warmups + 1 measured):**

```json
{
  "dacapo_result": {
    "iterations": [
      { "n": 1, "warmup": true, "start_ts": "2026-03-29T10:00:00.123456", "time_ms": 137563, "latency_usec": {
          "simple":       { "p50": 1191,    "p90": 1244,    "p99": 9318,    "p999": 9486,    "p9999": 68742,   "max": 612930,  "num_events": 800000 },
          "smooth_100ms": { "p50": 1224,    "p90": 1901,    "p99": 9325,    "p999": 9830,    "p9999": 68825,   "max": 612930,  "num_events": 800000 },
          "smooth_full":  { "p50": 1375854, "p90": 2375593, "p99": 2516966, "p999": 2531538, "p9999": 2535177, "max": 2537396, "num_events": 800000 }
      }},
      { "n": 2, "warmup": true, "start_ts": "2026-03-29T10:02:18.012345", "time_ms": 135173, "latency_usec": {
          "simple":       { "p50": 1189,   "p90": 1236,   "p99": 9314,   "p999": 9453,   "p9999": 9766,   "max": 46101,  "num_events": 800000 },
          "smooth_100ms": { "p50": 1220,   "p90": 1875,   "p99": 9326,   "p999": 9723,   "p9999": 15754,  "max": 46101,  "num_events": 800000 },
          "smooth_full":  { "p50": 135880, "p90": 186994, "p99": 193380, "p999": 197474, "p9999": 201242, "max": 203106, "num_events": 800000 }
      }},
      { "n": 3, "warmup": true, "start_ts": "2026-03-29T10:04:33.456789", "time_ms": 135112, "latency_usec": {
          "simple":       { "p50": 1189,  "p90": 1236,   "p99": 9318,   "p999": 9453,   "p9999": 9756,   "max": 17485,  "num_events": 800000 },
          "smooth_100ms": { "p50": 1220,  "p90": 1884,   "p99": 9330,   "p999": 9723,   "p9999": 16480,  "max": 31782,  "num_events": 800000 },
          "smooth_full":  { "p50": 59146, "p90": 108135, "p99": 116795, "p999": 119808, "p9999": 124516, "max": 127873, "num_events": 800000 }
      }},
      { "n": 4, "warmup": true, "start_ts": "2026-03-29T10:06:48.567890", "time_ms": 135140, "latency_usec": {
          "simple":       { "p50": 1189,  "p90": 1239,   "p99": 9314,   "p999": 9453,   "p9999": 9805,   "max": 17954,  "num_events": 800000 },
          "smooth_100ms": { "p50": 1220,  "p90": 1892,   "p99": 9326,   "p999": 9750,   "p9999": 17783,  "max": 28784,  "num_events": 800000 },
          "smooth_full":  { "p50": 90087, "p90": 124850, "p99": 136163, "p999": 139853, "p9999": 143904, "max": 146132, "num_events": 800000 }
      }},
      { "n": 5, "warmup": false, "start_ts": "2026-03-29T10:09:03.890123", "time_ms": 135106, "latency_usec": {
          "simple":       { "p50": 1189,  "p90": 1239,   "p99": 9318,   "p999": 9453,   "p9999": 9789,   "max": 17861,  "num_events": 800000 },
          "smooth_100ms": { "p50": 1220,  "p90": 1884,   "p99": 9328,   "p999": 9748,   "p9999": 15018,  "max": 28577,  "num_events": 800000 },
          "smooth_full":  { "p50": 86441, "p90": 116187, "p99": 125915, "p999": 129310, "p9999": 133849, "max": 135999, "num_events": 800000 }
      }}
    ],
    "result_ms": 135106
  },
  "temperatures": { ... },
  "memory": { ... },
  "threads": { ... }
}
```

**SPECjbb — composite mode:**

```json
{
  "specjbb_result": {
    "hbirmax": 220,
    "hbirset": 200,
    "maxjops": 136,
    "critjops": 0
  },
  "temperatures": { ... },
  "memory": { ... },
  "threads": { ... }
}
```

---
