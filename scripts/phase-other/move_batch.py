# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

# move_batch.py — core logic for jbr-move-batch (docs/spec-other.md §6).
#
# Moves whole batches (batches/<BATCHID>/ plus every run directory named by
# a result_dir in the batch's manifest) from one project into another,
# creating the target project if needed. All checks run before anything is
# created or moved; any failure raises RuntimeError and touches nothing.

import json
import os
import shutil
import sys

import project as _project
from init_project import init_project


def _read_manifest(source: str, batchid: str):
    """Return the parsed manifest of a finished batch, or raise RuntimeError."""
    bdir = _project.batch_dir(source, batchid)
    for name in (_project.DONE_MANIFEST, _project.ABORTED_MANIFEST):
        path = os.path.join(bdir, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"cannot read {path}: {exc}") from exc
    if os.path.isdir(bdir):
        raise RuntimeError(
            f"batch {batchid} has no {_project.DONE_MANIFEST} or "
            f"{_project.ABORTED_MANIFEST} in {bdir}"
        )
    raise RuntimeError(f"no such batch: {batchid} ({bdir} does not exist)")


def _in_progress_batchid(source: str):
    """Return the batchid of the source project's BATCH_IN_PROGRESS.json, or
    None if there is none. Raise RuntimeError if it exists but is unreadable."""
    path = _project.batch_file(source)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            batchid = json.load(fh).get("batchid")
    except (OSError, ValueError, AttributeError) as exc:
        raise RuntimeError(f"cannot read {path}: {exc}; refusing to move batches") from exc
    if not batchid:
        raise RuntimeError(f"{path} has no batchid; refusing to move batches")
    return batchid


def _check_target(source: str, target: str, description) -> bool:
    """Validate the target project dir. Return True if it must be created."""
    if os.path.realpath(target) == os.path.realpath(source):
        raise RuntimeError(f"--to is the source project itself: {target}")
    if os.path.exists(target):
        if not os.path.isfile(os.path.join(target, _project.MARKER)):
            raise RuntimeError(
                f"{target} exists but is not a project (no {_project.MARKER})"
            )
        if description is not None:
            raise RuntimeError(
                "-m/--description only applies when the target project is created"
            )
        return False
    enclosing = _project.find_project_or_none(os.path.dirname(target))
    if enclosing is not None:
        raise RuntimeError(
            f"{target} lies inside the project {enclosing}; projects cannot be nested"
        )
    return True


def plan_move(source: str, target: str, batchids: list, description=None):
    """Run all checks of spec §6.4 and return the plan.

    Returns (create, plan, missing):
      create  -- True if the target project must be created
      plan    -- list of (batchid, [result_dir, ...]) with existing run dirs
      missing -- list of (batchid, result_dir) whose run dir does not exist
    """
    create = _check_target(source, target, description)

    in_progress = _in_progress_batchid(source)
    seen = set()
    unique = []
    for b in batchids:
        if b not in seen:
            seen.add(b)
            unique.append(b)

    if in_progress is not None and in_progress in seen:
        raise RuntimeError(
            f"batch {in_progress} is in progress; complete it, or abandon it "
            "with 'jbr-execute --abort', first"
        )

    plan = []
    missing = []
    no_runs_found = []
    planned_runs = set()
    for batchid in unique:
        manifest = _read_manifest(source, batchid)
        runs = []
        listed = found = 0
        for run in manifest.get("runs", []) or []:
            result_dir = run.get("result_dir") if isinstance(run, dict) else None
            if not result_dir:
                continue
            listed += 1
            if result_dir in planned_runs:
                found += 1  # listed by an earlier batch of this invocation
                continue
            if os.path.lexists(os.path.join(source, result_dir)):
                found += 1
                planned_runs.add(result_dir)
                runs.append(result_dir)
            else:
                missing.append((batchid, result_dir))
        if listed and not found:
            no_runs_found.append(batchid)
        plan.append((batchid, runs))

    if no_runs_found:
        raise RuntimeError(
            "none of the runs listed in these batches exist in the source "
            "project (wrong result_dir?): " + ", ".join(no_runs_found)
        )

    collisions = []
    for batchid, runs in plan:
        for rel in [os.path.join(_project.BATCHES_DIRNAME, batchid)] + runs:
            if os.path.lexists(os.path.join(target, rel)):
                collisions.append(rel)
    if collisions:
        raise RuntimeError(
            f"already exist in {target}: " + ", ".join(collisions)
        )

    return create, plan, missing


def run_move_batch(source: str, target: str, batchids: list,
                   description=None, dry_run: bool = False) -> None:
    """Full jbr-move-batch pipeline (spec §6). Raises RuntimeError on any
    failed check, before anything is created or moved."""
    target = os.path.abspath(target)
    create, plan, missing = plan_move(source, target, batchids, description)

    for batchid, result_dir in missing:
        print(f"Warning: batch {batchid}: run directory {result_dir} does not "
              "exist, skipped", file=sys.stderr)

    if create:
        if dry_run:
            print(f"would create project {target}")
        else:
            init_project(target, description)
            print(f"created project {target}")

    for batchid, runs in plan:
        bdst = os.path.join(target, _project.BATCHES_DIRNAME, batchid)
        if not dry_run:
            for rel in runs:
                dst = os.path.join(target, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(os.path.join(source, rel), dst)
            os.makedirs(os.path.dirname(bdst), exist_ok=True)
            shutil.move(_project.batch_dir(source, batchid), bdst)
        verb = "would move" if dry_run else "moved"
        print(f"{batchid}: {verb} to {bdst} ({len(runs)} runs)")
