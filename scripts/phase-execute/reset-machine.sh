#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# Resets machine state before a benchmark run, to reduce cross-run noise:
#   1. sync                          - flush dirty pages to disk
#   2. drop_caches                   - discard page cache / dentries / inodes
#   3. restart systemd-journald      - reset its accumulated in-memory state
#
# Runs its root commands via 'sudo -n' and never prompts for a password: it
# needs the rules of config/jbenchrunner.sudoers (which match these commands
# exactly; change both together), or an open sudo ticket.

set -eu -o pipefail

trap 'echo "Error on line ${LINENO}: ${BASH_COMMAND} (exit code $?)" >&2' ERR

usage() {
    cat >&2 <<EOF
Usage: reset-machine.sh [-h|--help]

Resets machine state before a benchmark run (sync, drop_caches, restart
systemd-journald). Requires the rules of config/jbenchrunner.sudoers, or an
open sudo ticket.

Options:
  -h, --help    Show this help message
EOF
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage ;;
        *) echo "reset-machine.sh: unexpected argument: $arg" >&2; usage ;;
    esac
done

sync

echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null

sudo -n systemctl restart systemd-journald
