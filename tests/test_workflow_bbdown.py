from __future__ import annotations

from vmaf_workflow.bbdown import (
    build_bilibili_plan,
    find_stream_index,
    parse_bbdown_streams,
)


def test_parse_bbdown_streams_extracts_complete_fields() -> None:
    output = "0. [4K 超清] [3840x2160] [HEVC] [60.000] [10800 kbps] [~250.00 MB]"

    stream = parse_bbdown_streams(output)[0]

    assert stream.index == 0
    assert stream.quality_label == "4K 超清"
    assert stream.resolution == "3840x2160"
    assert stream.width == 3840
    assert stream.height == 2160
    assert stream.codec == "HEVC"
    assert stream.codec_family == "HEVC"
    assert stream.fps == 60.0
    assert stream.bitrate_kbps == 10800
    assert stream.bitrate_source == "videoBandwidth"
    assert stream.size_text == "~250.00 MB"
    assert stream.ext == "mp4"
    assert stream.raw == output


def test_build_bilibili_plan_uses_platform_label_not_actual_height() -> None:
    streams = parse_bbdown_streams(
        "\n".join(
            [
                "0. [720P 高清] [1280x720] [HEVC] [30.000] [1800 kbps] [~50.00 MB]",
                "1. [1080P 高帧率] [1920x822] [HEVC] [60.000] [4500 kbps] [~120.00 MB]",
            ]
        )
    )

    plan, skipped = build_bilibili_plan(streams)

    assert [stream.index for stream in plan] == [1]
    assert skipped["quality_label_below_target"] == [0]


def test_build_bilibili_plan_shadows_fallback_1080_for_same_codec() -> None:
    streams = parse_bbdown_streams(
        "\n".join(
            [
                "3. [1080P 高码率] [1920x1080] [AVC] [30.000] [6000 kbps] [~150.00 MB]",
                "4. [1080P 高清] [1920x1080] [AVC] [30.000] [3500 kbps] [~90.00 MB]",
            ]
        )
    )

    plan, skipped = build_bilibili_plan(streams)

    assert [stream.index for stream in plan] == [3]
    assert skipped["shadowed_by_higher_1080_label"] == [4]


def test_build_bilibili_plan_keeps_fallback_1080_when_codec_has_no_higher_1080() -> (
    None
):
    streams = parse_bbdown_streams(
        "\n".join(
            [
                "5. [1080P 高清] [1920x1080] [AV1] [30.000] [3000 kbps] [~80.00 MB]",
                "6. [1080P 高帧率] [1920x1080] [HEVC] [60.000] [4500 kbps] [~120.00 MB]",
            ]
        )
    )

    plan, skipped = build_bilibili_plan(streams)

    assert [stream.index for stream in plan] == [5, 6]
    assert skipped["shadowed_by_higher_1080_label"] == []


def test_find_stream_index_matches_signature_when_fresh_index_changes() -> None:
    planned = parse_bbdown_streams(
        "2. [1080P 高帧率] [1920x822] [HEVC] [60.000] [4500 kbps] [~120.00 MB]"
    )[0]
    fresh_streams = parse_bbdown_streams(
        "\n".join(
            [
                "0. [4K 超清] [3840x2160] [HEVC] [60.000] [10800 kbps] [~250.00 MB]",
                "7. [1080P 高帧率] [1920x822] [HEVC] [60.000] [4500 kbps] [~120.00 MB]",
            ]
        )
    )

    assert find_stream_index(planned, fresh_streams) == 7
