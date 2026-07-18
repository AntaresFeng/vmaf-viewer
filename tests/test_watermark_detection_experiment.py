from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "devscripts"
    / "explore_watermark_detection.py"
)
SPEC = importlib.util.spec_from_file_location(
    "explore_watermark_detection", SCRIPT_PATH
)
assert SPEC is not None
assert SPEC.loader is not None
watermark_detection = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = watermark_detection
SPEC.loader.exec_module(watermark_detection)

MediaInfo = watermark_detection.MediaInfo
analyze_samples = watermark_detection.analyze_samples
analysis_size = watermark_detection.analysis_size
choose_reference_frame = watermark_detection.choose_reference_frame
edge_search_mask = watermark_detection.edge_search_mask
extract_gray_frames = watermark_detection.extract_gray_frames
find_candidates = watermark_detection.find_candidates
normalize_reference = watermark_detection.normalize_reference
parse_fraction = watermark_detection.parse_fraction
sample_times = watermark_detection.sample_times


def test_extract_gray_frames_batches_rawvideo_output(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, *, check, capture_output):
        commands.append(command)
        assert check is True
        assert capture_output is True
        return type("Result", (), {"stdout": bytes(range(24))})()

    monkeypatch.setattr(watermark_detection.subprocess, "run", fake_run)

    frames = extract_gray_frames(
        Path("video.mp4"), 12.5, 3, (4, 2), "ffmpeg"
    )

    assert frames.shape == (3, 2, 4)
    assert frames.ravel().tolist() == list(range(24))
    command = commands[0]
    assert command[command.index("-ss") + 1] == "12.500000"
    assert command[command.index("-frames:v") + 1] == "3"
    assert command[command.index("-fps_mode") + 1] == "passthrough"


def test_choose_reference_frame_batches_contiguous_candidates(monkeypatch) -> None:
    calls: list[tuple[float, int]] = []
    candidates = np.stack(
        [np.full((2, 2), value, dtype=np.uint8) for value in (1, 2, 3)]
    )

    def fake_extract(path, timestamp, count, size, ffmpeg):
        calls.append((timestamp, count))
        return candidates

    monkeypatch.setattr(watermark_detection, "extract_gray_frames", fake_extract)
    monkeypatch.setattr(
        watermark_detection,
        "alignment_score",
        lambda candidate, distorted: abs(float(candidate[0, 0]) - 2.0),
    )

    chosen, offset, score = choose_reference_frame(
        Path("reference.mp4"),
        10.0,
        MediaInfo("reference.mp4", 4, 2, 2.0, 100.0),
        np.zeros((2, 2), dtype=np.uint8),
        (4, 2),
        1,
        "ffmpeg",
    )

    assert calls == [(9.5, 3)]
    assert np.array_equal(chosen, candidates[1])
    assert offset == 0.0
    assert score == 0.0


def test_analyze_samples_bounds_workers_and_preserves_order(monkeypatch) -> None:
    executor_settings: dict[str, object] = {}

    class RecordingExecutor:
        def __init__(self, *, max_workers, thread_name_prefix):
            executor_settings["max_workers"] = max_workers
            executor_settings["thread_name_prefix"] = thread_name_prefix

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def map(self, function, values):
            return [function(value) for value in values]

    def fake_analyze_sample(timestamp, **kwargs):
        frame = np.full((1, 1), timestamp, dtype=np.float64)
        return frame, frame, frame, {"timestamp": timestamp}

    monkeypatch.setattr(watermark_detection, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(watermark_detection, "analyze_sample", fake_analyze_sample)

    results = analyze_samples(
        [1.0, 2.0, 3.0, 4.0, 5.0],
        distorted_path=Path("distorted.mp4"),
        reference_path=Path("reference.mp4"),
        reference_info=MediaInfo("reference.mp4", 4, 2, 2.0, 100.0),
        size=(4, 2),
        radius_frames=1,
        ffmpeg="ffmpeg",
    )

    assert executor_settings == {
        "max_workers": 4,
        "thread_name_prefix": "watermark-sample",
    }
    assert [result[3]["timestamp"] for result in results] == [
        1.0,
        2.0,
        3.0,
        4.0,
        5.0,
    ]


def test_parse_fraction_handles_video_frame_rates() -> None:
    assert parse_fraction("60/1") == 60.0
    assert parse_fraction("30000/1001") == 30000 / 1001
    assert parse_fraction("0/0") == 0.0
    assert parse_fraction(None) == 0.0


def test_analysis_size_preserves_aspect_ratio_and_even_height() -> None:
    info = MediaInfo("video.mp4", 3840, 2160, 60.0, 10.0)

    assert analysis_size(info, 960) == (960, 540)


def test_sample_times_avoid_video_ends() -> None:
    timestamps = sample_times(100.0, 5)

    assert timestamps == [8.0, 29.0, 50.0, 71.0, 92.0]


def test_normalize_reference_compensates_global_level_and_contrast() -> None:
    reference = np.arange(100, dtype=np.uint8).reshape(10, 10) + 40
    distorted = np.clip(reference.astype(np.float32) * 1.1 + 7, 0, 255).astype(
        np.uint8
    )

    normalized = normalize_reference(reference, distorted)

    assert float(np.median(np.abs(normalized - distorted))) < 1.5


def test_find_candidates_locates_persistent_top_right_region() -> None:
    median_z = np.zeros((60, 100), dtype=np.float32)
    frequency = np.zeros((60, 100), dtype=np.float32)
    median_z[5:12, 82:96] = 8.0
    frequency[5:12, 82:96] = 1.0
    search_mask = edge_search_mask(median_z.shape, 0.2)

    candidates, active_mask = find_candidates(
        median_z,
        frequency,
        search_mask,
        minimum_median_z=3.0,
        minimum_frequency=0.56,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.corner == "top-right"
    assert candidate.x <= 82
    assert candidate.y <= 5
    assert candidate.x + candidate.width >= 96
    assert candidate.y + candidate.height >= 12
    assert active_mask[7, 90] == 255


def test_find_candidates_rejects_intermittent_residual() -> None:
    median_z = np.zeros((60, 100), dtype=np.float32)
    frequency = np.zeros((60, 100), dtype=np.float32)
    median_z[5:12, 82:96] = 8.0
    frequency[5:12, 82:96] = 0.4

    candidates, _ = find_candidates(
        median_z,
        frequency,
        edge_search_mask(median_z.shape, 0.2),
        minimum_median_z=3.0,
        minimum_frequency=0.56,
    )

    assert candidates == []
