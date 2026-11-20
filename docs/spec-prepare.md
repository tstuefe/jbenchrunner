# jbenchrunner — Prepare Phase Specification

**Part of:** [PRD.md](PRD.md) (see master for goals, target users, repository layout, non-functional requirements, and decisions log)  
**Scope:** Configuration file formats, JVM flag assembly, and `jbr-prepare` — the tool that reads a `.run.toml` file, resolves all references, and writes a fully-resolved `BATCH_IN_PROGRESS.json` ready for execution.

---

## 1 Configuration

Configuration of a run happens via configuration files mainly; some settings can be modified via CLI arguments to the runner.

All configuration files are TOML (TOML 1.0, read with Python's stdlib `tomllib`; PRD.md D-76).
Document config files with `#` comments, which may appear anywhere. There is no special
comment key: a `COMMENT` key is treated like any other unknown field.

In this spec, "object" means a TOML table (a `[section]`, an `[[array-of-tables]]` element, or an
inline `{ ... }` table) and "array" a TOML array. TOML has no `null`: a field documented as
"null or omitted" is simply omitted.

A key may appear only once per table; a repeated key is a validation error.

**Long lists.** An array may span several lines, with `#` comments between the items and a
trailing comma; use this to group and annotate long `jvmargs`, `tags` or `bench` lists:

```toml
jvmargs = [
    "-XX:+UseCompactObjectHeaders",
    # diagnostics
    "-XX:+UnlockDiagnosticVMOptions",
    "-XX:+PrintVMInfoAtExit",
    # logging
    "-Xlog:arena*:arena.log::filecount=0",
]
```

There are no split keys: a `jvmargs_<suffix>`, `tags_<suffix>` or `bench_<suffix>` key (from
configs written before PRD.md D-76) is a validation error at any level, not the usual
unknown-field warning, since ignoring it would silently leave flags, tags or benchmarks out of
the batch.

### 1.0 Benchmark installations and the timestamp plugin

The benchmark installation directories are machine-specific and are set in the host config
(`dacapo_dir`, `specjbb_dir`, §1.6; PRD.md D-83).

The dacapo timestamp plugin jar is always resolved as `dacapo-timestamp-plugin/dacapoExtension.jar`
relative to the repository root (fixed conventional path; not configurable).

---

### 1.1 JVM Flag Profile (`.profile.toml`)

Contains an ordered list of JVM flags. Each entry is a single flag. Flags containing spaces (e.g.
paths with spaces) are expressed as individual array elements — no shell quoting needed.

```toml
# G1 baseline with large region size
flags = [
    "-XX:+UseG1GC",
    "-XX:G1HeapRegionSize=16m",
    "-XX:MaxGCPauseMillis=200",
    "-XX:ErrorFile=/my path/hs_err.txt",
]
```

### 1.2 Benchmark Definition (`.bench.toml`)

Describes a benchmark type, its parameters, and any JVM flags the benchmark requires to run correctly
(e.g. system properties). These flags represent benchmark requirements, not tuning.

**dacapo example — plain run:**
```toml
# dacapo batik database benchmark, large workload
benchmark = "dacapo"
sub_benchmark = "batik"
datasize = "large"
iterations = 10
minheap = 1759
jvmargs = ["-Djdk.xml.maxElementDepth=1000"]
```

**dacapo example — with Java version constraint:**
```toml
# dacapo cassandra large workload. Requires JDK 21 or earlier
benchmark = "dacapo"
sub_benchmark = "cassandra"
datasize = "large"
iterations = 5
minheap = 2048
max_java_version = 21
jvmargs = ["-Djava.security.manager=allow"]
```

**specjbb example:**
```toml
# specjbb2015 single-JVM composite mode
benchmark = "specjbb"
minheap = 8192
jvmargs = ["-Dconfig.test.specLicense=50"]
```

Fields:

| Field | Required | Values | Notes |
|---|---|---|---|
| `benchmark` | yes | `"dacapo"` \| `"specjbb"` | Determines which benchmark `jbr-execute` launches. |
| `sub_benchmark` | dacapo only | string | dacapo benchmark name, e.g. `"h2"`, `"tomcat"`. |
| `datasize` | dacapo only | `"small"` \| `"default"` \| `"large"` \| `"huge"` \| `"vlarge"` | Default: `"default"`. |
| `iterations` | dacapo only | integer | Total iteration count. Iterations 1..N-1 are warmups; iteration N is the measured run. Default: `10`. |
| `jvmargs` | no | array of strings | Benchmark-specific JVM flags; applied first in flag assembly. |
| `min_java_version` | no | integer | Minimum JDK feature version (inclusive) required to run this benchmark. Run entries whose testee JDK version is below this value are skipped with a warning. |
| `max_java_version` | no | integer | Maximum JDK feature version (inclusive) supported by this benchmark. Run entries whose testee JDK version is above this value are skipped with a warning. |
| `minheap` | required when `heapsize_factors` is set in `.run.toml`; otherwise optional | number | Estimated minimum heap size in MB sufficient for this benchmark (modern JDK, G1, default settings). Used as the base value for heap sweep size calculation (see §1.5). |

### 1.3 Bench Group Definition (`.benchgroup.toml`)

Defines a named, ordered list of benchmark definitions. Used in `.run.toml` via the `benchgroup`
field to expand a single run entry into one entry per member.

Files live in `config/benchgroup/`.

```toml
# All dacapo large workload benchmarks
members = [
    "dacapo-avrora-large",
    "dacapo-batik-large",
    "dacapo-biojava-large",
    "dacapo-cassandra-large",
    "dacapo-eclipse-large",
    "dacapo-graphchi-large",
    "dacapo-h2-large",
    "dacapo-h2o-large",
    "dacapo-jme-large",
    "dacapo-jython-large",
    "dacapo-kafka-large",
    "dacapo-luindex-large",
    "dacapo-lusearch-large",
    "dacapo-pmd-large",
    "dacapo-spring-large",
    "dacapo-sunflow-large",
    "dacapo-tomcat-large",
    "dacapo-xalan-large",
    "dacapo-zxing-default",
]
```

Fields:

| Field | Required | Type | Notes |
|---|---|---|---|
| `members` | yes | array of strings | Ordered array of bench names (without extension). Each must resolve to a `.bench.toml` in `config/bench/`. Order is preserved during expansion. Must not be empty. Duplicate names within a single group are a validation error. |

Members may not reference another `.benchgroup.toml` — no nesting, flat list only.

---

### 1.4 Batch Run Definition (`.run.toml`)

Defines which benchmarks to run, and an ordered list of run entries. The benchmarks are selected
once, at the top level: one or more `.bench.toml` names (`bench`, one name or an array of names), or one
`.benchgroup.toml` name (`benchgroup`). Each run entry references a JDK by name and zero or more
profiles by name, and describes *how* to run (flags, environment, collectors, ...); every run
entry is run against every selected benchmark. Run entries cannot select benchmarks (PRD.md D-72) —
a batch that needs different benchmarks for different configurations is split into several
`.run.toml` files, passed together to `jbr-prepare` (§3).

While the other config files are aimed for re-use, these files are more aimed at ad-hoc run definition
for specific benchmarking questions (although they certainly can be reused if necessary).

`jbr-prepare` accepts one or more `.run.toml` files in a single invocation (§3). When more than
one is given, every batch-level field described in this section (`heapsize_factors`, `jvmargs`,
`tags`, `bench`/`benchgroup`, `jdkname`, `profiles`, and so on) applies only within its own file —
nothing is merged across files (see PRD.md D-61). Two kinds of field are exceptions, because they
are batch-wide: `repeat` (§1, PRD.md D-79) and the collector settings (`collectors`,
`collectors_preset`, `perf_events_preset`; §1.4.1, PRD.md D-84),
which every input file must resolve to the same values. Each file's run entries are expanded independently
against that file's own batch-level defaults, and the resulting fully-resolved run lists are then
concatenated in file-argument order.

```toml
# GC algorithm comparison on an ad-hoc group of two benchmarks
tags = ["gc-comparison-2025"]
heapsize_factors = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
jvmargs = ["-XX:+UseCompactObjectHeaders"]
bench = ["dacapo-h2-large", "dacapo-tomcat-large"]

[[runs]]
jdkname = "jdk-21"
profiles = ["g1-baseline"]

[[runs]]
jdkname = "jdk-21"
profiles = ["shenandoah-tuned"]

[[runs]]
jdkname = "jdk-25"
profiles = ["g1-baseline"]
jvmargs = ["-Xss2m"]
```

This expands into 3 entries × 2 benchmarks × 6 heap factors = 36 runs.

Run entries are `[[runs]]` tables; an entry with no fields of its own is a bare `[[runs]]`. All
batch-level keys must come before the first `[[runs]]` header: TOML assigns every key after a
header to that table, so a batch-level key written further down silently becomes a field of the
last run entry above it (or, for `bench`/`benchgroup`, a validation error).

Top-level fields:

| Field | Required | Type | Notes |
|---|---|---|---|
| `heapsize_factors` | no | array of numbers | Array of positive numbers. When present, every run entry is expanded into N runs — one per factor. Each factor is applied to the `minheap` value of the referenced `.bench.toml` to compute the heap size (see §1.5). Absence of this field means no sweep; the user must supply an explicit `-Xmx` somewhere in the assembled flags (profiles, run-entry `jvmargs`, CLI flags) — absence of any `-Xmx` is a validation error. |
| `dynamic_heap` | no | boolean | Only meaningful together with `heapsize_factors` (same file-level-only scope, no per-run-entry override); it is a validation error to set it without `heapsize_factors` also set in the same file. Default: `false` — heap-sweep expansion injects `-Xms`/`-Xmx`/`-XX:+AlwaysPreTouch` as it does today. When `true`, heap-sweep expansion injects only `-Xmx` from the swept size, leaving `-Xms` and pre-touch behavior unset (see §1.5). |
| `jvmargs` | no | array of strings | Array of JVM flag strings applied to every run in the batch. See §2 for assembly order. |
| `tags` | no | array of strings | Array of tag strings applied to all runs in the batch. Merged (union) with per-run `tags`. |
| `dacapo_iterations` | no | integer | Warmup iteration count (DaCapo Harness `-n`) applied to every dacapo run in the batch, overriding that run's `.bench.toml` `iterations` field. Non-dacapo runs (e.g. specjbb) are unaffected. Overridden by the per-run-entry `dacapo_iterations`; both are overridden by `--dacapo-iterations` on the CLI, which wins over everything (see §3). Useful for quickly re-running a batch with fewer/more warmups without editing every `.bench.toml`. |
| `repeat` | no | integer | Positive integer: repeat each run entry this many times, interleaved by round (see `--repeat`, §3). File-level only, no per-run-entry override. Default: `1`. Overridden by `--repeat` on the CLI. Unlike the other batch-level fields it is not scoped to its own file's entries: repetition is applied once over the combined run list of all input files, so the value applies to the whole batch, and input files that set different values are a validation error unless `--repeat` is given (PRD.md D-79). |
| `jvmenv` | no | object (string/int values) | Environment variables set in the JVM subprocess's environment for every run in the batch, e.g. `jvmenv = { MALLOC_ARENA_MAX = 4 }`. Merged with per-run `jvmenv`; on key collision the per-run value wins. Values are coerced to strings. |
| `do_cpu_pinning` | no | boolean | Enables CPU pinning (via `taskset --cpu-list`, using the host's `cpu_list`, §1.6) for every run in the batch. Overridden by the per-run-entry `do_cpu_pinning`. Default: `false`. See spec-execute.md §1, §2 step g. |
| `use_ramdisk` | no | boolean | Enables use of the host's `ramdisk_path` (§1.6) for every run in the batch's result directory (spec-execute.md §6). Overridden by the per-run-entry `use_ramdisk`. Default: `false` — a configured `ramdisk_path` alone is no longer sufficient; it must also be opted into here or per-run. It is a validation error for the effective value to be `true` for any run when the host has no `ramdisk_path` configured (see §1 step 1c). |
| `dacapo_args` | no | array of strings | Extra command-line arguments passed verbatim to the DaCapo Harness (e.g. `["-t", "16"]` for thread count), inserted after `--print-stats` and before the sub-benchmark name (see spec-execute.md §2 step g.ii). Applied to every dacapo run in the batch. Non-dacapo runs are unaffected. Overridden by the per-run-entry `dacapo_args`. Default: none (no extra args). |
| `bench` | one of `bench` / `benchgroup` required | string or array of strings | Name of a `.bench.toml` file (without extension) in `config/bench/`, or a non-empty array of such names to select an ad-hoc group of benchmarks; a name may appear only once. Every run entry is run against every named benchmark, in order. Mutually exclusive with `benchgroup`. |
| `benchgroup` | one of `bench` / `benchgroup` required | string | Name of a `.benchgroup.toml` file (without extension) in `config/benchgroup/`. Expanded into its members (§1.3); every run entry is run against every member, in member order. Mutually exclusive with `bench`. |
| `collectors` | no | object | Which data collectors run, their sampling interval and log compression, for every run of the batch. Mutually exclusive with `collectors_preset`. Batch level only (no per-run-entry override); must agree across input files. See §1.4.1. |
| `collectors_preset` | no | string | Name of a collector preset (§1.11) used for every run of the batch. Mutually exclusive with `collectors`. Batch level only; must agree across input files. See §1.4.1. |
| `perf_events_preset` | no | string | Name of a perf events preset (§1.12): the events the `perf` collector counts, for every run of the batch. Must agree across input files. Absent: perf's own default event set (`perf stat` without `-e`). Batch level only. See §1.4.2. |
| `jdkname` | no | string | Batch-level default JDK name (without extension), used by any run entry that doesn't specify its own `jdkname`. Overridden by the per-run-entry `jdkname`. A run entry with neither an entry-level nor a batch-level `jdkname` uses `default` (`config/jdks/default.jdk.toml`). |
| `profiles` | no | array of strings | Batch-level default list of profile names (without `.profile.toml` extension). Used by any run entry that doesn't specify its own `profiles`. Overridden wholesale (not merged) by the per-run-entry `profiles`. Default: none (empty array). |

Per-run entry fields (a `bench`, `benchgroup`, `collectors`, `collectors_preset`,
or `perf_events_preset` key in a run entry is a validation error,
not an ignored unknown field — see PRD.md D-72, D-80):

| Field | Required | Type | Notes |
|---|---|---|---|
| `jdkname` | no | string | JDK name (without extension), resolved via `config/jdks/<jdkname>.jdk.toml` (see §1.7). Takes precedence over the batch-level `jdkname`. An entry that omits it falls back to the batch-level `jdkname`, if set, else to `default` (`config/jdks/default.jdk.toml`). |
| `profiles` | no | array of strings | Array of profile names (without `.profile.toml` extension) from `config/profiles/`. When present (even as `[]`), wholesale-overrides the batch-level `profiles`; when absent, falls back to the batch-level `profiles`, or an empty array if that's unset too. |
| `jvmargs` | no | array of strings | Array of additional JVM flag strings for this run only. See §2 for assembly order. |
| `tags` | no | array of strings | Array of tag strings for this run entry (applied to its runs for every selected benchmark). Merged (union) with the batch-level `tags`. |
| `jvmenv` | no | object (string/int values) | Environment variables set in the JVM subprocess's environment for this run only. Merged with the batch-level `jvmenv`; on key collision this per-run value wins. Values are coerced to strings. |
| `do_cpu_pinning` | no | boolean | Enables CPU pinning for this run only, overriding the batch-level `do_cpu_pinning`. See spec-execute.md §1, §2 step g. |
| `use_ramdisk` | no | boolean | Enables ramdisk use for this run's result directory only, overriding the batch-level `use_ramdisk`. See spec-execute.md §6. |
| `dacapo_args` | no | array of strings | Extra command-line arguments passed verbatim to the DaCapo Harness for this run only, overriding the batch-level `dacapo_args`. Ignored for non-dacapo runs. |
| `dacapo_iterations` | no | integer | Warmup iteration count for this run only, overriding the batch-level `dacapo_iterations` (which in turn overrides this run's `.bench.toml` `iterations` field). Both are overridden by `--dacapo-iterations` on the CLI, which wins over everything. Ignored for non-dacapo runs. |

#### 1.4.1 Collector settings (`collectors`, `collectors_preset`)

A batch's collector settings (spec-execute.md §5.1) are one **collectors structure**, the same
for every run of the batch (PRD.md D-84):

```toml
interval = 5
compression = true
enabled = ["cpu_temp", "pidstat", "proc", "nmt"]
```

| Key | Required | Type | Notes |
|---|---|---|---|
| `interval` | yes | integer | Sampling interval in seconds, passed to every collector via `-i`. Positive. `--collector-interval` on the CLI wins over it (§3). |
| `compression` | yes | boolean | Selected collectors write their output zstd-compressed (`<file>.zst`) instead of plain text — see spec-execute.md §5.2 for the set and the file naming. It is a validation error for the effective value to be `true` for any run when the `zstd` binary is not on `PATH` (see §3 step 1c). See PRD.md D-58. |
| `enabled` | yes | array of strings | Short names of the collectors to run, see below. May be empty. Unknown names and duplicates are validation errors. |

Any other key is a validation error. All three keys are
required, so a collectors structure is always complete — there are no hidden defaults.

Collector short names, and what they run:

| Name | Collector (spec-execute.md §5.1) | Config (§1.10) |
|---|---|---|
| `cpu_temp` | `cpu-temp-logger.py` | `cpu-temp-logger` |
| `pidstat` | `pidstat-logger.sh` | `pidstat-logger` |
| `proc` | `proc-logger.py` | `proc-logger` |
| `perf` | `perf-logger.sh` | `perf-logger` |
| `nmt` | `jcmd VM.native_memory summary`; also appends the NMT JVM flags (§2 step 6) | `nmt` |
| `system_map` | `jcmd System.map` | `system_map` |
| `thread_print` | `jcmd Thread.print` | `thread_print` |

A collectors structure is used in two places: as a preset file (§1.11), and at the batch level
of a `.run.toml`, in one of two mutually exclusive forms:

- `collectors = { ... }` (or a `[collectors]` table) — the structure itself, or
- `collectors_preset = "<name>"` — the complete settings of the preset `<name>` (§1.11).

Setting both is a validation error. Run entries cannot set either (PRD.md D-80).

**Resolution:** each `.run.toml` file resolves to its collector settings, or, if it sets neither,
to the preset `cheap-10s`, the only implicit default. `--collector-interval` then overrides the
interval. With several input files, all must resolve to the same settings — compared after
resolution, so a preset and an identical explicit structure agree, and the interval is not
compared when `--collector-interval` is given; otherwise `jbr-prepare` aborts.

```toml
collectors_preset = "all-10s"
# or: collectors = { interval = 2, compression = false, enabled = ["proc", "nmt"] }
```

Runs that need different collector settings go into separate batches: within a batch, every run
pays the same collector overhead.

The result is what the batch requests. `jbr-prepare` then drops from the whole batch every
requested collector that cannot run — this machine cannot run it (probed, §1.6), or its
`restrict_to` (§1.10) does not cover the JDK of every run — with a warning (§3 step
1d-collectors). What is left is stored once in `BATCH_IN_PROGRESS.json` (§4); `jbr-execute` starts
exactly that set for every run, fails a run whose collector does not start, and copies the
settings into each run's `info.json` (spec-execute.md §4.1, §5.3).

#### 1.4.2 Perf events (`perf_events_preset`)

Which events the `perf` collector counts is a choice of the experiment, not of the host, so it
is set at the batch level of a `.run.toml` (PRD.md D-81):

```toml
collectors_preset = "all-10s"
perf_events_preset = "l1-tlb"
```

- `perf_events_preset = "<name>"` — count the events listed in the preset `<name>` (§1.12).
- absent — count perf's own default event set: `perf stat` is run without `-e`, and whatever it
  reports is recorded. That set depends on the CPU, the kernel and the perf version.

Presets are only a convenience for holding an event list: `BATCH_IN_PROGRESS.json` and
`info.json` record the resolved events (`collectors.perf_events`, §4), or `null` for perf's
default set, never the preset name.

Event names may be CPU-specific; choosing a set that fits the host is up to the user.
`jbr-prepare` does not check the events. To keep a misfit from failing every run, `jbr-execute`
checks the batch's whole perf command once at batch start, after machine stabilization, and
aborts the batch before the first run if perf rejects it or reports an event as
`<not supported>` (spec-execute.md §1.3 step 4e, PRD.md D-86). Nothing is dropped: to change the
events, abandon the batch and prepare it again.

The check runs only if `perf` is still in `collectors.enabled` after step 1d-collectors. A
`perf_events_preset` whose batch's collector settings do not request `perf` is ignored with a
warning.

---

### 1.5 Heap Size Sweep

When `heapsize_factors` is present in a `.run.toml`, every run entry is expanded by
`jbr-prepare` into N separate runs — one per factor — before writing `BATCH_IN_PROGRESS.json`.
Each expanded run gets its own pre-assigned RUNID (GUID) and appears as a distinct entry.

**Heap size calculation:**

For each factor F and the `minheap` value (in MB) from the referenced `.bench.toml`:

```
heap_mb = round(F × minheap)   [rounded to nearest integer MB]
```

The result is expressed as `<heap_mb>m` (e.g. `3518m`). If `minheap` is absent from a `.bench.toml`
referenced by any run in the batch, `jbr-prepare` aborts with an error.

**Flag injection per sweep step:**

```
-Xms<heap_mb>m  -Xmx<heap_mb>m  -XX:+AlwaysPreTouch
```

If the batch's `dynamic_heap` field is `true`, only `-Xmx<heap_mb>m` is injected — `-Xms` and
`-XX:+AlwaysPreTouch` are omitted, leaving the heap free to grow dynamically up to `-Xmx`. Default
(`dynamic_heap` absent or `false`): all three flags are injected, as above.

These flags are appended immediately after the bench flags, during `.bench.toml` processing (see §2).

The originating factor F is also recorded verbatim (not recomputed from `heapsize`) on the run
entry as `heapsize_factor` (see §3), so it survives into `info.json` (spec-execute.md §4.1) and can
be queried and displayed by `jbr-filt`/`jbr-ls` (spec-analyze.md §1.1.2, §1.2.3). Absent for runs
not part of a heap-size sweep.

**Progress header** for a sweep run includes the heap size:

```
[3/8] bench=h2-large  jdkname=jdk-21  profiles=g1-baseline  heap=3518m
```

---

### 1.6 Machine Configuration (`<hostname>.host.toml`)

Located at `config/hosts/<hostname>.host.toml`, where `<hostname>` is the exact output of the `hostname`
command on the machine that will run the benchmarks. If that file does not exist,
`config/hosts/default.host.toml` is used instead (with a note); it is an error if that is missing too.

Contains machine-specific parameters, including the paths to this machine's benchmark
installations (PRD.md D-83).

```toml
# myserver — Sapphire Rapids, perf counters available
maxheap = 65536
ramdisk_path = "/dev/shm/jbenchrunner"
cpu_list = "0-3"
dacapo_dir = "/path/to/dacapo"
specjbb_dir = "/path/to/specjbb2015"
```

Fields:

| Field | Required | Type | Notes |
|---|---|---|---|
| `maxheap` | no | integer | Maximum heap size in MB this machine can support. When present, `jbr-prepare` will skip benchmark runs whose heap size exceeds this threshold. See §3 step 1i. |
| `ramdisk_path` | no | string | Absolute path to a ramdisk (tmpfs/ramfs) mount on this machine. When set, each run's result directory is physically created there instead of directly under the project's `results/`, with `results/<runid>` becoming a symlink to it for the duration of the run — see spec-execute.md §6. Copied back to `results/<runid>` and removed from the ramdisk after each run finishes, regardless of outcome. |
| `cpu_list` | required when any run has effective `do_cpu_pinning: true` (see §1.4); optional otherwise | string | CPU ID list in `taskset --cpu-list` syntax, e.g. `"0-3"` (range) or `"1,3,5,7,9"` (comma-separated list). Passed verbatim to `taskset` — no jbenchrunner-side parsing beyond a basic syntax sanity check (see §3 step 1c). Used by `jbr-execute` to wrap the benchmark process launch (spec-execute.md §1, §2 step g) when `do_cpu_pinning` is active for a run. |
| `dacapo_dir` | yes (when running dacapo benchmarks) | string | Absolute path to the dacapo installation directory (must contain a `dacapo-*chopin.jar`). |
| `specjbb_dir` | yes (when running specjbb benchmarks) | string | Absolute path to the SPECjbb2015 installation directory (must contain `specjbb2015.jar`). |
| `test_jdk` | no | string | Absolute path (a `JAVA_HOME`) of the JDK the integration tests run benchmarks with. Not used by any jbr tool. |

There is no host config switch for collectors (PRD.md D-82). Which collectors a run requests is
set in `.run.toml` (§1.4.1); whether this machine can run the machine-dependent ones is probed by
`jbr-prepare` (§3 step 1d-collectors), with the same checks the collector tests use
(`scripts/common/collector_probe.py`):

| Collector | Can run when |
|---|---|
| `cpu_temp` | one `read-cpu-temp.sh` read succeeds |
| `pidstat` | `pidstat` is on `PATH` |
| `proc` | `/proc/<pid>/stat` and `/proc/<pid>/status` exist (for `jbr-prepare`'s own pid) |
| `perf` | `perf` is on `PATH` (the events are checked by `jbr-execute` at batch start, §1.4.2) |

A collector that passes its probe and then fails to start is a hard failure for that run (see
spec-execute.md §5.3). A `has_cpu_temp`, `has_pidstat`, `has_linux_procfs` or `has_perf` key
from older host configs is an unknown field (warning).

### 1.7 JDK Configuration (`.jdk.toml`)

Located at `config/jdks/<name>.jdk.toml`, where `<name>` is the JDK name used in `"jdkname"` fields
(e.g. `jdk-21`, `openj9-11`).

Contains the location of a specific JDK installation. The user is responsible for keeping
these paths correct across machines.

```toml
# SAP JVM 21 installation
java_home = "/opt/sapjvm-21"
```

Fields:

| Field | Required | Type | Notes |
|---|---|---|---|
| `java_home` | yes | string | Absolute path to the JDK installation directory. The java binary is derived as `<java_home>/bin/java`. |

---

### 1.8 JDK Type Configuration

Located at `config/jdk-types/<jdk-type>.jdktype.toml`, where `<jdk-type>` is either `openjdk` (HotSpot) or `openj9`.

Contains JDK type-specific parameters that apply to all JVMs of that type, regardless of version. Used during the final stage of JVM flag assembly (see §2, step 5) to append extra flags and the GC log option.

**Example: OpenJDK (HotSpot)**

```toml
# OpenJDK (HotSpot) JDK type configuration
gc_log_option = "-Xlog:gc*:file=gc.log::filecount=0"
extra_jvmargs = ["-XX:+PrintFlagsFinal"]
```

**Example: OpenJ9**

```toml
# OpenJ9 JDK type configuration
gc_log_option = "-Xverbosegclog:gc.log"
# no extra_jvmargs
```

Fields:

| Field | Required | Type | Notes |
|---|---|---|---|
| `gc_log_option` | yes | string | GC log flag passed verbatim to the JVM. The path `gc.log` is relative — it resolves to the result directory because the benchmark runs with that directory as cwd (spec-execute.md §2 step c). No placeholder substitution is performed. |
| `extra_jvmargs` | no | array of strings | JVM flags to append unconditionally to every run using this JDK type. If omitted, no extra flags are added. Useful for flags that should always be present for a given JDK type. |

---

### 1.9 External Name Translation

When a field name originates outside the project — from a tool's column header, a kernel
pseudo-file, or an external process's stdout — it **must** be translated before being stored
as a JSON key. The translated name must satisfy the field-name constraint from AGENTS.md
(`A-Za-z0-9_` only, `snake_case`).

Translation applies the following steps in order:

1. **Rate suffix** — `/s` → `_per_sec` (e.g. `minflt/s` → `minflt_per_sec`, `kB_rd/s` → `kb_rd_per_sec`)
2. **Percent prefix** — leading `%` → `pct_` (e.g. `%CPU` → `pct_cpu`, `%MEM` → `pct_mem`)
3. **Unit suffix** — a known unit suffix (`kB`, `MB`, etc.) is dropped; the unit is documented in the schema table, not in the key name
4. **CamelCase / PascalCase → snake_case** — split on word boundaries (e.g. `VmPeak` → `vm_peak`, `PPid` → `ppid`, `THP_enabled` → `thp_enabled`, `RssAnon` → `rss_anon`)
5. **Remaining illegal characters** — any character not in `A-Za-z0-9_` is replaced with `_`
6. **Lower-case** the entire result
7. **Normalise underscores** — collapse consecutive `_` to one; strip leading/trailing `_`

Each collector that reads external names maintains an **explicit static mapping table** in the
script. The table is the normative source of truth for that collector; the steps above are the
derivation procedure used to build the table when new external names need to be added.

Canonical translation tables for current sources are in [spec-post-process.md — Addendum A](spec-post-process.md#addendum-a--external-name-translation-tables).

`perf-logger.sh` writes raw `perf stat` CSV (not JSON), so no translation applies.

---

### 1.10 Collector Configuration (`<name>.collector.toml`)

Located at `config/collectors/<name>.collector.toml`. Exactly one file per collector is required; a missing file is a hard error. Names: `cpu-temp-logger`, `pidstat-logger`, `proc-logger`, `perf-logger`, `nmt`, `system_map`, `thread_print` (for the short names used in `.run.toml`, see §1.4.1).

```toml

[[restrict_to]]
jdk_type = "openjdk"
min = 21
```

`restrict_to` is optional. If it is absent, the collector applies to all JDK types and versions (the file may then contain only comments, or be empty).

| Field | Required | Type | Notes |
|---|---|---|---|
| `restrict_to` | no | array of objects | Restricts the collector to the listed JDK type/version combinations; a JDK matches if any entry matches. Absent = applies to all JDKs. An explicitly empty array means "never". |
| `restrict_to[].jdk_type` | yes | string | `openjdk` or `openj9` (as detected, §3 step 1d). |
| `restrict_to[].min` / `.max` | no | integer | Inclusive bounds on the JDK feature (major) version. Omitted = unbounded. |

A collector that does not apply to the JDK of some run is removed from the batch's collector set at prepare time, for all runs, with a warning (§3 step 1d-collectors), and so not started (spec-execute.md §5.1); its output files are absent and post-processing skips them. Collectors whose command or output differ per JDK type (e.g. `jcmd` on OpenJ9) are separate collectors, each with its own `restrict_to`.

### 1.11 Collector Preset (`<name>.collector-preset.toml`)

Located at `config/collector-presets/<name>.collector-preset.toml`, where `<name>` is the value
used in `collectors_preset` (§1.4.1). The file is a collectors structure (§1.4.1), with the same
keys and rules:

```toml
# Collectors with no measurable effect on the benchmark
interval = 10
compression = false
enabled = ["cpu_temp", "pidstat", "proc"]
```

Shipped:

| Preset | `enabled` | `interval` | `compression` |
|---|---|---|---|
| `none` | (none) | 10 | `false` |
| `cheap-1s`, `cheap-10s`, `cheap-60s` | `cpu_temp`, `pidstat`, `proc` — no measurable effect on the benchmark | 1, 10, 60 | `true` |
| `all-1s`, `all-10s`, `all-60s` | all seven | 1, 10, 60 | `true` (the System.map and Thread.print logs are large) |

`nmt`, `system_map` and `thread_print` go through `jcmd` and cause safepoints; `perf` usually
needs host setup (privileges, §1.4.2). So none of them is in `cheap-*`. `cheap-10s` is also the default for runs
that set no collector settings at any level. `all-*` lists every collector explicitly, so it has to
be updated when a collector is added. Further presets can be added as files.

`jbr-prepare` loads only the presets a batch actually uses (including `cheap-10s` when some run
falls back to it); a used preset file that is missing or invalid is a hard error (§3 step 1c).

### 1.12 Perf Events Preset (`<name>.perf-events-preset.toml`)

Located at `config/perf-events-presets/<name>.perf-events-preset.toml`, where `<name>` is the
value used in `perf_events_preset` (§1.4.2). A named list of perf events, nothing more:

```toml
# L1 data cache and data TLB; generic names, but not every CPU supports all of them
events = [
    "cycles",
    "instructions",
    "L1-dcache-loads",
    "L1-dcache-load-misses",
    "dTLB-loads",
    "dTLB-load-misses",
]
```

| Key | Required | Type | Notes |
|---|---|---|---|
| `events` | yes | array of strings | Event names as accepted by `perf stat -e`, one per entry (an entry may itself contain commas, e.g. `cpu/event=0x3c,umask=0/`). Non-empty; duplicates are a validation error. perf's default set is chosen by omitting `perf_events_preset`, not by an empty preset. |

Any other key is a validation error. Presets may use CPU-specific names; nothing checks that a
preset fits a host other than the perf check at batch start (§1.4.2). A used preset file
that is missing or invalid is a hard error (§3 step 1c).

---

## 2 JVM Flag Assembly

Flags are assembled in this fixed order for every run:

1. **Bench flags** — requirements for the benchmark to function correctly (from `.bench.toml`).
   Heap sweep flags (if any) are appended here, immediately after the bench flags.
2. **Profile flags** — Sets of JVM flags (from `.profile.toml` files).
3. **Run-entry flags** — from `.run.toml`: the top-level `jvmargs` (if any), followed by the
   per-run entry `jvmargs` (if any). The two arrays are concatenated in that order.
4. **CLI flags** — `--jvmargs` / `--jvmarg` arguments passed directly to `jbr-prepare` on
   the command line.
5. **JDK type flags + GC log flag** — appended last by `jbr-prepare` after detecting the JDK type
   from the binary (see §3 step 1d). It appends the appropriate extra flags (e.g. `-XX:+PrintFlagsFinal`
   for OpenJDK) followed by the GC log flag (e.g. `-Xlog:gc*:file=gc.log::filecount=0` for OpenJDK,
   `-Xverbosegclog:gc.log` for OpenJ9). The path is relative and resolves to the result
   directory because the benchmark runs with that directory as cwd (spec-execute.md §2 step c).
6. **NMT flags** — appended last, only when `nmt` is in the batch's effective collector set
   (§1.4.1), after the machine probe and `restrict_to` checks (§3 step 1d-collectors):
   ```
   -XX:NativeMemoryTracking=summary -XX:+UnlockDiagnosticVMOptions -XX:+PrintNMTStatistics
   ```
   Enables the periodic NMT collector (spec-execute.md §5.1) to actually get data, and prints an
   NMT summary to the JVM's own stdout at exit as a cross-check. Absent otherwise.

---

## 3 `jbr-prepare`

Reads one or more `.run.toml` files, resolves all references, assembles fully-resolved run entries,
and writes `<project>/BATCH_IN_PROGRESS.json`, plus frozen copies of the `.run.toml` files in
`<project>/batches/<BATCHID>/`. No benchmark processes are launched.

Must be run inside a project (PRD.md §4); outside one it aborts. The `.run.toml` paths are
resolved against the current working directory as usual; the files may live anywhere, by
convention in the project (e.g. `<project>/run/`). With no `<file.run.toml>` argument,
`<project>/example.run.toml` is used (`jbr-init` puts a copy there, spec-other.md §5); it is an
error if that file does not exist.

```
jbr-prepare [options] [<file.run.toml> ...]
```

Multiple `<file.run.toml>` arguments may be given; they are processed in the order given on the
command line (§3.1). Each file's own batch-level defaults apply only to its own entries (§1.4,
PRD.md D-61); the resulting run lists are concatenated in that same order before `--repeat` and
`seq` numbering are applied.

Options:

| Option | Notes |
|---|---|
| `--jvmargs "<flags>"` | Applied to all runs; appended after run-entry flags, before JDK type detection flags (see §2). |
| `--jvmarg <flag>` | Single flag, repeatable. Applied to all runs. Use when a flag value contains spaces (e.g. paths). |
| `--tags <tag,...>` | Comma-separated list of tags. Merged (union) with `tags` from `.run.toml` (both global and per-run). Stored per-run in `BATCH_IN_PROGRESS.json`. |
| `--collector-interval <secs>` | Sampling interval in seconds passed to all collectors via `-i`; wins over the `interval` of every run's collector settings from `.run.toml` (§1.4.1). Default: `10` if none is set anywhere. The fully-resolved effective value for each run is stored per-run in `BATCH_IN_PROGRESS.json` as `collectors.interval_secs` (see §4); there is no separate batch-level field in the output. |
| `--dacapo-iterations <N>` | Warmup iteration count (DaCapo Harness `-n`) for every dacapo run in the batch; wins over both the batch-level and per-run-entry `dacapo_iterations` fields from `.run.toml`, which in turn override each run's `.bench.toml` `iterations` field. Non-dacapo runs are unaffected. No separate top-level field in `BATCH_IN_PROGRESS.json` — the resolved value is already baked into each run entry's `iterations` field. |
| `--repeat <N>` | Repeat each run entry N times (default: `1`, i.e. no repetition). Applied once, after all input files' entries have been expanded and concatenated (§3.1): entries are interleaved by round across that whole combined list, not per file — given entries A and B (from one or several files, in their concatenated order) with `--repeat 3` the output order is A B A B A B, with `repeat_no` values 0 0 1 1 2 2. Must be a positive integer. Overrides the `.run.toml` `repeat` field (§1); default `1` if neither is set. |
| `--sudo` | Mark the batch as sudo-mode: stored as `sudo_mode` in `BATCH_IN_PROGRESS.json` (see §4). `jbr-execute` reads this field and may then run the root commands of machine stabilization/reset and `--shutdown`, granted by the rules of `config/jbenchrunner.sudoers` (PRD.md D-87) — there is no equivalent flag on `jbr-execute` itself, so the decision is made once, here, and applies uniformly across every resume of the batch (see spec-execute.md §1). Default: `false`. |
| `--stabilize-machine` | Let `jbr-execute` stabilize the machine for benchmarking (CPU governor, turbo boost, ASLR, caches, services, swap, overcommit; `prepare-for-benchmarks.sh`) once at batch start, and reset machine state (sync, drop_caches, restart systemd-journald; `reset-machine.sh`) before every run, including the first (spec-execute.md §1.3). The settings weaken security and last until reboot, so this is an explicit opt-in on every `jbr-prepare`, with no host config default (PRD.md D-88). Default: off. Stored as `stabilize_machine` in `BATCH_IN_PROGRESS.json` (§4). Without `--sudo` an error. Linux only: elsewhere `jbr-execute` skips it with a warning. |

### 3.1 Preparation Steps

**Guard:** first, if the `active-batch` link (PRD.md §7) exists and points to a batch in another
project, `jbr-prepare` aborts, naming that project. A dangling link (the other project was moved
or deleted) also aborts, with a message saying so; it is cleared by `jbr-execute --abort` in the
project (spec-execute.md §1), or by removing the link by hand.

Then, if `<project>/BATCH_IN_PROGRESS.json` already exists, `jbr-prepare` checks each of its
run entries for whether `result_dir` exists on disk (spec-execute.md §1.2). If none do, the batch
was never started — it's safe to discard; `jbr-prepare` prints a note and overwrites it at the
end (step 3), automatically, no flag needed, and also removes that batch's
`batches/<BATCHID>/` directory:

```
Note: BATCH_IN_PROGRESS.json exists but has not been started; overwriting it.
```

Otherwise (any `result_dir` exists, or the file can't be read/parsed) it aborts — see Abort
conditions below.

**Steps** (run in order; abort on any error in the Abort-conditions table before creating any
output; a Warn-and-continue condition prints to stdout and keeps going):

1. Validate and resolve all inputs:
   a. Load `config/hosts/<hostname>.host.toml`, or `config/hosts/default.host.toml` if absent
      (§1.6); verify the jar(s) for each benchmark type used in the batch exist (`dacapo-*chopin.jar`
      under its `dacapo_dir` plus the timestamp plugin jar; `specjbb2015.jar` under its
      `specjbb_dir`). This check runs once step b knows the benchmark types used.
   b. For each `.run.toml` file, in the order given on the command line: parse it and all
      referenced `.bench.toml`, `.benchgroup.toml`, and `.profile.toml` files, and tag every
      resulting entry with `source_file` (that file's base name, §4), so the tag survives
      expansion. That file's own batch-level defaults (`bench`/`benchgroup`, `jdkname`,
      `profiles`, `jvmargs`, `tags`, `heapsize_factors`, etc. — §1.4) apply only to its own
      entries; nothing is merged across files (PRD.md D-61).
      - i. **Bench/benchgroup resolution and expansion:** resolve that file's benchmark list —
        the `bench` names, or the members of its `benchgroup`. Expand
        every run entry into one entry per benchmark, in list order (entry-major: all benchmarks
        of the first entry, then of the second, ...), cloning `jdkname`, `profiles`, `jvmargs`,
        `tags`, `jvmenv`, `do_cpu_pinning`, `collectors`, and `dacapo_iterations` onto
        each. From here on this file's contribution is a flat list of single-benchmark entries.
      - ii. **Heap-sweep expansion:** if that file's `heapsize_factors` is set, expand every one of
        its entries into N entries — one per factor — computing `heap_mb = round(F × minheap)` and
        injecting `-Xms<heap_mb>m -Xmx<heap_mb>m -XX:+AlwaysPreTouch` (or, if that file's
        `dynamic_heap` is `true`, just `-Xmx<heap_mb>m`) immediately after the bench flags (see
        §1.5). No-op if that file's `heapsize_factors` is absent.

      Once every file has been parsed and expanded this way, concatenate all files' flat entry
      lists into one combined list, in file-argument order (each file's own entry order preserved
      within its span). From here on in these steps, and in step 2 below, "the batch" refers to
      this combined list — steps c onward apply to it as a whole (shared host config, JDK
      detection deduplicated across all files' entries, etc.).
   c. From the host config (step a), sanity-check `cpu_list`'s syntax (full
      semantic validation is left to `taskset` at execute time). Then resolve the per-run toggles
      `do_cpu_pinning`, `use_ramdisk`, and the batch-wide collector settings (§1.4.1: requested
      collector set, interval, compression; loading the preset files used, §1.11) and perf events
      preset (§1.4.2: loading the perf events preset files used, §1.12), aborting if the input
      files' settings differ — see the condition tables below for exactly when each degrades gracefully
      (pinning, ramdisk) versus hard-aborts (`collectors.compression` without `zstd`). `ramdisk_path`, once validated, is copied verbatim into
      `BATCH_IN_PROGRESS.json` (§4); no capacity check happens here since a batch may be prepared
      long before it's executed (that check is per-run, at execute time — spec-execute.md §6).
   d. For each unique JDK in the batch: load its `config/jdks/<jdkname>.jdk.toml`, run
      `java -version 2>&1` (`<java_home>/bin/java`), and from that output determine the integer
      feature version and JDK type (`openj9` if any line contains `Semeru` or `OpenJ9`, else
      `openjdk`), using the parser shared with `jbr-execute` (`scripts/common/jdk_detect.py`,
      spec-execute.md §2 step e). Both are stored per run as `jdk_type`/`jdk_major` (§4). Load the matching `config/jdk-types/<type>.jdktype.toml`.
   d-collectors. **Collector applicability:** load every `config/collectors/<name>.collector.toml`
      (§1.10). From the batch's requested collector set (step c), remove every collector that
      cannot run: `cpu_temp`, `pidstat`, `proc` or `perf` when this machine fails its probe (§1.6;
      each requested collector is probed once), `perf` also when the machine will not be
      stabilized and `perf_event_paranoid` is 3 or higher (PRD.md D-85), and any collector whose `restrict_to` does not
      cover the detected JDK type/version of every run (step d). Each removal applies to the
      whole batch and prints a warning (see Warn-and-continue conditions). The rest is stored
      once as `collectors.enabled` (§4); `jbr-execute` starts exactly these for every run
      (spec-execute.md §5.1). `nmt` in it also drives NMT flag injection (§2 step 6).
   d-perf. **Perf events** (§1.4.2): if `perf` is still enabled, store the preset's events once
      as `collectors.perf_events` (§4), or `null` for perf's default set. The events are not
      checked here; `jbr-execute` checks the perf command at batch start (spec-execute.md §1.3
      step 4e).
   e. **Version filter:** drop any entry whose testee JDK version falls outside its `.bench.toml`'s
      `[min_java_version, max_java_version]` (each bound optional) — see Warn-and-continue
      conditions.
   f. Assemble `jvmargs` for every remaining entry (all 5 steps of §2: bench, profile, run-entry,
      CLI, then JDK-type + GC-log flags), plus the NMT flags (§2 step 6) where `nmt` is in
      `collectors.enabled`.
   g. Abort if a run's assembled flags contain conflicting `-Xmx`/`-Xms` values (§2).
   h. Parse each run's effective `-Xmx` (suffixes `k`/`m`/`g`/`t` converted to MB) as `heapsize`;
      abort if a run has none.
   i. **Maxheap filter:** if the host config sets `maxheap`, drop any run whose `heapsize` exceeds
      it — see Warn-and-continue conditions.

2. Apply the effective repeat count (`--repeat`, else the `.run.toml` `repeat` field, else `1`;
   §1) if `> 1` over the combined run list from step 1 (see the `--repeat` option above), then
   pre-compute a RUNID (GUID) for each resulting run entry and a GUID for the batch itself. `seq` is assigned as one contiguous 1-based sequence across the whole batch, regardless
   of how many `.run.toml` files contributed to it.

3. Create `<project>/batches/<BATCHID>/` and copy every input `.run.toml` file into it,
   byte for byte, under its base name. Then write `<project>/BATCH_IN_PROGRESS.json` (see §4)
   with all planned runs fully resolved.

On success, print the batch's effective collectors, then the completion message:

```
Collectors: cpu_temp,pidstat,proc  interval=10s  compression=off
Preparation complete. <N> run(s) written to BATCH_IN_PROGRESS.json.
Use 'jbr-execute' to execute, or 'jbr-execute --abort' to discard.
```

#### Abort conditions

Hard errors — nothing is written; the batch as prepared so far is discarded. With multiple input
files, an abort condition encountered while processing any one file discards the entire multi-file
batch — partial output from files processed before the failing one is never written.

| Step | Condition |
|---|---|
| — | Not inside a project (PRD.md §4). |
| Guard | The `active-batch` link points to a batch in another project, or is dangling. |
| Guard | `<project>/BATCH_IN_PROGRESS.json` exists with at least one run already started, or the existing file can't be read/parsed. |
| 1b | Two input `.run.toml` files have the same base name (their frozen copies would collide). |
| 1a | Host config missing or invalid, or sets `perf_events` (removed, §1.6); a used benchmark type's directory is not set in it, or its jar is missing. |
| 1b-i | A referenced `.bench.toml` / `.benchgroup.toml` / `.profile.toml` is missing; the top level gives both `bench` and `benchgroup`, or neither; a run entry gives `bench` or `benchgroup`; `bench` is not a non-empty string or a non-empty array of non-empty strings, or a name appears twice in it; any `jvmargs_<suffix>`, `tags_<suffix>` or `bench_<suffix>` key (§1); a `benchgroup` is empty or has a duplicate member. |
| 1b-ii | `heapsize_factors` is set but a referenced `.bench.toml` has no `minheap`. |
| 1b | `dynamic_heap` is `true` in a `.run.toml` file that has no `heapsize_factors`. |
| 2 | `--repeat` is not given and two input `.run.toml` files set different `repeat` values. |
| 1b | A `collectors` object (§1.4.1) misses a key, has an unknown key or a value of the wrong type; `collectors` and `collectors_preset` are both set; a run entry sets `collectors`, `collectors_preset` or `perf_events_preset` (batch level only, D-80). |
| 1c | `cpu_list` fails its syntax check (`^\d+(-\d+)?(,\d+(-\d+)?)*$`); `ramdisk_path` doesn't exist or isn't a directory; the input files' collector settings differ (§1.4.1); `collectors.compression` is effectively `true` but `zstd` isn't on `PATH`. |
| 1d | A referenced `.jdk.toml` is missing, or the `.jdktype.toml` for the detected type is missing. |
| 1c | A collector preset file (§1.11) used by the batch (including `cheap-10s` as the default) is missing or invalid. |
| 1c | A perf events preset file (§1.12) used by the batch is missing or invalid. |
| 1d-collectors | A `config/collectors/<name>.collector.toml` is missing. |
| 1g | A run's assembled flags contain conflicting `-Xmx`/`-Xms` values. |
| 1h | A run's assembled flags contain no `-Xmx`. |

#### Warn-and-continue conditions

Non-fatal — printed to stdout; preparation continues.

| Step | Condition | Effect |
|---|---|---|
| 1c | `do_cpu_pinning` effectively `true` for a run, but the host has no `cpu_list`, no `cpu_list_harness`, or fails a `taskset --cpu-list <cpu_list> sleep 0` probe (checked in that order). | Pinning disabled for the affected run(s). |
| 1c | `use_ramdisk` effectively `true` for a run, but the host has no `ramdisk_path`. | Ramdisk disabled for the affected run(s). |
| 1d-perf | The batch sets `perf_events_preset`, but its collector settings do not request `perf`. | Warning only; the preset is ignored. |
| 1d-collectors | A requested collector fails its machine probe (§1.6). | That collector removed from the whole batch. |
| 1d-collectors | `perf` is requested, the machine will not be stabilized (see step 16), and `/proc/sys/kernel/perf_event_paranoid` is 3 or higher: unprivileged perf can count nothing (PRD.md D-85). | `perf` removed from the whole batch. |
| 1d-collectors | `perf` stays enabled, the machine will not be stabilized, and `perf_event_paranoid` is 2. | Warning only — perf will count user space only, not kernel events. |
| 1d-collectors | A requested collector's `restrict_to` does not cover the detected JDK type/version of some run. | That collector removed from the whole batch, for all runs. |
| 1d-collectors | `system_map` is enabled without `collectors.compression`. | Warning only — the raw `system.map.log` will be large. |
| 1d-collectors | `thread_print` is enabled without `collectors.compression`. | Warning only — the raw `thread-print.log` will be large. |
| 1e | Testee JDK version outside `[min_java_version, max_java_version]`. | Run entry skipped. |
| 1i | Run's `heapsize` exceeds the host's `maxheap`. | Run entry skipped. |
| 16 | The machine will not be stabilized: `--stabilize-machine` is not given, or the host is not Linux. | Warning only. When stabilization is effective, `Machine stabilization: on` is printed instead, followed by the main settings and that they weaken security until reboot. |

Warnings that concern several runs are printed once ("one or more runs ...") and do not list the
affected runs.

---

## 4 `BATCH_IN_PROGRESS.json` Schema

Written once by `jbr-prepare` and **never modified** after that point. When the batch completes,
it is moved unchanged to `batches/<batchid>/batch.json`; when it is abandoned, to
`batches/<batchid>/batch-aborted.json` (spec-execute.md §1). Per-run runtime state
(status, PID) lives in `run-info.json` inside each run's result directory (see spec-execute.md §4.2).

All run-entry fields are **fully pre-resolved** at batch creation time: bench flags, heap-sweep
flags, profile flags, run-entry flags, CLI flags, JDK-type flags, the GC log flag, and the JDK
path. **No outside config files are needed to execute the batch.**

```json
{
  "batchid":     "<GUID>",
  "started_at":   "<ISO-8601 UTC>",
  "hostname":     "<hostname>",
  "ramdisk_path":          "/dev/shm/jbenchrunner",
  "cpu_list":              "0-3",
  "sudo_mode":             false,
  "stabilize_machine":     true,
  "collectors":   { "interval_secs": 5, "compression": false,
                    "enabled": ["cpu_temp", "pidstat", "proc", "perf"],
                    "perf_events": ["cycles", "instructions", "cache-misses"] },

  "runs": [
    {
      "runid":     "<RUNID>",
      "seq":        1,
      "repeat_no":  0,
      "source_file": "gc-comparison.run.toml",

      "bench_json":   "h2-large",
      "bench_group":  "dacapo-all-large",
      "jdkname":      "jdk-21",
      "java_home":    "/opt/jdk-21",
      "jdk_type":     "openjdk",
      "jdk_major":    21,
      "profiles":     ["g1-baseline"],
      "tags":         ["gc-comparison-2025"],
      "do_cpu_pinning": false,

      "benchmark":     "dacapo",
      "sub_benchmark": "h2",
      "datasize":      "large",
      "iterations":    10,
      "dacapo_args":   ["-t", "16"],
      "dacapo_dir":    "/path/to/dacapo",

      "heapsize":    5120,
      "heapsize_factor": 2.0,

      "result_dir":   "results/<RUNID>",

      "jvmargs":    [
        "-Djdk.xml.maxElementDepth=1000",
        "-Xms5120m", "-Xmx5120m", "-XX:+AlwaysPreTouch",
        "-XX:+UseG1GC",
        "-XX:+PrintFlagsFinal",
        "-Xlog:gc*:file=gc.log::filecount=0"
      ],
      "jvmenv":      { "MALLOC_ARENA_MAX": "4", 
                        "GLIBC_TUNABLES": "glibc.malloc.trim_threshold=131072"
      }
    }
  ]
}
```

Each field lives at exactly one level — **Global** (once, at the top of the file) or **Run**
(once per entry in `runs[]`, even when every run in the batch happens to carry the same value —
see the Decisions Log in PRD.md for why). A field is never duplicated at both levels.

| Field | Level | Type | Notes |
|---|---|---|---|
| `batchid` | Global | string | GUID for the batch as a whole. |
| `started_at` | Global | string | ISO-8601 UTC timestamp of when `jbr-prepare` was invoked. |
| `hostname` | Global | string | Output of `hostname` at batch creation time. Used to enforce same-machine check in `jbr-execute`. |
| `ramdisk_path` | Global | string, optional | Copied verbatim from `config/hosts/<hostname>.host.toml` at batch creation time (§1.6). Absent if not configured. See spec-execute.md §6 for how it's used during execution. |
| `cpu_list` | Global | string, optional | Copied verbatim from `config/hosts/<hostname>.host.toml` at batch creation time (§1.6). Absent if not configured. Used by `jbr-execute` to wrap the benchmark process launch (spec-execute.md §1, §2 step g) for any run entry with effective `do_cpu_pinning: true`. |
| `sudo_mode` | Global | boolean | From `jbr-prepare --sudo` (default `false`). `jbr-execute` reads this — not a CLI flag of its own — to decide whether it may run root commands via the sudoers rules (spec-execute.md §1). Uniform across the whole batch and every resume. |
| `stabilize_machine` | Global | boolean | `true` if `jbr-prepare --stabilize-machine` was given, else `false` (PRD.md D-88). Controls whether `jbr-execute` runs `prepare-for-benchmarks.sh` at batch start and `reset-machine.sh` before every run. See spec-execute.md §1.3. |
| `collectors` | Global | object | The batch's fully-resolved collector settings (§1.4.1), the same for every run (PRD.md D-84). Always present; a batch without it was prepared by an older `jbr-prepare` and is refused by `jbr-execute` (spec-execute.md §1.3). |
| `collectors.interval_secs` | Global | integer | Effective sampling interval in seconds: `--collector-interval` if given, else the `interval` of the resolved collector settings (§1.4.1). |
| `collectors.compression` | Global | boolean | The `compression` of the resolved collector settings (§1.4.1). When `true`, `jbr-execute` pipes the stdout of a fixed set of collectors through `zstd` and stores it as `<file>.zst` (spec-execute.md §5.2). |
| `collectors.enabled` | Global | array of strings | Short names (§1.4.1) of the collectors `jbr-execute` starts for every run, in the order `cpu_temp`, `pidstat`, `proc`, `perf`, `nmt`, `system_map`, `thread_print`: the requested set minus every collector this machine cannot run or whose `restrict_to` does not cover the JDK of every run (§3 step 1d-collectors). If it contains `nmt`, every run's `jvmargs` already include the NMT flags (§2 step 6). May be empty. |
| `collectors.perf_events` | Global | array of strings or null | The events the `perf` collector counts: the `perf_events_preset` events (§1.4.2), passed comma-joined to `perf stat -e` via `perf-logger.sh --counters`; never empty. `null`: no preset, perf's default set (`perf-logger.sh` without `--counters`); also `null` when `perf` is not in `enabled`. Always present. |
| `runid` | Run | string | Pre-assigned RUNID (GUID) for this run. Stable — never regenerated. |
| `seq` | Run | integer | 1-based position of this run in the overall execution order. |
| `repeat_no` | Run | integer | 0-based repetition index. `0` for the first (or only) occurrence of the run entry. When `--repeat N` is given, each logical run entry appears N times with `repeat_no` values `0` through `N-1`. Always present; value is `0` when no repetition was requested. Copied into `info.json` (spec-execute.md §4.1), so it can be listed and filtered by `jbr-ls`/`jbr-filt` (spec-analyze.md). |
| `source_file` | Run | string | Base name of the `.run.toml` file this entry originated from; its frozen copy is `batches/<BATCHID>/<source_file>` in the project (§3 step 3). Always present, including for a single-file batch — the schema never diverges between the one-file and multi-file case. See PRD.md D-62. |
| `bench_json` | Run | string | Name of the `.bench.toml` file (without extension) that defines this run. |
| `bench_group` | Run | string | Name of the `.benchgroup.toml` (without extension) that this entry was expanded from. Present only for entries expanded from a top-level `benchgroup`; omitted for entries from `bench`. Informational only. |
| `jdkname` | Run | string | Effective JDK name: run-entry `jdkname` if present, else the batch-level `jdkname`. Informational; the operative path is `java_home`. |
| `java_home` | Run | string | Absolute path to the JDK installation directory, copied from `config/jdks/<jdkname>.jdk.toml` at batch creation time. The java binary is `<java_home>/bin/java`. |
| `jdk_type` | Run | string | JDK type detected in §3 step 1d (`openjdk` or `openj9`). `jbr-execute` compares it with its own detection and warns on a mismatch; `info.json` records the detected value (spec-execute.md §2 step e). |
| `jdk_major` | Run | integer | JDK feature version detected in §3 step 1d (Java 8 = `8`). Cross-checked by `jbr-execute` like `jdk_type`. |
| `profiles` | Run | array of strings | Effective profile names: run-entry `profiles` if present (wholesale, not merged), else the batch-level `profiles`, else empty array. |
| `tags` | Run | array of strings | Effective tags: union of CLI `--tags` + batch-level `tags` + run-entry `tags`. Always an array; empty array if none. Duplicates are removed. |
| `do_cpu_pinning` | Run | boolean | Effective value: run-entry `do_cpu_pinning` if present, else the batch-level `do_cpu_pinning`, else `false` — **and** forced to `false` when this host doesn't support pinning (missing `cpu_list`/`cpu_list_harness`, or a failing `taskset --cpu-list <cpu_list> sleep 0` probe; §3 step 1c), regardless of what was requested. When `true`, `jbr-execute` launches this run's benchmark process wrapped in `taskset --cpu-list <cpu_list>` (spec-execute.md §1, §2 step g), using the batch's top-level `cpu_list`. |
| `use_ramdisk` | Run | boolean | Effective value: run-entry `use_ramdisk` if present, else the batch-level `use_ramdisk`, else `false` — **and** forced to `false` when the host config has no `ramdisk_path` (§3 step 1c), regardless of what was requested. When `true`, `jbr-execute` creates this run's result directory on the batch's top-level `ramdisk_path` instead of directly under the project's `results/` (spec-execute.md §6). When `false` (the default), the ramdisk is not used for this run even if `ramdisk_path` is configured; with `-v`/`--verbose`, `jbr-execute` prints `No Ramdisk will be used - IO jitter can occur`. |
| `benchmark` | Run | string | `"dacapo"` or `"specjbb"`, resolved from `.bench.toml`. |
| `sub_benchmark` | Run | string | dacapo benchmark name; omitted for specjbb runs. |
| `datasize` | Run | string | dacapo size parameter; omitted for specjbb runs. |
| `iterations` | Run | integer | dacapo total iteration count (1..N-1 are warmups, N is measured). Effective value: `--dacapo-iterations` if given, else run-entry `dacapo_iterations` if present, else the batch-level `dacapo_iterations`, else this run's `.bench.toml` `iterations`. Omitted for specjbb runs. |
| `dacapo_args` | Run | array of strings | Effective extra DaCapo Harness args: per-run-entry `dacapo_args` if present, else the batch-level `dacapo_args`, else empty. Present only for dacapo runs; always an array. |
| `dacapo_dir` | Run | string | Absolute path to the dacapo installation directory, copied from `dacapo_dir` in the host config (§1.6). Present only for dacapo runs. |
| `specjbb_dir` | Run | string | Absolute path to the SPECjbb2015 installation directory, copied from `specjbb_dir` in the host config (§1.6). Present only for specjbb runs. |
| `heapsize` | Run | integer | Effective heap size in integer MB, parsed from the effective `-Xmx` in the fully-assembled flag list (last occurrence wins; suffixes converted to MB). Always present. |
| `heapsize_factor` | Run | number | The heap-sweep factor F (see §1.5) verbatim from `heapsize_factors`, e.g. `2.0`. Present only for runs expanded from a heap-size sweep; omitted otherwise. |
| `result_dir` | Run | string | Path to the run result directory, relative to the project root (`results/<RUNID>`). Pre-computed from `runid`. |
| `jvmargs` | Run | array of strings | Complete ordered JVM flag list, fully resolved (all 5 assembly steps from §2). |
| `jvmenv` | Run | object (string values) | Effective environment variables for the JVM subprocess: batch-level `jvmenv` merged with run-entry `jvmenv`, per-run wins on key collision. Always an object; empty object if none. Set on the JVM subprocess's environment in addition to the inherited ambient environment (spec-execute.md §2). |
