# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# retag.py — core logic for jbr-retag (docs/spec-other.md §1).
#
# jbr-retag adds/removes tags on the info.json of existing run result
# directories, or lists their current tags. Unlike the phase-analyze tools
# it is not read-only, so this module provides the mutation primitives:
# tag-value validation, the in-progress-batch guard, applying add/remove
# operations to a tag list, and an atomic info.json writer.

import json
import os
import sys
import tempfile


def resolve_runids(positional_args: list, stdin_is_tty: bool) -> list:
    """Resolve the RUNID list from positional args or piped stdin (spec §1.1).

    Two-tier resolution — unlike jbr-filt/jbr-ls's three-tier
    ``discover_run_dirs``, there is no "scan all runs of the project" fallback,
    since jbr-retag mutates data and an omitted RUNID list must never
    silently retarget every run in the repository:

      1. positional_args, if non-empty.
      2. stdin, one RUNID per line, if stdin is not a terminal (may be
         empty, e.g. an upstream filter matched nothing — that's fine,
         there's just nothing to do).

    Raises ValueError if neither source is available (no positional args
    and stdin is a terminal) — this is the case that must be a hard
    error rather than silently defaulting to "all runs of the project".
    Also raises ValueError for any RUNID that is not a bare directory name
    (see validate_runid).
    """
    if positional_args:
        runids = list(positional_args)
    elif not stdin_is_tty:
        lines = sys.stdin.read().splitlines()
        runids = [line for line in lines if line.strip()]
    else:
        runids = None
    if runids is not None:
        for runid in runids:
            validate_runid(runid)
        return runids
    raise ValueError(
        "no RUNIDs given — pass them as arguments or pipe them via stdin "
        "(e.g. `jbr-filt ... | jbr-retag ...`); there is no fallback to "
        "all runs of the project"
    )


def validate_runid(runid: str) -> None:
    """Raise ValueError if *runid* is not a bare run directory name.

    RUNIDs are joined onto results/ (and, for jbr-move/jbr-copy, onto the
    --to directory, where --force removes an existing destination), so a
    path separator or a '.'/'..' component would reach outside them.
    """
    if runid in ("", ".", "..") or "/" in runid or "\0" in runid:
        raise ValueError(f"invalid RUNID {runid!r}: must be a bare run directory name")


def validate_tag(tag: str) -> None:
    """Raise ValueError if *tag* is not a valid tag value.

    A tag must be non-empty and must not contain ',' — jbr-ls joins tags
    with ',' for display and jbr-filt --tags substring-matches on tag
    elements, so a comma inside a tag would corrupt both (spec §1.3).
    """
    if tag == "":
        raise ValueError("tag must not be empty")
    if "," in tag:
        raise ValueError(f"tag {tag!r} must not contain ','")


def check_batch_in_progress(project_dir: str, force: bool) -> None:
    """Raise RuntimeError if project_dir/BATCH_IN_PROGRESS.json exists and
    *force* is not set (spec §1.5). Only this project's batch file is
    checked: a batch in another project never writes into this project.
    """
    if force:
        return
    batch_path = os.path.join(project_dir, "BATCH_IN_PROGRESS.json")
    if os.path.exists(batch_path):
        raise RuntimeError(
            f"{batch_path} exists (a batch is in progress or was "
            "interrupted); refusing to proceed. Pass --force to "
            "override."
        )


def load_info_json(run_dir: str) -> dict:
    """Load and return the parsed info.json for *run_dir*.

    Raises OSError if the file is missing/unreadable, or ValueError if it
    contains malformed JSON.
    """
    info_path = os.path.join(run_dir, "info.json")
    with open(info_path, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSON in {info_path}: {exc}") from exc


def atomic_write_json(path: str, data: dict) -> None:
    """Write *data* as JSON to *path* atomically (temp file + os.replace).

    The temporary file is created in the same directory as *path* so the
    rename is atomic on POSIX (same filesystem) — spec §1.8.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def apply_tag_ops(current_tags: list, adds: list, removes: list) -> tuple:
    """Apply remove-then-add tag operations (spec §1.2, §1.3).

    Removes are applied before adds, so a tag given to both -r and -a in
    the same invocation ends up present. New tags are appended in the
    order given; the relative order of untouched existing tags is
    preserved.

    Returns
    -------
    (new_tags, lines) : tuple[list[str], list[str]]
        new_tags is the resulting tag list. lines describes each applied
        or noted operation, in "+<tag>" / "-<tag>" / "-<tag> (not
        present)" form — adding an already-present tag is a silent no-op
        and produces no line.
    """
    tags = list(current_tags)
    lines = []

    for tag in removes:
        if tag in tags:
            tags.remove(tag)
            lines.append(f"-{tag}")
        else:
            lines.append(f"-{tag} (not present)")

    for tag in adds:
        if tag not in tags:
            tags.append(tag)
            lines.append(f"+{tag}")

    return tags, lines
