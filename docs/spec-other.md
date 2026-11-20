# jbenchrunner — Miscellaneous Tools Specification

**Part of:** [PRD.md](PRD.md) (see master for goals, target users, repository layout, non-functional requirements, and decisions log)
**Scope:** Tools that operate on existing result directories outside the normal prepare → execute → post-process → analyze pipeline. These are not phases — they don't produce or consume pipeline artefacts in sequence, they act directly on results that already exist. Currently: `jbr-retag`, `jbr-move`, `jbr-copy`, `jbr-failures`, `jbr-move-batch` (moves whole batches between projects, §6), plus `jbr-init`, which creates a project (§5). All tools in this document except `jbr-init` must be run inside a project (PRD.md §4) and abort outside one; all relative paths below are relative to the project root. Future home for other such tools, e.g. a planned tool to delete or move whole result directories.

---

## 1 `jbr-retag` — Add / Remove Tags on Result Directories

`jbr-retag` adds, removes, or lists the `tags` field of one or more existing run result directories. Unlike `jbr-filt` and `jbr-ls`, it is **not** read-only — but in every other respect it follows the same scope: it operates only on run directories that have a `result.json` (i.e. successful and successfully post-processed runs), per D-44. Directories without a `result.json` are silently skipped, exactly as they are by `jbr-filt`/`jbr-ls`.

### 1.1 Synopsis

```
jbr-retag [-a|--add TAG]... [-r|--remove TAG]... [--force] RUNID...
jbr-retag [--force] RUNID...    # no -a/-r: print current tags
jbr-retag -h|--help
```

`RUNID` values are bare run directory names, resolved as `results/<RUNID>`. Input resolution is a **two-tier** version of `jbr-filt`/`jbr-ls`'s three-tier priority (spec-analyze.md §1.1.1 / §1.2.1):

1. **Positional arguments** — if any `RUNID` values are given on the command line, those are used.
2. **stdin** — if no positional arguments are given and stdin is not a terminal (i.e. data is being piped or redirected in), RUNIDs are read one per line from stdin. This may legitimately yield an empty list (e.g. an upstream filter matched nothing) — that's fine, there's simply nothing to do.

Unlike `jbr-filt`/`jbr-ls`, there is **no third tier** — if no positional arguments are given and stdin is a terminal (nothing piped in), `jbr-retag` aborts with an error rather than falling back to "all runs of the project". This tool mutates data; an accidentally omitted RUNID list must never silently retarget every run in the project.

This enables direct pipeline composition, exactly like `jbr-ls`:

```sh
jbr-filt -b dacapo --sub batik | jbr-retag -a needs-recheck
```

### 1.2 Options

| Flag | Meaning |
|---|---|
| `-a`, `--add TAG` | Add `TAG` to the run's tag set. Repeatable. |
| `-r`, `--remove TAG` | Remove `TAG` from the run's tag set. Repeatable. |
| `--force` | Proceed even if `BATCH_IN_PROGRESS.json` exists (see §1.5). |
| `-h`, `--help` | Usage |

`-a` and `-r` may be combined and repeated freely in a single invocation, e.g. `jbr-retag -r old-name -a new-name RUNID`. If the same tag string appears in both `-a` and `-r` in one invocation, removal is applied first, then addition — the tag ends up present.

If neither `-a` nor `-r` is given, `jbr-retag` runs in **list mode** (§1.4).

### 1.3 Tag semantics

- Adding a tag that is already present is a silent no-op (not an error).
- Removing a tag that is not present is a no-op; a one-line note is printed for that run (to stdout, alongside the run's other output lines — see §1.6 for the exact format), and processing continues.
- If, after applying all `-a`/`-r` operations, a run's tag set is unchanged, its `info.json` is left untouched (no rewrite) — see §1.6 for the corresponding output line.
- A tag value must be non-empty and must not contain `,` — rejected as a fatal error at argument-parsing time, before any file is touched. `jbr-ls` joins tags with `,` for display and `jbr-filt --tags` substring-matches on tag elements; a comma inside a tag would corrupt both.
- New tags are appended in the order given on the command line. The relative order of untouched existing tags is preserved.

### 1.4 List mode (no `-a`/`-r`)

Prints the given RUNIDs and their current tags as a table (`runid`, `tags` columns), reusing the same formatting and terminal-hyperlink behavior as `jbr-ls` (spec-analyze.md §1.2) for exactly the given RUNIDs — no sorting, no column selection, no auto-omit. `runid` is a clickable terminal hyperlink to the run directory when the output stream is an interactive terminal that supports OSC 8 hyperlinks; otherwise plain text. Same as `jbr-ls`/`jbr-filt`, this never affects piped/redirected output.

### 1.5 In-progress batch guard

Before touching any file, `jbr-retag` checks whether the project's `BATCH_IN_PROGRESS.json` exists. Only the current project's batch file is checked, not the `active-batch` link (PRD.md §7): a batch in another project never writes into this project's runs. If it does, the tool aborts immediately with an error and makes no changes — regardless of which RUNIDs were given, and regardless of whether any of them actually belong to that batch. `jbr-execute` may still be writing per-run state for an in-progress batch, and racing it is unsafe. Pass `--force` to proceed anyway.

### 1.6 Output

**List mode:** table as described in §1.4, to stdout.

**Mutating mode:** one or more lines per run to stdout, e.g.:

```
a1bcd3ca-e00c-46fd-8121-c1207ddfc6af: +needs-recheck
a1bcd3ca-e00c-46fd-8121-c1207ddfc6af: -flaky (not present)
b2cde4db-f11d-57ge-9232-d2318eegd7bg: unchanged
```

`runid` is a clickable terminal hyperlink under the same conditions as list mode. Each applied add/remove gets its own line; a run with no net change after applying its operations prints a single `unchanged` line instead.

### 1.7 Error handling

- A RUNID whose directory has no `result.json`: silently skipped, no warning — matching `jbr-filt`/`jbr-ls` (D-44).
- A RUNID whose directory does not exist, or whose `result.json` is present but `info.json` is missing or malformed: a `Warning:` line to stderr, that run is skipped, and processing continues with the remaining RUNIDs (matching `jbr-ls`'s behavior).
- Invalid tag value (empty, or containing `,`): fatal, aborts before any file is written. Applies to the whole invocation, not per-run — checked once at argument-parsing time.
- `BATCH_IN_PROGRESS.json` present without `--force`: fatal, aborts before any file is written.

### 1.8 Write safety

Each run's `info.json` is updated by writing a temporary file in the same directory and renaming it into place — the same pattern `jbr-execute` uses elsewhere for `info.json` and result directories — so a crash mid-write cannot leave a corrupt or partial `info.json`. Only the `tags` field is modified; every other field is preserved verbatim.

### 1.9 Examples

```sh
# Tag two runs as part of a comparison
jbr-retag -a gc-comparison-2025 a1bcd3ca-e00c-46fd-8121-c1207ddfc6af b2cde4db-f11d-57ge-9232-d2318eegd7bg

# Rename a tag across a filtered set
jbr-filt -t old-name | jbr-retag -r old-name -a new-name

# List current tags for specific runs
jbr-retag a1bcd3ca-e00c-46fd-8121-c1207ddfc6af

# Force through despite an in-progress batch
jbr-retag --force -a investigate a1bcd3ca-e00c-46fd-8121-c1207ddfc6af
```

---

## 2 `jbr-move` — Move Result Directories to Another Location

`jbr-move` moves one or more existing run result directories out of the project's `results/` into a destination directory, preserving each directory's RUNID name. It operates on **any existing run directory** — unlike `jbr-filt`/`jbr-ls` (D-44) or `jbr-retag` (§1), there is no `result.json` requirement, and no `info.json` requirement either: the tool only cares that `results/<RUNID>` exists. A primary use case is archiving failed or interrupted runs that never produced a `result.json`.

### 2.1 Synopsis

```
jbr-move --to DIR [--force] RUNID...
jbr-move -h|--help
```

RUNID resolution is the same two-tier scheme as `jbr-retag` (§1.1): positional arguments if given, else RUNIDs read one per line from piped stdin; if neither source supplies any RUNIDs (no positional args and stdin is a terminal), the tool aborts with an error. There is no "all runs of the project" fallback.

```sh
jbr-filt --tags stale | jbr-move --to /mnt/archive/jbenchrunner
```

### 2.2 Options

| Flag | Meaning |
|---|---|
| `--to DIR` | Required. Destination directory; each run lands at `DIR/<RUNID>/`. `DIR` (and any missing parents) is created if it does not already exist. |
| `--force` | (a) proceed even if `BATCH_IN_PROGRESS.json` exists (§2.4); (b) if `DIR/<RUNID>/` already exists, replace it instead of aborting (§2.5). |
| `-h`, `--help` | Usage |

### 2.3 Scope

Any RUNID whose `results/<RUNID>` directory does not exist is silently skipped with a `Warning:` to stderr; processing continues with the rest.

### 2.4 In-progress batch guard

Identical to `jbr-retag` (§1.5): if `BATCH_IN_PROGRESS.json` exists, `jbr-move` aborts immediately with an error and moves nothing, unless `--force` is given.

### 2.5 Destination collision handling

Before moving anything, `jbr-move` validates every RUNID whose source directory exists: if `DIR/<RUNID>/` already exists, this is a **fatal error** — the whole invocation aborts before any directory is touched, and the error names every colliding RUNID. Pass `--force` to instead replace each pre-existing destination directory with the newly moved one.

This is a pre-flight check across the whole RUNID set, not a per-run skip: a single collision (without `--force`) prevents every run in the invocation from being moved, so a batch is never left half-moved.

### 2.6 Output

One line per successfully moved run, to stdout:

```
<runid>: moved to /mnt/archive/jbenchrunner/<runid>
```

`runid` is a clickable terminal hyperlink to the destination directory (its post-move location) under the same OSC 8 conditions as `jbr-ls`/`jbr-filt`/`jbr-retag`.

### 2.7 Error handling

- RUNID whose `results/<RUNID>` does not exist: `Warning:` to stderr, skipped, processing continues.
- Destination collision without `--force`: fatal, aborts before any directory is touched (§2.5).
- `--to` path exists and is not a directory: fatal, aborts before any directory is touched.
- `BATCH_IN_PROGRESS.json` present without `--force`: fatal, aborts before any directory is touched.

### 2.8 Examples

```sh
# Archive everything tagged "stale"
jbr-filt --tags stale | jbr-move --to /mnt/archive/jbenchrunner

# Move specific runs, replacing any existing copies at the destination
jbr-move --to /mnt/archive/jbenchrunner --force a1bcd3ca-e00c-46fd-8121-c1207ddfc6af
```

---

## 3 `jbr-copy` — Copy Result Directories to Another Location

Identical to `jbr-move` (§2) in every respect — RUNID resolution, scope, the in-progress batch guard, destination collision handling, output format, error handling — except the source directory is left in place: `results/<RUNID>` still exists after a successful copy, alongside the new copy at `DIR/<RUNID>/`.

### 3.1 Synopsis

```
jbr-copy --to DIR [--force] RUNID...
jbr-copy -h|--help
```

### 3.2 Options

Same as `jbr-move` (§2.2).

### 3.3 Output

```
<runid>: copied to /mnt/archive/jbenchrunner/<runid>
```

### 3.4 Examples

```sh
# Copy a filtered set out for a colleague, without touching the originals
jbr-filt -b specjbb --jdkmajor '>=21' | jbr-copy --to /tmp/share-with-alex
```

---

## 4 `jbr-failures` — List Result Directories of Runs That Did Not Complete

`jbr-failures` is the tool for result directories of failed runs. Those have no `result.json`, so `jbr-filt`/`jbr-ls` never see them (D-44), and `jbr-status` only covers the current or last batch. Read-only.

### 4.1 Synopsis

```
jbr-failures [-a|--all]
jbr-failures -h|--help
```

### 4.2 Scope

Scans all subdirectories of the project's `results/` (no RUNID arguments, no stdin). A directory is listed if:

- its `run-info.json` has `status` `"FAILED"` or `"STOPPED"`, or
- its `info.json` or `run-info.json` is missing or unreadable, or `run-info.json` has no `status`. Such a directory is shown with status `"unknown"`, which is only determined on the fly and never written back.

`DONE` and `RUNNING` runs are not listed. A run that the current batch is just launching can briefly show as `"unknown"` before its `run-info.json` is written.

### 4.3 Output

Sorted by `run-info.json`'s `time` ascending; directories without a time come first.

By default, only the RUNIDs are printed, one per line, as plain text, suitable for piping into `jbr-move`/`jbr-copy`.

With `-a|--all`, a table with a header line, columns auto-sized, `-` for missing values:

| Column | Source |
|---|---|
| `runid` | directory name; a terminal hyperlink to the directory, as in `jbr-ls` |
| `time` | `run-info.json` `time`, cut to seconds, as in `jbr-ls` |
| `duration` | `run-info.json` `duration_secs` |
| `status` | `run-info.json` `status`, or `unknown` |
| `status_info` | `run-info.json` `status_info` (spec-execute.md §4.2) |
| `exit_code` | `run-info.json` `exit_code` |
| `name` | `info.json` `name` |
| `jdkname` | `info.json` `jdk.name` |
| `tags` | `info.json` `tags`, comma-separated |

### 4.4 Examples

```sh
# What failed, and why?
jbr-failures -a

# Get all failed runs out of the project
jbr-failures | jbr-move --to /mnt/archive/failed
```

---

## 5 `jbr-init` — Create a Project

`jbr-init` creates a project (PRD.md §4) by writing its marker file `jbr-project.json`, and
puts a copy of `config/run/example.run.toml` into it.

### 5.1 Synopsis

```
jbr-init [-m|--description <text>] [<dir>]
jbr-init -h|--help
```

`<dir>` defaults to the current working directory. It is created (including missing parents) if
it does not exist.

### 5.2 Behaviour

- Writes `<dir>/jbr-project.json`, holding `description` if `-m` was given, else an empty
  object `{}`.
- Copies `config/run/example.run.toml` to `<dir>/example.run.toml`, the default input of
  `jbr-prepare` when no `.run.toml` is given (spec-prepare.md §3). An existing file of that name
  is left alone. `jbr-move-batch`, which also creates projects (§6), does not copy it.
- Nothing else is created: `batches/` is created by `jbr-prepare`, `results/` by
  `jbr-execute`, `reports/` by `jbr-ls --report`.
- Prints the absolute path of the project directory as the only line on stdout, so it can be
  used as `cd $(jbr-init my-project)`.

`jbr-project.json` fields (all optional; `{}` is a valid marker):

| Field | Type | Notes |
|---|---|---|
| `description` | string | Free text: what this result set is about. |

The project's name is its directory name; it is not stored in the file, so a project can be
renamed with `mv`.

### 5.3 Error handling

All fatal; nothing is written:

- `<dir>/jbr-project.json` already exists.
- `<dir>` lies inside another project, i.e. a parent directory contains `jbr-project.json`
  (projects cannot be nested).
- `<dir>` exists and is not a directory, or cannot be created.

### 5.4 Migrating `<reporoot>/results/`

Result directories from the earlier layout (all runs in `<reporoot>/results/`) are migrated
once, into one project, with `scripts/helpers/migrate-results-to-project.py`; there is no
fallback to the old layout (PRD.md D-73):

```sh
jbr-init /data/jbr/legacy -m "all runs before the project layout"
scripts/helpers/migrate-results-to-project.py [-n] <reporoot>/results /data/jbr/legacy
```

- Every run directory `<reporoot>/results/<RUNID>/` is moved to `<project>/results/<RUNID>/`.
- Every `<reporoot>/results/batch-<BATCHID>-done.json` becomes
  `<project>/batches/<BATCHID>/batch.json`, with each run's `result_dir` set to
  `results/<RUNID>` (relative to the project), so `jbr-status` finds the runs. Its
  `source_file` keeps the old absolute path; old batches have no frozen `.run.toml` copies.
- `LAST_BATCH_IN_PROGRESS.json` (a mere copy) and any other file stay in `<reporoot>/results/`.
- It refuses to start, moving nothing, if `<reporoot>/results/BATCH_IN_PROGRESS.json` exists (finish or
  abort that batch with the old tools first), if the target is not a project, or if a run or
  batch already exists in the project. `-n` only prints what would be moved.

Splitting the migrated project into several projects can be done later with `jbr-move-batch`
(§6).

---

## 6 `jbr-move-batch` — Move Whole Batches to Another Project

`jbr-move-batch` moves one or more batches, with their batch information and their run result
directories, from the current project into another project, which is created if it does not
exist. It is the tool for splitting a project, or for merging batches into an existing one.
The source project is the current project (PRD.md §4), like for every other tool.

### 6.1 Synopsis

```
jbr-move-batch --to DIR [-m|--description <text>] [-n|--dry-run] BATCHID...
jbr-move-batch -h|--help
```

At least one `BATCHID` is required; there is no stdin input. A `BATCHID` given more than once
is moved once.

### 6.2 Options

| Flag | Meaning |
|---|---|
| `--to DIR` | Required. The target project. If `DIR` does not exist, it is created as a project, as `jbr-init DIR` would (§5), but without the `example.run.toml` copy. If it exists, it must be a project: `DIR/jbr-project.json` must exist. |
| `-m`, `--description <text>` | Description for the new project's `jbr-project.json` (§5.2). Only valid when `DIR` does not exist yet; an error otherwise. |
| `-n`, `--dry-run` | Run all checks and print what would be done, but create and move nothing. |
| `-h`, `--help` | Usage |

### 6.3 What is moved

For each `BATCHID`:

- the batch directory `batches/<BATCHID>/` as a whole (its manifest `batch.json` or
  `batch-aborted.json`, and the frozen `.run.toml` copies), to `DIR/batches/<BATCHID>/`;
- every run directory named by a `result_dir` in that manifest's `runs`, to the same relative
  path below `DIR` (i.e. `results/<RUNID>` to `DIR/results/<RUNID>`).

No file is rewritten: `result_dir` is relative to the project, and no file in a project stores
an absolute path into it (PRD.md §4). Nothing else is touched: in particular `reports/` stays in
the source project, so links from its reports to moved runs break (except for reports written
with `--self-contained`).

Moving uses a rename where possible and falls back to copy-and-delete across filesystems. Per
batch, its run directories are moved first and its batch directory last.

### 6.4 Checks

All of the following are checked before anything is created or moved; any failure is fatal, and
nothing is touched:

- Each `BATCHID` must have a directory `batches/<BATCHID>/` holding `batch.json` or
  `batch-aborted.json`.
- No `BATCHID` may be the batch of the source project's `BATCH_IN_PROGRESS.json`; that batch must
  be completed or abandoned (`jbr-execute --abort`) first. If `BATCH_IN_PROGRESS.json` exists
  but cannot be read, every move is refused. Other, finished batches can be moved while a batch
  is in progress, since nothing of theirs is written any more. There is no `--force`.
- `DIR` must not be the source project. If `DIR` exists, it must be a project; if it does not,
  it must be creatable as one (§5.3: not inside another project, which includes the source
  project).
- Collisions: neither `DIR/batches/<BATCHID>` nor any `DIR/results/<RUNID>` to be moved may
  already exist. All collisions are named in the error.

A run named in a manifest whose directory no longer exists in the source project (e.g. moved out
earlier with `jbr-move`) is not an error: a `Warning:` line is printed to stderr, and the rest of
the batch is moved. If *none* of a batch's runs exist, however, that is fatal (one error naming
every such batch), since it almost always means wrong `result_dir` values rather than runs moved
out on purpose. A batch whose manifest lists no runs at all is not affected by this.

### 6.5 Output

If `DIR` was created, first `created project DIR`. Then one line per batch, to stdout:

```
<batchid>: moved to <DIR>/batches/<batchid> (<n> runs)
```

`<n>` counts the run directories actually moved. With `-n`, the same lines are printed with
`would create project` and `would move to` instead.

### 6.6 Examples

```sh
# Split two batches off into a new project
jbr-move-batch --to ../g1-tuning -m "G1 region size experiments" \
    3f2c9a10-0b7e-4c51-9d3e-2a7f8e1c5b44 9a81d2e7-6c3f-4f0a-8b15-7e2d4c9a0f31

# Check first what would happen
jbr-move-batch -n --to ../g1-tuning 3f2c9a10-0b7e-4c51-9d3e-2a7f8e1c5b44
```

---

## 7 Future tools

This document is the intended home for other non-phase tools that operate directly on existing result directories. A `jbr-remove` deletion tool was proposed alongside `jbr-move`/`jbr-copy` but was dropped from scope — deleting result directories outright was judged unnecessary for now given `jbr-move` already covers getting a run out of a project.
