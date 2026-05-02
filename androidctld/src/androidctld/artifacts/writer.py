"""Screen artifact rendering and persistence."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from androidctld.artifacts.models import ScreenArtifacts
from androidctld.artifacts.screen_payloads import build_screen_artifact_payload
from androidctld.errors import DaemonError, DaemonErrorCode
from androidctld.refs.models import RefRegistry
from androidctld.rendering.screen_xml import render_screen_xml
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime_policy import SCREENSHOT_MAX_BINARY_BYTES
from androidctld.semantics.public_models import PublicScreen


@dataclass
class StagedArtifactWrite:
    artifacts: ScreenArtifacts
    file_updates: tuple[StagedFileUpdate, ...] = ()

    def commit(self) -> None:
        for update in self.file_updates:
            update.commit()

    def discard(self) -> None:
        for update in self.file_updates:
            update.discard()

    def rollback(self) -> None:
        for update in reversed(self.file_updates):
            update.rollback()


@dataclass
class StagedFileUpdate:
    staged_path: Path
    final_path: Path
    backup_path: Path | None = None
    original_backed_up: bool = False
    final_replaced: bool = False

    def commit(self) -> None:
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        if self.backup_path is not None:
            try:
                self.final_path.replace(self.backup_path)
            except FileNotFoundError:
                pass
            else:
                self.original_backed_up = True
        try:
            self.staged_path.replace(self.final_path)
        except Exception:
            if self.original_backed_up and self.backup_path is not None:
                self.backup_path.replace(self.final_path)
                self.original_backed_up = False
            raise
        self.final_replaced = True

    def rollback(self) -> None:
        if not self.final_replaced:
            return
        if self.original_backed_up and self.backup_path is not None:
            with suppress(FileNotFoundError):
                self.final_path.unlink()
            self.backup_path.replace(self.final_path)
            self.original_backed_up = False
            self.final_replaced = False
            return
        with suppress(FileNotFoundError):
            self.final_path.unlink()
        self.final_replaced = False

    def discard(self) -> None:
        for path in (self.staged_path, self.backup_path):
            if path is None:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue


class ArtifactWriter:
    def stage_screen(
        self,
        session: WorkspaceRuntime,
        public_screen: PublicScreen,
        *,
        sequence: int,
        source_snapshot_id: int,
        captured_at: str,
        ref_registry: RefRegistry | None = None,
    ) -> StagedArtifactWrite:
        token = uuid.uuid4().hex
        staged_screen_json = self.stage_screen_json_update(
            session,
            public_screen,
            sequence=sequence,
            source_snapshot_id=source_snapshot_id,
            captured_at=captured_at,
            ref_registry=ref_registry,
            token=token,
        )
        staged_screen_xml = self.stage_screen_xml_update(
            session,
            public_screen,
            sequence=sequence,
            token=token,
        )

        artifacts = ScreenArtifacts(
            screen_json=staged_screen_json.final_path.as_posix(),
            screen_xml=staged_screen_xml.final_path.as_posix(),
        )
        return StagedArtifactWrite(
            artifacts=artifacts,
            file_updates=(staged_screen_json, staged_screen_xml),
        )

    def stage_screen_json_update(
        self,
        session: WorkspaceRuntime,
        public_screen: PublicScreen,
        *,
        sequence: int,
        source_snapshot_id: int,
        captured_at: str,
        ref_registry: RefRegistry | None = None,
        token: str | None = None,
    ) -> StagedFileUpdate:
        screens_dir = session.artifact_root / "screens"
        screens_dir.mkdir(parents=True, exist_ok=True)
        write_token = token or uuid.uuid4().hex
        json_path = screens_dir / f"{artifact_stem(sequence)}.json"
        staged_json_path = staged_path_for(json_path, write_token)
        write_json_file(
            staged_json_path,
            build_screen_artifact_payload(
                public_screen,
                ref_registry or RefRegistry(),
                sequence=sequence,
                source_snapshot_id=source_snapshot_id,
                captured_at=captured_at,
            ),
        )
        return StagedFileUpdate(
            staged_path=staged_json_path,
            final_path=json_path,
            backup_path=backup_path_for(json_path, write_token),
        )

    def stage_screen_xml_update(
        self,
        session: WorkspaceRuntime,
        public_screen: PublicScreen,
        *,
        sequence: int,
        token: str | None = None,
    ) -> StagedFileUpdate:
        screens_dir = session.artifact_root / "artifacts" / "screens"
        screens_dir.mkdir(parents=True, exist_ok=True)
        write_token = token or uuid.uuid4().hex
        xml_path = screens_dir / f"{artifact_stem(sequence)}.xml"
        staged_xml_path = staged_path_for(xml_path, write_token)
        write_text_file(staged_xml_path, render_screen_xml(public_screen))
        return StagedFileUpdate(
            staged_path=staged_xml_path,
            final_path=xml_path,
            backup_path=backup_path_for(xml_path, write_token),
        )

    def write_screenshot_png(self, session: WorkspaceRuntime, body: bytes) -> Path:
        if len(body) > SCREENSHOT_MAX_BINARY_BYTES:
            raise ValueError(
                f"screenshot PNG exceeds {SCREENSHOT_MAX_BINARY_BYTES} byte budget"
            )
        screenshots_dir = session.artifact_root / "screenshots"
        try:
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            if not screenshots_dir.is_dir():
                raise _artifact_root_unwritable("namespace-not-directory")
        except DaemonError:
            raise
        except Exception as error:
            raise _artifact_root_unwritable("namespace-create-failed") from error

        try:
            next_index = _next_screenshot_index(screenshots_dir)
        except Exception as error:
            raise _artifact_root_unwritable("namespace-scan-failed") from error
        binary_flag = getattr(os, "O_BINARY", 0)
        while True:
            try:
                candidate = (screenshots_dir / f"shot-{next_index:05d}.png").resolve()
            except Exception as error:
                raise _artifact_write_failed("candidate-resolve-failed") from error
            fd: int | None = None
            handle = None
            try:
                fd = os.open(
                    candidate,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | binary_flag,
                )
            except FileExistsError:
                next_index += 1
                continue
            except Exception as error:
                raise _artifact_write_failed("candidate-open-failed") from error
            failure_reason = "candidate-fdopen-failed"
            try:
                handle = os.fdopen(fd, "wb")
                fd = None
                failure_reason = "candidate-write-failed"
                handle.write(body)
                failure_reason = "candidate-flush-failed"
                handle.flush()
                failure_reason = "candidate-close-failed"
                handle.close()
                return candidate
            except Exception as error:
                if handle is not None:
                    with suppress(Exception):
                        handle.close()
                if fd is not None:
                    with suppress(OSError):
                        os.close(fd)
                with suppress(OSError):
                    candidate.unlink()
                raise _artifact_write_failed(failure_reason) from error


def _next_screenshot_index(screenshots_dir: Path) -> int:
    highest_index = 0
    for path in screenshots_dir.glob("shot-*.png"):
        suffix = path.stem.removeprefix("shot-")
        if not suffix.isdigit():
            continue
        highest_index = max(highest_index, int(suffix))
    return highest_index + 1


def _artifact_root_unwritable(reason: str) -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.ARTIFACT_ROOT_UNWRITABLE,
        message="artifact root is not writable",
        details={"reason": reason},
    )


def _artifact_write_failed(reason: str) -> DaemonError:
    return DaemonError(
        code=DaemonErrorCode.ARTIFACT_WRITE_FAILED,
        message="artifact write failed",
        details={"reason": reason},
    )


def staged_path_for(final_path: Path, token: str) -> Path:
    return final_path.with_name(f"{final_path.name}.tmp-{token}")


def backup_path_for(final_path: Path, token: str) -> Path:
    return final_path.with_name(f"{final_path.name}.bak-{token}")


def artifact_stem(sequence: int) -> str:
    return f"obs-{sequence:05d}"


def write_json_file(path: Path, payload: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def write_text_file(path: Path, payload: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
