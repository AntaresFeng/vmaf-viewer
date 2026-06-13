from __future__ import annotations

from .models import FileRecord, ParsedVmaf
from .parser import parse_vmaf_file


class VmafCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[int, float, ParsedVmaf]] = {}

    def get(self, record: FileRecord) -> ParsedVmaf:
        cached = self._items.get(record.id)
        if cached is not None:
            size, mtime, parsed = cached
            if size == record.size and mtime == record.mtime:
                return parsed

        parsed = parse_vmaf_file(record)
        self._items[record.id] = (record.size, record.mtime, parsed)
        return parsed

    def clear(self) -> None:
        self._items.clear()
