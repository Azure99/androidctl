from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.writer import ArtifactWriter
from androidctld.commands.result_models import semantic_artifact_payload
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.refs.models import RefRegistry
from androidctld.semantics.public_models import (
    PublicApp,
    PublicFocus,
    PublicNode,
    PublicScreen,
    PublicSurface,
    build_public_groups,
)

from ..support.runtime_store import runtime_store_for_workspace


def _make_screen(*, screen_id: str, sequence: int, label: str) -> PublicScreen:
    return PublicScreen(
        screen_id=screen_id,
        app=PublicApp(
            package_name="com.android.settings",
            activity_name="SettingsActivity",
        ),
        surface=PublicSurface(
            keyboard_visible=False,
            focus=PublicFocus(),
        ),
        groups=build_public_groups(
            targets=(
                PublicNode(
                    ref="n1",
                    role="text",
                    label=label,
                    state=(),
                    actions=(),
                ),
            ),
        ),
        omitted=(),
        visible_windows=(),
        transient=(),
    )


def _commit_screen(
    writer: ArtifactWriter,
    runtime: Any,
    public_screen: PublicScreen,
    *,
    sequence: int,
    source_snapshot_id: int,
    captured_at: str,
    ref_registry: RefRegistry | None = None,
) -> ScreenArtifacts:
    staged = writer.stage_screen(
        runtime,
        public_screen,
        sequence=sequence,
        source_snapshot_id=source_snapshot_id,
        captured_at=captured_at,
        ref_registry=ref_registry,
    )
    try:
        staged.commit()
    except Exception:
        staged.rollback()
        staged.discard()
        raise
    staged.discard()
    return staged.artifacts


def test_writer_keeps_result_local_paths_when_screen_id_is_stable(
    tmp_path: Path,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    first_screen = _make_screen(
        screen_id="screen-1234567890123456",
        sequence=1,
        label="Wi-Fi",
    )
    second_screen = _make_screen(
        screen_id="screen-1234567890123456",
        sequence=2,
        label="Bluetooth",
    )

    first_artifacts = _commit_screen(
        writer,
        runtime,
        first_screen,
        sequence=1,
        source_snapshot_id=101,
        captured_at="2026-04-13T00:00:01Z",
        ref_registry=RefRegistry(),
    )
    second_artifacts = _commit_screen(
        writer,
        runtime,
        second_screen,
        sequence=2,
        source_snapshot_id=102,
        captured_at="2026-04-13T00:00:02Z",
        ref_registry=RefRegistry(),
    )

    assert first_artifacts.screen_json != second_artifacts.screen_json
    assert set(second_artifacts.model_dump().keys()) == {
        "screen_json",
        "screen_xml",
        "screenshot_png",
    }
    assert Path(first_artifacts.screen_json).name == "obs-00001.json"
    assert Path(second_artifacts.screen_json).name == "obs-00002.json"
    assert Path(first_artifacts.screen_xml).name == "obs-00001.xml"
    assert Path(second_artifacts.screen_xml).name == "obs-00002.xml"
    assert Path(first_artifacts.screen_json).is_file()
    assert Path(second_artifacts.screen_json).is_file()
    assert Path(first_artifacts.screen_xml).is_file()
    assert Path(second_artifacts.screen_xml).is_file()
    assert not (runtime.artifact_root / "artifacts" / "obs-00001.md").exists()
    assert not (runtime.artifact_root / "artifacts" / "obs-00001.xml").exists()
    assert not (runtime.artifact_root / "artifacts" / "obs-00002.md").exists()
    assert not (runtime.artifact_root / "artifacts" / "obs-00002.xml").exists()
    assert not (runtime.artifact_root / "screens" / "obs-00002.diff.json").exists()
    assert not (runtime.artifact_root / "screens" / "obs-00002.diff.md").exists()

    first_payload = json.loads(Path(first_artifacts.screen_json).read_text("utf-8"))
    second_payload = json.loads(Path(second_artifacts.screen_json).read_text("utf-8"))

    assert first_payload["screenId"] == second_payload["screenId"]
    assert first_payload["sequence"] == 1
    assert second_payload["sequence"] == 2


def test_writer_persists_internal_json_and_public_screen_xml(tmp_path: Path) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    screen = _make_screen(
        screen_id="screen-1234567890123456",
        sequence=1,
        label="Wi-Fi",
    )

    artifacts = _commit_screen(
        writer,
        runtime,
        screen,
        sequence=1,
        source_snapshot_id=101,
        captured_at="2026-04-13T00:00:01Z",
        ref_registry=RefRegistry(),
    )

    assert Path(artifacts.screen_json).parent.name == "screens"
    assert artifacts.screen_xml is not None
    assert (
        Path(artifacts.screen_xml).parent
        == runtime.artifact_root / "artifacts" / "screens"
    )
    assert (runtime.artifact_root / "screens" / "obs-00001.json").is_file()
    assert (runtime.artifact_root / "artifacts" / "screens" / "obs-00001.xml").is_file()
    root = ElementTree.fromstring(Path(artifacts.screen_xml).read_text("utf-8"))
    assert root.tag == "screen"
    assert root.attrib == {"screenId": screen.screen_id}
    node = root.find("./groups/targets/text")
    assert node is not None
    assert node.attrib["label"] == "Wi-Fi"
    assert "role" not in node.attrib
    assert "actions" not in node.attrib
    assert "state" not in node.attrib

    screen_payload = json.loads(Path(artifacts.screen_json).read_text("utf-8"))
    target_group = next(
        group for group in screen_payload["groups"] if group["name"] == "targets"
    )
    json_node = target_group["nodes"][0]
    assert json_node["ref"] == "n1"
    assert json_node["actions"] == []
    assert json_node["state"] == []

    assert not (runtime.artifact_root / "artifacts" / "obs-00001.xml").exists()
    assert not (
        runtime.artifact_root / "artifacts" / "screens" / "obs-00001.md"
    ).exists()


def test_screen_artifacts_preserves_screen_xml_when_screenshot_attaches() -> None:
    artifacts = ScreenArtifacts(
        screen_json="/repo/.androidctl/screens/obs-00001.json",
        screen_xml="/repo/.androidctl/artifacts/screens/obs-00001.xml",
    )

    updated = artifacts.with_screenshot("/repo/.androidctl/screenshots/shot-00001.png")

    assert updated.screen_json == artifacts.screen_json
    assert updated.screen_xml == artifacts.screen_xml
    assert updated.screenshot_png == "/repo/.androidctl/screenshots/shot-00001.png"


def test_semantic_artifact_payload_publishes_screen_xml_and_screenshot() -> None:
    payload = semantic_artifact_payload(
        ScreenArtifacts(
            screen_json="/repo/.androidctl/screens/obs-00001.json",
            screen_xml="/repo/.androidctl/artifacts/screens/obs-00001.xml",
            screenshot_png="/repo/.androidctl/screenshots/shot-00001.png",
        )
    )

    assert payload.model_dump(by_alias=True, mode="json", exclude_none=True) == {
        "screenshotPng": "/repo/.androidctl/screenshots/shot-00001.png",
        "screenXml": "/repo/.androidctl/artifacts/screens/obs-00001.xml",
    }


def test_semantic_artifact_payload_omits_absent_screen_xml() -> None:
    payload = semantic_artifact_payload(
        ScreenArtifacts(screen_json="/repo/.androidctl/screens/obs-00001.json")
    )

    assert payload.model_dump(by_alias=True, mode="json", exclude_none=True) == {}


def test_staged_screen_rollback_restores_existing_final_and_cleans_files(
    tmp_path: Path,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    original_screen = _make_screen(
        screen_id="screen-1234567890123456",
        sequence=1,
        label="Wi-Fi",
    )
    replacement_screen = _make_screen(
        screen_id="screen-1234567890123456",
        sequence=1,
        label="Bluetooth",
    )
    artifacts = _commit_screen(
        writer,
        runtime,
        original_screen,
        sequence=1,
        source_snapshot_id=101,
        captured_at="2026-04-13T00:00:01Z",
        ref_registry=RefRegistry(),
    )
    final_path = Path(artifacts.screen_json)
    original_content = final_path.read_text(encoding="utf-8")

    staged = writer.stage_screen(
        runtime,
        replacement_screen,
        sequence=1,
        source_snapshot_id=101,
        captured_at="2026-04-13T00:00:01Z",
        ref_registry=RefRegistry(),
    )
    staged.commit()
    staged_payload = json.loads(final_path.read_text(encoding="utf-8"))
    assert staged_payload["groups"][0]["name"] == "targets"
    assert staged_payload["groups"][0]["nodes"][0]["label"] == "Bluetooth"

    staged.rollback()
    staged.discard()

    assert final_path.read_text(encoding="utf-8") == original_content
    assert list(final_path.parent.glob("*.tmp-*")) == []
    assert list(final_path.parent.glob("*.bak-*")) == []


def test_staged_screen_rollback_restores_existing_xml_and_cleans_files(
    tmp_path: Path,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    original_screen = _make_screen(
        screen_id="screen-1234567890123456",
        sequence=1,
        label="Wi-Fi",
    )
    replacement_screen = _make_screen(
        screen_id="screen-1234567890123456",
        sequence=1,
        label="Bluetooth",
    )
    artifacts = _commit_screen(
        writer,
        runtime,
        original_screen,
        sequence=1,
        source_snapshot_id=101,
        captured_at="2026-04-13T00:00:01Z",
        ref_registry=RefRegistry(),
    )
    final_json_path = Path(artifacts.screen_json)
    final_xml_path = Path(artifacts.screen_xml)
    original_json = final_json_path.read_text(encoding="utf-8")
    original_xml = final_xml_path.read_text(encoding="utf-8")

    staged = writer.stage_screen(
        runtime,
        replacement_screen,
        sequence=1,
        source_snapshot_id=101,
        captured_at="2026-04-13T00:00:01Z",
        ref_registry=RefRegistry(),
    )
    staged.commit()
    assert "Bluetooth" in final_xml_path.read_text(encoding="utf-8")

    staged.rollback()
    staged.discard()

    assert final_json_path.read_text(encoding="utf-8") == original_json
    assert final_xml_path.read_text(encoding="utf-8") == original_xml
    assert list(final_json_path.parent.glob("*.tmp-*")) == []
    assert list(final_json_path.parent.glob("*.bak-*")) == []
    assert list(final_xml_path.parent.glob("*.tmp-*")) == []
    assert list(final_xml_path.parent.glob("*.bak-*")) == []


def test_xml_commit_failure_rolls_back_previously_committed_json(
    tmp_path: Path,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    screen = _make_screen(
        screen_id="screen-1234567890123456",
        sequence=1,
        label="Wi-Fi",
    )
    staged = writer.stage_screen(
        runtime,
        screen,
        sequence=1,
        source_snapshot_id=101,
        captured_at="2026-04-13T00:00:01Z",
        ref_registry=RefRegistry(),
    )
    xml_update = next(
        update for update in staged.file_updates if update.final_path.suffix == ".xml"
    )
    xml_update.staged_path.unlink()

    def commit_with_cleanup() -> None:
        try:
            staged.commit()
        except Exception:
            staged.rollback()
            staged.discard()
            raise

    with pytest.raises(FileNotFoundError):
        commit_with_cleanup()

    assert not Path(staged.artifacts.screen_json).exists()
    assert not Path(staged.artifacts.screen_xml).exists()


def test_write_screenshot_png_rejects_oversized_body_without_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    monkeypatch.setattr("androidctld.artifacts.writer.SCREENSHOT_MAX_BINARY_BYTES", 2)

    with pytest.raises(ValueError, match="screenshot PNG exceeds"):
        writer.write_screenshot_png(runtime, b"abc")

    assert not (runtime.artifact_root / "screenshots" / "shot-00001.png").exists()
    assert not (runtime.artifact_root / "screenshots").exists()


def test_write_screenshot_png_writes_small_body(tmp_path: Path) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()

    path = writer.write_screenshot_png(runtime, b"\x89PNG\r\n\x1a\n")

    assert path == runtime.artifact_root / "screenshots" / "shot-00001.png"
    assert path.read_bytes() == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (
            lambda runtime: runtime.artifact_root.write_text("not a directory"),
            "namespace-create-failed",
        ),
        (
            lambda runtime: (
                runtime.artifact_root.mkdir(parents=True),
                (runtime.artifact_root / "screenshots").write_text("not a directory"),
            ),
            "namespace-create-failed",
        ),
    ],
)
def test_write_screenshot_png_classifies_namespace_non_directory(
    tmp_path: Path,
    setup: Any,
    reason: str,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    setup(runtime)

    with pytest.raises(DaemonError) as exc_info:
        writer.write_screenshot_png(runtime, b"\x89PNG\r\n\x1a\n")

    assert exc_info.value.code is DaemonErrorCode.ARTIFACT_ROOT_UNWRITABLE
    assert exc_info.value.details == {"reason": reason}


def test_write_screenshot_png_classifies_mkdir_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()

    def fail_mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
        del self, args, kwargs
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    with pytest.raises(DaemonError) as exc_info:
        writer.write_screenshot_png(runtime, b"\x89PNG\r\n\x1a\n")

    assert exc_info.value.code is DaemonErrorCode.ARTIFACT_ROOT_UNWRITABLE
    assert exc_info.value.details == {"reason": "namespace-create-failed"}


def test_write_screenshot_png_classifies_scan_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()

    def fail_glob(self: Path, pattern: str) -> Any:
        del self, pattern
        raise OSError("scan failed")

    monkeypatch.setattr(Path, "glob", fail_glob)

    with pytest.raises(DaemonError) as exc_info:
        writer.write_screenshot_png(runtime, b"\x89PNG\r\n\x1a\n")

    assert exc_info.value.code is DaemonErrorCode.ARTIFACT_ROOT_UNWRITABLE
    assert exc_info.value.details == {"reason": "namespace-scan-failed"}


def test_write_screenshot_png_classifies_resolve_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    original_resolve = Path.resolve

    def fail_shot_resolve(self: Path, *args: Any, **kwargs: Any) -> Path:
        if self.name.startswith("shot-"):
            raise OSError("resolve failed")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_shot_resolve)

    with pytest.raises(DaemonError) as exc_info:
        writer.write_screenshot_png(runtime, b"\x89PNG\r\n\x1a\n")

    assert exc_info.value.code is DaemonErrorCode.ARTIFACT_WRITE_FAILED
    assert exc_info.value.details == {"reason": "candidate-resolve-failed"}


def test_write_screenshot_png_classifies_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()

    def fail_open(*args: Any, **kwargs: Any) -> int:
        del args, kwargs
        raise PermissionError("denied")

    monkeypatch.setattr(os, "open", fail_open)

    with pytest.raises(DaemonError) as exc_info:
        writer.write_screenshot_png(runtime, b"\x89PNG\r\n\x1a\n")

    assert exc_info.value.code is DaemonErrorCode.ARTIFACT_WRITE_FAILED
    assert exc_info.value.details == {"reason": "candidate-open-failed"}


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("fdopen", "candidate-fdopen-failed"),
        ("write", "candidate-write-failed"),
        ("flush", "candidate-flush-failed"),
        ("close", "candidate-close-failed"),
    ],
)
def test_write_screenshot_png_classifies_stream_failure_and_cleans_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    reason: str,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    original_fdopen = os.fdopen

    class FailingHandle:
        def __init__(self, fd: int) -> None:
            self._fd = fd

        def write(self, body: bytes) -> int:
            if failure == "write":
                raise OSError("write failed")
            return os.write(self._fd, body)

        def flush(self) -> None:
            if failure == "flush":
                raise OSError("flush failed")

        def close(self) -> None:
            os.close(self._fd)
            if failure == "close":
                raise OSError("close failed")

    def maybe_fail_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if failure == "fdopen":
            raise OSError("fdopen failed")
        del args, kwargs
        return FailingHandle(fd)

    monkeypatch.setattr(os, "fdopen", maybe_fail_fdopen)

    with pytest.raises(DaemonError) as exc_info:
        writer.write_screenshot_png(runtime, b"\x89PNG\r\n\x1a\n")

    assert exc_info.value.code is DaemonErrorCode.ARTIFACT_WRITE_FAILED
    assert exc_info.value.details == {"reason": reason}
    assert not (runtime.artifact_root / "screenshots" / "shot-00001.png").exists()
    monkeypatch.setattr(os, "fdopen", original_fdopen)


def test_write_screenshot_png_retries_exclusive_open_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = runtime_store_for_workspace(tmp_path).get_runtime()
    writer = ArtifactWriter()
    screenshots_dir = runtime.artifact_root / "screenshots"
    screenshots_dir.mkdir(parents=True)
    (screenshots_dir / "shot-00001.png").write_bytes(b"existing")
    monkeypatch.setattr(
        "androidctld.artifacts.writer._next_screenshot_index",
        lambda _: 1,
    )

    path = writer.write_screenshot_png(runtime, b"\x89PNG\r\n\x1a\n")

    assert path == screenshots_dir / "shot-00002.png"
    assert path.read_bytes() == b"\x89PNG\r\n\x1a\n"
    assert (screenshots_dir / "shot-00001.png").read_bytes() == b"existing"
