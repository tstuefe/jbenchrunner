#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# Starts all run-phase collectors for a target PID, writes their logs to the
# current directory, and exits after the target process and all collectors stop.
# Usage: run-all-collectors.sh <pid>
# This is just for playing around, and testing

set -eu -o pipefail

trap 'echo "run-all-collectors.sh: error on line $LINENO (exit code $?)" >&2' ERR

usage() {
    cat >&2 <<EOF
Usage: run-all-collectors.sh <pid>

Starts all collectors for the specified process using a 10-second interval.
Collector output is written to the current directory as temps.jsonl,
pidstat.log, proclog.log, and perflog.log, with matching .err files.
The script waits for the target process to finish; every collector, including
the temperature logger, exits on its own once the process is gone.
EOF
    exit 1
}

if [[ $# -eq 1 ]] && [[ "$1" == "--help" || "$1" == "-h" ]]; then
    usage
fi

if [[ $# -ne 1 ]]; then
    usage
fi

pid="$1"
if ! [[ "$pid" =~ ^[0-9]+$ ]] || [[ "$pid" -le 0 ]]; then
    echo "run-all-collectors.sh: invalid pid: $pid" >&2
    exit 1
fi

if [[ ! -d "/proc/$pid" ]]; then
    echo "run-all-collectors.sh: process $pid does not exist" >&2
    exit 1
fi

script_dir=$(cd "$(dirname "$0")" && pwd)
interval=10

python3 "$script_dir/cpu-temp-logger.py" -i "$interval" "$pid" >temps.jsonl 2>temps.err &
temps_pid=$!
bash "$script_dir/pidstat-logger.sh" -i "$interval" "$pid" >pidstat.log 2>pidstat.err &
python3 "$script_dir/proc-logger.py" -i "$interval" "$pid" >proclog.log 2>proclog.err &
bash "$script_dir/perf-logger.sh" -i "$interval" --counters cycles,instructions "$pid" >perflog.log 2>perflog.err &

while [[ -d "/proc/$pid" ]]; do
    sleep 1
done

wait "$temps_pid" 2>/dev/null || true
