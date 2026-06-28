from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


Manifest = dict[str, Any]


@dataclass(frozen=True)
class StreamRecord:
    quality_label: str
    resolution: str | None = None
    codec: str | None = None
    fps: float | None = None
    bitrate_kbps: float | None = None
    size_text: str | None = None
    size_bytes: int | None = None
    raw: Manifest | None = None

    def signature(self) -> str:
        size = self.size_text if self.size_text is not None else self.size_bytes
        return "|".join(
            "" if value is None else str(value)
            for value in (
                self.quality_label,
                self.resolution,
                self.codec,
                self.fps,
                self.bitrate_kbps,
                size,
            )
        )

    def to_manifest(self) -> Manifest:
        return {
            "source": getattr(self, "source", None),
            "index": getattr(self, "index", None),
            "quality_label": self.quality_label,
            "resolution": self.resolution,
            "width": getattr(self, "width", None),
            "height": getattr(self, "height", None),
            "codec": self.codec,
            "codec_family": getattr(self, "codec_family", None),
            "fps": self.fps,
            "bitrate_kbps": self.bitrate_kbps,
            "bitrate_source": getattr(self, "bitrate_source", None),
            "size_text": self.size_text,
            "size_bytes": self.size_bytes,
            "format_id": getattr(self, "format_id", None),
            "ext": getattr(self, "ext", None),
            "protocol": getattr(self, "protocol", None),
            "container": getattr(self, "container", None),
        }


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    stdin: str | None = None

    def to_manifest(self) -> Manifest:
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdin": self.stdin,
        }


@dataclass(frozen=True)
class DownloadDecision:
    downloader: str
    stream: StreamRecord | None = None
    status: str = "planned"
    reason: str | None = None
    fallback_label: str | None = None
    output_path: Path | None = None
    command: CommandResult | None = None

    def to_manifest(self) -> Manifest:
        return {
            "downloader": self.downloader,
            "stream": None if self.stream is None else self.stream.to_manifest(),
            "status": self.status,
            "reason": self.reason,
            "fallback_label": self.fallback_label,
            "output_path": str(self.output_path)
            if self.output_path is not None
            else None,
            "command": None if self.command is None else self.command.to_manifest(),
        }
