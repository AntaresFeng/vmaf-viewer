from __future__ import annotations

import hashlib
import itertools
import stat as _stat
from pathlib import Path
from typing import Iterator

from .models import FileRecord

_SUPPORTED_VMAF_LOG_PATTERNS = ("*.json", "*.csv", "*.xml")


def rglob_skip_dot_dirs(
    root: str | Path,
    pattern: str,
    *,
    case_sensitive: bool | None = None,
) -> Iterator[Path]:
    """Walk *root* skipping directories whose name starts with '.'.

    Equivalent to ``Path(root).rglob(pattern, case_sensitive=case_sensitive)``
    except that dot-prefixed directories are pruned during traversal — they are
    neither descended into nor yielded as matches.

    Requires Python 3.12+ (depends on ``Path.walk`` and
    ``Path.match(case_sensitive=...)``).
    """
    root = Path(root)

    for current_dir, dirnames, filenames in root.walk():
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in itertools.chain(dirnames, filenames):
            full_path = current_dir / name
            # Match on the relative path to avoid false positives from
            # the root path interacting with multi-segment patterns.
            rel_path = full_path.relative_to(root)
            if rel_path.match(pattern, case_sensitive=case_sensitive):
                yield full_path


def _stable_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]


def scan_vmaf_files(root: Path) -> list[FileRecord]:
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        return []

    candidates = set()
    for pattern in _SUPPORTED_VMAF_LOG_PATTERNS:
        candidates.update(rglob_skip_dot_dirs(root, pattern))

    records: list[FileRecord] = []
    for path in sorted(candidates):
        st = path.stat()
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
