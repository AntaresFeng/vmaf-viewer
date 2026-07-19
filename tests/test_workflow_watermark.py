from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from vmaf_workflow.config import EasyVmafSettings
from vmaf_workflow.download_state import invalidate_downstream
from vmaf_workflow.packager import package_project
from vmaf_workflow.prepare import (
    PrepareError,
    prepare_project,
    select_bilibili_representative,
)
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_plan import write_remote_plan
from vmaf_workflow.status import inspect_workflow_status
from vmaf_workflow.watermark_detection import (
    Candidate,
    DetectionResult,
    DetectionSettings,
    MediaInfo,
    map_normalized_edges,
    map_real_edges_to_target,
    outward_bbox,
)


BVID = "BV1jm756EEzH"
NORMALIZED_EDGES = {
    "left": 0.8,
    "top": 0.02,
    "right": 0.95,
    "bottom": 0.08,
}


def _entry(path: str, width: int, height: int, codec: str) -> dict:
    return {
        "path": path,
        "role": "distorted",
        "width": width,
        "height": height,
        "codec": codec,
    }


def test_selects_unique_bilibili_1080p_avc_representative() -> None:
    files = [
        _entry(f"{BVID}-1080P-AVC.mp4", 1920, 1080, "h264"),
        _entry(f"{BVID}-4K-AV1.mp4", 3840, 2160, "av1"),
        _entry("youtube-1080.mp4", 1920, 1080, "h264"),
    ]

    selected = select_bilibili_representative(files, BVID)

    assert selected["path"] == f"{BVID}-1080P-AVC.mp4"


@pytest.mark.parametrize(
    "files",
    [
        [_entry(f"{BVID}-4K-AVC.mp4", 3840, 2160, "h264")],
        [_entry(f"{BVID}-1080P-AV1.mp4", 1920, 1080, "av1")],
        [_entry("other-1080P-AVC.mp4", 1920, 1080, "h264")],
        [
            _entry(f"{BVID}-1080P-AVC.mp4", 1920, 1080, "h264"),
            _entry(f"{BVID}-second-1080P-AVC.mp4", 1920, 1080, "h264"),
        ],
    ],
)
def test_representative_selection_rejects_missing_or_multiple(
    files: list[dict],
) -> None:
    with pytest.raises(PrepareError, match="exactly one"):
        select_bilibili_representative(files, BVID)


@pytest.mark.parametrize(
    ("width", "height"),
    [(1920, 1080), (2560, 1440), (3840, 2160)],
)
def test_maps_analysis_normalized_box_to_true_media_resolution(
    width: int,
    height: int,
) -> None:
    analysis_edges = {"left": 768, "top": 10.8, "right": 912, "bottom": 43.2}
    normalized = {
        "left": analysis_edges["left"] / 960,
        "top": analysis_edges["top"] / 540,
        "right": analysis_edges["right"] / 960,
        "bottom": analysis_edges["bottom"] / 540,
    }

    mapped = map_normalized_edges(normalized, width, height)

    assert mapped == {
        "left": width * 0.8,
        "top": height * 0.02,
        "right": width * 0.95,
        "bottom": height * 0.08,
    }


@pytest.mark.parametrize(
    ("real_size", "target_size", "margin", "expected"),
    [
        ((2560, 1440), (1920, 1080), 8, (1528, 13, 304, 82)),
        ((3840, 2160), (1920, 1080), 8, (1528, 13, 304, 82)),
        ((1920, 1080), (3840, 2160), 16, (3056, 27, 608, 162)),
    ],
)
def test_maps_true_resolution_through_easyvmaf_stretch(
    real_size: tuple[int, int],
    target_size: tuple[int, int],
    margin: int,
    expected: tuple[int, int, int, int],
) -> None:
    real_edges = map_normalized_edges(NORMALIZED_EDGES, *real_size)
    target_edges = map_real_edges_to_target(real_edges, *real_size, *target_size)

    bbox = outward_bbox(target_edges, *target_size, margin)

    assert (bbox["x"], bbox["y"], bbox["width"], bbox["height"]) == expected


def test_outward_bbox_clamps_margin_to_frame_edges() -> None:
    bbox = outward_bbox(
        {"left": 1.2, "top": 2.1, "right": 1918.4, "bottom": 1079.2},
        1920,
        1080,
        8,
    )

    assert bbox == {
        "x": 0,
        "y": 0,
        "width": 1920,
        "height": 1080,
        "left": 0,
        "top": 0,
        "right": 1920,
        "bottom": 1080,
    }


def test_prepare_without_bvid_skips_detector(tmp_path: Path, monkeypatch) -> None:
    project = _base_project(tmp_path, with_bvid=False)
    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", _fake_probe)

    def fail_detector(*_args, **_kwargs):
        raise AssertionError("detector must not run without BVID")

    monkeypatch.setattr("vmaf_workflow.prepare.detect_watermark", fail_detector)

    inventory = prepare_project(project, project.video_dir / "reference.mp4")

    assert inventory["watermark_detection"] == {
        "applicable": False,
        "state": "not_applicable",
        "detector": "reference-assisted-positive-residual-v1",
    }
    assert inventory["content_exclusions"] == []


def test_prepare_present_writes_exclusion_summary_and_audit_mappings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _base_project(tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", _fake_probe)
    monkeypatch.setattr(
        "vmaf_workflow.prepare.detect_watermark",
        lambda distorted, reference, output: (
            calls.append(Path(distorted)) or _result("present", output)
        ),
    )

    inventory = prepare_project(project, project.video_dir / "reference.mp4")

    assert calls == [project.video_dir / f"{BVID}-1080P-AVC.mp4"]
    assert inventory["watermark_detection"]["state"] == "present"
    assert inventory["content_exclusions"] == [
        {
            "kind": "bilibili_watermark",
            "normalized_edges": NORMALIZED_EDGES,
        }
    ]
    summary = _read_json(project.watermark_summary_path)
    assert summary["workflow"]["representative_path"] == (f"{BVID}-1080P-AVC.mp4")
    mappings = {item["path"]: item for item in summary["workflow"]["media_mappings"]}
    assert mappings["youtube-1440.mp4"]["real_pixel_edges"]["right"] == 2432
    assert mappings["reference.mp4"]["width"] == 3840


def test_prepare_present_removes_recorded_full_frame_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _base_project(tmp_path)
    old_result = project.video_dir / "youtube-1440_vmaf.json"
    old_result.write_text("{}", encoding="utf-8")
    manifest = _read_json(project.manifest_path)
    manifest["results"] = {"files": [str(old_result)]}
    _write_json(project.manifest_path, manifest)
    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", _fake_probe)
    monkeypatch.setattr(
        "vmaf_workflow.prepare.detect_watermark",
        lambda _distorted, _reference, output: _result("present", output),
    )

    prepare_project(project, project.video_dir / "reference.mp4")

    assert not old_result.exists()
    assert "results" not in _read_json(project.manifest_path)


def test_prepare_absent_continues_without_exclusion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _base_project(tmp_path)
    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", _fake_probe)
    monkeypatch.setattr(
        "vmaf_workflow.prepare.detect_watermark",
        lambda _distorted, _reference, output: _result("absent", output),
    )

    inventory = prepare_project(project, project.video_dir / "reference.mp4")

    assert inventory["watermark_detection"]["state"] == "absent"
    assert inventory["content_exclusions"] == []
    assert project.watermark_summary_path.is_file()


def test_prepare_uncertain_keeps_diagnostics_and_does_not_write_inventory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _base_project(tmp_path)
    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", _fake_probe)
    monkeypatch.setattr(
        "vmaf_workflow.prepare.detect_watermark",
        lambda _distorted, _reference, output: _result("uncertain", output),
    )

    with pytest.raises(PrepareError, match="multiple candidates"):
        prepare_project(project, project.video_dir / "reference.mp4")

    assert not project.media_inventory_path.exists()
    assert project.watermark_summary_path.is_file()
    assert _read_json(project.watermark_summary_path)["state"] == "uncertain"


@pytest.mark.parametrize(
    ("path", "override", "message"),
    [
        ("youtube-1440.mp4", {"width": 2048, "height": 1080}, "aspect ratio"),
        ("youtube-1440.mp4", {"sample_aspect_ratio": "4:3"}, "square pixels"),
        ("youtube-1440.mp4", {"rotation": 90}, "rotation"),
        ("youtube-1440.mp4", {"width": None}, "missing dimensions"),
    ],
)
def test_prepare_rejects_unsupported_media_geometry(
    tmp_path: Path,
    monkeypatch,
    path: str,
    override: dict,
    message: str,
) -> None:
    project = _base_project(tmp_path)

    def probe(media_path: Path) -> dict:
        metadata = _fake_probe(media_path)
        if media_path.name == path:
            metadata.update(override)
        return metadata

    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", probe)
    monkeypatch.setattr(
        "vmaf_workflow.prepare.detect_watermark",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not detect")),
    )

    with pytest.raises(PrepareError, match=message):
        prepare_project(project, project.video_dir / "reference.mp4")


def test_package_and_remote_plan_apply_filter_to_every_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _base_project(tmp_path)
    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", _fake_probe)
    monkeypatch.setattr(
        "vmaf_workflow.prepare.detect_watermark",
        lambda _distorted, _reference, output: _result("present", output),
    )
    prepare_project(project, project.video_dir / "reference.mp4")

    package_manifest = package_project(project)
    plan = write_remote_plan(
        project,
        EasyVmafSettings(repo_dir=Path("/opt/easyVmaf")),
    )

    assert package_manifest["inventory_sha256"]
    assert package_manifest["watermark_analysis_sha256"]
    with tarfile.open(project.default_package_path, "r") as archive:
        names = archive.getnames()
    assert (
        f"{project.video_dir.name}/.workflow/watermark-analysis/summary.json" in names
    )
    assert not any(name.endswith(".png") for name in names)
    assert plan["score_scope"] == "content_excluding_regions"
    assert plan["content_exclusions"] == [
        {
            "kind": "bilibili_watermark",
            "normalized_edges": NORMALIZED_EDGES,
        }
    ]
    assert all(command["pre_filter"] for command in plan["commands"])
    assert all("-pre_filter" in command["command"] for command in plan["commands"])
    by_path = {command["distorted"]["path"]: command for command in plan["commands"]}
    assert by_path["youtube-1440.mp4"]["target_resolution"] == {
        "width": 1920,
        "height": 1080,
    }
    assert by_path[f"{BVID}-4K-AV1.mp4"]["target_resolution"] == {
        "width": 3840,
        "height": 2160,
    }
    assert by_path["youtube-1440.mp4"]["excluded_bbox"] == {
        "x": 1528,
        "y": 13,
        "width": 304,
        "height": 82,
        "left": 1528,
        "top": 13,
        "right": 1832,
        "bottom": 95,
    }
    assert by_path[f"{BVID}-4K-AV1.mp4"]["excluded_bbox"]["x"] == 3056
    script = project.remote_plan_script_path.read_text(encoding="utf-8")
    assert "filter=drawbox" in script
    assert "required -pre_filter option" in script


def test_absent_remote_plan_keeps_full_frame_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _base_project(tmp_path)
    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", _fake_probe)
    monkeypatch.setattr(
        "vmaf_workflow.prepare.detect_watermark",
        lambda _distorted, _reference, output: _result("absent", output),
    )
    prepare_project(project, project.video_dir / "reference.mp4")
    package_project(project)

    plan = write_remote_plan(project, EasyVmafSettings(repo_dir=Path("/easy")))

    assert plan["score_scope"] == "full_frame"
    assert plan["content_exclusions"] == []
    assert all(command["pre_filter"] is None for command in plan["commands"])
    assert all("-pre_filter" not in command["command"] for command in plan["commands"])


def test_coordinate_change_invalidates_existing_package_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _base_project(tmp_path)
    monkeypatch.setattr("vmaf_workflow.prepare._probe_media", _fake_probe)
    monkeypatch.setattr(
        "vmaf_workflow.prepare.detect_watermark",
        lambda _distorted, _reference, output: _result("present", output),
    )
    prepare_project(project, project.video_dir / "reference.mp4")
    package_project(project)
    inventory = _read_json(project.media_inventory_path)
    summary = _read_json(project.watermark_summary_path)
    inventory["content_exclusions"][0]["normalized_edges"]["left"] = 0.79
    summary["normalized_edges"]["left"] = 0.79
    _write_json(project.media_inventory_path, inventory)
    _write_json(project.watermark_summary_path, summary)

    status = inspect_workflow_status(project)

    assert status.stage == "prepared"
    assert str(project.package_manifest_path) in status.missing_artifacts


def test_download_invalidation_removes_watermark_analysis(
    tmp_path: Path,
) -> None:
    project = _base_project(tmp_path)
    project.watermark_analysis_dir.mkdir()
    project.watermark_summary_path.write_text("{}", encoding="utf-8")
    manifest = {
        "media_inventory": "old",
        "package": {"path": "old"},
        "bilibili": {"bvid": BVID},
    }

    invalidate_downstream(project, manifest)

    assert not project.watermark_analysis_dir.exists()
    assert "media_inventory" not in manifest
    assert manifest["bilibili"]["bvid"] == BVID


def _base_project(tmp_path: Path, *, with_bvid: bool = True) -> WorkflowProject:
    video_dir = tmp_path / "video0"
    workflow_dir = video_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    for name in (
        "reference.mp4",
        f"{BVID}-1080P-AVC.mp4",
        f"{BVID}-4K-AV1.mp4",
        "youtube-1440.mp4",
    ):
        (video_dir / name).write_bytes(name.encode("utf-8"))
    manifest = {"project_dir": str(video_dir), "workflow_dir": str(workflow_dir)}
    if with_bvid:
        manifest["bilibili"] = {"bvid": BVID}
    _write_json(workflow_dir / "manifest.json", manifest)
    return WorkflowProject(video_dir, workflow_dir)


def _fake_probe(path: Path) -> dict:
    sizes = {
        "reference.mp4": (3840, 2160, "h264"),
        f"{BVID}-1080P-AVC.mp4": (1920, 1080, "h264"),
        f"{BVID}-4K-AV1.mp4": (3840, 2160, "av1"),
        "youtube-1440.mp4": (2560, 1440, "vp9"),
    }
    width, height, codec = sizes[path.name]
    return {
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
        "fps": 60.0,
        "codec": codec,
        "container": "mov,mp4",
        "sample_aspect_ratio": "1:1",
        "display_aspect_ratio": "16:9",
        "rotation": 0,
    }


def _result(state: str, output_dir: Path) -> DetectionResult:
    candidates: tuple[Candidate, ...]
    if state == "absent":
        candidates = ()
    else:
        first = Candidate(
            x=768,
            y=10,
            width=144,
            height=33,
            x_norm=0.8,
            y_norm=0.02,
            width_norm=0.15,
            height_norm=0.06,
            corner="top-right",
            score=500.0,
            median_z=8.0,
            frequency=1.0,
            pixels=100,
        )
        if state == "uncertain":
            second = Candidate(
                x=10,
                y=10,
                width=100,
                height=30,
                x_norm=0.01,
                y_norm=0.02,
                width_norm=0.1,
                height_norm=0.05,
                corner="top-left",
                score=400.0,
                median_z=7.0,
                frequency=1.0,
                pixels=80,
            )
            candidates = (first, second)
        else:
            candidates = (first,)
    return DetectionResult(
        state=state,  # type: ignore[arg-type]
        distorted=MediaInfo("distorted.mp4", 1920, 1080, 60.0, 100.0),
        reference=MediaInfo("reference.mp4", 3840, 2160, 60.0, 100.0),
        analysis_width=960,
        analysis_height=540,
        candidates=candidates,
        alignments=(),
        settings=DetectionSettings(),
        output_dir=str(output_dir),
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
