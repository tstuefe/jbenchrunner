# Where we need sudo, and why

Every place jbenchrunner runs a command as root, in the order they happen
during a batch: when each process starts, how long it runs, and why it needs
root.

All of this applies only to batches prepared with `jbr-prepare --sudo`
(`sudo_mode`). Steps 1 and 2 also require `--stabilize-machine`, step 3
`jbr-execute --shutdown`.

perf is not on this list: it never runs as root (PRD.md D-85). See
"perf: no root, `perf_event_paranoid` instead" below.

## 0. No sudo ticket: the sudoers rules

Every root command below is granted by its own rule in
`config/jbenchrunner.sudoers` (see "Granting exactly these commands" below)
and run with `sudo -n`, so nothing ever prompts for a password and no sudo
ticket is kept open during the batch (PRD.md D-87). An open ticket would let
every process on the terminal run anything as root, the JVM under test
included.

| What | When | How long |
|---|---|---|
| `sudo -n -l <command>` (`scripts/phase-execute/execute.py`, `_check_sudo_rules`) | At `jbr-execute` start, before the first run: one command per feature the batch needs (`tee /proc/sys/vm/drop_caches` for stabilization, `shutdown -h now` for `--shutdown`) | Instant; only lists, runs nothing |

If a rule is missing, the batch stops right there instead of at its first
reset or after its last run. `prepare-for-benchmarks.sh` does the same check
for itself. Both checks also pass with an open sudo ticket, e.g. on a host
without the rules where you ran `sudo -v` by hand; but nothing keeps such a
ticket alive.

## 1. Batch start: stabilizing the machine (`prepare-for-benchmarks.sh`)

Runs once per `jbr-execute` invocation (including resumes), after the sudo
check and before the first run. The whole script takes a few
seconds. It runs as the invoking user; each command below is a short-lived
root process.

| Command | Why root |
|---|---|
| `sudo -n tee .../cpufreq/scaling_governor` (every CPU) | sysfs cpufreq files are root-owned and writable only by root |
| `sudo -n tee .../intel_pstate/no_turbo` or `.../cpufreq/boost` | same (turbo boost switch) |
| `sudo -n tee .../scaling_max_freq` (only if `FIX_CPU_FREQUENCY=1`, which is off) | same |
| `sudo -n sysctl kernel.randomize_va_space=0` | Changing kernel settings under `/proc/sys` needs root |
| `sudo -n sysctl kernel.perf_event_paranoid=0` | same; this is what gives the unprivileged perf collector kernel-level counts |
| `sudo -n tee /proc/sys/vm/drop_caches` | Dropping the page cache is root-only (CAP_SYS_ADMIN) |
| `sudo -n systemctl stop <service>` (irqbalance, bluetooth, cups, avahi-daemon(.socket), cron; only the active ones) | Stopping system services needs root (or polkit permission) |
| `sudo -n swapoff -a` | root-only (CAP_SYS_ADMIN) |
| `sudo -n sysctl vm.overcommit_memory=2` and `vm.overcommit_ratio=100` | `/proc/sys` setting |

Nothing is reverted afterwards; the settings last until reboot. They weaken
security, so stabilization is never on by default (`--stabilize-machine` on
every `jbr-prepare`), and `jbr-execute` ends with a reboot recommendation
unless `--shutdown` powered the machine off (PRD.md D-88).

## 2. Before every run: resetting the machine (`reset-machine.sh`)

Runs before each run, including the first, before the JVM is launched. A few
seconds each time. Runs as the invoking user; `sync` needs no root.

| Command | Why root |
|---|---|
| `sudo -n tee /proc/sys/vm/drop_caches` | root-only |
| `sudo -n systemctl restart systemd-journald` | system service control |

## 3. After the batch: shutdown (`jbr-execute --shutdown`)

| What | When | How long | Why root |
|---|---|---|---|
| `sudo -n shutdown -h now` (`execute.py`, `_run_shutdown_sequence`) | Only once the whole batch has finished, after a `wall` warning and a 5-minute wait that Ctrl-C cancels; not run if the batch was stopped with Ctrl-C | Instant | Powering off the machine needs root (or polkit permission) |


## perf: no root, `perf_event_paranoid` instead

Both perf uses run as the invoking user, never via sudo (PRD.md D-85):

- `jbr-execute`'s perf check at batch start, after stabilization (PRD.md
  D-86): `perf-logger.sh --check`, i.e. `perf stat -x ; [-e <events>] -- true`,
  once per invocation, under a second.
- the perf collector (`perf-logger.sh`): `perf stat -x ; -e <events>
  --interval-print ... -p <jvm pid>`, attached to the JVM for the whole run.

What unprivileged perf can count is decided by
`/proc/sys/kernel/perf_event_paranoid`. Kernel-level counts therefore need
`--stabilize-machine` (and hence `--sudo`), which sets it to 0 at batch start.
Without stabilization, perf counts whatever the host's level allows. Data from
such runs is reduced and should not be compared with stabilized runs (which
differ in other ways anyway). Each run records the host's level in
`run-info.json` (`stabilization.kernel.perf_event_paranoid`).

### What `perf_event_paranoid` restricts

`perf_event_paranoid` doesn't restrict particular event types. It restricts
what kind of measurement an unprivileged user may make: user-space only or
user plus kernel, one process or a whole CPU, and whether tracepoint data is
accessible.

What each level allows a normal user (no root, no CAP_PERFMON):

| `perf_event_paranoid` | Allowed without privileges |
|---|---|
| -1 | everything |
| 0 (set by machine stabilization) | own processes, user + kernel; also whole-CPU (`-a`) counting |
| 1 | own processes, user + kernel; no whole-CPU counting |
| 2 (upstream default since Linux 4.6) | own processes, **user space only** |
| 3 / 4 (Debian/Ubuntu kernel patch) | nothing |

By kind of event:

- **Hardware and raw PMU events** (`cycles`, `instructions`, `cache-misses`,
  `cpu/event=0x3c,umask=0/`): at level 2, perf retries automatically with
  kernel counting excluded and prints a warning. The numbers then leave out
  kernel time. They aren't wrong, but they differ from a full count.
- **Software events** (`task-clock`, `context-switches`, `page-faults`,
  `cpu-migrations`): they follow the same per-process rules; in practice they
  work at level 2 too.
- **Tracepoints** (`sched:*`, `syscalls:*`, ...): in practice they need root.
  perf looks up their IDs in tracefs, which only root can read by default, and
  the raw tracepoint data needs level -1.
- **Whole-CPU events** (uncore, RAPL `power/energy-pkg/`): they need level 0
  or lower. They also can't be attached to a single process, so they don't fit
  `perf-logger.sh -p <pid>`.

### What jbenchrunner does at each level

| Level | Without stabilization | With stabilization |
|---|---|---|
| 1 or lower | full user + kernel counts | full user + kernel counts |
| 2 | `jbr-prepare` warns; perf counts user space only. The fallback warning lands in the perf log and is skipped by post-processing; events carry a `:u` modifier (`cycles_u` instead of `cycles` in the results), which keeps them apart from full counts | full counts (stabilization sets 0) |
| 3 or higher | `jbr-prepare` drops `perf` from the batch with a warning, like any collector the machine cannot run | full counts (stabilization sets 0; the perf check at batch start runs after it) |

After stabilization, `jbr-execute` also warns if `perf_event_paranoid` is
still above 0 while perf is enabled.

### Alternative: CAP_PERFMON on the perf binary

Giving the perf binary CAP_PERFMON (Linux 5.8+) makes the paranoid level
irrelevant for perf only, without lowering it for the rest of the system. The
kernel's perf-security documentation sets it up this way:

```
groupadd perf_users
chgrp perf_users /usr/bin/perf
chmod o-rwx /usr/bin/perf
setcap "cap_perfmon,cap_sys_ptrace,cap_syslog=ep" /usr/bin/perf
```

Only members of `perf_users` can then run perf with those rights. jbenchrunner
doesn't detect this: `jbr-prepare` still drops or warns about perf based on the
paranoid level alone.

Not yet verified on a target host: perf's automatic kernel-excluding retry at
level 2 (and the resulting `:u` event names), and the exact meaning of level 4
on Ubuntu. Check with `perf stat -e cycles -p <pid>` as a normal user.

## Granting exactly these commands: `config/jbenchrunner.sudoers`

`config/jbenchrunner.sudoers` is a sudoers template with one `NOPASSWD` rule
per command of steps 1 to 3, matching the exact command lines the scripts run.
Installation steps are in the file's header. sudo refuses any other argument,
so the rules grant these settings and nothing else (PRD.md D-87).

The rules are granted to the group `jbenchrunners`. They are permanent, not
per batch: every process of every member can use them without a password, the
JVM under test included, e.g. to switch off ASLR or shut the machine down. So
keep the group small, best a dedicated benchmark user that is used for nothing
else; install the rules only on benchmark machines, and leave out the shutdown
rule where nobody uses `--shutdown`.

The scripts and the rules must be changed together: a command the scripts run
differently from the rule fails with "a password is required". The test
`scripts/phase-execute/tests/test_sudoers_template.py` compares both, so the
suite fails on a mismatch.

Two sudoers details matter when editing the rules:

- `*` in an argument also matches spaces and slashes. A rule
  `tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` would therefore
  allow writing any file as root (by passing extra file names to `tee`). The
  per-CPU rules use `[0-9]`, `[0-9][0-9]` and `[0-9][0-9][0-9]` instead,
  which match exactly one character each (CPUs 0-999).
- `,`, `:`, `=` and `\` in an argument must be escaped with `\`
  (`sysctl kernel.randomize_va_space\=0`).

`tee` takes the value to write from stdin, which sudoers cannot restrict. For
the files concerned (`drop_caches`, governor, turbo boost) the worst a caller
can do is pick another setting.

## Summary

All root work is short, fixed commands at fixed points: batch start, before
each run, and the end of the batch, each granted by its own sudoers rule. No
long-lived root process and no open sudo ticket remain.
