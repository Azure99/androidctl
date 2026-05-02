from __future__ import annotations

import pytest

from androidctl.setup import accessibility
from androidctl.setup import adb as setup_adb


def test_parse_enabled_services_treats_empty_and_null_as_empty() -> None:
    assert accessibility.parse_enabled_services("") == ()
    assert accessibility.parse_enabled_services(" null \n") == ()


def test_canonical_component_name_expands_short_class_name() -> None:
    assert (
        accessibility.canonical_component_name(
            "com.rainng.androidctl/.agent.service.DeviceAccessibilityService"
        )
        == setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE
    )


def test_merge_enabled_services_preserves_existing_services_and_appends_agent() -> None:
    assert accessibility.merge_enabled_services("service.one/service.Service") == (
        "service.one/service.Service:" f"{setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE}"
    )


def test_merge_enabled_services_does_not_duplicate_agent_service() -> None:
    current = (
        "service.one/service.Service:" f"{setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE}"
    )

    assert accessibility.merge_enabled_services(current) == current


def test_merge_enabled_services_does_not_duplicate_short_agent_service() -> None:
    current = "com.rainng.androidctl/.agent.service.DeviceAccessibilityService"

    assert accessibility.merge_enabled_services(current) == current


def test_service_is_enabled_uses_colon_delimited_entries() -> None:
    assert accessibility.service_is_enabled(
        f"other/service:{setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE}"
    )
    assert accessibility.service_is_enabled(
        "other/service:com.rainng.androidctl/.agent.service.DeviceAccessibilityService"
    )
    assert not accessibility.service_is_enabled(
        f"{setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE}.suffix"
    )


def test_enable_agent_accessibility_writes_merged_list_and_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_values = [
        "service.one/service.Service",
        (
            "service.one/service.Service:"
            f"{setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE}"
        ),
        "1",
    ]
    put_calls: list[tuple[str, str, str]] = []

    def fake_get_secure_setting(
        key: str,
        *,
        serial: str,
        adb_path: str = "adb",
    ) -> str:
        del adb_path
        assert serial == "device-1"
        assert key in {
            accessibility.ENABLED_ACCESSIBILITY_SERVICES,
            accessibility.ACCESSIBILITY_ENABLED,
        }
        return get_values.pop(0)

    def fake_put_secure_setting(
        key: str,
        value: str,
        *,
        serial: str,
        adb_path: str = "adb",
    ) -> setup_adb.AdbCommandResult:
        del adb_path
        put_calls.append((serial, key, value))
        return setup_adb.AdbCommandResult()

    monkeypatch.setattr(setup_adb, "get_secure_setting", fake_get_secure_setting)
    monkeypatch.setattr(setup_adb, "put_secure_setting", fake_put_secure_setting)

    result = accessibility.enable_agent_accessibility(serial="device-1")

    assert result.changed_service_list
    assert result.enabled_services.endswith(setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE)
    assert put_calls == [
        (
            "device-1",
            accessibility.ENABLED_ACCESSIBILITY_SERVICES,
            "service.one/service.Service:"
            f"{setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE}",
        ),
        (
            "device-1",
            accessibility.ACCESSIBILITY_ENABLED,
            accessibility.ACCESSIBILITY_ENABLED_VALUE,
        ),
    ]


def test_enable_agent_accessibility_keeps_existing_service_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = (
        "service.one/service.Service:" f"{setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE}"
    )
    get_values = [current, current, "1"]
    put_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        setup_adb,
        "get_secure_setting",
        lambda key, *, serial, adb_path="adb": get_values.pop(0),
    )
    monkeypatch.setattr(
        setup_adb,
        "put_secure_setting",
        lambda key, value, *, serial, adb_path="adb": put_calls.append((key, value)),
    )

    result = accessibility.enable_agent_accessibility(serial="device-1")

    assert not result.changed_service_list
    assert result.enabled_services == current
    assert put_calls == [
        (accessibility.ACCESSIBILITY_ENABLED, accessibility.ACCESSIBILITY_ENABLED_VALUE)
    ]


def test_enable_agent_accessibility_maps_adb_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_secure_setting(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise setup_adb.SetupAdbError("ADB_SETTINGS_FAILED", "settings failed")

    monkeypatch.setattr(setup_adb, "get_secure_setting", fake_get_secure_setting)

    with pytest.raises(accessibility.SetupAccessibilityError) as exc_info:
        accessibility.enable_agent_accessibility(serial="device-1")

    assert exc_info.value.code == "ADB_SETTINGS_FAILED"
    assert exc_info.value.layer == "accessibility"


def test_enable_agent_accessibility_retries_unconfirmed_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_values = [
        "null",
        "null",
        "1",
        "null",
        setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE,
        "1",
    ]
    put_calls: list[tuple[str, str]] = []
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        setup_adb,
        "get_secure_setting",
        lambda key, *, serial, adb_path="adb": get_values.pop(0),
    )
    monkeypatch.setattr(
        setup_adb,
        "put_secure_setting",
        lambda key, value, *, serial, adb_path="adb": put_calls.append((key, value)),
    )
    monkeypatch.setattr(
        accessibility.time,
        "sleep",
        lambda delay: sleep_calls.append(delay),
    )

    result = accessibility.enable_agent_accessibility(
        serial="device-1",
        attempts=2,
        retry_delay_seconds=0.25,
    )

    assert result.changed_service_list
    assert result.enabled_services == setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE
    assert sleep_calls == [0.25]
    assert put_calls == [
        (
            accessibility.ENABLED_ACCESSIBILITY_SERVICES,
            setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE,
        ),
        (
            accessibility.ACCESSIBILITY_ENABLED,
            accessibility.ACCESSIBILITY_ENABLED_VALUE,
        ),
        (
            accessibility.ENABLED_ACCESSIBILITY_SERVICES,
            setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE,
        ),
        (
            accessibility.ACCESSIBILITY_ENABLED,
            accessibility.ACCESSIBILITY_ENABLED_VALUE,
        ),
    ]


@pytest.mark.parametrize(
    ("verified_services", "verified_enabled", "expected_message"),
    [
        ("other/service", "1", "AndroidCtl Accessibility"),
        (setup_adb.ANDROIDCTL_ACCESSIBILITY_SERVICE, "0", "globally enabled"),
    ],
)
def test_enable_agent_accessibility_requires_readback_confirmation(
    verified_services: str,
    verified_enabled: str,
    expected_message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_values = ["null", verified_services, verified_enabled]
    monkeypatch.setattr(
        setup_adb,
        "get_secure_setting",
        lambda key, *, serial, adb_path="adb": get_values.pop(0),
    )
    monkeypatch.setattr(
        setup_adb,
        "put_secure_setting",
        lambda key, value, *, serial, adb_path="adb": setup_adb.AdbCommandResult(),
    )

    with pytest.raises(accessibility.SetupAccessibilityError) as exc_info:
        accessibility.enable_agent_accessibility(serial="device-1", attempts=1)

    assert exc_info.value.code == "ACCESSIBILITY_ENABLE_NOT_CONFIRMED"
    assert expected_message in exc_info.value.message
