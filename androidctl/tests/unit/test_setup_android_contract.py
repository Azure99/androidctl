from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from androidctl.setup import adb, pairing


def test_host_setup_activity_contract_matches_android_sources() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest_path = repo_root / "android/app/src/main/AndroidManifest.xml"
    contract_path = (
        repo_root
        / "android/app/src/main/java/com/rainng/androidctl/SetupActivityContract.kt"
    )
    provisioning_path = (
        repo_root
        / "android/app/src/main/java/com/rainng/androidctl/agent/auth"
        / "HostTokenProvisioning.kt"
    )
    manifest = ElementTree.parse(manifest_path).getroot()
    namespace = "{http://schemas.android.com/apk/res/android}"

    setup_activity = _single_manifest_element(
        manifest,
        tag="activity",
        android_name=".SetupActivity",
        namespace=namespace,
    )
    accessibility_service = _single_manifest_element(
        manifest,
        tag="service",
        android_name=".agent.service.DeviceAccessibilityService",
        namespace=namespace,
    )
    actions = [
        action.attrib[f"{namespace}name"]
        for action in setup_activity.findall("./intent-filter/action")
    ]

    contract_source = contract_path.read_text(encoding="utf-8")
    provisioning_source = provisioning_path.read_text(encoding="utf-8")

    assert (f"{adb.ANDROIDCTL_PACKAGE}/.SetupActivity") == adb.ANDROIDCTL_SETUP_ACTIVITY
    assert setup_activity.attrib[f"{namespace}exported"] == "true"
    assert actions == [adb.ANDROIDCTL_SETUP_ACTION]
    assert _kotlin_const(contract_source, "ACTION_SETUP") == adb.ANDROIDCTL_SETUP_ACTION
    assert _kotlin_const(contract_source, "COMPONENT_CLASS_NAME") == (
        "com.rainng.androidctl.SetupActivity"
    )
    assert _kotlin_const(provisioning_source, "EXTRA_DEVICE_TOKEN") == (
        pairing.SETUP_DEVICE_TOKEN_EXTRA
    )
    assert (
        _expanded_manifest_component(
            package=adb.ANDROIDCTL_PACKAGE,
            android_name=accessibility_service.attrib[f"{namespace}name"],
        )
        == adb.ANDROIDCTL_ACCESSIBILITY_SERVICE
    )


def _single_manifest_element(
    manifest: ElementTree.Element,
    *,
    tag: str,
    android_name: str,
    namespace: str,
) -> ElementTree.Element:
    matches = [
        element
        for element in manifest.findall(f".//{tag}")
        if element.attrib.get(f"{namespace}name") == android_name
    ]
    assert len(matches) == 1
    return matches[0]


def _kotlin_const(source: str, name: str) -> str:
    match = re.search(rf'const val {re.escape(name)} = "([^"]+)"', source)
    assert match is not None
    return match.group(1)


def _expanded_manifest_component(*, package: str, android_name: str) -> str:
    class_name = (
        f"{package}{android_name}" if android_name.startswith(".") else android_name
    )
    return f"{package}/{class_name}"
