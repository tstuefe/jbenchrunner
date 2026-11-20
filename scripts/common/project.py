# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# project.py — project discovery and the per-user active-batch link.
#
# Usage:
#   from common.project import find_project, runs_dir, batch_file, ...
#   project = find_project()          # nearest ancestor of the cwd with jbr-project.json
#
# A project (PRD.md §4) is a directory marked by jbr-project.json. It is
# found by walking up from the current working directory; there is no other
# way to select one. Layout (PRD.md §5):
#
#   <project>/jbr-project.json
#   <project>/BATCH_IN_PROGRESS.json      (while a batch is prepared/active)
#   <project>/batches/<BATCHID>/          (frozen .run.toml copies, batch.json)
#   <project>/results/<RUNID>/
#   <project>/reports/<name>/
#
# The active-batch link (PRD.md §7, spec-execute.md §1.1a) is a symlink
# $XDG_STATE_HOME/jbenchrunner/active-batch (default
# ~/.local/state/jbenchrunner/active-batch) to the absolute path of the
# active batch's BATCH_IN_PROGRESS.json. It holds nothing else.

import json
import os

MARKER = "jbr-project.json"
BATCH_FILENAME = "BATCH_IN_PROGRESS.json"
RUNS_DIRNAME = "results"
BATCHES_DIRNAME = "batches"
REPORTS_DIRNAME = "reports"
DONE_MANIFEST = "batch.json"
ABORTED_MANIFEST = "batch-aborted.json"


class ProjectError(RuntimeError):
    """Raised when no project can be found."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def find_project_or_none(start: str = None):
    """Return the absolute path of the nearest directory at or above *start*
    (default: the current working directory) that contains jbr-project.json,
    or None if there is none."""
    current = os.path.abspath(start if start is not None else os.getcwd())
    while True:
        if os.path.isfile(os.path.join(current, MARKER)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def find_project(start: str = None) -> str:
    """Like find_project_or_none, but raise ProjectError if there is no project."""
    project = find_project_or_none(start)
    if project is None:
        raise ProjectError(
            "not inside a jbr project (no jbr-project.json in the current "
            "directory or any parent). Use 'jbr-init' to create one."
        )
    return project


def runs_dir(project: str) -> str:
    return os.path.join(project, RUNS_DIRNAME)


def batches_dir(project: str) -> str:
    return os.path.join(project, BATCHES_DIRNAME)


def batch_dir(project: str, batchid: str) -> str:
    return os.path.join(project, BATCHES_DIRNAME, batchid)


def batch_file(project: str) -> str:
    return os.path.join(project, BATCH_FILENAME)


def reports_dir(project: str) -> str:
    return os.path.join(project, REPORTS_DIRNAME)


# ---------------------------------------------------------------------------
# Finished batches
# ---------------------------------------------------------------------------

def newest_finished_batch(project: str):
    """Return (path, batch_dict, aborted) of the newest batch.json or
    batch-aborted.json in <project>/batches/, by its started_at; None if
    there is none. Unreadable manifests are ignored."""
    root = batches_dir(project)
    if not os.path.isdir(root):
        return None
    best = None
    for name in os.listdir(root):
        for manifest, aborted in ((DONE_MANIFEST, False), (ABORTED_MANIFEST, True)):
            path = os.path.join(root, name, manifest)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            key = str(data.get("started_at", ""))
            if best is None or key > best[0]:
                best = (key, path, data, aborted)
    if best is None:
        return None
    return best[1], best[2], best[3]


# ---------------------------------------------------------------------------
# active-batch link
# ---------------------------------------------------------------------------

def active_batch_link() -> str:
    """Return the path of the active-batch link (it need not exist)."""
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(state_home, "jbenchrunner", "active-batch")


def read_active_batch():
    """Return the link's target path, or None if there is no link."""
    link = active_batch_link()
    if not os.path.islink(link):
        return None
    return os.readlink(link)


def _same_path(a: str, b: str) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


def active_batch_conflict(project: str):
    """Return an error message if the active-batch link belongs to another
    project or is dangling; None if there is no link or it points to this
    project's batch file."""
    target = read_active_batch()
    if target is None:
        return None
    if not os.path.exists(target):
        return (
            f"the active-batch link {active_batch_link()} points to {target}, "
            "which no longer exists (project moved or deleted?). "
            "Remove the link, or run 'jbr-execute --abort'."
        )
    if _same_path(target, batch_file(project)):
        return None
    return (
        f"another project has an active batch: {target}. "
        "Complete it, or abandon it with 'jbr-execute --abort' in that project."
    )


def set_active_batch(project: str) -> None:
    """Point the active-batch link at this project's batch file. A link that
    already points there is left as is."""
    link = active_batch_link()
    target = os.path.abspath(batch_file(project))
    current = read_active_batch()
    if current is not None and _same_path(current, target):
        return
    os.makedirs(os.path.dirname(link), exist_ok=True)
    tmp = link + ".tmp"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)


def clear_active_batch(project: str) -> bool:
    """Remove the active-batch link if it points to this project's batch file
    or is dangling. Return True if a link was removed."""
    target = read_active_batch()
    if target is None:
        return False
    if os.path.exists(target) and not _same_path(target, batch_file(project)):
        return False
    os.remove(active_batch_link())
    return True
