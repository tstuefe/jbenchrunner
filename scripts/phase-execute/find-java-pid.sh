#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

set -eu -o pipefail

for arg in "$@"; do
    if [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
        echo "Usage: find-java-pid.sh <pid>"
        echo ""
        echo "Given a pid <pid>, search for the first process named "java" that is a child process of <pid>."
        echo "If found, prints it to stdout"
        exit 1;
    fi
done

trap 'echo "Error on line ${LINENO}: ${BASH_COMMAND} (exit code $?)" >&2' ERR

# Technique: instead of recursing down from root (expensive for wide trees, e.g. PID 1),
# we enumerate all "java" processes via pgrep, then walk each one's parent chain upward
# until we either find root-pid (success) or reach PID 1 (not a descendant).
ROOT_PID=${1:?find_java_pid: need root pid}

CANDIDATES=$(pgrep -x java 2>/dev/null) || true
[[ -z "${CANDIDATES}" ]] && return

for PID in ${CANDIDATES}; do
    # ROOT_PID itself may be java
    if [[ "${PID}" == "${ROOT_PID}" ]]; then
        echo "${PID}"
        exit 0
    fi
    # Walk the parent chain of this candidate upward looking for ROOT_PID
    CUR=$(ps -o ppid= -p "${PID}" 2>/dev/null | tr -d '[:space:]')
    while [[ -n "${CUR}" && "${CUR}" != "0" ]]; do
        if [[ "${CUR}" == "${ROOT_PID}" ]]; then
            echo "${PID}"
            exit 0
        fi
        [[ "${CUR}" == "1" ]] && break
        CUR=$(ps -o ppid= -p "${CUR}" 2>/dev/null | tr -d '[:space:]')
    done
done

exit 1

