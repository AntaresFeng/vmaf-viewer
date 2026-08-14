from __future__ import annotations

import hashlib
import itertools
import stat as _stat
from pathlib import Path
from collections.abc import Iterable, Iterator

from natsort import os_sorted

from .models import FileRecord

_SUPPORTED_VMAF_LOG_PATTERNS = ("*.json", "*.csv", "*.xml")


def rglob_skip_dot_dirs(
    root: str | Path,
    pattern: str | Iterable[str],
    *,
    case_sensitive: bool | None = None,
) -> Iterator[Path]:
    """Walk *root* skipping directories whose name starts with '.'.

    *pattern* may be a single glob or an iterable of globs; a path is yielded
    when it matches any of them. With a single pattern this is equivalent to
    ``Path(root).rglob(pattern, case_sensitive=case_sensitive)`` except that
    dot-prefixed directories are pruned during traversal — they are neither
    descended into nor yielded as matches.

    Requires Python 3.12+ (depends on ``Path.walk`` and
    ``Path.match(case_sensitive=...)``).
    """
    root = Path(root)
    patterns = (pattern,) if isinstance(pattern, str) else tuple(pattern)

    for current_dir, dirnames, filenames in root.walk():
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in itertools.chain(dirnames, filenames):
            full_path = current_dir / name
            # Match on the relative path to avoid false positives from
            # the root path interacting with multi-segment patterns.
            rel_path = full_path.relative_to(root)
            if any(
                rel_path.match(p, case_sensitive=case_sensitive) for p in patterns
            ):
                yield full_path


def _stable_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]


def scan_vmaf_files(root: Path) -> list[FileRecord]:
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        return []

    candidates = os_sorted(rglob_skip_dot_dirs(root, _SUPPORTED_VMAF_LOG_PATTERNS))

    records: list[FileRecord] = []
    for path in candidates:
        try:
            st = path.stat()
        except FileNotFoundError:
            # Dangling symlink or a path that vanished during the scan.
            continue
        if not _stat.S_ISREG(st.st_mode):
            continue
        relative_path = path.relative_to(root).as_posix()
        records.append(
            FileRecord(
                id=_stable_id(relative_path),
                name=path.name,
                path=path,
                relative_path=relative_path,
                size=st.st_size,
                mtime=st.st_mtime,
            )
        )
    return records
