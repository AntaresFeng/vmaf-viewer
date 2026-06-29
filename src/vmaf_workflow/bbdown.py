from __future__ import annotations

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


def parse_bbdown_streams(output: str) -> list[StreamRecord]:
    streams: list[StreamRecord] = []
    for line in output.splitlines():
        match = _STREAM_LINE_RE.match(line)
        if match is None:
            continue

        codec = match.group("codec")
        streams.append(
            StreamRecord(
                source="bilibili",
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
        if stream.quality_label not in target_labels:
            if stream.index is not None:
                skipped["quality_label_below_target"].append(stream.index)
            continue

        if (
            stream.quality_label == config.FALLBACK_1080_LABEL
            and stream.codec in high_1080_codecs
        ):
            if stream.index is not None:
                skipped["shadowed_by_higher_1080_label"].append(stream.index)
            continue

        plan.append(stream)

    return plan, skipped


def find_stream_index(
    planned: StreamRecord, fresh_streams: Iterable[StreamRecord]
) -> int | None:
    for fresh in fresh_streams:
        if fresh.index is not None and fresh.signature() == planned.signature():
            return fresh.index
    return None


def bbdown_info_argv(exe_path: Path, bvid: str, config_path: Path) -> list[str]:
    return [str(exe_path), bvid, "--config-file", str(config_path), "-info"]


def bbdown_interactive_argv(exe_path: Path, bvid: str, config_path: Path) -> list[str]:
    return [str(exe_path), bvid, "--config-file", str(config_path), "-ia"]
