# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""
Locate and open collector log files that may be stored zstd-compressed.

With the .run.toml option ``collectors.compression`` (spec-execute.md 5.2) a collector log such
as ``nmt.log`` is written as ``nmt.log.zst``. Readers go through this module instead of
building ``<run_dir>/nmt.log`` themselves:

    path = resolve_collector_file(run_dir, "nmt.log")     # "nmt.log", "nmt.log.zst" or None
    with open_collector_file(path) as fh:                  # text stream, either way
        ...

compress_file(path) compresses a finished file in place (path -> path.zst).

Compressed files are decoded by streaming through ``zstd -dc`` (PRD.md D-58); nothing is
held in memory beyond what the caller reads. Python stdlib only.
"""

import os
import subprocess
import sys

ZST_SUFFIX = ".zst"


def _stderr_warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def resolve_collector_file(run_dir, name, warn=None):
    """Return the path of collector log *name* in *run_dir*, or None if absent.

    Looks for ``<name>`` and ``<name>.zst``. If both exist the plain file wins, with a
    warning (this happens only when someone decompressed a log by hand).
    """
    warn = warn or _stderr_warn
    plain = os.path.join(run_dir, name)
    compressed = plain + ZST_SUFFIX
    if os.path.exists(plain):
        if os.path.exists(compressed):
            warn(f"both '{plain}' and '{compressed}' exist; using the plain file")
        return plain
    if os.path.exists(compressed):
        return compressed
    return None


class _ZstdTextReader:
    """Context manager yielding a text stream decoded from a .zst file via ``zstd -dc``.

    A truncated final frame (what a killed compressor leaves behind) is not an error: what
    was decoded up to that point is delivered and a warning is issued when the stream is
    closed. Any other zstd failure raises ValueError at close.
    """

    def __init__(self, path, warn):
        self._path = path
        self._warn = warn
        self._proc = None
        self._stream = None

    def __enter__(self):
        try:
            self._proc = subprocess.Popen(
                ["zstd", "-dc", "--", self._path],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"cannot read '{self._path}': the 'zstd' binary was not found on PATH"
            ) from exc
        import io
        self._stream = io.TextIOWrapper(self._proc.stdout, encoding="utf-8", errors="replace")
        return self._stream

    def __exit__(self, exc_type, exc, tb):
        # Drain what the caller did not read, so zstd is never blocked on a full pipe
        # (and so its exit status reflects the whole file, not an early close).
        try:
            if exc_type is None:
                while self._stream.read(1 << 16):
                    pass
        finally:
            self._stream.close()
            err = self._proc.stderr.read().decode("utf-8", errors="replace").strip()
            self._proc.stderr.close()
            rc = self._proc.wait()
        if exc_type is None and rc != 0:
            # zstd 1.5.x says "premature end"; other versions "unexpected end of file"
            if "premature end" in err.lower() or "unexpected end of file" in err.lower():
                self._warn(f"'{self._path}' is truncated (compressor was killed?); "
                           "using the data decoded up to the cut")
            else:
                raise ValueError(f"zstd failed on '{self._path}' (exit {rc}): {err}")
        return False


def open_collector_file(path, warn=None):
    """Open a collector log for reading as text; *path* ends in ``.zst`` if compressed.

    Use as a context manager. Raises RuntimeError if a compressed file is opened and the
    ``zstd`` binary is missing.
    """
    warn = warn or _stderr_warn
    if path.endswith(ZST_SUFFIX):
        return _ZstdTextReader(path, warn)
    return open(path, "r", encoding="utf-8", errors="replace")


# Compressor for files compressed after the run (GC log, heapsizes.txt; spec-post-process.md
# §1). Same level as the collectors (PRD.md D-58). -f replaces a stale <path>.zst; --rm
# deletes <path> only once <path>.zst is complete.
_COMPRESS_FILE_CMD = ["zstd", "-1", "-q", "-f", "--rm"]


def compress_file(path):
    """Compress *path* in place to ``<path>.zst`` and remove *path*.

    Returns None on success, else an error message (``zstd`` missing or failing); in that
    case *path* is left as it was.
    """
    try:
        proc = subprocess.run(_COMPRESS_FILE_CMD + ["--", path],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)
    except FileNotFoundError:
        return f"cannot compress '{path}': the 'zstd' binary was not found on PATH"
    if proc.returncode != 0:
        return f"zstd failed on '{path}' (exit {proc.returncode}): {proc.stderr.strip()}"
    return None
