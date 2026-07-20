from pathlib import Path

from fastapi.testclient import TestClient

from vmaf_viewer.app import create_app


def test_api_files_returns_scanned_json_files():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))

    response = client.get("/api/files")

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["files"]] == [
        "alpha_vmaf.json",
        "beta_vmaf.json",
    ]
    assert body["data_dir"].endswith("tests/fixtures")


def test_api_data_dir_switches_scan_root(tmp_path):
    fixture = Path("tests/fixtures/alpha_vmaf.json")
    new_dir = tmp_path / "new-jsons"
    new_dir.mkdir()
    (new_dir / "gamma_vmaf.json").write_text(
        fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))

    response = client.post("/api/data-dir", json={"data_dir": str(new_dir)})

    assert response.status_code == 200
    body = response.json()
    assert body["data_dir"] == new_dir.resolve().as_posix()
    assert [item["name"] for item in body["files"]] == ["gamma_vmaf.json"]
    assert [item["name"] for item in client.get("/api/files").json()["files"]] == [
        "gamma_vmaf.json"
    ]


def test_api_data_dir_rejects_invalid_directory_without_changing_current(tmp_path):
    not_a_dir = tmp_path / "not-a-dir.txt"
    not_a_dir.write_text("not a directory", encoding="utf-8")
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))
    original = client.get("/api/files").json()["data_dir"]

    response = client.post("/api/data-dir", json={"data_dir": str(not_a_dir)})

    assert response.status_code == 400
    assert "directory" in response.json()["detail"]
    assert client.get("/api/files").json()["data_dir"] == original


def test_api_compare_returns_summary_and_charts():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))
    files = client.get("/api/files").json()["files"]

    response = client.post(
        "/api/compare",
        json={
            "file_ids": [item["id"] for item in files],
            "thresholds": [95, 90, 80, 60],
            "max_points": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["frame_domain"] == {"start": 0, "end": 4}
    assert "common_range" not in body
    assert [row["name"] for row in body["summary"]] == [
        "beta_vmaf.json",
        "alpha_vmaf.json",
    ]
    assert all("common_frames" not in row for row in body["summary"])
    alpha = next(row for row in body["summary"] if row["name"] == "alpha_vmaf.json")
    beta = next(row for row in body["summary"] if row["name"] == "beta_vmaf.json")
    assert alpha["stats"]["q1"] == 80.0
    assert alpha["stats"]["median"] == 90.0
    assert alpha["stats"]["q3"] == 96.0
    assert beta["stats"]["q1"] == 88.75
    assert beta["stats"]["median"] == 90.0
    assert beta["stats"]["q3"] == 91.25
    assert set(body["series"]) == {item["id"] for item in files}


def test_api_compare_supports_mixed_json_csv_and_xml_logs(tmp_path):
    alpha = Path("tests/fixtures/alpha_vmaf.json")
    (tmp_path / "alpha_vmaf.json").write_text(
        alpha.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "gamma_vmaf.csv").write_text(
        "Frame,vmaf,integer_motion\n0,93,1\n1,92,2\n2,91,3\n3,90,4\n",
        encoding="utf-8",
    )
    (tmp_path / "delta_vmaf.xml").write_text(
        """
        <VMAF version="fixture">
          <frames>
            <frame frameNum="0" vmaf="89"/>
            <frame frameNum="1" vmaf="88"/>
            <frame frameNum="2" vmaf="87"/>
            <frame frameNum="3" vmaf="86"/>
          </frames>
        </VMAF>
        """,
        encoding="utf-8",
    )
    client = TestClient(create_app(data_dir=tmp_path))
    files = client.get("/api/files").json()["files"]

    response = client.post(
        "/api/compare",
        json={"file_ids": [item["id"] for item in files], "max_points": 100},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in files] == [
        "alpha_vmaf.json",
        "delta_vmaf.xml",
        "gamma_vmaf.csv",
    ]
    assert {row["name"] for row in body["summary"]} == {
        "alpha_vmaf.json",
        "gamma_vmaf.csv",
        "delta_vmaf.xml",
    }
    assert body["frame_domain"] == {"start": 0, "end": 4}


def test_api_compare_skips_bad_json_and_keeps_valid_results(tmp_path):
    fixture = Path("tests/fixtures/alpha_vmaf.json")
    (tmp_path / "alpha_vmaf.json").write_text(
        fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "bad_vmaf.json").write_text("{not json", encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    files = client.get("/api/files").json()["files"]

    response = client.post(
        "/api/compare",
        json={
            "file_ids": [item["id"] for item in files],
            "thresholds": [90],
            "max_points": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["name"] for row in body["summary"]] == ["alpha_vmaf.json"]
    assert set(body["series"]) == {files[0]["id"]}
    assert body["warnings"] == ["Invalid JSON in bad_vmaf.json"]


def test_api_compare_skips_bad_csv_and_keeps_valid_results(tmp_path):
    fixture = Path("tests/fixtures/alpha_vmaf.json")
    (tmp_path / "alpha_vmaf.json").write_text(
        fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "bad_vmaf.csv").write_text("vmaf\n99.0\n", encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    files = client.get("/api/files").json()["files"]

    response = client.post(
        "/api/compare",
        json={
            "file_ids": [item["id"] for item in files],
            "thresholds": [90],
            "max_points": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["name"] for row in body["summary"]] == ["alpha_vmaf.json"]
    assert body["warnings"] == ["bad_vmaf.csv is missing 'Frame' column"]


def test_api_compare_rejects_empty_selection():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))

    response = client.post("/api/compare", json={"file_ids": [], "thresholds": [90]})

    assert response.status_code == 400
    assert response.json()["detail"] == "Select at least one VMAF log file."


def test_api_compare_rejects_unknown_file_id():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))

    response = client.post(
        "/api/compare", json={"file_ids": ["missing"], "thresholds": [90]}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown file id: missing"


def test_api_metrics_returns_metric_names_for_one_file():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))
    file_id = client.get("/api/files").json()["files"][0]["id"]

    response = client.get(f"/api/file/{file_id}/metrics")

    assert response.status_code == 200
    assert response.json()["metrics"] == ["vmaf", "integer_motion"]


def test_api_series_returns_requested_metric_range():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))
    files = client.get("/api/files").json()["files"]

    response = client.post(
        "/api/series",
        json={
            "file_ids": [files[0]["id"]],
            "metrics": ["integer_motion"],
            "start": 1,
            "end": 3,
            "max_points": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["series"][files[0]["id"]]["integer_motion"]["points"] == [
        [1, 1.5],
        [2, 2.0],
        [3, 2.5],
    ]


def test_api_series_filters_each_file_by_native_frame_numbers(tmp_path):
    (tmp_path / "dense_vmaf.json").write_text(
        '{"frames":['
        '{"frameNum":0,"metrics":{"vmaf":90}},'
        '{"frameNum":1,"metrics":{"vmaf":91}},'
        '{"frameNum":2,"metrics":{"vmaf":92}},'
        '{"frameNum":3,"metrics":{"vmaf":93}}'
        "]}",
        encoding="utf-8",
    )
    (tmp_path / "subsampled_vmaf.json").write_text(
        '{"frames":['
        '{"frameNum":0,"metrics":{"vmaf":80}},'
        '{"frameNum":2,"metrics":{"vmaf":82}},'
        '{"frameNum":4,"metrics":{"vmaf":84}}'
        "]}",
        encoding="utf-8",
    )
    client = TestClient(create_app(data_dir=tmp_path))
    files = client.get("/api/files").json()["files"]

    response = client.post(
        "/api/series",
        json={
            "file_ids": [item["id"] for item in files],
            "metrics": ["vmaf"],
            "start": 1,
            "end": 3,
            "max_points": 100,
        },
    )

    assert response.status_code == 200
    points_by_name = {
        item["name"]: response.json()["series"][item["id"]]["vmaf"]["points"]
        for item in files
    }
    assert points_by_name["dense_vmaf.json"] == [[1, 91.0], [2, 92.0], [3, 93.0]]
    assert points_by_name["subsampled_vmaf.json"] == [[2, 82.0]]


def test_index_returns_clear_response_when_frontend_is_missing():
    client = TestClient(
        create_app(data_dir=Path("tests/fixtures")), raise_server_exceptions=False
    )

    response = client.get("/")

    assert response.status_code in {200, 404}
    if response.status_code == 404:
        assert response.json()["detail"] == "Viewer frontend is not available yet."


def test_api_metrics_returns_bad_request_for_invalid_json(tmp_path):
    (tmp_path / "bad_vmaf.json").write_text("{not json", encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    file_id = client.get("/api/files").json()["files"][0]["id"]

    response = client.get(f"/api/file/{file_id}/metrics")

    assert response.status_code == 400
    assert "Invalid JSON" in response.json()["detail"]


def test_api_series_returns_bad_request_for_invalid_json(tmp_path):
    (tmp_path / "bad_vmaf.json").write_text("{not json", encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    file_id = client.get("/api/files").json()["files"][0]["id"]

    response = client.post(
        "/api/series",
        json={"file_ids": [file_id], "metrics": ["vmaf"], "max_points": 100},
    )

    assert response.status_code == 400
    assert "Invalid JSON" in response.json()["detail"]


def test_api_metrics_and_series_return_bad_request_for_invalid_xml(tmp_path):
    (tmp_path / "bad_vmaf.xml").write_text("<VMAF><frames>", encoding="utf-8")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    file_id = client.get("/api/files").json()["files"][0]["id"]

    metrics_response = client.get(f"/api/file/{file_id}/metrics")
    series_response = client.post(
        "/api/series",
        json={"file_ids": [file_id], "metrics": ["vmaf"], "max_points": 100},
    )

    assert metrics_response.status_code == 400
    assert series_response.status_code == 400
    assert "Invalid XML" in metrics_response.json()["detail"]
    assert "Invalid XML" in series_response.json()["detail"]


def test_api_metrics_returns_bad_request_for_non_utf8_csv(tmp_path):
    (tmp_path / "bad_vmaf.csv").write_bytes(b"frameNum,vmaf\n0,\xff\n")
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    file_id = client.get("/api/files").json()["files"][0]["id"]

    response = client.get(f"/api/file/{file_id}/metrics")

    assert response.status_code == 400
    assert "Invalid CSV" in response.json()["detail"]


def test_api_returns_bad_request_for_invalid_frame_num(tmp_path):
    (tmp_path / "bad_vmaf.json").write_text(
        '{"frames":[{"frameNum":null,"metrics":{"vmaf":99}}]}',
        encoding="utf-8",
    )
    client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
    file_id = client.get("/api/files").json()["files"][0]["id"]

    metrics_response = client.get(f"/api/file/{file_id}/metrics")
    series_response = client.post(
        "/api/series",
        json={"file_ids": [file_id], "metrics": ["vmaf"], "max_points": 100},
    )
    compare_response = client.post(
        "/api/compare",
        json={"file_ids": [file_id], "max_points": 100},
    )

    assert metrics_response.status_code == 400
    assert series_response.status_code == 400
    assert compare_response.status_code == 200
    assert "invalid frameNum" in metrics_response.json()["detail"]
    assert "invalid frameNum" in series_response.json()["detail"]
    assert compare_response.json()["summary"] == []
    assert compare_response.json()["warnings"] == [
        "bad_vmaf.json has invalid frameNum: None"
    ]


def test_api_rejects_invalid_max_points():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))
    file_id = client.get("/api/files").json()["files"][0]["id"]

    compare_response = client.post(
        "/api/compare",
        json={"file_ids": [file_id], "max_points": 1},
    )
    series_response = client.post(
        "/api/series",
        json={"file_ids": [file_id], "metrics": ["vmaf"], "max_points": 1},
    )

    assert compare_response.status_code == 422
    assert series_response.status_code == 422
