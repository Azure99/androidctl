from __future__ import annotations

import time
from dataclasses import dataclass

from androidctl.setup import adb as setup_adb

ENABLED_ACCESSIBILITY_SERVICES = "enabled_accessibility_services"
ACCESSIBILITY_ENABLED = "accessibility_enabled"
ACCESSIBILITY_ENABLED_VALUE = "1"
MANUAL_ACCESSIBILITY_FALLBACK = (
    "open Android App info, allow restricted settings if shown, then enable "
    "AndroidCtl Accessibility in Accessibility settings"
)
DEFAULT_ENABLE_ATTEMPTS = 5
DEFAULT_ENABLE_RETRY_DELAY_SECONDS = 0.5


@dataclass(frozen=True)
class AccessibilityEnableResult:
    changed_service_list: bool
    enabled_services: str


class SetupAccessibilityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.layer = "accessibility"


def parse_enabled_services(value: str) -> tuple[str, ...]:
    normalized = value.strip()
    if not normalized or normalized == "null":
        return ()
    return tuple(
        service.strip() for service in normalized.split(":") if service.strip()
    )


def canonical_component_name(component: str) -> str:
    package, separator, class_name = component.partition("/")
    if not separator:
        return component
    if class_name.startswith("."):
        class_name = f"{package}{class_name}"
    return f"{package}/{class_name}"


def merge_enabled_services(
    value: str,
    *,
    service: str = setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE,
) -> str:
    services = parse_enabled_services(value)
    if any(component_names_match(existing, service) for existing in services):
        return ":".join(services)
    return ":".join((*services, service))


def service_is_enabled(
    value: str,
    *,
    service: str = setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE,
) -> bool:
    return any(
        component_names_match(existing, service)
        for existing in parse_enabled_services(value)
    )


def component_names_match(left: str, right: str) -> bool:
    return canonical_component_name(left) == canonical_component_name(right)


def enable_agent_accessibility(
    *,
    serial: str,
    adb_path: str = "adb",
    attempts: int = DEFAULT_ENABLE_ATTEMPTS,
    retry_delay_seconds: float = DEFAULT_ENABLE_RETRY_DELAY_SECONDS,
) -> AccessibilityEnableResult:
    normalized_attempts = max(1, attempts)
    last_error: SetupAccessibilityError | None = None
    for attempt_index in range(normalized_attempts):
        try:
            return _enable_agent_accessibility_once(serial=serial, adb_path=adb_path)
        except SetupAccessibilityError as error:
            last_error = error
            if (
                attempt_index == normalized_attempts - 1
                or error.code != "ACCESSIBILITY_ENABLE_NOT_CONFIRMED"
            ):
                raise
            time.sleep(max(0.0, retry_delay_seconds))
    if last_error is not None:
        raise last_error
    raise SetupAccessibilityError(
        "ACCESSIBILITY_ENABLE_NOT_CONFIRMED",
        "Android settings did not report AndroidCtl Accessibility as enabled",
    )


def _enable_agent_accessibility_once(
    *,
    serial: str,
    adb_path: str,
) -> AccessibilityEnableResult:
    try:
        current_services = setup_adb.get_secure_setting(
            ENABLED_ACCESSIBILITY_SERVICES,
            serial=serial,
            adb_path=adb_path,
        )
        merged_services = merge_enabled_services(current_services)
        changed_service_list = merged_services != ":".join(
            parse_enabled_services(current_services)
        )
        if changed_service_list:
            setup_adb.put_secure_setting(
                ENABLED_ACCESSIBILITY_SERVICES,
                merged_services,
                serial=serial,
                adb_path=adb_path,
            )
        setup_adb.put_secure_setting(
            ACCESSIBILITY_ENABLED,
            ACCESSIBILITY_ENABLED_VALUE,
            serial=serial,
            adb_path=adb_path,
        )
        verified_services = setup_adb.get_secure_setting(
            ENABLED_ACCESSIBILITY_SERVICES,
            serial=serial,
            adb_path=adb_path,
        )
        verified_enabled = setup_adb.get_secure_setting(
            ACCESSIBILITY_ENABLED,
            serial=serial,
            adb_path=adb_path,
        )
    except setup_adb.SetupAdbError as error:
        raise SetupAccessibilityError(error.code, error.message) from error

    if not service_is_enabled(verified_services):
        raise SetupAccessibilityError(
            "ACCESSIBILITY_ENABLE_NOT_CONFIRMED",
            "Android settings did not report AndroidCtl Accessibility as enabled",
        )
    if verified_enabled != ACCESSIBILITY_ENABLED_VALUE:
        raise SetupAccessibilityError(
            "ACCESSIBILITY_ENABLE_NOT_CONFIRMED",
            "Android settings did not report Accessibility as globally enabled",
        )
    return AccessibilityEnableResult(
        changed_service_list=changed_service_list,
        enabled_services=verified_services,
    )
