from __future__ import annotations

import json

from vmaf_workflow.ytdlp import (
    load_after_video_downloads,
    load_sidecar_downloads,
    normalize_ytdlp_format,
    parse_ytdlp_preflight,
)


def test_normalize_ytdlp_format_extracts_stream_record_fields() -> None:
    raw_format = {
        "format_id": 137,
        "format_note": "1080p",
        "resolution": "1920x1080",
        "width": "1920",
        "height": 1080,
        "vcodec": "avc1.640028",
        "fps": "29.97",
        "vbr": 4200.6,
        "tbr": 5000,
        "filesize": 123456789,
        "filesize_approx": 987654321,
        "ext": "mp4",
        "protocol": "https",
        "container": "mp4_dash",
    }

    stream = normalize_ytdlp_format(raw_format, index=3)

    assert stream.source == "youtube"
    assert stream.index == 3
    assert stream.quality_label == "1080p"
    assert stream.resolution == "1920x1080"
    assert stream.width == 1920
    assert stream.height == 1080
    assert stream.codec == "avc1.640028"
    assert stream.codec_family == "AVC"
    assert stream.fps == 29.97
    assert stream.bitrate_kbps == 4200.6
    assert stream.bitrate_source == "vbr"
    assert stream.size_bytes == 123456789
    assert stream.size_text is None
    assert stream.format_id == "137"
    assert stream.ext == "mp4"
    assert stream.protocol == "https"
    assert stream.container == "mp4_dash"
    assert stream.raw is raw_format


def test_normalize_ytdlp_format_builds_resolution_and_uses_tbr_fallback() -> None:
    stream = normalize_ytdlp_format(
        {
            "format_note": "1440p",
            "width": 2560,
            "height": 1440,
            "vcodec": "vp09.00.51.08",
            "tbr": 8000.2,
            "filesize_approx": 2222,
        },
        index=None,
    )

    assert stream.resolution == "2560x1440"
    assert stream.bitrate_kbps == 8000.2
    assert stream.bitrate_source == "tbr"
    assert stream.size_bytes == 2222


def test_parse_ytdlp_preflight_filters_by_height_codec_and_protocol() -> None:
    raw_info = {
        "formats": [
            {"format_id": "18", "height": 720, "vcodec": "avc1", "acodec": "mp4a", "protocol": "https"},
            {"format_id": "999", "height": 999, "vcodec": "avc1", "acodec": "none", "protocol": "https"},
            {"format_id": "1000", "height": 1000, "vcodec": "avc1", "acodec": "none", "protocol": "https"},
            {"format_id": "399", "height": 1080, "vcodec": "none", "acodec": "none", "protocol": "https"},
            {"format_id": "401", "height": 2160, "vcodec": "av01", "acodec": "none", "protocol": "https"},
            # video-only 1080p over HLS: same shape as 1000 but m3u8 must be excluded
            {"format_id": "271", "height": 1080, "vcodec": "avc1", "acodec": "none", "protocol": "m3u8_native"},
        ],
        "requested_downloads": [
            {"format_id": "251", "height": None, "vcodec": "none", "acodec": "opus", "protocol": "https"},
            {"format_id": "401", "height": 2160, "vcodec": "av01", "acodec": "none", "protocol": "https"},
        ],
    }

    selected, requested = parse_ytdlp_preflight(raw_info)

    assert [stream.index for stream in selected] == [2, 4]
    assert [stream.format_id for stream in selected] == ["1000", "401"]
    assert [stream.index for stream in requested] == [None]
    assert [stream.format_id for stream in requested] == ["401"]


def test_load_after_video_downloads_reads_jsonl_requested_downloads(tmp_path) -> None:
    jsonl_path = tmp_path / "after_video.jsonl"
    lines = [
        {
            "requested_downloads": [
                {
                    "format_id": "137",
                    "height": 1080,
                    "vcodec": "avc1",
                    "acodec": "none",
                    "protocol": "https",
                },
                {"format_id": "140", "vcodec": "none", "acodec": "mp4a"},
            ]
        },
        {
            "requested_downloads": [
                {
                    "format_id": "399",
                    "height": 1080,
                    "vcodec": "av01",
                    "acodec": "none",
                    "protocol": "https",
                }
            ]
        },
    ]
    jsonl_path.write_text(
        "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
    )

    streams = load_after_video_downloads(jsonl_path)

    assert [stream.format_id for stream in streams] == ["137", "399"]
    assert [stream.index for stream in streams] == [None, None]


def test_load_sidecar_downloads_reads_infojson_target_formats(tmp_path) -> None:
    first = tmp_path / "one.info.json"
    first.write_text(
        json.dumps(
            {
                "format_id": "401",
                "format_note": "2160p",
                "height": 2160,
                "vcodec": "av01",
                "acodec": "none",
                "protocol": "https",
                "requested_downloads": [
                    {
                        "format_id": "137",
                        "height": 1080,
                        "vcodec": "avc1",
                        "acodec": "none",
                        "protocol": "https",
                    },
                    {"format_id": "140", "vcodec": "none", "acodec": "mp4a"},
                ],
            }
        ),
        encoding="utf-8",
    )
    second = tmp_path / "two.info.json"
    second.write_text(
        json.dumps(
            {"format_id": "18", "height": 720, "vcodec": "avc1", "acodec": "mp4a"}
        ),
        encoding="utf-8",
    )

    streams = load_sidecar_downloads(tmp_path)

    assert [stream.format_id for stream in streams] == ["401", "137"]
    assert [stream.index for stream in streams] == [None, None]
