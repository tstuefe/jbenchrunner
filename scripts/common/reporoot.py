# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# reporoot.py — walk-up resolution of the jbenchrunner repository root.
#
# Usage:
#   from scripts.common.reporoot import find_reporoot
#   root = find_reporoot(__file__)
#
# The repository root is defined as the nearest ancestor directory that contains
# both a config/ and a libexec/ directory.  Scripts must never accept the repo root as an
# argument or environment variable; they always derive it at runtime.

import os


def find_reporoot(start_path: str) -> str:
    """Walk up the directory tree from start_path until a directory containing
    both config/ and libexec/ is found, then return its absolute path.

    Args:
        start_path: A file or directory path to start the search from.
                    Typically pass __file__ from the calling script.

    Returns:
        The absolute path of the repository root directory.

    Raises:
        RuntimeError: If no directory containing config/ and libexec/ is
                      found before reaching the filesystem root.
    """
    current = os.path.abspath(start_path)
    if os.path.isfile(current):
        current = os.path.dirname(current)

    while True:
        if all(os.path.isdir(os.path.join(current, d)) for d in ("config", "libexec")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError(
                "Could not find repository root: no ancestor directory contains "
                "config/ and libexec/ (started from: {})".format(start_path)
            )
        current = parent
