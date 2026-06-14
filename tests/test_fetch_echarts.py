from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


EXPECTED_URL = "https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fetch_echarts.py"
SPEC = importlib.util.spec_from_file_location("fetch_echarts", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
fetch_echarts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_echarts)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def tmp_file_for(output: Path) -> Path:
    return output.with_name(f"{output.name}.tmp")


def fake_download(monkeypatch: pytest.MonkeyPatch, content: bytes, calls: list[tuple[str, Path, int]]) -> None:
    def download_file(url: str, destination: Path, *, timeout: int) -> None:
        calls.append((url, Path(destination), timeout))
        Path(destination).write_bytes(content)

    monkeypatch.setattr(fetch_echarts, "download_file", download_file)


def test_rejects_small_download_without_replacing_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "vendor" / "echarts.min.js"
    output.parent.mkdir()
    output.write_bytes(b"existing asset")
    calls: list[tuple[str, Path, int]] = []

    monkeypatch.setattr(fetch_echarts, "OUTPUT", output)
    monkeypatch.setattr(fetch_echarts, "EXPECTED_SHA256", sha256(b"tiny"), raising=False)
    fake_download(monkeypatch, b"tiny", calls)

    with pytest.raises(SystemExit, match="unexpectedly small"):
        fetch_echarts.main()

    assert output.read_bytes() == b"existing asset"
    assert not tmp_file_for(output).exists()


def test_rejects_hash_mismatch_without_replacing_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "vendor" / "echarts.min.js"
    output.parent.mkdir()
    output.write_bytes(b"existing asset")
    downloaded = b"x" * 500_001
    calls: list[tuple[str, Path, int]] = []

    monkeypatch.setattr(fetch_echarts, "OUTPUT", output)
    monkeypatch.setattr(fetch_echarts, "EXPECTED_SHA256", sha256(b"different content"), raising=False)
    fake_download(monkeypatch, downloaded, calls)

    with pytest.raises(SystemExit, match="SHA256 mismatch"):
        fetch_echarts.main()

    assert output.read_bytes() == b"existing asset"
    assert not tmp_file_for(output).exists()


def test_verified_download_replaces_final_file_and_removes_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "vendor" / "echarts.min.js"
    downloaded = b"x" * 500_001
    calls: list[tuple[str, Path, int]] = []

    monkeypatch.setattr(fetch_echarts, "OUTPUT", output)
    monkeypatch.setattr(fetch_echarts, "EXPECTED_SHA256", sha256(downloaded), raising=False)
    fake_download(monkeypatch, downloaded, calls)

    fetch_echarts.main()

    assert output.read_bytes() == downloaded
    assert not tmp_file_for(output).exists()
    assert calls == [(EXPECTED_URL, tmp_file_for(output), 30)]
