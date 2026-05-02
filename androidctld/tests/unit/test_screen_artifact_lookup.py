from __future__ import annotations

import json
from pathlib import Path

import pytest

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.screen_lookup import lookup_source_screen_artifact
from androidctld.runtime.models import ScreenState

from .support.runtime import build_runtime

_SOURCE_SCREEN_ID = "screen-00041"
_CURRENT_SCREEN_ID = "screen-00042"


def make_runtime(tmp_path: Path, *, current_artifact: Path | None = None):
    runtime = build_runtime(
        tmp_path,
        screen_sequence=42,
        current_screen_id=_CURRENT_SCREEN_ID,
    )
    runtime.screen_state = ScreenState(
        public_screen=None,
        artifacts=(
            None
            if current_artifact is None
            else ScreenArtifacts(screen_json=current_artifact.as_posix())
        ),
    )
    return runtime


def write_screen_artifact(
    path: Path,
    *,
    screen_id: str,
    sequence: int,
    extra_fields: dict[str, object] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "screenId": screen_id,
        "sequence": sequence,
        "sourceSnapshotId": sequence,
        "capturedAt": "2026-04-27T00:00:00Z",
        "packageName": "com.android.settings",
        "activityName": "com.android.settings.Settings",
        "keyboardVisible": False,
        "groups": [
            {"name": "targets", "nodes": []},
            {"name": "keyboard", "nodes": []},
            {"name": "system", "nodes": []},
            {"name": "context", "nodes": []},
            {"name": "dialog", "nodes": []},
        ],
        "repairBindings": {},
    }
    if extra_fields is not None:
        payload.update(extra_fields)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_malformed_artifact(
    path: Path,
    *,
    screen_id: str,
    sequence: int,
) -> Path:
    return write_screen_artifact(
        path,
        screen_id=screen_id,
        sequence=sequence,
        extra_fields={"debugOnly": {"score": 7}},
    )


def test_lookup_source_screen_artifact_is_read_only(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    screens_dir = runtime.artifact_root / "screens"
    valid_path = write_screen_artifact(
        screens_dir / "obs-00041.json",
        screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    unrelated_malformed_path = write_malformed_artifact(
        screens_dir / "obs-00099.json",
        screen_id="screen-99999",
        sequence=99,
    )

    lookup = lookup_source_screen_artifact(runtime, _SOURCE_SCREEN_ID)

    assert lookup.status == "found"
    assert lookup.path == valid_path
    assert lookup.payload is not None
    assert lookup.payload.screen_id == _SOURCE_SCREEN_ID
    assert lookup.payload.sequence == 41
    assert unrelated_malformed_path.exists()


def test_lookup_source_screen_artifact_handles_unreadable_missing_source_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    screens_dir = runtime.artifact_root / "screens"
    unreadable_path = write_screen_artifact(
        screens_dir / "obs-00042.json",
        screen_id="screen-99999",
        sequence=42,
    )
    original_read_text = Path.read_text

    def unreadable_read_text(self: Path, encoding: str = "utf-8") -> str:
        if self == unreadable_path:
            raise PermissionError("denied")
        return original_read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", unreadable_read_text)

    lookup = lookup_source_screen_artifact(runtime, _SOURCE_SCREEN_ID)

    assert lookup.status == "not_found"
    assert lookup.path is None
    assert lookup.payload is None
    assert tuple(scanned.artifact.status for scanned in lookup.scanned) == (
        "unreadable",
    )
    assert unreadable_path.exists()


def test_lookup_source_screen_artifact_invalid_selection_has_no_payload(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    screens_dir = runtime.artifact_root / "screens"
    write_screen_artifact(
        screens_dir / "obs-00041.json",
        screen_id=_SOURCE_SCREEN_ID,
        sequence=41,
    )
    invalid_path = write_malformed_artifact(
        screens_dir / "obs-00042.json",
        screen_id=_SOURCE_SCREEN_ID,
        sequence=42,
    )

    lookup = lookup_source_screen_artifact(runtime, _SOURCE_SCREEN_ID)

    assert lookup.status == "invalid_artifact"
    assert lookup.path == invalid_path
    assert lookup.payload is None
