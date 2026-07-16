from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RemoteStateError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_remote_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RemoteStateError(f"remote state is required: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RemoteStateError(f"remote state is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise RemoteStateError(f"remote state must be a JSON object: {path}")
    if data.get("schema_version") != 1:
        raise RemoteStateError("remote state schema_version must be 1")
    return data


def write_remote_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(
        json.dumps(
            state,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
