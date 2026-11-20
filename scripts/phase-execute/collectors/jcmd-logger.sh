#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# scripts/phase-execute/collectors/jcmd-logger.sh
# Periodically runs `<java_home>/bin/jcmd <pid> <subcommand>` and streams an
# ISO-8601 timestamp plus the raw jcmd output to stdout. Generic across any
# jcmd diagnostic subcommand (e.g. "VM.native_memory summary",
# "VM.info", "Thread.print") — the harness supplies the concrete subcommand
# via --cmd; this script has no knowledge of what it runs.
# Invoked as: jcmd-logger.sh -i SECONDS [-n COUNT] --java-home <path> --cmd "<subcommand>" <pid>

set -eu -o pipefail

trap 'echo "jcmd-logger.sh: error on line $LINENO (exit code $?)" >&2' ERR

usage() {
    cat >&2 <<EOF
Usage: jcmd-logger.sh [OPTIONS] <pid>

Periodically runs '<java_home>/bin/jcmd <pid> <subcommand>' and streams an
ISO-8601 timestamp plus the raw output to stdout.

Positional Arguments:
  <pid>                          Process ID to monitor

Options:
  -i, --interval SECONDS         Interval between samples, in seconds (required)
  -n, --num COUNT                Maximum number of samples (optional; default: unlimited)
      --java-home PATH           JDK installation directory; jcmd is run as <PATH>/bin/jcmd (required)
      --cmd SUBCOMMAND           jcmd subcommand to run, e.g. "VM.native_memory summary" (required)
  -h, --help                     Show this help message

Example:
  jcmd-logger.sh -i 10 --java-home /opt/jdk-21 --cmd "VM.native_memory summary" 12345
EOF
    exit 1
}

pid=""
interval=""
num=""
java_home=""
cmd=""

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
        --java-home)
            java_home="$2"
            shift 2
            ;;
        --cmd)
            cmd="$2"
            shift 2
            ;;
        -*)
            echo "jcmd-logger.sh: unknown option: $1" >&2
            usage
            ;;
        *)
            if [[ -z "$pid" ]]; then
                pid="$1"
                shift
            else
                echo "jcmd-logger.sh: unexpected argument: $1" >&2
                usage
            fi
            ;;
    esac
done

# Validate required arguments
if [[ -z "$pid" ]]; then
    echo "jcmd-logger.sh: missing required argument: pid" >&2
    usage
fi

if [[ -z "$interval" ]]; then
    echo "jcmd-logger.sh: missing required option: --interval" >&2
    usage
fi

if [[ -z "$java_home" ]]; then
    echo "jcmd-logger.sh: missing required option: --java-home" >&2
    usage
fi

if [[ -z "$cmd" ]]; then
    echo "jcmd-logger.sh: missing required option: --cmd" >&2
    usage
fi

# Validate pid is a positive integer
if ! [[ "$pid" =~ ^[0-9]+$ ]] || [[ "$pid" -le 0 ]]; then
    echo "jcmd-logger.sh: invalid pid: $pid" >&2
    exit 1
fi

# Unlike pidstat/perf (which delegate their repeat interval to the wrapped
# tool), this script drives its own loop, so 0 is a valid interval — paired
# with -n 1 it takes a single immediate sample and stops (AGENTS.md Collectors).
if ! [[ "$interval" =~ ^[0-9]+$ ]]; then
    echo "jcmd-logger.sh: invalid interval: $interval" >&2
    exit 1
fi

if [[ -n "$num" ]]; then
    if ! [[ "$num" =~ ^[0-9]+$ ]] || [[ "$num" -le 0 ]]; then
        echo "jcmd-logger.sh: invalid num: $num" >&2
        exit 1
    fi
fi

jcmd_bin="$java_home/bin/jcmd"
if [[ ! -x "$jcmd_bin" ]]; then
    echo "jcmd-logger.sh: jcmd binary not found or not executable: $jcmd_bin" >&2
    exit 1
fi

if [[ ! -e "/proc/$pid" ]]; then
    echo "jcmd-logger.sh: process $pid does not exist" >&2
    exit 1
fi

iteration=0
while [[ -e "/proc/$pid" ]] && { [[ -z "$num" ]] || [[ "$iteration" -lt "$num" ]]; }; do
    date +%Y-%m-%dT%H:%M:%S.%3N%:z

    # A non-zero exit here usually just means the monitored process ended
    # between the alive-check above and this invocation; only surface it as
    # an error (to stderr, without killing the loop) if the process is in
    # fact still there — an occasional failed sample must not take down the
    # whole collector for the rest of the run.
    exit_code=0
    "$jcmd_bin" "$pid" $cmd || exit_code=$?
    if [[ "$exit_code" -ne 0 ]] && [[ -e "/proc/$pid" ]]; then
        echo "jcmd-logger.sh: jcmd exited with code $exit_code" >&2
    fi

    iteration=$((iteration + 1))
    if [[ -z "$num" ]] || [[ "$iteration" -lt "$num" ]]; then
        # Sleep in 1-second slices so a vanished pid ends the loop within about
        # a second, however large the interval is (PRD.md D-59).
        for ((s = 0; s < interval; s++)); do
            sleep 1
            [[ -e "/proc/$pid" ]] || break
        done
    fi
done

if [[ ! -e "/proc/$pid" ]]; then
    echo "jcmd-logger.sh: process $pid no longer exists, exiting" >&2
fi
