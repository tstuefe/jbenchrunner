#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# Usage: print-link.sh <target> <text>

set -eu -o pipefail

# Function to safely print clickable links
print_link() {
    local target="$1"
    local text="$2"

    # Check if VTE_VERSION is set (GNOME Terminal, Tilix, etc.)
    # or TERM_PROGRAM indicates iTerm2, both of which support OSC 8
    # clickable hyperlinks, and TERM isn't "dumb".
    if { [ -n "${VTE_VERSION-}" ] || [ "${TERM_PROGRAM-}" = "iTerm.app" ]; } && [ "${TERM-}" != "dumb" ]; then
        # Output the clickable hyperlink
        printf '\e]8;;file://%s\e\\%s\e]8;;\e\\\n' "$target" "$text"
    else
        # Fallback for dumb terminals: print plain text with the raw path
        printf '%s (%s)\n' "$text" "$target"
    fi
}


print_link "$1" "$2"


