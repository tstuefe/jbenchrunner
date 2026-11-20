Project Requirement Document (PRD)

# jbenchrunner — Java Benchmark Harness · PRD

**Status:** Draft v2.0
**Audience:** JVM engineers / performance analysts  
**Scope:** dacapo 23.11, specjbb2015 (single-JVM mode)

---

## Phase specifications

Detailed specifications for each phase are in separate documents:

| Document | Covers |
|---|---|
| [spec-prepare.md](spec-prepare.md) | Configuration file formats, JVM flag assembly, `jbr-prepare`, `BATCH_IN_PROGRESS.json` schema |
| [spec-execute.md](spec-execute.md) | `jbr-execute`, `jbr-status`, per-run execution steps, run result artefacts (`info.json`, `run-info.json`), data collectors |
| [spec-post-process.md](spec-post-process.md) | `jbr-pp`, `result.json` schema and examples |
| [addendum-external-name-translation.md](addendum-external-name-translation.md) | External name translation tables (Addendum A) used by data collectors |
| [spec-analyze.md](spec-analyze.md) | `jbr-filt`, `jbr-ls` |
| [spec-other.md](spec-other.md) | Non-phase tools: `jbr-init` (creates a project), and tools operating directly on existing result directories: `jbr-retag`, `jbr-move`, `jbr-copy`, `jbr-failures`; `jbr-move-batch` (moves batches between projects) |

---

## 1 Problem Statement

Running Java benchmarks correctly is difficult. A single specjbb run can take 2–3 hours; a dacapo suite
run longer still. A misconfigured JVM flag, a wrong classpath, or a forgotten GC log option silently
invalidates a run and wastes that time. Comparing results across JDKs or option sets then requires
manual bookkeeping that is error-prone and hard to reproduce.

jbenchrunner provides a *thin, opinionated shell* around existing benchmarks: it constructs
the correct invocation, captures all relevant artefacts, records run metadata in a structured way, and offers
lightweight analysis tooling — all from the command line, for JVM experts.

---

## 2 Goals & Non-Goals

### 2.1 Goals

- Make it impossible to start a benchmark run without recording all parameters that define it.
- Capture stdout, stderr, and the GC log from every benchmark process.
- Produce a machine-readable `result.json` per run that aggregates metrics without re-parsing raw logs.
- Enable flexible, filter-based comparison of any set of runs within a project (§4) in a plain-text table.
- Be zero-dependency for the benchmarks themselves — no patches, no wrappers injected into the JVM under test.
- Support operation across multiple machines and CPU architectures.

### 2.2 Non-Goals

- Multi-process benchmark modes (specjbb DISTRIBUTED / MULTICONTROLLER).
- GUI or web interface of any kind.
- Automated system tuning (CPU frequency, turbo boost) — operator responsibility.
- Statistical aggregation across multiple forks (users run the harness N times manually).
- Remote / SSH execution.
- Modifying benchmark source code or configuration files (e.g. `specjbb2015.props`).

---

## 3 Target Users

**JVM performance engineers** who are already comfortable with the command line, reading GC logs, and
interpreting benchmark output. The harness assumes fluency with JVM flags and benchmark semantics; it does
not explain them.

---

## 4 Conventions

### `<reporoot>`

`<reporoot>` denotes the root directory of the jbenchrunner repository.
Scripts **must** determine this directory at runtime from their own location within the repo tree —
it is **never** an externally supplied argument or environment variable.

The resolution algorithm: walk up the directory tree from the script's own path until a directory
containing both a `config/` and a `libexec/` directory is found. That directory is `<reporoot>`.

### Project

A *project* is a directory holding one result set: all runs that may be compared with each other
and appear in one common report. Runs in different projects are never compared. A project holds
any number of batches; a batch may be prepared from several `.run.toml` files, and a `.run.toml`
file may be prepared again in later batches (e.g. for v1, v2, v3 of a patch). A batch belongs to
exactly one project. The project layout is shown in §5.

A project is marked by a file `jbr-project.json` in its root directory, created by `jbr-init`
(spec-other.md). Every tool that reads or writes run results determines the current project at
runtime: walk up the directory tree from the current working directory until a directory
containing `jbr-project.json` is found. That directory is `<project>`. There is no option,
environment variable or configuration setting that selects a project otherwise. Outside a
project, these tools abort with an error. Projects cannot be nested.

A project may live anywhere, inside or outside `<reporoot>`, and can be moved or renamed as a
whole: no file inside a project stores an absolute path into the project.

### Run property names

Every run property exposed by the analysis tools has exactly one name, all lowercase, which is
used identically as:

- the `jbr-filt` filter option (`--<name>`),
- the `jbr-ls` column name (in `-o`/`-O`, `-s`, `-g`, `--compare`),
- the `jbr-ls` table header.

When a property gets a filter or a column, it uses this name; adding one never introduces a
second name for the same property. Where practical, the name is also the property's key in
`info.json` / `result.json`; this is preferred but not required (see D-64). Exception: `--since`
/ `--until` are comparisons on `time`, not property names.

---

## 5 Repository Layout

```
jbenchrunner/
├── bin/                            # user-facing commands: thin bash wrappers (jbr-init, jbr-prepare, jbr-execute, jbr-status, jbr-pp, jbr-filt, jbr-ls, jbr-retag, jbr-move, jbr-move-batch, jbr-copy, jbr-failures), see D-77
├── libexec/                        # the Python CLI scripts behind bin/ (<command>.py)
├── config/
│   ├── profiles/                   # JVM flag profiles
│   │   └── <name>.profile.toml
│   ├── bench/                      # benchmark definitions
│   │   └── <name>.bench.toml
│   ├── benchgroup/                 # groups of benchmarks (see spec-run.md §1.3)
│   │   └── <name>.benchgroup.toml
│   ├── run/                        # templates for run definitions; a project's own run definitions live in the project (§4)
│   │   └── <name>.run.toml
│   ├── hosts/                      # machine-specific configuration, incl. benchmark paths (D-83)
│   │   └── <hostname>.host.toml
│   ├── collectors/                 # per-collector applicability (<name>.collector.toml, `restrict_to`)
│   ├── collector-presets/          # named collector sets (<name>.collector-preset.toml)
│   ├── jdk-types/                  # JDK type configuration (openjdk.jdktype.toml, openj9.jdktype.toml)
│   │    └── <jdk-type>.jdktype.toml
│   └── jdks/                       # JDK configurations
│        └── <jdk-name>.jdk.toml
├── dacapo-timestamp-plugin/        # dacapo timestamp extension (part of this project)
│   └── dacapoExtension.jar
├── docs/                           # project documentation
│   ├── PRD.md                      # this file — master overview
│   ├── spec-prepare.md             # prepare phase specification
│   ├── spec-execute.md             # execute phase specification
│   ├── spec-run.md                 # superseded — redirects to spec-prepare.md and spec-execute.md
│   ├── spec-post-process.md        # post-process phase specification
│   ├── spec-analyze.md             # analysis phase specification
│   ├── spec-other.md               # non-phase tools operating directly on existing result directories (jbr-retag, ...)
│   └── addendum-external-name-translation.md  # external name translation tables (Addendum A)
├── jdks/                           # user-provided
│   └── <platform>/                 # e.g. linux-x86_64, linux-aarch64
│       └── <jdkname>/              # e.g. "openjdk-25" or "my-patched-jdk-v1.0"
│           └── bin/java
├── scripts/                        # helper scripts, not directly user-facing
│   ├── phase-prepare/              # scripts used during the prepare phase
│   │   └── tests/
│   ├── phase-execute/              # scripts used during the execute phase
│   │   ├── find-java-pid.sh
│   │   ├── collectors/             # data collector scripts and tests
│   │   └── tests/
│   ├── phase-post-process/         # scripts for post-processing raw results
│   │   ├── extract-heapsizes.py
│   │   ├── gnuplot_utils.py        # shared gnuplot helpers used by the plot-*.py scripts
│   │   ├── parse_dacapo_results.py
│   │   ├── plot-heapsizes.py
│   │   ├── plot-pidstat.py
│   │   ├── post-process-perf-log.py
│   │   └── tests/
│   └── phase-analyze/              # scripts for analysis and comparison
```

Project layout (§4); a project may live anywhere:

```
<project>/
├── jbr-project.json                # project marker, created by jbr-init; optional "description"
├── BATCH_IN_PROGRESS.json          # present only while a batch is prepared, active or interrupted
├── batches/                        # one directory per batch, created by jbr-prepare
│   └── <BATCHID>/
│       ├── <name>.run.toml         # frozen copies of the .run.toml files the batch was prepared from
│       ├── batch.json              # manifest of the completed batch
│       └── batch-aborted.json      # instead of batch.json, if the batch was abandoned (jbr-execute --abort)
├── results/                        # individual run result directories (RUNID is a GUID) ...
│   ├── <RUNID-1>/                  #  ... one directory per run; many may exist
│   └── <RUNID-2>/
└── reports/
    └── <name>/                     # jbr-ls --report <name>
```

---

## 6 Overall Flow

```
  .run.toml --+
  .bench.toml |    +---------------------------------------------------+
  .profile.toml+-->|  jbr-prepare  (phase-prepare)                  |
  .host.toml       |  - validates & resolves all config                |
                   |  - assembles JVM flag lists                       |
                   |  => writes BATCH_IN_PROGRESS.json                 |
                   +---------------------------------------------------+
                                    |
                                    |  <project>/BATCH_IN_PROGRESS.json
                                    v
                   +---------------------------------------------------+
                   |  jbr-execute  (phase-execute)                  |
                   |  - handles batches of multiple benchmark runs     |
                   |  - launches JVM processes                         |
                   |  - runs data collectors as separate subprocesses  |
                   |  => writes info.json, run-info.json per run       |
                   |  => writes stdout/stderr/gc logs per run          |
                   |  => writes collector JSON-lines logs per run      |
                   +---------------------------------------------------+
                                    |
                                    |  <project>/results/<RUNID>/ - one result dir per benchmark run
                                    v
                   +---------------------------------------------------+
                   |  jbr-pp  (phase-post-process)                      |
                   |  - parses benchmark stdout                        |
                   |  => writes result.json per run                    |
                   +---------------------------------------------------+
                                    |
                                    |  <project>/results/<RUNID>/result.json
                                    v
                   +---------------------------------------------------+
                   |  jbr-filt / jbr-ls  (phase-analyze)                 |
                   |  - filter and tabulate run results                |
                   +---------------------------------------------------+
```

- post-processing phase (`jbr-pp`) is idempotent, can be repeated at will. 
- analysis phase does not store data (`jbr-filt` and `jbr-ls`); it is read-only.

---

## 7 Non-Functional Requirements

- **Metadata completeness:** `info.json` is written after all parameters have been deduced and before the JVM is launched. A partial or failed run always has a complete `info.json`.
- **Idempotent post-processing:** Running `jbr-pp` on the same directory multiple times produces
  the same `result.json`; it overwrites, never appends.
- **No side-effects on input dirs:** Tools must never write into the directories referenced by
  `dacapo_dir` or `specjbb_dir` in the host config, or into `jdks/`.
- **Shell robustness:** All shell scripts use `set -eu -o pipefail` and a trap that prints the failing
  line number and exit code on error.
- **Python stdlib only:** All Python scripts use only Python 3 standard library modules. No third-party
  packages.
- **Config format:** All hand-edited configuration files (`config/`, `.run.toml`) are TOML (D-76);
  everything the tools write (batch files, `info.json`, `run-info.json`, `result.json`, collector
  logs) stays JSON / JSON lines.
- **Per-run JSON key disjointness:** The top-level key sets of `info.json`, `run-info.json`, and
  `result.json` must be pairwise disjoint. A key appearing in more than one of these files is a
  schema error. This invariant enables `jbr-filt` to merge all three objects with a simple
  top-level union and evaluate `jq` expressions against the result without ambiguity.
- **One active batch per user and host:** a batch is *active* from the moment `jbr-execute`
  starts it until it completes or is abandoned with `--abort`; an interrupted batch stays active,
  a prepared but never started one is not. While a batch is active,
  `$XDG_STATE_HOME/jbenchrunner/active-batch` (default `~/.local/state/jbenchrunner/active-batch`)
  is a symlink to its project's `BATCH_IN_PROGRESS.json`, and `jbr-prepare` and
  `jbr-execute` refuse to work on a batch in any other project. Other users on the same host
  are not stopped by it; that remains the operator's responsibility (§2.2).
- **Minimal sudo:** `jbr-execute` never runs as root, never prompts for a password and keeps
  no sudo ticket. With `--sudo`, elevated privileges are only ever invoked, via `sudo -n` and the
  rules of `config/jbenchrunner.sudoers` (D-87), for specific steps that genuinely require them: machine reset before each run,
  machine stabilization at batch start, and `--shutdown`. No collector runs as root, `perf`
  included (D-85). docs/where-we-need-sudo-and-why.md lists every root command.

---

## 8 Decisions Log

| # | Question | Decision |
|---|---|---|
| D-15 | Heap sweep definition location | `heapsize_factors` lives in `.run.toml`, not `.bench.toml`, because it's a run-time choice about how many heap sizes to test, not an intrinsic property of the benchmark. |
| D-37 | `maxheap` machine cap | Operator-set deliberate cap (MB) — not read from `/proc/meminfo` — since automatic detection was rejected in favor of an explicit choice. |
| D-39 | `-Xmx` unit in `jvmargs` vs `heapsize` | `jvmargs` (verbatim, unit suffix included) and `heapsize` (normalised integer MB) deliberately serve different purposes: a faithful record of what ran vs. a comparable/filterable value. |
| D-41 | Phase separation: executor vs post-processing outputs | `exit_code`/`status` are omitted from `result.json` because they are always 0/SUCCESS for any run that has one. |
| D-42 | `on_failure` removed from config | `on_failure` has been removed from `.run.toml` and `BATCH_IN_PROGRESS.json`. There is no stop-on-error behaviour: a failed run never halts the batch (see D-56, which removed the `--stop-on-error` flag that originally implemented this). |
| D-44 | `jbr-filt` / `jbr-ls` scope | Discovery of failed or un-post-processed runs is deferred to a future diagnostic tool. |
| D-45 | Preserving per-iteration timestamps and warmup flags | Preserving iteration start timestamps and warmup distinction enables correlation with time-series collector data (CPU, temperature, memory) while retaining full iteration history. |
| D-46 | `jbr-pp` multiple file input? | not now, future versions maybe. |
| D-47 | Sudo-mode consistency + collector-availability hard-fail | Sudo mode moved from `jbr-execute` (per-invocation) to `jbr-prepare` (persisted) because a per-invocation flag couldn't guarantee uniform application across batch resumes; a future `taskset`-based CPU-pinning feature will independently need sudo. A collector hard-failure reuses the existing `STOPPED` status rather than a new value, purely so it's retried like any other `STOPPED` run on resume. |
| D-49 | CPU pinning | A dedicated pinning-violation check (cross-checking `pidstat.jsonl`'s `cpu` field against `cpu_list`) was considered and deferred. |
| D-50 | Machine reset moved to host config | Default flipped to `true` (opposite of the old `--reset-machine` flag's default) because resetting before every run is what most users want, and forgetting the old flag was an easy footgun. Superseded by D-78: the separate `reset_machine_between_runs` field is gone, `stabilize_machine` (default `false`) governs the reset too. |
| D-51 | `jbr-retag` — first tool that mutates a written run artefact | `jbr-retag` is a deliberate, narrow exception to the otherwise-universal §7 rule that `info.json` is complete and final once written — only `tags` is ever touched. Because it mutates rather than reads, it does not belong under the read-only "analyze" phase (§6); it lives in its own `spec-other.md`, the intended home for tools that act directly on existing result directories outside the prepare → execute → post-process → analyze pipeline. An omitted RUNID list aborts with an error rather than falling back to "all runs of the project", since that must never silently retarget every run in the project. |
| D-53 | NMT collector — jcmd-based collectors as a new category | The jcmd collector mechanism was generalized (`_JCMD_COLLECTOR_DEFS`) rather than built NMT-specific because further jcmd-based diagnostics were anticipated (`VM.info`, `Thread.print`, `VM.metaspace`, `System.map`, `GC.run`). `GC.run` forces a full GC and perturbs the measured JVM — intended only for dedicated liveset-probing runs, never combined with a real measurement run; this is a documentation-level caution, not tool-enforced. |
| D-55 | DaCapo result stability metrics — `stddev_ms` / `stderr_ms` | Only `stderr_ms` (plus the derived `stderr_pct`) is exposed as a `jbr-ls` column; `stddev_ms` stays JSON-only, since standard error is the more directly actionable number for judging run-to-run noise at a glance. |
| D-56 | `--stop-on-error` removed; `--shutdown` added | `--stop-on-error` was dropped as unused — a failed run always continues, retry/investigation happens after the fact via `jbr-status`. `--shutdown` reuses the existing sudo ticket (since D-87: its sudoers rule, checked at batch start) rather than prompting separately, since an unattended run must not block on a password prompt nobody is there to answer. |
| D-57 | Collectors run as own subprocesses, not threads | Collectors must be separate processes so they can be pinned to specific CPUs: each one is simply launched via `taskset`, which is not practical for a thread inside the runner process. |
| D-58 | Compressed collector logs (`collector_compression`) | Selected collectors' stdout is piped through the `zstd` CLI as a separate process (`zstd -1 -q -T1 -c`), rather than compressed by a Python helper. Rationale: works on any host Python (`compression.zstd` exists only from Python 3.14), constant memory, collector scripts unchanged. Accepted risk: the CLI flushes only after 128 KB of input or at EOF, so a hard kill of `zstd` (SIGKILL, crash) loses whatever it still buffers; a clean shutdown (collector exits, `zstd` sees EOF) loses nothing. Rejected: a Python helper with a sync flush per sample (needs Python 3.14 or a gzip fallback, and is a second code path; the flush would have cost only about 5-15 KB per run on real logs). Level 1: level 3 compressed our logs no better. An interrupted (Ctrl-C) run is retried with its result directory wiped (spec-execute.md §1.2), so a truncated `.zst` from an interrupted run is never kept. `zstd` is a hard requirement whenever the option is on. The switch is a `.run.toml` parameter (batch-level, overridden per run), not a host property: greppable versus compact logs is a per-experiment choice, and only the availability of `zstd` is a host matter (prepare checks it). |
| D-59 | Every collector takes the JVM PID | Collectors are launched in their own process group (`process_group=0`), so Ctrl-C reaches only the runner and `stop_collectors` is the single shutdown path for every collector and compressor. If the runner dies before it gets there, nothing else ends the collectors. Therefore every collector receives the JVM PID as its liveness anchor and exits when the JVM is gone, even if it samples machine state and needs no PID for the measurement itself (`cpu-temp-logger.py`). Collectors notice the PID's disappearance by sleeping in 1-second slices with a `kill -0` between them, not by sleeping a whole interval: intervals can be large on long runs, and a collector still asleep when `stop_collectors` sends SIGTERM may be killed mid-write, tearing its last line or a compressed stream. `waitpid` was rejected because the JVM is not a child of the collector; `pidfd_open` because it is Linux-only and would still need the polling fallback. Rejected: a PID-less temperature logger, which would run forever as an orphan. Cleaning up after a runner crash (the JVM keeps running too) stays manual and out of scope. Collectors get a new process group but, unlike the JVM, not a new session (`start_new_session=True`): sudo tickets are per terminal by default (`timestamp_type=tty`), and a collector without a controlling terminal made `sudo -n perf` fail with "a password is required" despite a live ticket. Since D-85 no collector uses sudo; the collectors keep the terminal anyway. |
| D-60 | `BATCH_IN_PROGRESS.json` fields live at one level only | Every field is either batch-global (written once) or per-run (written into every `runs[]` entry) — never both, so there is exactly one place to look for a given field's value. `source_file`, `collector_interval`, and `perf_events` used to break this: `source_file` was dropped outright (unused beyond a cosmetic status-line "File:" label); `collector_interval` was collapsed from batch+per-run duplication down to per-run only (it already had per-run-entry override support); `perf_events` moved from batch-only to per-run-only, since it is conceptually a collector setting and the file is machine-written/machine-read, so duplicating the same value across every run entry costs nothing (since D-81 it lives in `collectors.perf_events`; since D-84 the whole `collectors` structure is batch-global). Machine-level facts (`ramdisk_path`, `cpu_list`, `sudo_mode`, `stabilize_machine`) stay batch-global, since they don't vary per run. (The host's `has_*` collector flags used to be copied here too; since D-66 they are folded into each run's `collectors.enabled` at prepare time instead.) |
| D-61 | Multiple `.run.toml` files per batch — no cross-file merge needed for batch-level fields | Generalizes D-60's `perf_events` rationale into a blanket invariant: every field that can be set in a `.run.toml` — whether written at that file's batch level or on an individual run entry — is fully resolved into each affected `runs[]` entry before `BATCH_IN_PROGRESS.json` is written; none of it survives as separate global state. Consequently every Global-level field in `BATCH_IN_PROGRESS.json` is, without exception, either computed by `jbr-prepare` itself (`batchid`, `started_at`, `hostname`) or sourced from outside `.run.toml` entirely (host config, CLI flags) — never from a `.run.toml`'s batch-level settings. This is what makes accepting multiple `.run.toml` files in one `jbr-prepare` invocation tractable: each file's batch-level defaults are resolved against only that file's own run entries (independently, exactly as for a single file today), and the resulting fully-resolved run lists are then concatenated in file-argument order. There is no batch-level state left to merge or reconcile across files, because none exists past resolution — only genuinely global concerns (`--sudo`, host config, other CLI flags) apply uniformly across the whole invocation, and those already did before this feature existed. Later exceptions, both batch-wide by nature, require the files to agree instead of merging them: `repeat` (D-79) and the collector settings (D-84). |
| D-62 | `source_file` — per-run provenance field | `BATCH_IN_PROGRESS.json` carries, on every run entry, the file name of the frozen copy (in `batches/<BATCHID>/`, D-73) of the `.run.toml` the entry originated from (Run-level, always present, including single-file batches). It names the frozen copy rather than the original path, because the original may since have been edited, moved or deleted, and because no file in a project may store an absolute path into the project (D-73). Needed once a batch can be built from multiple `.run.toml` files (spec-prepare.md §3): a bad or unexpected entry must be traceable to its source file without re-running preparation. Fits the D-61 invariant — derived from the CLI-supplied file list, not from inside any `.run.toml`, varying per run like every other Run-level field. |
| D-63 | jcmd-based collectors always use the JDK-under-test's own `jcmd` | `jcmd-logger.sh` resolves the binary strictly as `<java_home>/bin/jcmd` (`java_home` being the run entry's JDK-under-test), with no fallback to a `jcmd` on `PATH` or any other installed JDK. This is required, not just tidy: `jcmd`'s attach protocol is not compatible across JVM implementations (e.g. a J9 `jcmd` cannot talk to a HotSpot-derived VM and vice versa), so any `jcmd` other than the one shipped inside the exact JDK being benchmarked could silently fail to attach or return wrong data. |
| D-64 | Unified property names across JSON, `jbr-filt` and `jbr-ls` | A property has one lowercase name used for its `jbr-filt` flag (`--<name>`), its `jbr-ls` column (`-o`/`-O`, `-s`, `-g`, `--compare`) and its table header. Where practical the JSON key uses the same name (`runid`, `batchid`, `time`, `arch`, `jvmargs`, `maxjops`, `rssmax`, `tempmax`, ...); where it does not, the JSON key is left as-is and only the tools shorten it (`jdk.name` -> `jdkname`, `heap.heapsize` -> `heapsz`, `heapsize_factor` -> `heapszf`, `duration_secs` -> `duration`, `dacapo.sub_benchmark` -> `sub`, `cpu.pct_<m>_<s>` -> `cpu_<m>_<s>`). JSON sub-structures (`jdk`, `heap`, `dacapo_result`, `specjbb_result`, `memory`, ...) are kept. Rejected: separate CamelCase display names for headers (e.g. `RssMax`, `%CpuMax`), because a header that differs from the name you type for `-s`/`-g`/`--compare` forces a lookup. Exception: `--since`/`--until` keep their names, since they name a comparison on `time`, not a property. Per-sample `timestamp` fields in the `*.jsonl` time series are not run properties and keep their name. |
| D-65 | Averages of sampled time series are time-weighted only | `rssavg`, `mallocavg` and `cpu.pct_<m>_avg` are the time-weighted mean (area under the curve divided by the covered time span), not the arithmetic mean of the samples. Both used to be recorded side by side (`<x>avg` and `<x>twavg`); in practice they are almost identical, but the time-weighted value is the better indication of "cost" (memory or CPU held over time), and offering two near-equal averages only forces a choice. Consequence: the average is omitted when no sample carries a valid timestamp. `osthreads_avg` is unaffected (it never had a time-weighted counterpart). `result.json` files written before this change still hold the arithmetic mean under `_avg`; re-run `jbr-pp -f` to update them. |
| D-66 | Collector control: a complete `collectors` structure, or a preset | All collector settings of a run (which collectors run, `interval`, `compression`) form one structure with all three keys required, replacing the separate `nmt_tracking`/`system_map_tracking`/`thread_print_tracking`/`collector_interval`/`collector_compression` fields. Every collector is now selectable per run, including the four host-level ones that used to always run when the host supported them. The same structure is the format of preset files (`config/collector-presets/`); `.run.toml` holds either a full `collectors` structure or `collectors_preset`, never both (originally at batch and run level, the run level replacing the batch level whole; run level removed by D-80). Presets (shipped: `none`, `cheap-1s`/`-10s`/`-60s`, `all-1s`/`-10s`/`-60s`) solve having to remember which collectors exist; since presets and structures are always complete, there are no hidden per-key defaults. The only implicit default is the preset `cheap-10s` (`cpu_temp`, `pidstat`, `proc`: no measurable effect on the benchmark), so `perf` is off by default. Presets are files, not built in, so new combinations need no code change; `all-*` is an explicit list rather than a wildcard, accepting that it must be updated when a collector is added. Rejected: per-key merging across levels with presets that reset (first design; one boolean per collector, later `enabled`/`disabled` lists) — more flexible per run, but the rules (what a run-level preset resets, how explicit keys combine with it) were harder to predict than restating a three-key structure; all collectors off by default (a forgotten block silently loses all memory/CPU/temperature data, noticed only after a long batch); presets as booleans such as `"all": true` (contradictory combinations). Host `has_*` flags stay in the host config as capabilities (replaced by probing, D-82); prepare removes a requested but unsupported collector with a warning instead of failing, because `cheap-10s` is the default on every host. `BATCH_IN_PROGRESS.json` and `info.json` record the resulting list (`collectors.enabled`), i.e. what actually ran, not what was requested. |
| D-67 | One collector timestamp format, millisecond precision, no legacy parsing | All collectors print `2026-09-22T20:29:03.123+02:00` (spec-execute.md §5.2). Second precision was too coarse: with a 1 s interval, the gap between two Thread.print samples (1.12-1.36 s in practice, the jcmd attach time adds to the sleep) could not be derived from their timestamps. The bash collectors (`+02:00`) and the Python ones (`+0200`) also disagreed on the offset. The post-processing accepts only the new format; old logs are converted once by `scripts/helpers/migrate-timestamps.py` (then `jbr-pp -f`) instead of every parser accepting both, to keep backward compatibility out of the code. The same format is used for the run's start time: `info.json`'s `time` (written just before launch, second precision) and `run-info.json`'s `wct_start` (taken at launch, milliseconds) recorded the same moment about a second apart, so the former was dropped and the latter renamed to `time` (and `wct_secs` to `duration_secs`). It stays in `run-info.json` because the launch time describes this invocation, not the run's static setup. Note that a collector's timestamp is taken before its tool runs (for jcmd: before attach), so it is not the exact moment of the sample. |
| D-68 | Per-thread-category CPU usage from Thread.print | `thread-print.jsonl` and `result.json` carry CPU usage (IRIX %) per thread category, grouped per category (`c2`, `c1`, `gc`, `mutator`, `other`) together with the thread counts (spec-post-process.md §2.7, §3c.1). The interval between two dumps is taken from the threads' `elapsed=` values, not from the collector timestamps: those are taken before jcmd attaches, and the attach time varies (the real gap between 1 s samples was 1.12-1.36 s), while `elapsed=` is read inside the JVM at dump time, consistent across threads to its 10 ms resolution. The run-level `cpu_pct_avg` covers the first to last dump only (startup before the first dump is excluded); rejected: including startup via each thread's CPU time since start, because threads that ended before the last dump would be missing entirely. CPU time of threads that end between dumps is invisible to Thread.print; when the `proc` collector ran it is reconstructed as process CPU minus the sum of the categories and recorded as a separate CPU-only category `unaccounted` (plot: "Unaccounted/Short-lived"). Merging it into `other` was tried first and rejected: on dacapo tomcat (over 1000 threads started and ended per run) it was about 30 % of the process CPU, mostly short-lived application threads, so `other` hid a large share of the mutator CPU and swung inversely to `mutator`. |
| D-69 | Page faults: totals only, from proclog | `result.json` records only the total minor/major faults (`page_faults`, spec-post-process.md §2.8), read from the cumulative `/proc/<pid>/stat` counters in the last proclog sample; rates appear only in the `pagefaults.svg` plot. Rejected: a per-second average — faults are very unevenly distributed over a run (most happen at startup), so an average says little, and for fixed-work benchmarks (dacapo) it is misleading: a faster run shows a higher rate for the same number of faults. Rejected: pidstat's `minflt_per_sec`/`majflt_per_sec` as the source — per interval they match the proclog deltas, but pidstat only sees faults after it attached, while proclog's counters include everything since process start (in some runs several hundred thousand faults before the first sample). `minflt` is a 64-bit counter on 64-bit kernels, so overflow is not a concern. |
| D-70 | DaCapo latency summary: final iteration, two variants, never auto-omitted | `result.json` and `jbr-ls` carry p50/p99/p100 (= max) of the final iteration only, for the unsmoothed and the 100ms-smoothed variant; `smooth_full`, the other percentiles and the warmup iterations stay in `iterations[]`. Only p99 gets a standard error, since it's the headline tail metric. The absolute standard errors (`stderr_ms`, `p99_stderr`, ...) are left out of the column groups (`RESULTS`, `LATENCY`, `LATENCYSMOOTH`): the relative `*_stderr_pct` value is what tells you about noise, and the absolute value costs horizontal space. Latency columns are never auto-omitted: most benchmarks report no latency, so `-o LATENCY` would otherwise often make the requested columns silently disappear. |
| D-71 | Split `jvmargs`/`tags` via suffixed keys, repeated keys are errors | Long flag or tag lists can be broken up into `jvmargs_<suffix>` / `tags_<suffix>` keys, combined at load time (spec-prepare.md §1). This replaces an earlier rule that merged repeated `jvmargs`/`tags` keys: repeated keys are not portable JSON, and `jq`, editors and linters keep only the last one, so a reader of the file saw a different list than jbenchrunner used. Repeated keys of any name are now a validation error. Suffixes are names rather than indices because a name like `jvmargs_logging` documents the group; the parts are combined in file order (plain key first), not sorted by suffix. Rejected: numbered `jvmargs_<n>` sorted by number — gives an order but no meaning, and needs extra rules for gaps and leading zeros. Superseded by D-76: split keys were removed, since TOML arrays can span several lines with comments; repeated keys stay an error (TOML forbids them). |
| D-72 | Benchmark selection is batch-level only; ad-hoc groups via `bench_<suffix>` | A `.run.toml` selects its benchmarks once, at the top level (`bench`, split into `bench_<suffix>` keys for an ad-hoc group, or `benchgroup`), and every run entry runs against every selected benchmark — a file is *benchmarks × configurations*. Per-run-entry `bench`/`benchgroup` (overriding a batch-level default) was removed: no real config ever selected different benchmarks in different entries, and mixing the "what runs" and "how it runs" axes in one entry made both the files and the override rules harder to read. Mixed selections remain possible by splitting the batch into several `.run.toml` files (spec-prepare.md §3). A leftover per-entry `bench`/`benchgroup` is a validation error, not the usual unknown-field warning, because ignoring it would silently run a different benchmark than the file states. `bench` parts are single names (strings), not lists, so each `bench_<suffix>` names one benchmark. Splitting is accepted only in `.run.toml`, since other files (e.g. `BATCH_IN_PROGRESS.json`) have unrelated `bench_*` fields. Rejected: letting `bench` (top level or per entry) take an array — would have kept the two-level override rules, or needed a second syntax next to split keys. Changed by D-76: with split keys gone, an ad-hoc group is written as `bench = ["a", "b"]`. The rejected array form came back only at the top level; per-entry selection stays forbidden, so the two-level override rules did not return, and with no split keys there is no second syntax. |
| D-73 | Projects instead of one `results/` data lake | All results used to go into one `<reporoot>/results/` directory, with tags and filters selecting a set for analysis. In practice every analysis covers one self-contained set (e.g. "all GCs of this JDK", "patch v1, v2, v3 against a baseline"), and different sets are never compared; the shared directory made grooming hard, made post-processing the whole directory slow, and required remembering the tags that identified a set. A project is therefore defined as a comparison scope (§4). `.run.toml` files belong to the project, since they only have meaning together with its results, unlike profiles, benchmarks, hosts and collectors, which stay global in `config/`; `config/run/` keeps templates only. JDK configs are partly project-specific too (patched builds); a project-local JDK config was considered and deferred. Each batch keeps frozen copies of its `.run.toml` files: a project's run files are edited between batches, and the resolved manifest records what ran, but not what was written or what changed between two batches. The project is found from the working directory, the way git finds its repository, and in no other way. Rejected: a stored "current project" (one setting shared by all shells: a switch in one terminal while a batch runs in another sends results into the wrong project); a common project root in `config/config.toml` with selection by name (`-P`, `JBR_PROJECT`), which adds concepts without real gain and restricts where projects can live; a fallback to the old `results/` layout, which would mean two code paths for good (the old directory is moved once into a project instead). Projects are not nested, since a comparison scope inside another one has no clear meaning. One active batch per host stays a requirement (parallel batches compete for CPUs, CPU pinning and the ramdisk), so it needs state outside any project: the `active-batch` symlink (§7). It lives in the home directory, not under `/tmp`, because it must survive a reboot (after a crash or `--shutdown`, an interrupted batch must still block other projects and still be findable) and must not be removed by tmp cleaners during a long batch. It is a symlink rather than a file with fields of its own, so it holds one fact (where the active batch is) and cannot disagree with the batch file; batch ID, hostname and liveness are read from the batch file and `run-info.json` as before, and a dangling link shows that the project was moved or deleted mid-batch. The link is set when execution starts, not at preparation, in line with the existing rule that a prepared but unstarted batch may be overwritten. |
| D-74 | `jbr-move-batch` — batches, not runs, as the unit of moving between projects | Splitting a project with `jbr-move` moves run directories only and loses their batch information (manifest, frozen `.run.toml` copies), which `jbr-status` and provenance (D-62) need; `jbr-move-batch` moves a batch with all its runs. Unlike `jbr-move`/`jbr-retag`, it does not refuse to work whenever `BATCH_IN_PROGRESS.json` exists, only when asked to move that very batch: a finished batch's files are no longer written, so moving them cannot race `jbr-execute`, and a project often has a long batch running while older ones are groomed. For the same reason, and since merging two batches or two copies of a run has no meaning, there is no `--force`. Reports are left in the source project, and links in them to moved runs may break; rewriting or moving reports was judged not worth it. |
| D-75 | Minimum Python version 3.11 | The harness starts collectors with `subprocess.Popen(process_group=0)` (D-59), which exists from Python 3.11 on; the earlier `preexec_fn=os.setpgid` equivalent is documented as unsafe with threads, and `jbr-execute` runs a sudo keepalive thread. 3.11 rather than 3.12: one f-string with a backslash in its expression (PEP 701, 3.12) was the only 3.12 feature in use and was rewritten, so the minimum costs nothing and still covers older distribution Pythons on benchmark hosts. Code that needs a newer version (e.g. `compression.zstd`, 3.14, D-58) is avoided rather than guarded. |
| D-76 | Configuration files in TOML | Hand-maintained JSON config was tiresome: no comments (the `COMMENT` key allows one per object), no trailing commas, quotes around every key. TOML has comments and trailing commas and is read by the stdlib (`tomllib`, Python 3.11+, D-75), so no new dependency. Rejected: JSONC (comment-stripping before `json.load` — no standard, hand-rolled parser), JSON5 and YAML (third-party packages). Only hand-edited files moved; files the tools write stay JSON / JSON lines, because they are read with `jq` (`jbr-filt`) and are never edited by hand. Clean cut, no dual-format loader: all config was converted once (`scripts/helpers/convert-config-to-toml.py`, checked by reading every file back), and two formats would mean two parse paths and doubled tests for good. The stdlib has no TOML writer; the minimal `scripts/common/toml_writer.py` serves the converter and the test fixtures. The `COMMENT` key convention was dropped: it only existed because JSON has no comments. Existing top-level `COMMENT` values were turned into `#` comments (the converter does the same, and refuses a file with a nested `COMMENT`, which has no obvious place as a comment); a `COMMENT` key is now an unknown field like any other (a warning, or an error inside a collectors structure). No tool ever wrote `COMMENT` into generated JSON, so nothing else depended on it. The split keys (D-71) were removed as well: an array spread over several lines, with comments between the items, does the same job. A leftover split key is a validation error rather than an unknown-field warning, since ignoring it would silently drop flags, tags or benchmarks; the converter merges split keys of old JSON files into one array, in the order the old loader combined them. TOML 1.0 (what `tomllib` reads) has no `null` — an omitted key means the same — and inline tables must fit on one line, so run entries are `[[runs]]` tables. Frozen `.run.json` copies in batches prepared before the switch stay as they are; nothing reads them back. |
| D-77 | User-facing commands have no `.py` extension: sh wrappers in `bin/`, Python in `libexec/` | The implementation language is a detail users should not have to type, but the implementation files stay ordinary `.py` files for editors and tooling. Each `bin/<command>` is an identical-in-form bash wrapper that `exec`s `python3 ../libexec/<command>.py`. Rejected: dropping the extension in place (loses the `.py` files); symlinks (not to be shipped); packaging entry points via `pyproject.toml` (needs an install step, the tools run straight from the checkout). Wrappers do not resolve symlinks to themselves, so they must be invoked via their real path (or `bin/` put on `PATH`). No `.py` compatibility names are kept. |
| D-78 | Machine stabilization in host config, with CLI override (host config default removed by D-88) | `stabilize_machine` is a host config field (overridable by `jbr-prepare --[no-]stabilize-machine`), not a `.run.toml` field: it is a machine-wide fact, and a batch-level `.run.toml` field would break D-61 (several `.run.toml` files could disagree, with no merge rule). It also governs the per-run machine reset, which replaced the separate host field `reset_machine_between_runs` (D-50): a reset without stabilization removes little noise next to what an unstabilized machine leaves, the two overlap (sync, drop caches), and one switch avoids two meaningless combinations. This flips the reset's default from on to off: changing a machine as root should be an explicit opt-in per host. Rejected: keeping a separate switch for stabilizing once without per-run resets (e.g. to keep the page cache warm across runs) — no current use; it can return as its own option. An old host config still carrying `reset_machine_between_runs` only gets the unknown-key warning. Without `--sudo`, a host-config default is skipped with a warning, so preparing a batch without `--sudo` keeps working on hosts that enable it; an explicit `--stabilize-machine` without `--sudo` is an error, since the user asked for it in that invocation. The script degrades gracefully (warns for settings the machine does not support), but its other failures abort the batch rather than run on a half-stabilized machine. It runs on every `jbr-execute` invocation, since a reboot between resumes loses its settings, and it never reverts them (strict overcommit and stopped services are intended to last). |
| D-79 | `repeat` in `.run.toml` is batch-wide; differing values across input files are an error | `repeat` is the file-level counterpart of `jbr-prepare --repeat`, which still wins. Unlike other batch-level fields (D-61) it cannot be scoped to its own file's entries, since repetition interleaves rounds across the combined list of all input files. If several files set different values and no `--repeat` is given, the batch is rejected. Rejected: taking the maximum (or the first file's value), which would silently repeat some file's entries more often than that file asked for; per-run-entry `repeat`, which breaks the round-interleaved order. |
| D-80 | Collector and perf event settings are batch level only | `collectors`, `collectors_preset`, `perf_events_preset` and `ignore_unknown_perf_events` (removed by D-86) can be set only at the batch level of a `.run.toml`; in a run entry they are a validation error (like `bench`, D-72), not ignored. The run-level override of D-66 was never used in practice: collector settings are a property of the whole experiment. Runs that need different settings go into separate `.run.toml` files, which can still be prepared into one batch (D-61), so the per-run storage in `BATCH_IN_PROGRESS.json` stays (superseded by D-84: the settings are now batch-wide, and such runs go into separate batches). |
| D-81 | Perf events: chosen per `.run.toml` via presets, perf's default set when absent, checked per event at prepare time (check superseded by D-86) | The events used to be a host config list (`perf_events`, overridable by `jbr-prepare --perf-events`). But only what *can* be counted is a property of the host (CPU and kernel); what *is* counted is a choice of the experiment (cycles and page faults today, L1 behaviour tomorrow). Both host list and CLI option are removed; a `.run.toml` names a preset (`config/perf-events-presets/`), a plain list of possibly CPU-specific names with no further logic — fitting it to the host is up to the user. Presets are convenience holders only: the batch file and `info.json` record the resolved events, or `null` for perf's default set, never the preset name, as for collector presets (D-66). With no preset, `perf stat` runs without `-e`; previously a missing list disabled the collector. The default set varies with CPU, kernel and perf version (e.g. topdown metrics on newer Intel, `cpu_core`/`cpu_atom` on hybrid CPUs), which perf's human-readable output could not be parsed reliably for (decimal values such as `task-clock` msec, `# ...` metric comments); hence the switch to CSV (`-x`), with `;` as separator because event names may contain commas. A wrong event used to stop every run 5 s after start (perf exits, collector dead, run `STOPPED`). Now `jbr-prepare` checks every event on its own with `perf stat -e <event> -- true`, which counts for one process like the run's `-p`; an unknown or `<not supported>` event aborts preparation, or with `ignore_unknown_perf_events = true` is dropped with a warning. If all events are dropped, `perf` is disabled rather than falling back to the default set, which would count events nobody asked for. An event that cannot be checked for lack of privileges is kept with a warning. Rejected: one combined check of the whole list (a single bad event hides which one it is); checking event groups for multiplexing (the pct column records it). |
| D-82 | Collector availability probed on the machine, not declared in the host config | The host flags `has_cpu_temp`, `has_pidstat`, `has_linux_procfs` and `has_perf` declared which machine-dependent collectors a host can run; `jbr-prepare` removed the others from every run, and the collector tests skipped themselves by reading the same flags from `config/hosts/<hostname>.host.toml`. Declarations go stale and must be written for every machine, and the tests then depended on a host file named after the developer's machine: anywhere else without perf, pidstat or procfs (e.g. macOS) they failed rather than skipped. Both now use one set of probes (`scripts/common/collector_probe.py`): `perf`/`pidstat` on `PATH`, `/proc/<pid>/stat` and `status`, one `read-cpu-temp.sh` read. `jbr-prepare` probes each requested collector once per batch and drops one that fails, with a warning, as the flags did. Probing at prepare time is sound because a batch is bound to the host it was prepared on (`jbr-execute` checks the hostname). The probes check only that a collector can start; one that passes and then dies is still a hard failure for the run (D-47). The perf collector tests additionally require `perf_event_paranoid <= 1`, which is not part of the probe: whether events can be counted depends on `perf_event_paranoid` (D-85) and is checked at batch start (D-86). Rejected: keeping the flags as an extra opt-out (the collector set in `.run.toml`, D-66, already selects collectors). A leftover `has_*` key only gets the unknown-key warning. |
| D-83 | Benchmark and test JDK paths in the host config; `config/config.toml` removed | `dacapo_dir`, `specjbb_dir` and `test_jdk` were the only contents of `config/config.toml`, described as "project-wide, not machine-specific" — but they are paths into one machine's installations, so a shared or published checkout carried the developer's paths. They are now host config fields (`<hostname>.host.toml`, falling back to `default.host.toml`), which is where machine-specific settings already live. A host file replaces `default.host.toml`, it does not merge with it, so a machine with its own host file sets the paths there. With the file gone, `config/config.toml` no longer marks the repository root; a directory holding both `config/` and `libexec/` does. Rejected: a `config.toml.example` template next to an untracked `config.toml` (one more file to copy, and still machine-specific settings outside the host config); a dedicated marker file (exists only to exist). |
| D-84 | Collector settings are batch-wide: one `collectors` structure per batch, executed as prepared | The collector set decides what overhead a run pays (perf, NMT and the jcmd collectors measurably slow the benchmark), so runs of one batch compared with different sets compare different things. `BATCH_IN_PROGRESS.json` therefore holds one top-level `collectors` (interval, compression, enabled set, perf events) instead of one per run, and `jbr-prepare` makes every decision batch-wide: several input files must resolve to the same settings (compared after preset resolution; the interval is not compared when `--collector-interval` overrides it), else the batch is rejected, like differing `repeat` (D-79); a collector this machine cannot run (D-82) or whose `restrict_to` excludes the JDK of any run is dropped for all runs, with a warning — so in a mixed OpenJDK/OpenJ9 batch the OpenJDK runs lose `nmt` and `system_map` too. `jbr-execute` then relies on the batch file only: every run starts exactly that set, and a collector that fails to start or whose `restrict_to` no longer covers the run's JDK (the JDK at `java_home` changed since prepare; previously skipped with an info line) stops the run (`STOPPED`, `status_info` `"collector"`, D-47) instead of letting it run with fewer collectors. Each run's `info.json` keeps its own copy of the settings, so analysis tools are unaffected. A batch without a top-level `collectors` was prepared before this change and is refused with a request to re-prepare it; reading the old per-run format would mean a second code path for a transient case. Rejected: first file wins for differing files (silently ignores a file's own choice); aborting on a `restrict_to` mismatch in a mixed-JDK batch (consistent too, but dropping matches what an unavailable collector does); keeping `restrict_to` as the one allowed per-run difference (contradicts the point). |
| D-85 | perf never runs as root; kernel-level counts come from machine stabilization | `perf-logger.sh --sudo` ran `sudo -n perf stat -p <jvm pid>` when `perf_event_paranoid` was above 1: a root process attached to the JVM for the whole run, minutes to hours, and the main reason the sudo ticket had to stay usable by the collectors. perf now always runs as the invoking user, and what it can count depends on `perf_event_paranoid` alone: at 1 or lower, user and kernel events of the JVM; at 2 (the upstream default), user space only: perf falls back on its own with a warning, the log parser skips the warning, and the events show up with a `:u` modifier (`cycles_u` instead of `cycles`), which keeps such counts from being mixed up with full ones by accident; at 3 or higher (Debian/Ubuntu kernels), nothing. Machine stabilization sets the level to 0, not -1: 0 suffices for kernel counts of a process and also allows whole-CPU counting (`-a`); -1 additionally opens raw tracepoint data, which nothing here needs. So kernel-level perf data requires `--stabilize-machine` (and hence `--sudo`). `jbr-prepare` reads the level unless stabilization will be in effect (requested, `--sudo`, Linux): at 3 or higher it drops `perf` from the batch with a warning, like a collector this machine cannot run (D-82); at 2 it warns that perf will count user space only. Each run records the host's level in `run-info.json` (`stabilization.kernel.perf_event_paranoid`), so runs with reduced perf data can be told apart later. Rejected: keeping sudo for perf (a long-lived root process for little gain); CAP_PERFMON on the perf binary (a host setup step outside jbenchrunner, still possible: perf then counts everything whatever the level, but `jbr-prepare` would drop or warn about it based on the level); aborting at level 3 or higher instead of dropping (unlike every other unavailable collector). |
| D-86 | perf checked once at batch start, as the whole command line | D-81 had `jbr-prepare` check every event of the preset on its own (`perf stat -e <event> -- true`), sort it into ok / unknown / unsupported / not checked, and abort or, with `ignore_unknown_perf_events`, drop unusable events. Since perf runs unprivileged (D-85), the privileges that matter are those after machine stabilization, which runs in `jbr-execute`: at prepare time a host at `perf_event_paranoid` 3 or higher could check nothing ("not checked", kept). `jbr-execute` now checks once per invocation, after stabilization and before the first run, with `perf-logger.sh --check`: the collector's own command line, counting `true` instead of the JVM, so the check tests exactly what the runs will run, perf's default set included (previously unchecked), as well as events that only fail in combination. Any failure, or an event reported `<not supported>`, aborts the batch with perf's own message. Dropping events is gone with `ignore_unknown_perf_events`: the batch file is never modified and a batch runs as prepared (D-84), so `jbr-execute` cannot drop anything; an old `.run.toml` setting the key gets the unknown-key warning. D-81 rejected one combined check because a single bad event hides which one it is; but perf names an unknown event in its error message, a `<not supported>` line names its event, and a privilege error concerns no particular event. Cost: a wrong event is found at `jbr-execute` start instead of before the batch file is written, so changing the events takes `jbr-execute --abort` and a new `jbr-prepare`; a host problem is fixed and `jbr-execute` simply rerun. Rejected: keeping the per-event check at prepare time in addition (two checks with different privileges, and the drop feature it existed for cannot work at execute time); checking per event at execute time (more perf invocations, same result for an abort-only check). |
| D-87 | Root commands granted by per-command sudoers rules, not a helper script | `config/jbenchrunner.sudoers` is a template with one `NOPASSWD` rule per root command of `prepare-for-benchmarks.sh`, `reset-machine.sh` and `--shutdown`, each matching the exact command line the scripts run, so sudo itself refuses anything else. This lets the batch run without a general sudo ticket, which every process on the terminal could use, the JVM under test included: `jbr-execute` no longer runs `sudo -v` and a keepalive loop, but checks with `sudo -n -l` at batch start that the rules it needs are installed. `scripts/phase-execute/tests/test_sudoers_template.py` checks that the scripts' root commands and the rules match. All root commands have fixed arguments, and the scripts already call one `sudo -n <tool>` per step, so the rules need almost no code change and no jbenchrunner code runs as root. The one subtle part is the per-CPU sysfs files: in sudoers arguments `*` also matches spaces and slashes, so `tee .../cpu*/cpufreq/scaling_governor` would allow writing any file as root; the rules use `[0-9]` classes (one character each, CPUs 0-999) instead. Rejected: a root-owned helper script in `/usr/local/sbin` with fixed actions (one sudoers line, and per-CPU loops in readable bash, but about 170 lines of root code to review, an install step to repeat after every change and a version check against stale copies); sudoers regular expressions (need sudo 1.9.10+); cpupower for the CPU settings (a further dependency, kernel-version-bound on Ubuntu, no turbo control in older versions, and covers only 3 of the settings). The rules go to a group, `jbenchrunners`, and the docs recommend a dedicated benchmark user in it: the rules are permanent and usable by every process of every member, the JVM under test included, so the group, not the rules, bounds who can switch these settings or shut the machine down. Cost: scripts and rules must change together; a mismatch makes the command fail with "a password is required" unless a ticket is open. |
| D-88 | Machine stabilization only on explicit request; reboot recommended afterwards | Stabilization weakens security until reboot: ASLR off, `perf_event_paranoid=0`, swap off, strict overcommit, services stopped. With D-78's host config default, `stabilize_machine = true` on a host made every `jbr-prepare --sudo` there stabilize the machine without being asked. Now it happens only with `jbr-prepare --stabilize-machine` (together with `--sudo`), given on every prepare; the host config field and `--no-stabilize-machine` are gone, and a leftover field only gets the unknown-key warning (its effect is now the safe one: no stabilization, and `jbr-prepare` says so). The choice is stored in the batch, so resumes stabilize alike without repeating the flag. `jbr-prepare` names the main settings and the security cost when stabilization is on, and `jbr-execute` ends with a reboot recommendation whenever it started `prepare-for-benchmarks.sh`, however the batch ends (completed, Ctrl-C, error), except after a successful `--shutdown`. Rejected: `--sudo` alone switching stabilization on (one switch, but `--sudo` is also needed for `--shutdown`, which should not imply changing the machine); reverting the settings at batch end (needs the previous values recorded, and the stopped services restarted, for a state a reboot restores anyway); a hard error for the leftover host field (a stale field is harmless now). |
