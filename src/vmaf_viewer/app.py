from __future__ import annotations

import os
from bisect import bisect_left, bisect_right
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .cache import VmafCache
from .compare import compare_files
from .models import FileRecord, ParsedVmaf
from .parser import VmafParseError
from .scanner import scan_vmaf_files
from .stats import downsample_series

DEFAULT_THRESHOLDS = [95.0, 90.0, 80.0, 60.0]


class CompareRequest(BaseModel):
    file_ids: list[str]
    metric: str | None = None
    thresholds: list[float] = Field(default_factory=lambda: DEFAULT_THRESHOLDS.copy())
    max_points: int = Field(default=2000, ge=2, le=100000)


class SeriesRequest(BaseModel):
    file_ids: list[str]
    metrics: list[str]
    start: int = 0
    end: int | None = None
    max_points: int = Field(default=2000, ge=2, le=100000)


class AppState:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.cache = VmafCache()

    def records(self) -> list[FileRecord]:
        return scan_vmaf_files(self.data_dir)

    def selected_records(self, file_ids: list[str]) -> list[FileRecord]:
        by_id = {record.id: record for record in self.records()}
        missing = [file_id for file_id in file_ids if file_id not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"Unknown file id: {missing[0]}")
        return [by_id[file_id] for file_id in file_ids]


def _default_data_dir() -> Path:
    configured = os.environ.get("VMAF_VIEWER_DATA_DIR")
    if configured:
        return Path(configured)
    return Path.cwd() / "videos"


def _file_api(record: FileRecord, total_frames: int | None = None, primary_metric: str | None = None) -> dict:
    item = record.to_api()
    if total_frames is not None:
        item["total_frames"] = total_frames
    if primary_metric is not None:
        item["primary_metric"] = primary_metric
    return item


def _parsed_or_http_error(cache: VmafCache, record: FileRecord) -> ParsedVmaf:
    try:
        return cache.get(record)
    except VmafParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"Unable to read {record.relative_path}") from exc


def create_app(data_dir: Path | None = None) -> FastAPI:
    state = AppState(data_dir or _default_data_dir())
    app = FastAPI(title="VMAF JSON Viewer")
    app.state.vmaf_viewer = state

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        index_path = static_dir / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Viewer frontend is not available yet.")
        return FileResponse(index_path)

    @app.get("/api/files")
    def api_files() -> dict:
        return {
            "data_dir": state.data_dir.as_posix(),
            "files": [record.to_api() for record in state.records()],
        }

    @app.post("/api/compare")
    def api_compare(request: CompareRequest) -> dict:
        records = state.selected_records(request.file_ids)
        try:
            return compare_files(
                records,
                state.cache,
                request.thresholds,
                metric=request.metric,
                max_points=request.max_points,
            )
        except VmafParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/file/{file_id}/metrics")
    def api_metrics(file_id: str) -> dict:
        record = state.selected_records([file_id])[0]
        parsed = _parsed_or_http_error(state.cache, record)
        return {
            "file": _file_api(record, parsed.total_frames, parsed.primary_metric),
            "metrics": list(parsed.metrics),
            "primary_metric": parsed.primary_metric,
            "total_frames": parsed.total_frames,
        }

    @app.post("/api/series")
    def api_series(request: SeriesRequest) -> dict:
        if request.start < 0:
            raise HTTPException(status_code=400, detail="start must be non-negative")
        if request.end is not None and request.end < request.start:
            raise HTTPException(status_code=400, detail="end must be greater than or equal to start")

        records = state.selected_records(request.file_ids)
        response_series: dict[str, dict[str, dict[str, list[list[float]]]]] = {}
        files: list[dict] = []

        for record in records:
            parsed = _parsed_or_http_error(state.cache, record)
            files.append(_file_api(record, parsed.total_frames, parsed.primary_metric))
            metric_series: dict[str, dict[str, list[list[float]]]] = {}
            start_index = bisect_left(parsed.frame_numbers, request.start)
            stop_index = (
                len(parsed.frame_numbers)
                if request.end is None
                else bisect_right(parsed.frame_numbers, request.end)
            )
            frames = parsed.frame_numbers[start_index:stop_index]

            for metric_name in request.metrics:
                if metric_name not in parsed.metrics:
                    raise HTTPException(
                        status_code=404,
                        detail=f"{record.name} is missing metric {metric_name}.",
                    )

                values = parsed.metrics[metric_name][start_index:stop_index]

                try:
                    points = downsample_series(frames, values, max_points=request.max_points)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                metric_series[metric_name] = {"points": points}

            response_series[record.id] = metric_series

        return {
            "files": files,
            "range": {"start": request.start, "end": request.end},
            "series": response_series,
        }

    return app


def main() -> None:
    uvicorn.run(
        "vmaf_viewer.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8765,
        reload=True,
    )
