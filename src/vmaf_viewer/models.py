from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileRecord:
    id: str
    name: str
    path: Path
    relative_path: str
    size: int
    mtime: float

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "relative_path": self.relative_path,
            "size": self.size,
            "mtime": self.mtime,
        }


@dataclass(frozen=True)
class ParsedVmaf:
    file: FileRecord
    frame_numbers: list[int]
    metrics: dict[str, list[float]]
    primary_metric: str | None

    @property
    def total_frames(self) -> int:
        return len(self.frame_numbers)


@dataclass(frozen=True)
class ThresholdSummary:
    threshold: float
    count: int
    ratio: float
