from pathlib import Path


STATIC_DIR = Path("src/vmaf_viewer/static")


def test_index_loads_metric_metadata_before_app_js():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    metadata_index = html.index('/static/metric_metadata.js')
    app_index = html.index('/static/app.js')

    assert "<h2>Detail View</h2>" in html
    assert metadata_index < app_index


def test_app_uses_detail_metric_state_and_not_primary_metric_toggle():
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "activeDetailMetrics" in app_js
    assert "Primary VMAF" not in app_js
    assert "VmafMetricMetadata.defaultDetailMetrics" in app_js
    assert "VmafMetricMetadata.toggleDetailMetric" in app_js
    assert "No active detail metrics." in app_js


def test_metric_chip_axis_tag_styles_exist():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".metric-axis-tag" in css
    assert ".metric-axis-tag-normalized" in css
    assert ".metric-axis-tag-raw" in css
