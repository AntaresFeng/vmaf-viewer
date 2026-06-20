from pathlib import Path


STATIC_DIR = Path("src/vmaf_viewer/static")


def test_distribution_layout_uses_equal_desktop_columns():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);" in css


def test_distribution_chart_options_keep_labels_readable():
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "grid: { top: 36, right: 18, bottom: 64, left: 52, containLabel: true }" in app_js
    assert 'name: "Frames",' in app_js
    assert "nameGap: 12" in app_js


def test_boxplot_hides_video_filename_labels():
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "grid: { top: 24, right: 18, bottom: 42, left: 44, containLabel: true }" in app_js
    assert "axisLabel: { show: false }" in app_js
