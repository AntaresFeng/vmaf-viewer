from __future__ import annotations

from dataclasses import dataclass
from typing import Any


Manifest = dict[str, Any]


@dataclass(frozen=True)
class StreamRecord:
    quality_label: str
    # Optional fields populated by per-source normalizers (BBDown / yt-dlp).
    source: str | None = None
    index: int | None = None
    resolution: str | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    codec_family: str | None = None
    fps: float | None = None
    bitrate_kbps: float | None = None
    bitrate_source: str | None = None
    size_text: str | None = None
    size_bytes: int | None = None
    format_id: str | None = None
    ext: str | None = None
    protocol: str | None = None
    container: str | None = None
    raw: Manifest | None = None

    def signature(self) -> str:
        return "|".join(
            "" if value is None else str(value)
            for value in (
                self.quality_label,
                self.resolution,
                self.codec,
                self.fps,
                self.bitrate_kbps,
            )
        )

    def to_manifest(self) -> Manifest:
        return {
            "source": self.source,
            "index": self.index,
            "quality_label": self.quality_label,
            "resolution": self.resolution,
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
            "codec_family": self.codec_family,
            "fps": self.fps,
            "bitrate_kbps": self.bitrate_kbps,
            "bitrate_source": self.bitrate_source,
            "size_text": self.size_text,
            "size_bytes": self.size_bytes,
            "format_id": self.format_id,
            "ext": self.ext,
            "protocol": self.protocol,
            "container": self.container,
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
    command: CommandResult | None = None

    def to_manifest(self) -> Manifest:
        return {
            "downloader": self.downloader,
            "stream": None if self.stream is None else self.stream.to_manifest(),
            "status": self.status,
            "reason": self.reason,
            "command": None if self.command is None else self.command.to_manifest(),
        }
