from androidctl.renderers._paths import normalize_public_path


def test_normalize_public_path_renders_screenshot_workspace_relative_path() -> None:
    assert (
        normalize_public_path(
            "/repo/.androidctl/screenshots/screen-00013.png",
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
        )
        == ".androidctl/screenshots/screen-00013.png"
    )


def test_normalize_public_path_renders_screen_xml_workspace_relative_path() -> None:
    assert (
        normalize_public_path(
            "/repo/.androidctl/artifacts/screens/screen-00013.xml",
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
        )
        == ".androidctl/artifacts/screens/screen-00013.xml"
    )


def test_normalize_public_path_preserves_internal_screen_absolute_path() -> None:
    assert (
        normalize_public_path(
            "/repo/.androidctl/screens/screen-00013.md",
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
        )
        == "/repo/.androidctl/screens/screen-00013.md"
    )


def test_normalize_public_path_preserves_external_absolute_path() -> None:
    assert (
        normalize_public_path(
            "/tmp/screen-00013.md",
            workspace_root="/repo",
            artifact_root="/repo/.androidctl",
        )
        == "/tmp/screen-00013.md"
    )


def test_normalize_public_path_renders_windows_screenshot_case_insensitively() -> None:
    assert (
        normalize_public_path(
            "d:/repo/.androidctl/screenshots/screen-00013.png",
            workspace_root="D:/repo",
            artifact_root="D:/repo/.androidctl",
        )
        == ".androidctl/screenshots/screen-00013.png"
    )


def test_normalize_public_path_renders_windows_screen_xml_case_insensitively() -> None:
    assert (
        normalize_public_path(
            "d:/repo/.androidctl/artifacts/screens/screen-00013.xml",
            workspace_root="D:/repo",
            artifact_root="D:/repo/.androidctl",
        )
        == ".androidctl/artifacts/screens/screen-00013.xml"
    )


def test_normalize_public_path_preserves_windows_internal_screen_case() -> None:
    assert (
        normalize_public_path(
            "d:/repo/.androidctl/screens/screen-00013.md",
            workspace_root="D:/repo",
            artifact_root="D:/repo/.androidctl",
        )
        == "d:/repo/.androidctl/screens/screen-00013.md"
    )
