
## Addendum A — External Name Translation Tables

Canonical mappings for each external source. These tables are the normative reference; the
collector scripts implement them as static mapping tables (see [spec-execute.md §5](spec-execute.md#5-data-sampling)).

### A.1 `pidstat -h -H -rdu` column headers

`-h` flattens the normally-separate CPU/memory/I/O sections into one line per sample, with a
single header printed up front; `-H` prints the `Time` column as seconds since the epoch instead
of local `HH:MM:SS`. `UID`, `PID`, and `Command` are intentionally not translated: `PID`
duplicates the top-level `pid` field already present in every JSON record, `Time` becomes that
record's `timestamp` (see `post-process-pidstat-log.py`), and `UID`/`Command` are dropped as
carrying no benchmarking value.

| External name | JSON field name |
|---|---|
| `%usr` | `pct_usr` |
| `%system` | `pct_system` |
| `%guest` | `pct_guest` |
| `%wait` | `pct_wait` |
| `%CPU` | `pct_cpu` |
| `CPU` | `cpu` |
| `minflt/s` | `minflt_per_sec` |
| `majflt/s` | `majflt_per_sec` |
| `VSZ` | `vsz` |
| `RSS` | `rss` |
| `%MEM` | `pct_mem` |
| `kB_rd/s` | `kb_rd_per_sec` |
| `kB_wr/s` | `kb_wr_per_sec` |
| `kB_ccwr/s` | `kb_ccwr_per_sec` |
| `iodelay` | `iodelay` |

### A.2 `/proc/<pid>/stat` field names

Field names from `proc(5)` are already `snake_case` and require no translation. They are stored verbatim.

### A.3 `/proc/<pid>/status` key names

| External name | JSON field name |
|---|---|
| `Name` | `name` |
| `Umask` | `umask` |
| `State` | `state` |
| `Tgid` | `tgid` |
| `Ngid` | `ngid` |
| `Pid` | `pid` |
| `PPid` | `ppid` |
| `TracerPid` | `tracer_pid` |
| `Uid` | `uid` |
| `Gid` | `gid` |
| `FDSize` | `fd_size` |
| `Groups` | `groups` |
| `NStgid` | `nstgid` |
| `NSpid` | `nspid` |
| `NSpgid` | `nspgid` |
| `NSsid` | `nssid` |
| `Kthread` | `kthread` |
| `VmPeak` | `vm_peak` |
| `VmSize` | `vm_size` |
| `VmLck` | `vm_lck` |
| `VmPin` | `vm_pin` |
| `VmHWM` | `vm_hwm` |
| `VmRSS` | `vm_rss` |
| `RssAnon` | `rss_anon` |
| `RssFile` | `rss_file` |
| `RssShmem` | `rss_shmem` |
| `VmData` | `vm_data` |
| `VmStk` | `vm_stk` |
| `VmExe` | `vm_exe` |
| `VmLib` | `vm_lib` |
| `VmPTE` | `vm_pte` |
| `VmSwap` | `vm_swap` |
| `HugetlbPages` | `hugetlb_pages` |
| `CoreDumping` | `core_dumping` |
| `THP_enabled` | `thp_enabled` |
| `untag_mask` | `untag_mask` |
| `Threads` | `threads` |
| `SigQ` | `sigq` |
| `SigPnd` | `sig_pnd` |
| `ShdPnd` | `shd_pnd` |
| `SigBlk` | `sig_blk` |
| `SigIgn` | `sig_ign` |
| `SigCgt` | `sig_cgt` |
| `CapInh` | `cap_inh` |
| `CapPrm` | `cap_prm` |
| `CapEff` | `cap_eff` |
| `CapBnd` | `cap_bnd` |
| `CapAmb` | `cap_amb` |
| `NoNewPrivs` | `no_new_privs` |
| `Seccomp` | `seccomp` |
| `Seccomp_filters` | `seccomp_filters` |
| `Cpus_allowed` | `cpus_allowed` |
| `Cpus_allowed_list` | `cpus_allowed_list` |
| `Mems_allowed` | `mems_allowed` |
| `Mems_allowed_list` | `mems_allowed_list` |
| `voluntary_ctxt_switches` | `voluntary_ctxt_switches` |
| `nonvoluntary_ctxt_switches` | `nonvoluntary_ctxt_switches` |

A mapping value of `null` means the key is explicitly ignored — it is silently dropped from
the JSON record without warning. Any key not present in the table at all is silently dropped,
but a warning is printed to stderr the **first time** each such key is seen (not repeated on
every sample).
