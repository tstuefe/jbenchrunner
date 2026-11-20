#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# perf-logger.sh - perf stat collector (docs/spec-execute.md §5.1).
# Runs 'perf stat' in CSV mode (-x ';') on a process at a fixed interval and
# streams its output to stdout. Without --counters, perf counts its default
# event set. Started by jbr-execute; see usage() for the invocation.
#
# Runs perf as the invoking user, never via sudo (PRD.md D-85): what perf can
# count depends on kernel.perf_event_paranoid (machine stabilization sets 0,
# which includes kernel-level counts; at 2 perf falls back to user space only).
#
# --check runs the same perf command once against /usr/bin/true instead of a
# process; jbr-execute uses it at batch start to test the batch's command line
# (PRD.md D-86).

set -eu -o pipefail

trap 'echo "perf-logger.sh: error on line $LINENO (exit code $?)" >&2' ERR

usage() {
    cat >&2 <<EOF
Usage: perf-logger.sh [OPTIONS] <pid>
       perf-logger.sh --check [--counters COUNTERS]

Runs 'perf stat' in CSV mode (-x ';') on a process and streams its output to stdout.
With --check, runs the same perf command once on 'true' instead (no pid, no
interval), prints perf's output to stdout and exits with perf's exit code.

Positional Arguments:
  <pid>                          Process ID to monitor

Options:
  -i, --interval SECONDS         Interval between samples in seconds (required)
  -n, --num COUNT                Maximum number of intervals (optional; default: unlimited)
  --counters COUNTERS            Comma-delimited list of perf counters (optional; default:
                                 perf's default event set, i.e. no -e)
  --check                        Check the perf command line instead of monitoring a process
  -h, --help                     Show this help message

Example:
  perf-logger.sh -i 10 12345
  perf-logger.sh -i 10 --counters cycles,instructions,branches 12345
  perf-logger.sh -i 5 -n 20 --counters cycles,L1-dcache-loads 9999
  perf-logger.sh --check --counters cycles,instructions
EOF
    exit 1
}

pid=""
interval=""
num=""
counters=""
check=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            ;;
        -i|--interval)
            interval="$2"
            shift 2
            ;;
        -n|--num)
            num="$2"
            shift 2
            ;;
        --counters)
            counters="$2"
            shift 2
            ;;
        --check)
            check=1
            shift
            ;;
        -*)
            echo "perf-logger.sh: unknown option: $1" >&2
            usage
            ;;
        *)
            if [[ -z "$pid" ]]; then
                pid="$1"
                shift
            else
                echo "perf-logger.sh: unexpected argument: $1" >&2
                usage
            fi
            ;;
    esac
done

# ';' as CSV separator: event names may contain commas (cpu/event=0x3c,umask=0/).
perf_cmd=("perf" "stat" "-x" ";")
if [[ -n "$counters" ]]; then
    perf_cmd+=("-e" "$counters")
fi

# --check: the same command, counting 'true' once (no pid, no interval)
if [[ "$check" -eq 1 ]]; then
    if [[ -n "$pid" || -n "$interval" || -n "$num" ]]; then
        echo "perf-logger.sh: --check takes no pid, --interval or --num" >&2
        usage
    fi
    exec "${perf_cmd[@]}" -- true 2>&1
fi

# Validate required arguments
if [[ -z "$pid" ]]; then
    echo "perf-logger.sh: missing required argument: pid" >&2
    usage
fi

if [[ -z "$interval" ]]; then
    echo "perf-logger.sh: missing required option: --interval" >&2
    usage
fi

# Validate pid is a positive integer
if ! [[ "$pid" =~ ^[0-9]+$ ]] || [[ "$pid" -le 0 ]]; then
    echo "perf-logger.sh: invalid pid: $pid" >&2
    exit 1
fi

# Validate interval is a non-negative number
if ! [[ "$interval" =~ ^[0-9]+(\.[0-9]+)?$ ]] || (( $(echo "$interval < 0" | bc -l) )); then
    echo "perf-logger.sh: invalid interval: $interval" >&2
    exit 1
fi

# Validate num if provided
if [[ -n "$num" ]]; then
    if ! [[ "$num" =~ ^[0-9]+$ ]] || [[ "$num" -le 0 ]]; then
        echo "perf-logger.sh: invalid num: $num" >&2
        exit 1
    fi
fi

# Build perf command
# perf does not accept 0 for --interval-print, so use 1ms minimum
interval_ms=$( printf "%.0f" "$(echo "$interval * 1000" | bc -l)" )
if [[ "$interval_ms" -eq 0 ]]; then
    interval_ms=1
fi

perf_cmd+=("--interval-print" "$interval_ms" "-p" "$pid")

if [[ -n "$num" ]]; then
    perf_cmd+=("--interval-count" "$num")
fi

# Print current local datetime in ISO format before starting perf
date +%Y-%m-%dT%H:%M:%S.%3N%:z

# Print pid we monitor
echo "Monitoring PID $pid"

# Execute perf; perf stat writes measurements to stderr, so redirect them to
# stdout for the raw perf log. Capture its exit code without triggering set -e.
"${perf_cmd[@]}" 2>&1 || exit_code=$?
exit_code=${exit_code:-0}

# A non-zero exit from perf may simply mean the monitored process ended while
# perf was running.  Only treat it as a real error if the process is still alive.
if [[ "$exit_code" -ne 0 ]]; then
    if [[ -e "/proc/$pid" ]]; then
        echo "perf-logger.sh: perf exited with code $exit_code and process $pid is still alive" >&2
        exit "$exit_code"
    fi
    # Process is gone — normal termination; exit cleanly
    exit 0
fi
