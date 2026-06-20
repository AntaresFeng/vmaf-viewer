from __future__ import annotations

import hashlib
from pathlib import Path
from shutil import copyfileobj
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
ECHARTS_URL = "https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.min.js"
EXPECTED_SHA256 = "b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0"
OUTPUT = ROOT / "src/vmaf_viewer/static/vendor/echarts.min.js"
MIN_SIZE = 500_000
DOWNLOAD_TIMEOUT = 30


def download_file(url: str, destination: Path, *, timeout: int) -> None:
    with urlopen(url, timeout=timeout) as response, destination.open("wb") as output:
        copyfileobj(response, output)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def temporary_output_for(output: Path) -> Path:
    return output.with_name(f"{output.name}.tmp")


def validate_download(path: Path) -> int:
    size = path.stat().st_size
    if size < MIN_SIZE:
        raise SystemExit(f"Downloaded ECharts file is unexpectedly small: {size} bytes")

    actual_sha256 = file_sha256(path)
    if actual_sha256 != EXPECTED_SHA256:
        raise SystemExit(
            f"Downloaded ECharts SHA256 mismatch: expected {EXPECTED_SHA256}, got {actual_sha256}"
        )
    return size


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = temporary_output_for(OUTPUT)
    if temporary_output.exists():
        temporary_output.unlink()

    try:
        download_file(ECHARTS_URL, temporary_output, timeout=DOWNLOAD_TIMEOUT)
        size = validate_download(temporary_output)
        temporary_output.replace(OUTPUT)
    finally:
        if temporary_output.exists():
            temporary_output.unlink()

    print(f"Saved {OUTPUT} ({size} bytes)")


if __name__ == "__main__":
    main()
