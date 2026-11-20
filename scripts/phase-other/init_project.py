# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# init_project.py — core logic for jbr-init (docs/spec-other.md §5).
#
# Creates a project (docs/PRD.md §4) by writing its marker file
# jbr-project.json into a directory, which is created if missing. With
# example_run set (jbr-init does, jbr-move-batch does not), a copy of that
# file is placed next to it as example.run.toml. Nothing else is created:
# batches/, results/ and reports/ appear on first use.

import json
import os
import shutil

from project import MARKER, find_project_or_none


EXAMPLE_RUN = "example.run.toml"


def init_project(directory: str, description: str = None,
                 example_run: str = None) -> str:
    """Create a project in *directory* and return its absolute path.

    If *example_run* is given, copy that file into the project as
    example.run.toml (an existing file of that name is left alone).

    Raises RuntimeError if the directory already is a project, lies inside
    another project, exists but is not a directory, or cannot be created.
    Nothing is written in those cases.
    """
    target = os.path.abspath(directory)
    marker = os.path.join(target, MARKER)

    if os.path.exists(target) and not os.path.isdir(target):
        raise RuntimeError(f"{target} exists and is not a directory")
    if os.path.exists(marker):
        raise RuntimeError(f"{target} already is a project ({MARKER} exists)")
    enclosing = find_project_or_none(os.path.dirname(target))
    if enclosing is not None:
        raise RuntimeError(
            f"{target} lies inside the project {enclosing}; projects cannot be nested"
        )

    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"cannot create {target}: {exc}") from exc

    data = {} if description is None else {"description": description}
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    if example_run is not None:
        dest = os.path.join(target, EXAMPLE_RUN)
        if not os.path.exists(dest):
            shutil.copyfile(example_run, dest)
    return target
