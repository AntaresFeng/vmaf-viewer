from __future__ import annotations

from pathlib import Path

import numpy as np


from vmaf_workflow import watermark_detection

extract_gray_frames = watermark_detection.extract_gray_frames
edge_search_mask = watermark_detection.edge_search_mask
find_candidates = watermark_detection.find_candidates
normalize_reference = watermark_detection.normalize_reference
sample_times = watermark_detection.sample_times


def test_extract_gray_frames_batches_rawvideo_output(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, *, check, capture_output):
        commands.append(command)
        assert check is True
        assert capture_output is True
        return type("Result", (), {"stdout": bytes(range(24))})()

    monkeypatch.setattr(watermark_detection.subprocess, "run", fake_run)

    frames = extract_gray_frames(Path("video.mp4"), 12.5, 3, (4, 2), "ffmpeg")

    assert frames.shape == (3, 2, 4)
    assert frames.ravel().tolist() == list(range(24))
    command = commands[0]
    assert command[command.index("-ss") + 1] == "12.500000"
    assert command[command.index("-frames:v") + 1] == "3"
    assert command[command.index("-fps_mode") + 1] == "passthrough"


def test_sample_times_avoid_video_ends() -> None:
    timestamps = sample_times(100.0, 5)

    assert timestamps == [8.0, 29.0, 50.0, 71.0, 92.0]


def test_normalize_reference_compensates_global_level_and_contrast() -> None:
    reference = np.arange(100, dtype=np.uint8).reshape(10, 10) + 40
    distorted = np.clip(reference.astype(np.float32) * 1.1 + 7, 0, 255).astype(np.uint8)

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
