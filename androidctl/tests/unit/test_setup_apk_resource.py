from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from androidctl.setup import apk_resource


def test_packaged_agent_apk_name_uses_public_release_name() -> None:
    assert (
        apk_resource.packaged_agent_apk_name("1.2.3")
        == "androidctl-agent-1.2.3-release.apk"
    )


@pytest.mark.parametrize("version", ["", "1.2", "1.2.3.dev1", "../1.2.3"])
def test_packaged_agent_apk_name_rejects_non_canonical_versions(version: str) -> None:
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        apk_resource.packaged_agent_apk_name(version)


def test_packaged_agent_apk_path_materializes_real_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_dir = tmp_path / "test_apk_resources"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    apk_path = package_dir / "androidctl-agent-1.2.3-release.apk"
    apk_path.write_bytes(b"apk")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(
        apk_resource,
        "AGENT_APK_RESOURCE_PACKAGE",
        "test_apk_resources",
    )

    with apk_resource.packaged_agent_apk_path("1.2.3") as resolved_path:
        assert resolved_path == apk_path
        assert resolved_path.read_bytes() == b"apk"


def test_packaged_agent_apk_path_materializes_zip_resource_lifetime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zip_path = tmp_path / "apk_resources.zip"
    with ZipFile(zip_path, "w") as resources_zip:
        resources_zip.writestr("zip_apk_resources/__init__.py", "")
        resources_zip.writestr(
            "zip_apk_resources/androidctl-agent-1.2.3-release.apk",
            b"apk",
        )
    monkeypatch.syspath_prepend(str(zip_path))
    monkeypatch.setattr(
        apk_resource,
        "AGENT_APK_RESOURCE_PACKAGE",
        "zip_apk_resources",
    )
    sys.modules.pop("zip_apk_resources", None)

    with apk_resource.packaged_agent_apk_path("1.2.3") as resolved_path:
        materialized_path = resolved_path
        assert resolved_path.is_file()
        assert resolved_path.read_bytes() == b"apk"

    assert not materialized_path.exists()


def test_packaged_agent_apk_path_fails_when_resource_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_dir = tmp_path / "empty_apk_resources"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(
        apk_resource,
        "AGENT_APK_RESOURCE_PACKAGE",
        "empty_apk_resources",
    )

    sys.modules.pop("empty_apk_resources", None)
    with (
        pytest.raises(FileNotFoundError, match="androidctl-agent-1.2.3-release.apk"),
        apk_resource.packaged_agent_apk_path("1.2.3"),
    ):
        pass
