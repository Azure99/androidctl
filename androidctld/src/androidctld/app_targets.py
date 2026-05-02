"""Shared app-target matching for open/wait surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from androidctld.errors import DaemonError, DaemonErrorCode


@dataclass(frozen=True)
class AppTargetMatch:
    requested_package_name: str
    resolved_package_name: str
    match_type: Literal["exact", "alias"]


APP_TARGET_ALIASES: frozenset[tuple[str, str]] = frozenset(
    {
        ("com.android.settings", "com.android.settings.intelligence"),
        ("com.android.settings", "com.google.android.settings.intelligence"),
        (
            "com.android.settings.intelligence",
            "com.google.android.settings.intelligence",
        ),
        (
            "com.google.android.settings.intelligence",
            "com.android.settings.intelligence",
        ),
    }
)


def match_app_target(
    requested_package_name: str,
    actual_package_name: str | None,
) -> AppTargetMatch | None:
    if actual_package_name is None:
        return None
    if actual_package_name == requested_package_name:
        return AppTargetMatch(
            requested_package_name=requested_package_name,
            resolved_package_name=actual_package_name,
            match_type="exact",
        )
    if (requested_package_name, actual_package_name) in APP_TARGET_ALIASES:
        return AppTargetMatch(
            requested_package_name=requested_package_name,
            resolved_package_name=actual_package_name,
            match_type="alias",
        )
    return None


def require_app_target_match(
    requested_package_name: str,
    actual_package_name: str | None,
) -> AppTargetMatch:
    match = match_app_target(requested_package_name, actual_package_name)
    if match is not None:
        return match
    raise DaemonError(
        code=DaemonErrorCode.OPEN_FAILED,
        message="open did not reach the requested application",
        retryable=True,
        details={
            "expectedPackageName": requested_package_name,
            "actualPackageName": actual_package_name,
        },
        http_status=200,
    )
