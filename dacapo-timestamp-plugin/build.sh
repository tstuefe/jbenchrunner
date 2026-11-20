#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# build.sh - rebuilds dacapoExtension.jar, the DaCapo timestamp plugin, with
# Maven. Only needed after changing the plugin: the built jar ships with
# jbenchrunner. Requires 'mvn' (and a JDK) on PATH. Works from any directory.
#
# Usage: build.sh [-h|--help]

set -eu -o pipefail

trap 'echo "build.sh: error on line $LINENO (exit code $?)" >&2' ERR

usage() {
    cat >&2 <<EOF
Usage: build.sh [-h|--help]

Rebuilds dacapoExtension.jar (the DaCapo timestamp plugin) with Maven,
next to this script. Requires 'mvn' and a JDK on PATH.

Options:
  -h, --help    Show this help message
EOF
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage ;;
        *) echo "build.sh: unexpected argument: $arg" >&2; usage ;;
    esac
done

if ! command -v mvn > /dev/null; then
    echo "build.sh: 'mvn' not found on PATH" >&2
    exit 1
fi

cd "$(dirname "$0")"
mvn clean package
cp ./target/dacapoExtension-1.0-SNAPSHOT.jar ./dacapoExtension.jar
