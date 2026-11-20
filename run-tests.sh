#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# run-tests.sh
# Runs the full test suite.
#
# Usage: run-tests.sh [--help|-h] [--parallel] [--verbose|-v] [--duration]

set -eu -o pipefail

trap 'echo "run-tests.sh: error on line $LINENO (exit code $?)" >&2' ERR

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

usage() {
    cat >&2 <<EOF
Usage: run-tests.sh [OPTIONS]

Runs the full test suite via Python unittest discover.

Options:
  --parallel     Run one 'unittest discover' process per scripts/ subdirectory
                 concurrently, instead of a single combined run. Each
                 subdirectory's output is captured to its own temp file and
                 printed, in order, once all have finished. Faster, but a
                 failure seen only with --parallel may be cross-test
                 interference rather than a real bug - rerun without
                 --parallel to check before chasing it as one.
  --verbose, -v  Verbose output (passed through to unittest as -v)
  --duration     Print every test's duration, slowest first (passed through
                 to unittest as --durations 0)
  -h, --help     Show this help message
EOF
    exit 1
}

parallel=false
verbose=false
duration=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --parallel)
            parallel=true
            shift
            ;;
        --verbose|-v)
            verbose=true
            shift
            ;;
        --duration)
            duration=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "run-tests.sh: unknown option: $1" >&2
            usage
            ;;
    esac
done

extra_opts=()
"$verbose" && extra_opts+=("-v")
"$duration" && extra_opts+=("--durations" "0")

cd "$SCRIPT_DIR"

if ! "$parallel"; then
    python3 -m unittest discover scripts --buffer "${extra_opts[@]+"${extra_opts[@]}"}"
    exit 0
fi

# --parallel: one 'unittest discover' process per scripts/ subdirectory, run
# concurrently (each is its own process, so per-process state like cwd can't
# race between them). '-t scripts' keeps every subdirectory's dotted test
# module names rooted the same way the combined run above would name them,
# so e.g. phase-execute/tests and phase-execute/collectors/tests can't collide.
tmpfiles=()
pids=()
dirs=()

cleanup() {
    rm -f "${tmpfiles[@]+"${tmpfiles[@]}"}"
}
trap cleanup EXIT

for dir in scripts/*/; do
    dir=${dir%/}
    # Skip non-package subdirectories (e.g. scripts/helpers/, no __init__.py):
    # 'unittest discover' requires its start directory to be importable, even
    # though it silently skips such directories when found via recursion.
    [[ -f "$dir/__init__.py" ]] || continue
    tmpfile=$(mktemp)
    tmpfiles+=("$tmpfile")
    dirs+=("$dir")
    python3 -m unittest discover -s "$dir" -t scripts --buffer "${extra_opts[@]+"${extra_opts[@]}"}" >"$tmpfile" 2>&1 &
    pids+=("$!")
done

failed=0
for pid in "${pids[@]}"; do
    wait "$pid" || failed=1
done

for i in "${!dirs[@]}"; do
    echo "=== ${dirs[$i]} ==="
    cat "${tmpfiles[$i]}"
    echo
done

exit "$failed"

