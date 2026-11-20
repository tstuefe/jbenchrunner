#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

set -eu -o pipefail

trap 'echo "pidstat-logger.sh: error on line $LINENO (exit code $?)" >&2' ERR

usage() {
    cat >&2 <<EOF
Usage: pidstat-logger.sh [OPTIONS] <pid>

Runs 'pidstat -h -H -rdu' with its built-in repeat interval on a process and
streams output to stdout.

Positional Arguments:
  <pid>                          Process ID to monitor

Options:
  -i, --interval SECONDS         Interval between samples, in whole seconds (required)
  -n, --num COUNT                Maximum number of intervals (optional; default: unlimited)
  -h, --help                     Show this help message

Example:
  pidstat-logger.sh -i 10 12345
  pidstat-logger.sh -i 5 -n 20 9999
EOF
    exit 1
}

pid=""
interval=""
num=""

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
        -*)
            echo "pidstat-logger.sh: unknown option: $1" >&2
            usage
            ;;
        *)
            if [[ -z "$pid" ]]; then
                pid="$1"
                shift
            else
                echo "pidstat-logger.sh: unexpected argument: $1" >&2
                usage
            fi
            ;;
    esac
done

# Validate required arguments
if [[ -z "$pid" ]]; then
    echo "pidstat-logger.sh: missing required argument: pid" >&2
    usage
fi

if [[ -z "$interval" ]]; then
    echo "pidstat-logger.sh: missing required option: --interval" >&2
    usage
fi

# Validate pid is a positive integer
if ! [[ "$pid" =~ ^[0-9]+$ ]] || [[ "$pid" -le 0 ]]; then
    echo "pidstat-logger.sh: invalid pid: $pid" >&2
    exit 1
fi

# pidstat's repeat interval is whole seconds only — no fractional or zero support.
if ! [[ "$interval" =~ ^[0-9]+$ ]] || [[ "$interval" -le 0 ]]; then
    echo "pidstat-logger.sh: invalid interval: $interval" >&2
    exit 1
fi

# Validate num if provided
if [[ -n "$num" ]]; then
    if ! [[ "$num" =~ ^[0-9]+$ ]] || [[ "$num" -le 0 ]]; then
        echo "pidstat-logger.sh: invalid num: $num" >&2
        exit 1
    fi
fi

if [[ ! -e "/proc/$pid" ]]; then
    echo "pidstat-logger.sh: process $pid does not exist" >&2
    exit 1
fi

# Build pidstat command.
# -h : flatten the CPU/memory/I/O sections into one line per sample (sysstat >= 8.1.6)
# -H : timestamps in seconds since the epoch instead of local HH:MM:SS (sysstat >= 11.6.0)
pidstat_cmd=("pidstat" "-h" "-H" "-rdu" "-p" "$pid" "$interval")
if [[ -n "$num" ]]; then
    pidstat_cmd+=("$num")
fi

# Print current local datetime in ISO format before starting pidstat
date +%Y-%m-%dT%H:%M:%S.%3N%:z

# Print pid we monitor
echo "Monitoring PID $pid"

# Execute pidstat; unlike 'perf stat', pidstat writes its report to stdout, so
# no stream redirection is needed. Capture its exit code without triggering set -e.
"${pidstat_cmd[@]}" || exit_code=$?
exit_code=${exit_code:-0}

# A non-zero exit from pidstat may simply mean the monitored process ended
# while pidstat was running.  Only treat it as a real error if the process is
# still alive.
if [[ "$exit_code" -ne 0 ]]; then
    if [[ -e "/proc/$pid" ]]; then
        echo "pidstat-logger.sh: pidstat exited with code $exit_code and process $pid is still alive" >&2
        exit "$exit_code"
    fi
    # Process is gone — normal termination; exit cleanly
    exit 0
fi
