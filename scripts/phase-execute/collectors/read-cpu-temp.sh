#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# Reads the current CPU package temperature and prints it in milli-centigrade to stdout.
#
# Intel:  reads /sys/class/thermal/thermal_zone* with type "x86_pkg_temp"
# ARM64:  reads /sys/class/thermal/thermal_zone* with type "cpu-thermal"
# AMD:    parses 'sensors', k10temp section, Tctl entry

set -eu -o pipefail

trap 'echo "Error on line ${LINENO}: ${BASH_COMMAND} (exit code $?)" >&2' ERR

usage() {
    cat >&2 <<EOF
Usage: read-cpu-temp.sh [-h|--help]

Reads the current CPU package temperature and prints it in milli-centigrade to stdout.
Supports Intel (x86_pkg_temp), ARM64 (cpu-thermal), and AMD (k10temp via sensors).

Options:
  -h, --help    Show this help message
EOF
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage ;;
        *) echo "read-cpu-temp.sh: unexpected argument: $arg" >&2; usage ;;
    esac
done

_read_thermal_zone() {
    local zone_name="${1:?_read_thermal_zone: need zone name}"
    for zone in /sys/class/thermal/thermal_zone*; do
        local type
        type=$(cat "${zone}/type" 2>/dev/null) || continue
        if [[ "${type}" == "${zone_name}" ]]; then
            cat "${zone}/temp"
            return 0
        fi
    done
    echo "Error: thermal zone '${zone_name}' not found" >&2
    return 1
}

ARCH=$(uname -m)

if [[ "${ARCH}" == "aarch64" ]]; then
    _read_thermal_zone "cpu-thermal"

elif [[ "${ARCH}" == "x86_64" ]]; then
    VENDOR=$(grep -m1 "vendor_id" /proc/cpuinfo | awk '{print $3}')
    if [[ "${VENDOR}" == "GenuineIntel" ]]; then
        _read_thermal_zone "x86_pkg_temp"
    elif [[ "${VENDOR}" == "AuthenticAMD" ]]; then
        if ! command -v sensors &>/dev/null; then
            echo "Error: 'sensors' not found (install lm-sensors)" >&2
            exit 1
        fi
        result=$(sensors | awk '
            /^k10temp/       { in_section = 1 }
            in_section && /^Tctl:/ {
                if (match($0, /[+-]?[0-9]+\.[0-9]+/)) {
                    printf "%d\n", substr($0, RSTART, RLENGTH) * 1000
                }
                exit
            }
        ')
        if [[ -z "${result}" ]]; then
            echo "Error: k10temp/Tctl not found in sensors output" >&2
            exit 1
        fi
        echo "${result}"
    else
        echo "Error: unrecognised x86_64 CPU vendor: ${VENDOR:-unknown}" >&2
        exit 1
    fi

else
    echo "Error: unsupported architecture: ${ARCH}" >&2
    exit 1
fi
