from __future__ import annotations

import hashlib
from pathlib import Path

from .models import FileRecord


def _stable_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]


def scan_vmaf_files(root: Path) -> list[FileRecord]:
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        return []

    records: list[FileRecord] = []
    for path in sorted(root.rglob("*_vmaf.json")):
        if not path.is_file():
            continue
        stat = path.stat()
        relative_path = path.relative_to(root).as_posix()
        records.append(
            FileRecord(
                id=_stable_id(relative_path),
                name=path.name,
                path=path,
                relative_path=relative_path,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    return records
