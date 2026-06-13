from __future__ import annotations

from pathlib import Path
from urllib.request import urlretrieve


ECHARTS_URL = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"
OUTPUT = Path("src/vmaf_viewer/static/vendor/echarts.min.js")


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(ECHARTS_URL, OUTPUT)
    size = OUTPUT.stat().st_size
    if size < 500_000:
        raise SystemExit(f"Downloaded ECharts file is unexpectedly small: {size} bytes")
    print(f"Saved {OUTPUT} ({size} bytes)")


if __name__ == "__main__":
    main()
