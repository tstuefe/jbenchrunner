# jbenchrunner — Analysis Phase Specification

**Part of:** [PRD.md](PRD.md) (see master for goals, target users, repository layout, non-functional requirements, and decisions log)  
**Scope:** The `jbr-filt` and `jbr-ls` tools for filtering and listing run result directories.

---

## 1 Analysis & Comparison

### 1.1 `jbr-filt` — Filter Run Directories

`jbr-filt` filters the set of run result directories and prints the names of those that match the given conditions. It is the primary way to select a subset of runs for further inspection or comparison.

Both `jbr-filt` and `jbr-ls` must be run inside a project (PRD.md §4); outside one they abort. They operate exclusively on run directories that have a `result.json` (i.e. successful and successfully post-processed runs). Directories without a `result.json` are silently skipped by both tools.

Every RUNID printed by `jbr-filt` or `jbr-ls` (including in `Warning:` diagnostics) is a clickable terminal hyperlink to `file://<project>/results/<RUNID>` when the output stream is an interactive terminal that supports OSC 8 hyperlinks; otherwise it is printed as plain text. This never affects piped/redirected output (e.g. `jbr-filt | jbr-ls`), which always stays plain text regardless of the invoking terminal's capabilities.

Each run is evaluated against the merged contents of its `info.json`, `run-info.json`, and `result.json` (a simple top-level key union — no key may appear in both files; see [PRD.md §6](PRD.md#6-non-functional-requirements)). Convenience flags cover the most common filter criteria directly; `--where` accepts any raw `jq` boolean expression over the merged object for everything else. All active filters are AND-ed by default. The output is a list of bare directory names, one per line, suitable for piping into other tools.

`jq` must be available on `PATH`.

#### 1.1.1 Synopsis

```
jbr-filt [FILTER...] [RUNID...]
```

`RUNID` values are bare run directory names (e.g. `228d007f-0546-4a17-9584-d91a448918d9`), resolved as `<project>/results/<RUNID>`. The input set is determined as follows:

1. **Positional arguments** — if any `RUNID` values are given on the command line, those are used.
2. **stdin** — if no positional arguments are given and stdin is not a terminal (i.e. data is being piped or redirected in), RUNIDs are read one per line from stdin.
3. **All result directories** — if no positional arguments are given and stdin is a terminal, all directories under the project's `results/` are used.

This enables pipeline composition: `jbr-filt --benchmark dacapo | jbr-filt --or --jdkmajor 17 --jdkmajor 21`.

#### 1.1.2 Convenience filters

| Flag | Semantics | `jq` equivalent |
|---|---|---|
| `-b`, `--benchmark <val>` | exact | `.benchmark == "<val>"` |
| `--sub <val>` | exact | `.dacapo.sub_benchmark == "<val>"` |
| `-d`, `--datasize <val>` | exact | `.dacapo.datasize == "<val>"` |
| `--jdkmajor <op><n>` | numeric; op is `>` `<` `>=` `<=` `==`; omitting op defaults to `==` | `.jdk.major <op> <n>` |
| `--heapszf <op><n>` | numeric; op is `>` `<` `>=` `<=` `==`; omitting op defaults to `==` | `.heapsize_factor <op> <n>` |
| `--repeat_no <op><n>` | integer; op is `>` `<` `>=` `<=` `==`; omitting op defaults to `==` | `.repeat_no <op> <n>` |
| `-S`, `--heapsz <op><n>` | numeric, MB; op is `>` `<` `>=` `<=` `==`; omitting op defaults to `==` | `.heap.heapsize <op> <n>` |
| `--duration <op><n>` | numeric, seconds; op is `>` `<` `>=` `<=` `==`; omitting op defaults to `==` | `.duration_secs <op> <n>` |
| `--startup_ms <op><n>` | numeric, ms; op is `>` `<` `>=` `<=` `==`; omitting op defaults to `==` | `.dacapo_result.startup_ms <op> <n>` |
| `--gc <val>` | exact | `.heap.gc == "<val>"` |
| `--jdktype <val>` | exact | `.jdk.type == "<val>"` |
| `--jdkname <val>` | substring on `jdk.name` | `.jdk.name \| contains("<val>")` |
| `--jdkversion <val>` | substring on `jdk.version` | `.jdk.version \| contains("<val>")` |
| `-t`, `--tags <val>` | matches if any element of `tags` equals val; if val contains glob characters (`*`, `?`, `[...]`), it is matched as a case-sensitive glob pattern instead (Python `fnmatch.fnmatchcase`). No match if `tags` is empty. Quote glob patterns in the shell | literal: `[.tags[] \| . == "<val>"] \| any`; glob: `[.tags[] \| test("<regex>")] \| any` |
| `-N`, `--name <val>` | case-sensitive glob match (Python `fnmatch.fnmatchcase`; `*`, `?`, `[...]`) on the compound run name `name` (e.g. `dacapo.h2`, `specjbb`; spec-execute.md); without glob characters it matches exactly. Quote glob patterns in the shell | `.name \| test("<regex>")` |
| `-H`, `--hostname <val>` | exact | `.hostname == "<val>"` |
| `--batchid <val>` | exact; `last` selects the newest batch in the project's `batches/` (by `started_at`) | `.batchid == "<val>"` |
| `--arch <val>` | exact | `.arch == "<val>"` |
| `--os <val>` | substring on `os` | `.os \| contains("<val>")` |
| `--jvmargs <val>` | any element of `jvmargs` contains val | `[.jvmargs[] \| contains("<val>")] \| any` |
| `--since <spec>` | `time` (second precision) on or newer than `<spec>` | `.time >= "<spec>"` |
| `--until <spec>` | `time` (second precision) on or older than `<spec>` | `.time <= "<spec>"` |
| `--where <jq-expr>` | raw `jq` boolean expression; repeatable | — |

If the same flag is repeated, each occurrence produces a separate filter expression; all are combined using the same AND (by default)/OR(if `--or` specified) combinator as the rest.

Any parse error causes the tool to abort (e.g. when parsing `--jdkmajor`).

##### `--since` / `--until` date/time specification

`<spec>` is `"YYYY-MM-DDTHH:MM:SS"` (matching `time` / jbr-ls's `time`
column), with either side omittable:

- A missing seconds component defaults to `:00`.
- A missing time (and its `T` separator) defaults to `00:00:00`.
- A missing date defaults to today.

Examples: `2026-08-06`, `2026-08-06T18:17`, `18:17:16` (today at 18:17:16).
Comparison is against `time` truncated to second precision, compared
lexicographically. Unlike journalctl, there is no support for relative or
symbolic values such as `today`, `yesterday`, or `now`.

Examples

```sh
# All specjbb runs that were done with JDK 11 or older
jbr-filt --b specjbb --jdkmajor '<=11'

# All runs from the 2026-08-06 batch, before 18:00
jbr-filt --since 2026-08-06 --until 2026-08-06T18:00

# All dacapo h2 and tomcat runs, by compound name (glob; quote it)
jbr-filt --or -N dacapo.h2 -N 'dacapo.tom*'
```

#### 1.1.3 Combinators

| Flag | Meaning |
|---|---|
| `--or` | OR all active filters (default: AND) |
| `--invert`, `-v` | negate the combined result |

`--or` is global: it changes the combinator for every active filter in the invocation. Mixed AND/OR logic can be achieved either by chaining two `jbr-filt` invocations via pipe, or by using a single `--where` expression:

```sh
# dacapo batik AND (jdk major 17 OR 21) — pipe approach
jbr-filt --b dacapo --sub batik | jbr-filt --or --jdkmajor 17 --jdkmajor 21

# equivalent — raw jq approach
jbr-filt --where '.benchmark == dacapo and .dacapo.sub_benchmark == batik and (.jdk.major == 17 or .jdk.major == 21)'
```

#### 1.1.4 Output

Matching run directory names printed to stdout, one per line, as bare names (e.g. `228d007f-0546-4a17-9584-d91a448918d9`) — not full paths.

#### 1.1.5 Other flags

| Flag | Meaning |
|---|---|
| `-h`, `--help` | Usage |

#### 1.1.6 Examples

```sh
# All DaCapo tomcat runs on JDK 21+
jbr-filt --benchmark dacapo --sub tomcat --jdkmajor ">=21"

# SPECjbb runs where maxjops exceeded 100
jbr-filt -b specjbb --where '.specjbb_result.maxjops > 100'

# Everything tagged with "G1" on this machine
jbr-filt -t G1 -H $(hostname)

# Everything with a tag starting with "G1" (glob; quote it)
jbr-filt -t 'G1*'

# Either dacapo or specjbb (--or applies to all flags)
jbr-filt --or --benchmark dacapo --benchmark specjbb

# Specific named runs
jbr-filt a1bcd3ca-e00c-46fd-8121-c1207ddfc6af b2cde4db-f11d-57ge-9232-d2318eegd7bg
```

---

### 1.2 `jbr-ls` — List Run Directories

`jbr-ls` reads the metadata of one or more run result directories and prints their properties as a formatted table (header row, then one row per directory), styled after `ps` output.

#### 1.2.1 Synopsis

```
jbr-ls [-o <cols>] [-O <cols>] [-s <col>[,<col>...]] [--sort-asc|--sort-desc] [-g <col>[,<col>...] | --no-header] [--no-autoomit] [RUNID...]
```

`RUNID` values are bare run directory names (e.g. `228d007f-0546-4a17-9584-d91a448918d9`), resolved as `<project>/results/<RUNID>`. The input set is determined as follows:

1. **Positional arguments** — if any `RUNID` values are given on the command line, those are used.
2. **stdin** — if no positional arguments are given and stdin is not a terminal, RUNIDs are read one per line from stdin.
3. **All result directories** — if no positional arguments are given and stdin is a terminal, all directories under the project's `results/` are used.

This enables direct use after `jbr-filt`: `jbr-filt --benchmark dacapo | jbr-ls -o RESULTS`.

#### 1.2.2 Column flags (`-o` / `-O`)

| Flag | Behaviour |
|---|---|
| `-o <cols>` | `DEFAULT` + `<cols>` — prepends the DEFAULT group then appends the specified columns |
| `-O <cols>` | `<cols>` only — no DEFAULT prepended |

`<cols>` is a comma-separated list of column names and/or group names. Group names are uppercase (e.g. `JVM`, `HOSTINFO`); column names are lowercase. A group name expands inline to its member columns. Plain invocation (no `-o` / `-O`) shows the DEFAULT group only.

Without `-O`, the variable-width columns `collectors`, `tags` and `jvmargs` are moved to the end of the column list (keeping their relative order), so that they do not push the other columns to the right; e.g. `jbr-ls -o RESULTS` shows `runid,time,name,<RESULTS columns>,tags`. With `-O`, columns appear exactly in the given order.

#### 1.2.3 Column definitions

Column names are the same as the corresponding `jbr-filt` flag names (§1.1.2), and each column's table header is its column name.

| Column | Source | Notes |
|---|---|---|
| `runid` | `info.json` → `runid` | Printed in full, e.g. `a1bcd3ca-e00c-46fd-8121-c1207ddfc6af`; clickable link to the run directory when the terminal supports it, or (in `--report` output) an `<a>` link to the run directory |
| `name` | `info.json` → `name` | Compound name as stored in `info.json` (`.name`) |
| `benchmark` | `info.json` → `benchmark` | `dacapo` or `specjbb` |
| `sub` | `info.json` → `dacapo.sub_benchmark` | `-` for specjbb runs |
| `datasize` | `info.json` → `dacapo.datasize` | `-` for specjbb runs |
| `time` | `run-info.json` → `time` | ISO-8601 truncated to second precision, no timezone suffix (e.g. `2026-08-06T11:30:35`) |
| `duration` | `run-info.json` → `duration_secs` | Wall-clock seconds; `-` if no `run-info.json` |
| `collint_secs` | `info.json` → `collectors.interval_secs` | Collector sampling interval in seconds (spec-execute.md §4.1); `-` if unknown |
| `collectors` | `info.json` → `collectors.enabled` | Comma-joined short names of the collectors that ran (spec-prepare.md §1.4.1), e.g. `cpu_temp,pidstat,proc,nmt`; empty if none; `-` if unknown. Variable width. |
| `tags` | `info.json` → `tags` | Tags joined by `,`; empty string if array is empty |
| `batchid` | `info.json` → `batchid` | UUID of the batch this run belongs to |
| `repeat_no` | `info.json` → `repeat_no` | 0-based repetition index (`jbr-prepare --repeat`); `0` if the run was not repeated; `-` if `info.json` predates the field |
| `jdkname` | `info.json` → `jdk.name` | JDK name, e.g. `openjdk-25` |
| `jdkmajor` | `info.json` → `jdk.major` | Integer, e.g. `21` |
| `jdktype` | `info.json` → `jdk.type` | `openjdk` or `openj9` |
| `jdkversion` | `info.json` → `jdk.version` | Numerical version, e.g. `21.0.4` |
| `hostname` | `info.json` → `hostname` | |
| `arch` | `info.json` → `arch` | |
| `os` | `info.json` → `os` | |
| `result_ms` | `result.json` → `dacapo_result.result_ms` | `-` for specjbb runs or missing `result.json` |
| `stderr_ms` | `result.json` → `dacapo_result.stderr_ms` | Standard error of `time_ms` over the stability tail (last 10/7/5 iterations for runs with >= 40/30/20 iterations); `-` for specjbb runs, runs with < 20 iterations, or missing `result.json` |
| `stderr_pct` | `result.json` → `dacapo_result.stderr_pct` | Relative standard error of `time_ms` (percent of the mean) over the same tail; `-` for specjbb runs, runs with < 20 iterations, or missing `result.json` |
| `p50`, `p99`, `p100` | `result.json` → `dacapo_result.p50` / `.p99` / `.p100` | Final iteration's unsmoothed tail latency in usec (p100 = max); `-` for benchmarks without latency output, specjbb runs, or missing `result.json` |
| `p50_smooth`, `p99_smooth`, `p100_smooth` | `result.json` → `dacapo_result.p50_smooth` etc. | Same, 100ms metered smoothing |
| `p99_stderr`, `p99_stderr_pct` | `result.json` → `dacapo_result.p99_stderr` / `.p99_stderr_pct` | Standard error of `p99` over the same tail as `stderr_ms`, in usec / in percent of the mean; additionally `-` for runs with < 20 iterations |
| `p99_smooth_stderr`, `p99_smooth_stderr_pct` | `result.json` → `dacapo_result.p99_smooth_stderr` / `.p99_smooth_stderr_pct` | Same, for `p99_smooth` |
| `startup_ms` | `result.json` → `dacapo_result.startup_ms` | Milliseconds from JVM launch to the start of the first iteration; `-` for specjbb runs, runs without iteration timestamps, or missing `result.json` |
| `maxjops` | `result.json` → `specjbb_result.maxjops` | `-` for dacapo runs or missing `result.json` |
| `critjops` | `result.json` → `specjbb_result.critjops` | `-` for dacapo runs or missing `result.json` |
| `hbirmax` | `result.json` → `specjbb_result.hbirmax` | `-` for dacapo runs or missing `result.json` |
| `hbirset` | `result.json` → `specjbb_result.hbirset` | `-` for dacapo runs or missing `result.json` |
| `heapsz` | `info.json` → `heap.heapsize` | Heap size in MB (integer), e.g. `16384` |
| `heapszf` | `info.json` → `heapsize_factor` | Heap-sweep factor (e.g. `2.0`); `-` if the run was not part of a heap-size sweep |
| `gc` | `info.json` → `heap.gc` | GC collector name, e.g. `g1gc`, `zgc`, `gencon`; `-` if not determinable |
| `jvmargs` | `info.json` → `jvmargs` | Sanitized JVM argument string (see below). `-` if `info.json` is absent. |
| `rssmax`, `rssavg` | `result.json` → `memory.rssmax` / `.rssavg` | Max / time-weighted average RSS in MB; `-` if not collected |
| `mallocmax`, `mallocavg` | `result.json` → `malloc.mallocmax` / `.mallocavg` | Max / time-weighted average NMT total malloc in MB; `-` if not collected |
| `minflt`, `majflt` | `result.json` → `page_faults.minflt` / `.majflt` | Total minor / major page faults of the process (spec-post-process.md §2.8); `-` if not collected |
| `arumax`, `aruavg` | `result.json` → `arena.used_mb_max` / `.used_mb_avg` | Max / time-weighted average arena in-use memory (last tick peak, all block sizes) in MB (spec-post-process.md §2.9); `-` if not logged |
| `ardmax`, `ardavg` | `result.json` → `arena.dirty_mb_max` / `.dirty_mb_avg` | Max / time-weighted average arena dirty memory in MB; `-` if not logged |
| `arcmax`, `arcavg` | `result.json` → `arena.cached_mb_max` / `.cached_mb_avg` | Max / time-weighted average arena memory held in block caches in MB; `-` if not logged |
| `tempmax`, `tempin`, `tempout` | `result.json` → `temperatures.tempmax` / `.tempin` / `.tempout` | Max / first-sample / last-sample CPU temperature; `-` if not collected |
| `cpu_<m>_<s>` | `result.json` → `cpu.pct_<json-m>_<s>` | pidstat CPU percentage; `<m>` is `usr`, `sys`, `guest`, `wait` (`sys` reads `pct_system_*`), `<s>` is `max` or `avg` (time-weighted average); `-` if not collected |
| `cpu_<s>` | `result.json` → `cpu.pct_cpu_<s>` | pidstat total %CPU; `<s>` as above; `-` if not collected |
| `osthreads_max` | `result.json` → `threads.osthreads_max` | Max OS thread count during the run; `-` if not collected |
| `osthreads_avg` | `result.json` → `threads.osthreads_avg` | Average OS thread count during the run; `-` if not collected |
| `<c>_max`, `<c>_avg` | `result.json` → `threads.<c>.num_threads_max` / `.num_threads_avg` | Max / time-weighted average thread count of thread-dump category `<c>`: `c2`, `c1`, `gc`, `mutator`, `other` (spec-post-process.md §2.7); `-` if not collected |
| `<c>_cpu_avg`, `<c>_cpu_max` | `result.json` → `threads.<c>.cpu_pct_avg` / `.cpu_pct_max` | Average / max CPU usage of thread-dump category `<c>` (as above, plus `unaccounted`: CPU time of threads no dump saw, needs the `proc` collector) in %, 100 = one core; `-` if not collected |

#### 1.2.4 `jvmargs` sanitization

Before display, the following flags are stripped from the `jvmargs` array:

This list is shared with `jbr-status --all`'s JVM Args column (see
`scripts/common/jvm_flag_filter.py`).

| Pattern | Matches |
|---|---|
| prefix `-Xlog:` | GC / JVM log options (e.g. `-Xlog:gc*:file=...`) |
| prefix `-Xverbosegclog:` | OpenJ9 GC log option |
| exact `-XX:+PrintFlagsFinal` | Harness-injected diagnostic flag |
| exact `-XX:+UnlockExperimentalVMOptions` | Harness/profile unlock flag |
| exact `-XX:+UnlockDiagnosticVMOptions` | Harness/profile unlock flag |
| exact `-XX:+PrintVMInfoAtExit` | Harness-injected diagnostic flag |
| prefix `-XX:ErrorFile=` | JVM crash log path |
| prefix `-Dconfig.test.specLicense=` | SPECjbb licence property |

The remaining flags are joined with a single space.

#### 1.2.5 Column groups

Groups are composable shorthands — multiple groups (and bare column names) may be combined freely in a single `-o` or `-O` invocation. Group names are uppercase to avoid ambiguity with column names.

| Group | Columns | Auto-omit candidates |
|---|---|---|
| `DEFAULT` | `runid,time,name,tags` | `tags` |
| `JVM` | `jdkname,jdktype,jdkmajor,jdkversion` | — |
| `RESULTS` | `duration,result_ms,stderr_pct,startup_ms,maxjops,critjops,hbirmax,hbirset` | `result_ms,stderr_pct,startup_ms,maxjops,critjops,hbirmax,hbirset` |
| `LATENCY` | `p50,p99,p100,p99_stderr_pct` | — (never auto-omitted) |
| `LATENCYSMOOTH` | `p50_smooth,p99_smooth,p100_smooth,p99_smooth_stderr_pct` | — (never auto-omitted) |
| `HOSTINFO` | `hostname,arch,os` | — |
| `FLAGS` | `heapsz,heapszf,gc,jvmargs` | `heapszf` |
| `MEM` | `rssmax,rssavg,mallocmax,mallocavg` | — (never auto-omitted) |
| `FAULTS` | `minflt,majflt` | — (never auto-omitted) |
| `ARENA` | `ardmax,ardavg` | — (never auto-omitted) |
| `ARENAALL` | `arumax,aruavg,ardmax,ardavg,arcmax,arcavg` | — (never auto-omitted) |
| `TEMP` | `tempmax,tempin,tempout` | — (never auto-omitted) |
| `CPU` | `cpu_usr_avg,cpu_sys_avg,cpu_avg` | — (never auto-omitted) |
| `CPUALL` | `cpu_usr_avg,cpu_sys_avg,cpu_guest_avg,cpu_wait_avg,cpu_avg,cpu_usr_max,cpu_sys_max,cpu_guest_max,cpu_wait_max,cpu_max` | — (never auto-omitted) |
| `CPUMAX` | `cpu_usr_max,cpu_sys_max,cpu_guest_max,cpu_wait_max,cpu_max` | — (never auto-omitted) |
| `THREADS` | `osthreads_max,osthreads_avg,c2_max,c2_avg,c1_max,c1_avg,gc_max,gc_avg,mutator_max,mutator_avg,other_max,other_avg` | all but `osthreads_max,osthreads_avg` |
| `THREADCPU` | `c2_cpu_avg,c1_cpu_avg,gc_cpu_avg,mutator_cpu_avg,other_cpu_avg,unaccounted_cpu_avg,c2_cpu_max,c1_cpu_max,gc_cpu_max,mutator_cpu_max,other_cpu_max,unaccounted_cpu_max` | all |

Note: columns are deduplicated in order of first appearance, even if a group such as `DEFAULT` is expanded multiple times.

#### 1.2.6 Auto-omit rule

Any column whose value is `-` for **every row in the output set** is silently dropped. This naturally condenses the display when the result set contains only one benchmark type. Disable with `--no-autoomit`.

#### 1.2.7 Sort order

Default: ascending by `time` (oldest first). Override with `-s <col>` and optionally `--sort-desc` / `--sort-asc`.

`-s` accepts a comma-separated list of columns, e.g. `-s colA,colB,colC` sorts by `colA`, then breaks ties with `colB`, then `colC`. `--sort-asc` / `--sort-desc` apply uniformly to all columns in the list — there is no per-column direction. An unrecognized column name is an error.

#### 1.2.7a Grouping (`-g` / `--group`)

`-g <col>[,<col>...]` prints a banner before any row whose values in the named column(s) differ from the previous row's (and before the first row):

```
col: value[, col2: value2...]
<header>
<separator>
```

This is a purely visual aid for scanning a sorted result set — it does not re-sort or re-group the data itself, so `-g` is normally paired with `-s` using the group columns as the leading sort key(s), e.g. `jbr-ls -s name,jdkname -g name` sorts by name then jdkname, and visually segments the output by name. Each `-g` column must already be part of the displayed columns (see `-o`/`-O`); an unrecognized or non-displayed column is an error. Group columns are exempt from auto-omit.

#### 1.2.8 Missing `result.json`

Directories without a `result.json` are silently skipped. Result-derived columns (`result_ms`, `stderr_ms`, `stderr_pct`, `startup_ms`, `maxjops`, `critjops`, `hbirmax`, `hbirset`, and the latency columns `p50`, `p99`, ...) show `-` when the benchmark type does not produce that metric. The `duration` column shows `-` if `run-info.json` is absent.

#### 1.2.9 Other flags

| Flag | Meaning |
|---|---|
| `-s <col>[,<col>...]` | Sort by the named column(s), comma-separated. Default: `time`. |
| `--sort-asc` | Sort ascending (explicit; this is the default). |
| `--sort-desc` | Sort descending (default is ascending). |
| `-g <col>[,<col>...]` | Print a group banner and fresh header before rows whose values in these columns change (see §1.2.7a) |
| `--no-header` | Omit the header and separator lines; print only the data rows (column widths still account for the header names). Mutually exclusive with `-g`, `--markdown` and `--report`. |
| `--no-autoomit` | Disable auto-omit; show all columns even if every value is `-` |
| `--markdown` | Print as a markdown table instead of a fixed-width plain-text table (see §1.2.9a). Mutually exclusive with `--report`. |
| `--report <name>` | Write a standalone HTML report into a new `<project>/reports/<name>` directory instead of printing to stdout (see §1.2.9b). Mutually exclusive with `--markdown`. |
| `--self-contained` | With `--report`, copy each displayed run's result directory into the report directory and link to the copies instead of the originals under `results/`. Requires `--report`. |
| `--force` | With `--report`, if `<project>/reports/<name>` already exists, wipe it and re-use it for the new report. Requires `--report`. |
| `--compare <col>[=<n>][,<col>[=<n>]...]` | For each named numeric, displayed column, append to every non-baseline row's value its percent delta from the baseline row's value, e.g. `4652 (+6.9%)`. Not computable (cell left as is) when either value is `-`/non-numeric or the baseline is zero. With `<col>=<n>`, see *Per-column threshold* below. |
| `--baseline <runid>` | Use this run's row as the baseline for `--compare` (it stays in its normal sort position). Without it, the baseline is the first row of the table, or with `-g`, the first row of each group. Requires `--compare`; mutually exclusive with `-g`. |
| `-h`, `--help` | Usage |

**Per-column threshold.** A compare column given as `<col>=<n>` (e.g. `--compare result_ms=2,rssavg=5`; `n` a number > 0, fractions allowed) only shows its delta in cells whose percent delta is at least ±`n` percent; other cells show just their value, with no `--color` span and no comparison-plot emoji/file. The test uses the percent delta as displayed (rounded to one decimal), so `(+5.0%)` is shown at `n` = 5. A column without `=<n>` has no threshold. Giving the same column twice with different thresholds is an error; a mention without `=<n>` never conflicts with one that has it.

#### 1.2.9a Markdown output (`--markdown`)

Prints a markdown table instead of the fixed-width plain-text table: a header row, a `---` separator row, and one row per run, each formatted as `| cell | cell | ... |`. Literal `|` characters in cell values are escaped as `\|` so they can't break the table structure. `runid` is printed as plain text (no terminal hyperlink) in this mode, regardless of whether a results directory is available.

With `-g`/`--group`, all groups still share the one table (a single header and separator row), so the rendered columns line up across groups. Markdown has no cell spanning, so each group instead starts with a banner row that holds the group's values, in bold (`**value**`), in the group columns' own positions and leaves every other cell empty. Every group but the first (which sits directly under the table's header) follows its banner row with a repeated header row, the column names in bold.

#### 1.2.9b HTML report output (`--report <name>`)

Instead of printing to stdout, creates a new directory `<project>/reports/<name>` (error if it already exists, unless `--force` is given, in which case it is wiped and re-created; `reports/` itself is created if missing) and writes the rendered table into `reports/<name>/index.html` as a standalone HTML document (`<!DOCTYPE html>` through `</html>`, with an embedded `<style>` block), with one `<table>` holding a header row and one row per run. On success, prints `created report in <dir>` (no table output on stdout). Cell text is HTML-escaped.

When the `runid` column is present, each `runid` cell is wrapped in an `<a>` link to its run directory, relative to `reports/<name>/` (so the report keeps working when the project is moved); otherwise `runid` is printed as plain text.

- Without `--self-contained`, links point at the original run directories in the project (e.g. `<a href="../../results/<runid>">`).
- With `--self-contained`, every displayed run's result directory is first copied into `reports/<name>/<runid>/`, and links point at those copies instead — the report directory is then self-contained and independent of the project's `results/`. These links (and the per-run RSS/malloc plot links below) point into the report directory (e.g. `<a href="<runid>">`), so the report keeps working when copied out of the project.

With `-g`/`--group`, all groups still share the one `<table>`, so columns line up across groups. There is no `<thead>`; instead each group gets its own `<tbody>`, starting with a banner row `<tr class="group"><th colspan="<ncols>">col: value, ...</th></tr>` that spans all columns, followed by a header row with the column names.

With `--compare` and `--color`, the delta part of a compared cell (`(<delta>)`) is wrapped in a `<span class="delta-good">` or `<span class="delta-bad">` (styled green/red in the embedded CSS) — or, for a `cpu_*` column, a `<span class="delta-hot">` or `<span class="delta-cold">` (styled orange/blue) instead of an ANSI color code — unlike plain-text `--color`, this doesn't depend on the output stream being an interactive terminal.

When the `runid` column is present and displayed, each RSS cell (`rssmax`, `rssavg`, other than `-`) whose run directory (the same one the `runid` link points at — the original under `results/`, or its copy under `reports/<name>/` with `--self-contained`) contains a `system_map.svg` gets a leading bar-chart emoji (U+1F4CA) linking to that file; lacking that, it falls back to a `pidstat-rss.svg` in the same directory, if present. Runs without either get no emoji. Likewise, each malloc cell (`mallocmax`, `mallocavg`, other than `-`) whose run directory contains an `nmt-malloc.svg` gets a leading bar-chart emoji linking to that file, and each total CPU cell (`cpu_max`, `cpu_avg`, other than `-`; not the `cpu_usr_*`/`cpu_sys_*`/`cpu_guest_*`/`cpu_wait_*` breakdown columns) one linking to `pidstat-cpu.svg` (the stacked system/user/guest CPU load plot), each page fault cell (`minflt`, `majflt`, other than `-`) one linking to `pagefaults.svg` (minor and major fault rates, spec-post-process.md §2.8), and each `ardavg` cell (other than `-`; not the other arena columns) one linking to `arena-report/combined-all.svg` (the combined arena plots stacked, spec-post-process.md §1; lacking that, e.g. for runs post-processed before it existed, `arena-report/combined-bytes.svg`, or its old name `arena-report/combined.svg`). Without the `runid` column displayed, no RSS, malloc, CPU, page fault or arena cell gets an emoji, since there is no way to resolve the run's directory.

**Comparison plots.** When an RSS column is also named in `--compare`, each non-baseline row's cell whose delta is computable gets a second bar-chart emoji, inserted inside the `(<delta>)` text, immediately before the delta value (e.g. `(📊+6.9%)`) — inside the `--color` span when both apply. This emoji links to a new comparison plot generated on the fly into `reports/<name>/` (never into `results/`, self-contained or not); this link is always relative to `reports/<name>/` (e.g. `<a href="system_map_compare_<row_runid>_<baseline_runid>.svg">`), self-contained or not. When both the row's run and its baseline have a `system.map.jsonl`, it's a `system_map_compare_<row_runid>_<baseline_runid>.svg`: a three-panel plot — both runs' total RSS as line plots (top), then the baseline's stacked RSS breakdown, then the row's own (bottom) — sharing one x-range and one y-range sized to fit both runs, so all three are directly comparable. When either lacks `system.map.jsonl` but both instead have a `pidstat.jsonl`, it falls back to a plainer `pidstat_compare_<row_runid>_<baseline_runid>.svg`: a single panel with just the two runs' process RSS as line plots (pidstat has no category breakdown to stack), again sharing one x-range and one y-range. The two data sources are never mixed within one comparison — a pair only gets a plot when the same source is usable for both sides. One such file is generated per (row, baseline) pair and reused across that row's compared RSS columns. No file is generated and no emoji is added when neither data source is usable for both runs, or when `runid` isn't displayed.

Malloc columns (`mallocmax`, `mallocavg`) named in `--compare` get the same treatment, from `nmt.jsonl` as the only data source: when both the row's run and its baseline have an `nmt.jsonl`, the emoji links to an `nmt_compare_<row_runid>_<baseline_runid>.svg` — a three-panel plot of both runs' total NMT malloc as line plots (top), then the baseline's stacked NMT malloc category breakdown, then the row's own (bottom), sharing one x-range and one y-range. One such file is generated per (row, baseline) pair and reused across that row's compared malloc columns; none is generated, and no emoji is added, when either run lacks `nmt.jsonl`.

Total CPU columns (`cpu_max`, `cpu_avg`) named in `--compare` likewise, from `pidstat.jsonl` as the only data source: when both the row's run and its baseline have a `pidstat.jsonl`, the emoji links to a `cpu_compare_<row_runid>_<baseline_runid>.svg` — a three-panel plot of both runs' total CPU load as line plots (top), then the baseline's stacked CPU load breakdown (system, user, guest; bottom to top), then the row's own (bottom), sharing one x-range and one y-range. Each stacked panel is drawn exactly like the per-run `pidstat-cpu.svg`, except for the shared axis ranges. One such file is generated per (row, baseline) pair and reused across that row's compared CPU columns; none is generated, and no emoji is added, when either run lacks `pidstat.jsonl`.

Page fault columns (`minflt`, `majflt`) named in `--compare` likewise, from `proclog.jsonl` as the only data source: when both the row's run and its baseline have a `proclog.jsonl`, the emoji links to a `faults_compare_<row_runid>_<baseline_runid>.svg` — a two-panel plot of both runs' cumulative minor faults (top) and cumulative major faults (bottom) as line plots, sharing one x-range, and per panel one y-range. Cumulative counts rather than rates: the last point of each line is the value in the table, and the slope shows where in the run the difference builds up. Each line starts at the run's first proclog sample, whose count already includes the faults since process start. One such file is generated per (row, baseline) pair and reused across that row's compared page fault columns; none is generated, and no emoji is added, when either run lacks `proclog.jsonl`.

Arena columns (`arumax`, `aruavg`, `ardmax`, `ardavg`, `arcmax`, `arcavg`) named in `--compare` likewise, from `arena-report/combined.json` as the only data source: when both the row's run and its baseline have one, the emoji links to an `arena_compare_<row_runid>_<baseline_runid>.svg` — a three-panel plot of both runs' combined arena in-use (last tick peak, top), dirty and cached memory (bottom) in MiB as line plots over JVM uptime, sharing one x-range, and per panel one y-range. A series neither run has (no last tick peak in the logs) gets no panel; one only one run has is drawn for that run only. One such file is generated per (row, baseline) pair and reused across that row's compared arena columns; none is generated, and no emoji is added, when either run lacks `arena-report/combined.json`.

#### 1.2.10 Column widths

Column widths auto-size to the widest value in each column (including the header). This does not apply to `--markdown` or `--report` output, whose cells are not padded.

#### 1.2.11 Examples

```sh
# Default view — all runs, newest first
jbr-ls

# Default view — specific runs only
jbr-ls 228d007f-0546-4a17-9584-d91a448918d9 a1bcd3ca-e00c-46fd-8121-c1207ddfc6af

# DEFAULT + JVM arguments (jvmargs is placed before the trailing tags column)
jbr-ls -o jvmargs

# DEFAULT + host info + JVM detail combined
jbr-ls -o HOSTINFO,JVM

# Results only (no DEFAULT prepended)
jbr-ls -O RESULTS

# DEFAULT + results for a filtered set
jbr-ls -o RESULTS

# JVM detail for a filtered set
jbr-filt --benchmark dacapo | xargs jbr-ls -o JVM
```
