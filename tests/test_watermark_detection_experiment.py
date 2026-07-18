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
analysis_size = watermark_detection.analysis_size
edge_search_mask = watermark_detection.edge_search_mask
find_candidates = watermark_detection.find_candidates
normalize_reference = watermark_detection.normalize_reference
parse_fraction = watermark_detection.parse_fraction
sample_times = watermark_detection.sample_times


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
