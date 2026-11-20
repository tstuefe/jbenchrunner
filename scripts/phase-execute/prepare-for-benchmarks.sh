#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# Stabilizes a Linux machine for benchmarking, to reduce run-to-run noise:
# CPU governor "performance", turbo boost off, (optionally) fixed CPU
# frequency, ASLR off, perf_event_paranoid=0, sync + drop caches, stop
# non-essential services, swap off, strict overcommit (ratio 100%).
# Settings are not reverted afterwards; they last until reboot.
#
# Does what it can: settings the machine does not support (e.g. no cpufreq
# in a VM, no known turbo boost control, no systemd) are skipped with a
# warning. Fails only on errors that should not happen.
#
# Runs as a normal user; only the commands that need root are run via
# "sudo -n", so it requires the rules of config/jbenchrunner.sudoers or an open
# sudo ticket (as reset-machine.sh does). The sudoers rules match these
# commands exactly; change both together. jbr-execute invokes it once at batch
# start when the batch was prepared with --stabilize-machine (spec-execute.md §1.3);
# reset-machine.sh then runs before every run. Can also be run by hand:
#
#   prepare-for-benchmarks.sh [-h|--help]

set -eu -o pipefail
trap 'echo "ERROR: script failed at line $LINENO (exit code $?)" >&2' ERR

usage() {
    cat >&2 <<EOF
Usage: prepare-for-benchmarks.sh [-h|--help]

Stabilizes a Linux machine for benchmarking (CPU governor, turbo boost,
ASLR, caches, services, swap, overcommit). Runs privileged commands via
'sudo -n'; requires the rules of config/jbenchrunner.sudoers, or an open sudo
ticket (run 'sudo -v' first).

Options:
  -h, --help    Show this help message
EOF
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage ;;
        *) echo "prepare-for-benchmarks.sh: unexpected argument: $arg" >&2; usage ;;
    esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This script only works on Linux" >&2
    exit 1
fi

# Works with an open sudo ticket or with the rules of config/jbenchrunner.sudoers;
# 'sudo -l <cmd>' checks one of the commands without running it.
if ! sudo -n -l /usr/sbin/sysctl kernel.perf_event_paranoid=0 > /dev/null 2>&1; then
    echo "This script needs non-interactive sudo: install config/jbenchrunner.sudoers" \
         "and run as a member of group 'jbenchrunners' (effective at the next login)," \
         "or open a sudo ticket first ('sudo -v')" >&2
    exit 1
fi

warn() {
    echo "WARNING: $*" >&2
}

# write a value to a file as root
# usage: sudo_write <value> <file>
sudo_write() {
    echo "$1" | sudo -n tee "$2" > /dev/null
}

# Setting min frequency
# Pros: removes jitter
# Cons: Somewhat unrealistic: CPU runs slower, which distorts the relationship
#       between CPU- and memory speed, under-emphasising the latter.
FIX_CPU_FREQUENCY=0

# Turbo boost increases performance at the cost of (a lot of) thermal jitter. In
# tests I found that effect to be so unpredictable that I want to remove it completely
DISABLE_TURBO_BOOST=1

SET_CPU_GOVERNOR_PERFORMANCE=1

# ASLR disabling:
# Pro: removes per-run jitter stemming from address space randomness
# Cons: Somewhat unrealistic; can taint isolated runs if the dice roll gets us a
#       bad memory layout
DISABLE_ASLR=1

# stop service
stop_if_active() {
    local svc=$1
    if systemctl is-active --quiet "$svc"; then
        echo "Stopping ${svc}..."
        sudo -n systemctl stop "$svc" || warn "failed to stop ${svc}"
    fi
}

# write a value to every existing file of a glob; warn if there is none
# usage: write_all <what> <value> <glob>
write_all() {
    local what=$1 value=$2 glob=$3
    local f found=0
    for f in $glob; do
        [[ -f "$f" ]] || continue
        found=1
        sudo_write "$value" "$f" || warn "failed to write '${value}' to ${f}"
    done
    if [[ $found -eq 0 ]]; then
        warn "${what}: not supported on this machine (no ${glob}), skipped"
    fi
}

if [[ ${SET_CPU_GOVERNOR_PERFORMANCE} -eq 1 ]]; then
    echo "Setting CPU governor..."
    write_all "CPU governor" "performance" "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
fi

if [[ ${DISABLE_TURBO_BOOST} -eq 1 ]]; then
    # if run on an intel CPU
    if [[ -f /sys/devices/system/cpu/intel_pstate/no_turbo ]]; then
        echo "Switching off turbo boost (Intel)..."
        sudo_write 1 /sys/devices/system/cpu/intel_pstate/no_turbo \
            || warn "failed to switch off turbo boost (Intel)"
    # if run on an AMD CPU (or Intel with acpi-cpufreq)
    elif [[ -f /sys/devices/system/cpu/cpufreq/boost ]]; then
        echo "Switching off turbo boost (AMD)..."
        sudo_write 0 /sys/devices/system/cpu/cpufreq/boost \
            || warn "failed to switch off turbo boost (AMD)"
    else
        warn "turbo boost: no known control (neither Intel intel_pstate/no_turbo nor cpufreq/boost), skipped"
    fi
fi

if [[ ${FIX_CPU_FREQUENCY} -eq 1 ]]; then
    if [[ -f /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq ]]; then
        CPU_MIN_FREQ=$(cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq)
        echo "Fixing CPU frequency (${CPU_MIN_FREQ})..."
        write_all "fixing CPU frequency" "${CPU_MIN_FREQ}" "/sys/devices/system/cpu/cpu*/cpufreq/scaling_max_freq"
    else
        warn "fixing CPU frequency: not supported on this machine (no cpufreq), skipped"
    fi
fi

if [[ ${DISABLE_ASLR} -eq 1 ]]; then
    echo "Turning off ASLR..."
    sudo -n sysctl kernel.randomize_va_space=0
fi

# Let unprivileged perf count user + kernel events of a process, and whole
# CPUs (-a); the perf collector runs without root and relies on this for
# kernel-level counts (PRD.md D-85).
sudo -n sysctl kernel.perf_event_paranoid=0

echo "Syncing FS ..."
sync

echo "Dropping caches ..."
sudo_write 3 /proc/sys/vm/drop_caches

# Stop nonessentials
if command -v systemctl > /dev/null; then
    echo "Stopping non-essential services..."
    stop_if_active irqbalance
    stop_if_active bluetooth
    stop_if_active cups
    stop_if_active avahi-daemon.socket
    stop_if_active avahi-daemon
    stop_if_active cron
else
    warn "stopping non-essential services: no systemctl, skipped"
fi

# switch off swap and switch to strict overcommit mode, with limit of 100%
echo "Switching swap off..."
sudo -n swapoff -a
echo "Switching to strict overcommit..."
sudo -n sysctl vm.overcommit_memory=2
sudo -n sysctl vm.overcommit_ratio=100


echo '---  machine stabilized ---'
