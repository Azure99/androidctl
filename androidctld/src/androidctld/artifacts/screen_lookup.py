"""Read-only source screen artifact lookup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from androidctld.artifacts.screen_payloads import ScreenArtifactPayload
from androidctld.runtime.models import WorkspaceRuntime
from androidctld.runtime.screen_state import current_artifacts

ArtifactScanStatus = Literal["valid", "malformed", "unreadable"]
SourceScreenArtifactStatus = Literal["found", "not_found", "invalid_artifact"]


@dataclass(frozen=True)
class ScannedArtifactPath:
    path: Path


@dataclass(frozen=True)
class ScreenArtifactScan:
    path: Path
    status: ArtifactScanStatus
    screen_id: str | None
    sequence: int | None
    payload: ScreenArtifactPayload | None = None


@dataclass(frozen=True)
class ScannedScreenArtifact:
    candidate: ScannedArtifactPath
    artifact: ScreenArtifactScan


@dataclass(frozen=True)
class SourceScreenArtifactLookup:
    status: SourceScreenArtifactStatus
    source_screen_id: str
    path: Path | None = None
    payload: ScreenArtifactPayload | None = None
    scanned: tuple[ScannedScreenArtifact, ...] = ()


def lookup_source_screen_artifact(
    session: WorkspaceRuntime,
    source_screen_id: str,
) -> SourceScreenArtifactLookup:
    """Find the newest source artifact without mutating artifact storage."""

    matching_candidates: list[ScreenArtifactScan] = []
    unknown_screen_candidates: list[ScreenArtifactScan] = []
    scanned_candidates: list[ScannedScreenArtifact] = []
    for candidate in screen_artifact_candidates(session):
        if not candidate.path.exists():
            continue
        artifact = scan_screen_artifact(candidate.path)
        scanned_candidates.append(
            ScannedScreenArtifact(candidate=candidate, artifact=artifact)
        )
        if artifact.screen_id == source_screen_id:
            matching_candidates.append(artifact)
            continue
        if artifact.screen_id is None and artifact.status != "valid":
            unknown_screen_candidates.append(artifact)
            continue

    scanned = tuple(scanned_candidates)
    if not matching_candidates:
        return SourceScreenArtifactLookup(
            status="not_found",
            source_screen_id=source_screen_id,
            scanned=scanned,
        )

    unknown_sequence_invalid = [
        artifact for artifact in unknown_screen_candidates if artifact.sequence is None
    ]
    if unknown_sequence_invalid:
        selected = sorted(
            unknown_sequence_invalid,
            key=lambda artifact: artifact.path.name,
            reverse=True,
        )[0]
        return SourceScreenArtifactLookup(
            status="invalid_artifact",
            source_screen_id=source_screen_id,
            path=selected.path,
            scanned=scanned,
        )

    matching_candidates.sort(
        key=lambda artifact: (
            -1 if artifact.sequence is None else artifact.sequence,
            artifact.path.name,
        ),
        reverse=True,
    )
    selected_match = matching_candidates[0]

    if unknown_screen_candidates:
        unknown_screen_candidates.sort(
            key=lambda artifact: (
                -1 if artifact.sequence is None else artifact.sequence,
                artifact.path.name,
            ),
            reverse=True,
        )
        selected_unknown = unknown_screen_candidates[0]
    else:
        selected_unknown = None

    if selected_unknown is not None:
        unknown_sequence = selected_unknown.sequence
        match_sequence = selected_match.sequence
        if (
            unknown_sequence is None
            or match_sequence is None
            or unknown_sequence >= match_sequence
        ):
            return SourceScreenArtifactLookup(
                status="invalid_artifact",
                source_screen_id=source_screen_id,
                path=selected_unknown.path,
                scanned=scanned,
            )

    selected = selected_match
    if selected.status != "valid":
        return SourceScreenArtifactLookup(
            status="invalid_artifact",
            source_screen_id=source_screen_id,
            path=selected.path,
            scanned=scanned,
        )
    if selected.payload is None:
        raise RuntimeError("valid source screen artifact is missing payload")
    return SourceScreenArtifactLookup(
        status="found",
        source_screen_id=source_screen_id,
        path=selected.path,
        payload=selected.payload,
        scanned=scanned,
    )


def screen_artifact_candidates(
    session: WorkspaceRuntime,
) -> list[ScannedArtifactPath]:
    candidates: dict[Path, ScannedArtifactPath] = {}

    def add_candidate(path: Path) -> None:
        resolved = path.resolve()
        candidates.setdefault(resolved, ScannedArtifactPath(path=path))

    current_artifact_bag = current_artifacts(session)
    current_artifact = (
        None if current_artifact_bag is None else current_artifact_bag.screen_json
    )
    if current_artifact is not None:
        current_artifact_path = Path(current_artifact)
        if is_internal_screen_artifact_path(current_artifact_path):
            add_candidate(current_artifact_path)

    screens_dir = session.artifact_root / "screens"
    if screens_dir.exists():
        for path in sorted(screens_dir.glob("obs-*.json")):
            add_candidate(path)

    return list(candidates.values())


def scan_screen_artifact(path: Path) -> ScreenArtifactScan:
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return ScreenArtifactScan(
            path=path,
            status="unreadable",
            screen_id=None,
            sequence=sequence_from_artifact_path(path),
        )
    except ValueError:
        return ScreenArtifactScan(
            path=path,
            status="malformed",
            screen_id=None,
            sequence=sequence_from_artifact_path(path),
        )

    if not isinstance(raw_payload, dict):
        return ScreenArtifactScan(
            path=path,
            status="malformed",
            screen_id=None,
            sequence=sequence_from_artifact_path(path),
        )

    screen_id = raw_payload.get("screenId")
    screen_id_hint = screen_id if isinstance(screen_id, str) else None
    sequence = raw_payload.get("sequence")
    sequence_hint = (
        sequence
        if isinstance(sequence, int) and not isinstance(sequence, bool)
        else sequence_from_artifact_path(path)
    )
    try:
        payload = ScreenArtifactPayload.model_validate_json(json.dumps(raw_payload))
    except ValidationError:
        return ScreenArtifactScan(
            path=path,
            status="malformed",
            screen_id=screen_id_hint,
            sequence=sequence_hint,
        )

    return ScreenArtifactScan(
        path=path,
        status="valid",
        screen_id=payload.screen_id,
        sequence=payload.sequence,
        payload=payload,
    )


def sequence_from_artifact_path(path: Path) -> int | None:
    name = path.name
    if not name.startswith("obs-") or not name.endswith(".json"):
        return None
    digits = name[4:-5]
    if not digits.isdigit():
        return None
    return int(digits)


def is_internal_screen_artifact_path(path: Path) -> bool:
    return sequence_from_artifact_path(path) is not None
