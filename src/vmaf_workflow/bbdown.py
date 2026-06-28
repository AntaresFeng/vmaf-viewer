from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Iterable
from pathlib import Path

from vmaf_workflow import config
from vmaf_workflow.models import StreamRecord


_STREAM_LINE_RE = re.compile(
    r"^\s*(?P<index>\d+)\.\s+"
    r"\[(?P<label>[^\]]+)\]\s+"
    r"\[(?P<resolution>(?P<width>\d+)x(?P<height>\d+))\]\s+"
    r"\[(?P<codec>[^\]]+)\]\s+"
    r"\[(?P<fps>\d+(?:\.\d+)?)\]\s+"
    r"\[(?P<bitrate>\d+)\s+kbps\]\s+"
    r"\[(?P<size>[^\]]+)\]\s*$"
)


@dataclass(frozen=True)
class BBDownStreamRecord(StreamRecord):
    source: str = "bilibili"
    index: int | None = None
    width: int | None = None
    height: int | None = None
    codec_family: str | None = None
    bitrate_source: str | None = None
    ext: str | None = None


def parse_bbdown_streams(output: str) -> list[StreamRecord]:
    streams: list[StreamRecord] = []
    for line in output.splitlines():
        match = _STREAM_LINE_RE.match(line)
        if match is None:
            continue

        codec = match.group("codec")
        streams.append(
            BBDownStreamRecord(
                index=int(match.group("index")),
                quality_label=match.group("label"),
                resolution=match.group("resolution"),
                width=int(match.group("width")),
                height=int(match.group("height")),
                codec=codec,
                codec_family=codec,
                fps=float(match.group("fps")),
                bitrate_kbps=int(match.group("bitrate")),
                bitrate_source="videoBandwidth",
                size_text=match.group("size"),
                ext="mp4",
                raw=line,
            )
        )
    return streams


def build_bilibili_plan(
    streams: Iterable[StreamRecord],
) -> tuple[list[StreamRecord], dict[str, list[int]]]:
    stream_list = list(streams)
    skipped: dict[str, list[int]] = {
        "quality_label_below_target": [],
        "shadowed_by_higher_1080_label": [],
    }
    target_labels = set(config.QUALITY_LABELS)
    high_1080_labels = set(config.HIGH_1080_LABELS)
    high_1080_codecs = {
        stream.codec
        for stream in stream_list
        if stream.quality_label in high_1080_labels and stream.codec is not None
    }

    plan: list[StreamRecord] = []
    for stream in stream_list:
        stream_index = _stream_index(stream)
        if stream.quality_label not in target_labels:
            _append_known_index(skipped["quality_label_below_target"], stream_index)
            continue

        if (
            stream.quality_label == config.FALLBACK_1080_LABEL
            and stream.codec in high_1080_codecs
        ):
            _append_known_index(skipped["shadowed_by_higher_1080_label"], stream_index)
            continue

        plan.append(stream)

    return plan, skipped


def find_stream_index(
    planned: StreamRecord, fresh_streams: Iterable[StreamRecord]
) -> int | None:
    matches = [
        fresh
        for fresh in fresh_streams
        if fresh.signature() == planned.signature() and _stream_index(fresh) is not None
    ]
    if len(matches) != 1:
        return None
    return _stream_index(matches[0])


def _stream_index(stream: StreamRecord) -> int | None:
    value = getattr(stream, "index", None)
    return value if isinstance(value, int) else None


def _append_known_index(indexes: list[int], value: int | None) -> None:
    if value is not None:
        indexes.append(value)


def bbdown_info_argv(exe_path: Path, bvid: str, config_path: Path) -> list[str]:
    return [str(exe_path), bvid, "--config-file", str(config_path), "-info"]


def bbdown_interactive_argv(exe_path: Path, bvid: str, config_path: Path) -> list[str]:
    return [str(exe_path), bvid, "--config-file", str(config_path), "-ia"]
