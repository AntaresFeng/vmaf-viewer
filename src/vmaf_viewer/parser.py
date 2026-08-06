from __future__ import annotations

import csv
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from lxml import etree
import orjson

from .models import FileRecord, ParsedVmaf


class VmafParseError(ValueError):
    """Raised when a VMAF log file cannot be parsed into frame metrics."""


_PRIMARY_CANDIDATES = ("vmaf", "vmaf_hd", "vmaf_4k")
_MISSING_FRAME_NUM = object()


@dataclass(frozen=True)
class RawFrame:
    frame_num: object
    metrics: Mapping[str, object]


class ParserStrategy(Protocol):
    def parse_frames(self, record: FileRecord) -> list[RawFrame]: ...


def select_primary_metric(metric_names: Iterable[str]) -> str | None:
    names = set(metric_names)
    exact = next((c for c in _PRIMARY_CANDIDATES if c in names), None)
    if exact:
        return exact
    return next((name for name in metric_names if "vmaf" in name), None)


def _frame_num(
    raw: object, record: FileRecord, *, allow_decimal_string: bool = True
) -> int:
    if raw is _MISSING_FRAME_NUM:
        raise VmafParseError(f"{record.relative_path} is missing frameNum")

    if isinstance(raw, bool):
        value = None
    elif isinstance(raw, int):
        value = raw
    elif allow_decimal_string and isinstance(raw, str):
        text = raw.strip()
        value = int(text) if text.isascii() and text.isdecimal() else None
    else:
        value = None

    if value is None or value < 0:
        raise VmafParseError(
            f"{record.relative_path} has invalid frameNum: {raw!r}"
        )
    return value


def _metric_value(raw: object) -> float:
    if isinstance(raw, bool):
        return math.nan
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return math.nan
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


class JsonVmafParser:
    """Parser for libvmaf ``log_fmt=json`` output."""

    def parse_frames(self, record: FileRecord) -> list[RawFrame]:
        try:
            data = orjson.loads(record.path.read_bytes())
        except orjson.JSONDecodeError as exc:
            raise VmafParseError(f"Invalid JSON in {record.relative_path}") from exc

        frames = data.get("frames") if isinstance(data, dict) else None
        if not isinstance(frames, list):
            raise VmafParseError(f"{record.relative_path} is missing frames")

        raw_frames: list[RawFrame] = []
        for item in frames:
            metrics = item.get("metrics") if isinstance(item, dict) else None
            if not isinstance(metrics, dict):
                continue
            raw_frames.append(
                RawFrame(
                    frame_num=_frame_num(
                        item.get("frameNum", _MISSING_FRAME_NUM),
                        record,
                        allow_decimal_string=False,
                    ),
                    metrics=metrics,
                )
            )
        return raw_frames


class CsvVmafParser:
    """Parser for libvmaf ``log_fmt=csv`` output.

    FFmpeg libvmaf writes CSV with the header row:
    ``Frame,<features>,vmaf,`` — note the column is ``Frame``, not ``frameNum``.
    """

    # FFmpeg libvmaf CSV column that holds the frame index
    _FRAME_COL = "Frame"

    def parse_frames(self, record: FileRecord) -> list[RawFrame]:
        try:
            with record.path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = reader.fieldnames or []
                # FFmpeg libvmaf CSV uses "Frame" as the frame identifier column
                if self._FRAME_COL not in fieldnames:
                    raise VmafParseError(
                        f"{record.relative_path} is missing '{self._FRAME_COL}' column"
                    )
                metric_names = [
                    name for name in fieldnames if name and name != self._FRAME_COL
                ]
                return [
                    RawFrame(
                        frame_num=row.get(self._FRAME_COL, _MISSING_FRAME_NUM),
                        metrics={name: row.get(name) for name in metric_names},
                    )
                    for row in reader
                ]
        except (csv.Error, UnicodeError) as exc:
            raise VmafParseError(f"Invalid CSV in {record.relative_path}") from exc


def _local_name(element: etree._Element) -> str:
    if not isinstance(element.tag, str):
        return ""
    return etree.QName(element).localname


def _find_frames_container(root: etree._Element) -> etree._Element | None:
    if _local_name(root) == "frames":
        return root
    direct = root.find("./{*}frames")
    if direct is not None:
        return direct
    return root.find(".//{*}frames")


class XmlVmafParser:
    """Parser for libvmaf ``log_fmt=xml`` output."""

    def parse_frames(self, record: FileRecord) -> list[RawFrame]:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        try:
            tree = etree.parse(str(record.path), parser)
        except etree.XMLSyntaxError as exc:
            raise VmafParseError(f"Invalid XML in {record.relative_path}") from exc

        frames_container = _find_frames_container(tree.getroot())
        if frames_container is None:
            raise VmafParseError(f"{record.relative_path} is missing frames")

        raw_frames: list[RawFrame] = []
        for frame in frames_container:
            if _local_name(frame) != "frame":
                continue
            attrib = frame.attrib
            raw_frames.append(
                RawFrame(
                    frame_num=attrib.get("frameNum", _MISSING_FRAME_NUM),
                    metrics={
                        name: value
                        for name, value in attrib.items()
                        if name != "frameNum"
                    },
                )
            )
        return raw_frames


_PARSERS: dict[str, ParserStrategy] = {
    ".json": JsonVmafParser(),
    ".csv": CsvVmafParser(),
    ".xml": XmlVmafParser(),
}


class VmafParserFactory:
    """Selects a parser for a VMAF log file by suffix."""

    def for_suffix(self, suffix: str) -> ParserStrategy:
        parser = _PARSERS.get(suffix.lower())
        if parser is None:
            raise VmafParseError(f"Unsupported VMAF log format: {suffix or '<none>'}")
        return parser


def parse_vmaf_file(record: FileRecord) -> ParsedVmaf:
    parser = VmafParserFactory().for_suffix(record.path.suffix)
    return _build_parsed(record, parser.parse_frames(record))


def _build_parsed(record: FileRecord, frames: list[RawFrame]) -> ParsedVmaf:
    frame_numbers: list[int] = []
    metric_names: list[str] = []
    metric_seen: set[str] = set()

    for item in frames:
        frame_num = _frame_num(item.frame_num, record)
        if frame_numbers and frame_num <= frame_numbers[-1]:
            previous = frame_numbers[-1]
            if frame_num == previous:
                raise VmafParseError(
                    f"{record.relative_path} has duplicate frameNum: {frame_num}"
                )
            raise VmafParseError(
                f"{record.relative_path} has out-of-order frameNum: "
                f"{frame_num} after {previous}"
            )
        frame_numbers.append(frame_num)
        for name in item.metrics:
            if name not in metric_seen:
                metric_seen.add(name)
                metric_names.append(name)

    values: dict[str, list[float]] = {name: [] for name in metric_names}
    for item in frames:
        for name in metric_names:
            values[name].append(_metric_value(item.metrics.get(name)))

    return ParsedVmaf(
        file=record,
        frame_numbers=frame_numbers,
        metrics=values,
        primary_metric=select_primary_metric(metric_names),
    )
