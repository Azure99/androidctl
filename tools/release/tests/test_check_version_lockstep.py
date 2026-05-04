from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tools.release.version_lockstep import (
    derive_android_version_code,
    format_report,
    run_checks,
)


def test_run_checks_pass_for_valid_fixture(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)

    report = run_checks(repo_root)

    assert report.ok
    assert report.canonical_version == "0.1.0"


def test_invalid_version_file_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(repo_root / "VERSION", "v0.1.0\n")

    report = run_checks(repo_root)

    assert not report.ok
    assert _find_failure(report, "canonical release version") is not None


@pytest.mark.parametrize(
    ("version_text", "expected_version_code"),
    [
        ("0.1.0", 1_000),
        ("1.2.3", 1_002_003),
        ("2100.0.0", 2_100_000_000),
    ],
)
def test_derive_android_version_code_accepts_valid_values(
    version_text: str,
    expected_version_code: int,
) -> None:
    assert derive_android_version_code(version_text) == expected_version_code


@pytest.mark.parametrize(
    "version_text",
    [
        "0.0.0",
        "1.1000.0",
        "1.0.1000",
        "2100.1.0",
    ],
)
def test_derive_android_version_code_rejects_invalid_values(version_text: str) -> None:
    with pytest.raises(ValueError, match="must"):
        derive_android_version_code(version_text)


def test_root_pyproject_version_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "pyproject.toml",
        _pyproject_text(version="0.1.1"),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "root project.version")
    assert failure is not None
    assert failure.actual == "0.1.1"


def test_root_pyproject_name_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "pyproject.toml",
        _pyproject_text(project_name="androidctld"),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "root project.name")
    assert failure is not None
    assert failure.actual == "androidctld"


def test_root_package_discovery_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "pyproject.toml",
        _pyproject_text(where='["androidctl/src"]'),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "root package discovery")
    assert failure is not None
    assert "contracts/src" in failure.expected


def test_child_packaging_entrypoint_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "androidctl/pyproject.toml",
        "[project]\nname='androidctl'\n",
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "child packaging entrypoint absent")
    assert failure is not None
    assert failure.path == repo_root / "androidctl/pyproject.toml"


def test_runtime_version_module_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "contracts/src/androidctl_contracts/_version.py",
        '__version__ = "0.1.2"\n',
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "contracts runtime __version__")
    assert failure is not None
    assert failure.actual == "0.1.2"


def test_python_reexport_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "androidctl/src/androidctl/__init__.py",
        "from ._version import __version__\n__all__ = []\n",
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "androidctl package __version__ re-export")
    assert failure is not None
    assert "__all__" in failure.expected


def test_daemon_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "androidctld/src/androidctld/daemon/service.py",
        (
            "from androidctld import __version__\n"
            "def build_health():\n"
            '    return HealthResult(version="0.1.0")\n'
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "daemon health version source")
    assert failure is not None


def test_android_version_name_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "android/app/build.gradle.kts",
        _build_gradle_text(
            version_name_expr='"0.1.0"',
            version_code_expr="canonicalReleaseVersionCode",
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android versionName source")
    assert failure is not None


def test_android_version_name_comment_decoy_still_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "android/app/build.gradle.kts",
        (
            "private fun readCanonicalReleaseVersion(): String {\n"
            "    val repoRoot = rootProject.projectDir.parentFile\n"
            '    val versionFile = repoRoot.resolve("VERSION")\n'
            '    return versionFile.readText(Charsets.UTF_8).removeSuffix("\\n")\n'
            "}\n\n"
            "val canonicalReleaseVersion = readCanonicalReleaseVersion()\n\n"
            "android {\n"
            "    defaultConfig {\n"
            '        versionName = "0.1.0"\n'
            "        // versionName = canonicalReleaseVersion\n"
            "    }\n"
            "}\n"
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android versionName source")
    assert failure is not None


def test_android_version_code_derivation_fails_for_invalid_canonical_version(
    tmp_path: Path,
) -> None:
    repo_root = _write_valid_repo(tmp_path, version="0.0.0")

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android versionCode derivation")
    assert failure is not None
    assert "versionCode must be in 1..2100000000" in failure.actual


def test_android_version_code_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "android/app/build.gradle.kts",
        _build_gradle_text(
            version_name_expr="canonicalReleaseVersion",
            version_code_expr="1",
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android versionCode source")
    assert failure is not None


def test_android_version_code_formula_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "android/app/build.gradle.kts",
        _build_gradle_text(
            version_name_expr="canonicalReleaseVersion",
            version_code_expr="canonicalReleaseVersionCode",
            major_multiplier="100_000L",
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android versionCode source")
    assert failure is not None


def test_android_version_code_range_check_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "android/app/build.gradle.kts",
        _build_gradle_text(
            version_name_expr="canonicalReleaseVersion",
            version_code_expr="canonicalReleaseVersionCode",
            include_minor_range_check=False,
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android versionCode source")
    assert failure is not None


def test_android_rpc_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root
        / "android/app/src/main/java/com/rainng/androidctl/agent/rpc/RpcEnvironment.kt",
        (
            "package com.rainng.androidctl.agent.rpc\n\n"
            "internal class RpcEnvironment(\n"
            '    val versionProvider: () -> String = { "0.1.0" },\n'
            ")\n"
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android RPC default version provider")
    assert failure is not None


def test_android_rpc_comment_decoy_still_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root
        / "android/app/src/main/java/com/rainng/androidctl/agent/rpc/RpcEnvironment.kt",
        (
            "package com.rainng.androidctl.agent.rpc\n\n"
            "import com.rainng.androidctl.BuildConfig\n\n"
            "internal class RpcEnvironment(\n"
            '    val versionProvider: () -> String = { "0.1.0" },\n'
            "    // val versionProvider: () -> String = { BuildConfig.VERSION_NAME },\n"
            ")\n"
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android RPC default version provider")
    assert failure is not None


def test_android_meta_get_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root
        / "android/app/src/main/java/com/rainng/androidctl/agent/rpc/MetaGetMethod.kt",
        (
            "package com.rainng.androidctl.agent.rpc\n\n"
            "internal class MetaGetMethod(\n"
            "    private val versionProvider: () -> String,\n"
            ") {\n"
            "    fun build(): MetaResponse =\n"
            "        MetaResponse(\n"
            '            service = "androidctl",\n'
            '            version = "0.1.0",\n'
            "        )\n"
            "}\n"
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android meta.get version source")
    assert failure is not None


def test_android_meta_get_dead_code_decoy_still_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root
        / "android/app/src/main/java/com/rainng/androidctl/agent/rpc/MetaGetMethod.kt",
        (
            "package com.rainng.androidctl.agent.rpc\n\n"
            "internal class MetaGetMethod(\n"
            "    private val versionProvider: () -> String,\n"
            ") {\n"
            "    fun build(): MetaResponse =\n"
            "        MetaResponse(\n"
            '            service = "androidctl",\n'
            '            version = "0.1.0",\n'
            "        )\n\n"
            "    private fun decoy(): MetaResponse {\n"
            "        if (false) {\n"
            "            return MetaResponse(\n"
            '                service = "androidctl",\n'
            "                version = versionProvider(),\n"
            "            )\n"
            "        }\n"
            '        return MetaResponse(service = "androidctl", version = "0.1.0")\n'
            "    }\n"
            "}\n"
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "Android meta.get version source")
    assert failure is not None


def test_androidctl_packaged_apk_package_data_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "pyproject.toml",
        _pyproject_text(apk_package_data='["*.txt"]'),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "androidctl packaged APK package data")
    assert failure is not None
    assert "*.apk" in failure.expected


def test_packaged_apk_resource_package_missing_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    (repo_root / "androidctl/src/androidctl/resources/__init__.py").unlink()

    report = run_checks(repo_root)

    failure = _find_failure(report, "packaged APK importlib.resources package")
    assert failure is not None


def test_packaged_apk_resource_resolver_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "androidctl/src/androidctl/setup/apk_resource.py",
        (
            'AGENT_APK_RESOURCE_PACKAGE = "androidctl.resources"\n'
            'AGENT_APK_NAME_TEMPLATE = "androidctl-agent-{version}.apk"\n'
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "packaged APK resource resolver source")
    assert failure is not None
    assert "androidctl-agent-{version}-release.apk" in failure.expected


def test_pypi_packaged_apk_staging_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "tools/release/pypi_release.py",
        _pypi_release_text(resource_dir="androidctl/resources"),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "PyPI packaged APK staging source")
    assert failure is not None
    assert "androidctl/src/androidctl/resources" in failure.expected


def test_pypi_packaged_apk_version_evidence_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "tools/release/pypi_release.py",
        _pypi_release_text(include_version_code_check=False),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "PyPI packaged APK version evidence source")
    assert failure is not None
    assert "versionCode" in failure.expected


@pytest.mark.parametrize(
    "pypi_text_kwargs",
    [
        {"version_code_operator": "=="},
        {"derive_version_code_arg": '"0.0.0"'},
        {"version_code_joiner": "and"},
        {"version_name_suffix": " and False"},
    ],
)
def test_pypi_packaged_apk_version_evidence_rejects_expression_drift(
    tmp_path: Path,
    pypi_text_kwargs: dict[str, Any],
) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "tools/release/pypi_release.py",
        _pypi_release_text(**pypi_text_kwargs),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "PyPI packaged APK version evidence source")
    assert failure is not None


@pytest.mark.parametrize(
    "pypi_text_kwargs",
    [
        {"include_version_code_check": False, "include_decoy": True},
        {
            "include_version_code_check": False,
            "include_decoy": True,
            "include_early_return_before_decoy": True,
        },
    ],
)
def test_pypi_packaged_apk_version_evidence_rejects_dead_code_decoys(
    tmp_path: Path,
    pypi_text_kwargs: dict[str, Any],
) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "tools/release/pypi_release.py",
        _pypi_release_text(**pypi_text_kwargs),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "PyPI packaged APK version evidence source")
    assert failure is not None


@pytest.mark.parametrize(
    "pypi_text_kwargs",
    [
        {"include_output_sha_check": False},
        {"checksum_suffix": " and False"},
        {"output_path_source": "paths.packaged_apk_source_path"},
    ],
)
def test_pypi_packaged_apk_version_evidence_rejects_checksum_drift(
    tmp_path: Path,
    pypi_text_kwargs: dict[str, Any],
) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "tools/release/pypi_release.py",
        _pypi_release_text(**pypi_text_kwargs),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "PyPI packaged APK version evidence source")
    assert failure is not None


def test_daemon_server_banner_source_drift_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "androidctld/src/androidctld/daemon/server.py",
        (
            "from androidctld import SERVICE_NAME, __version__\n\n"
            "class RequestHandler:\n"
            '    server_version = "androidctld/0.1.0"\n'
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "daemon server banner version source")
    assert failure is not None


def test_daemon_health_dead_code_decoy_still_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "androidctld/src/androidctld/daemon/service.py",
        (
            "from androidctld import __version__\n\n"
            "class DaemonService:\n"
            "    def _handle_health(self, payload):\n"
            '        return HealthResult(version="0.1.0")\n\n'
            "def decoy_health():\n"
            "    return HealthResult(version=__version__)\n"
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "daemon health version source")
    assert failure is not None


def test_daemon_server_banner_dead_code_decoy_still_fails(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "androidctld/src/androidctld/daemon/server.py",
        (
            "from androidctld import SERVICE_NAME, __version__\n\n"
            "class AndroidctldHttpServer:\n"
            "    def _build_handler(self):\n"
            "        class RequestHandler:\n"
            '            server_version = "androidctld/0.1.0"\n\n'
            "        class DecoyHandler:\n"
            '            server_version = f"{SERVICE_NAME}/{__version__}"\n\n'
            "        return RequestHandler\n"
        ),
    )

    report = run_checks(repo_root)

    failure = _find_failure(report, "daemon server banner version source")
    assert failure is not None


def test_failure_message_contains_path_expected_and_actual(tmp_path: Path) -> None:
    repo_root = _write_valid_repo(tmp_path)
    _write_file(
        repo_root / "pyproject.toml",
        _pyproject_text(version="0.1.1"),
    )

    report = run_checks(repo_root)
    rendered = format_report(report)

    assert "pyproject.toml" in rendered
    assert "expected: 0.1.0" in rendered
    assert "actual: 0.1.1" in rendered


def _find_failure(report, check_name: str):
    for failure in report.failures:
        if failure.check_name == check_name:
            return failure
    return None


def _write_valid_repo(repo_root: Path, version: str = "0.1.0") -> Path:
    _write_file(repo_root / "VERSION", f"{version}\n")
    _write_file(
        repo_root / "pyproject.toml",
        _pyproject_text(version=version),
    )
    _write_file(
        repo_root / "contracts/src/androidctl_contracts/_version.py",
        f'__version__ = "{version}"\n',
    )
    _write_file(
        repo_root / "androidctl/src/androidctl/_version.py",
        f'__version__ = "{version}"\n',
    )
    _write_file(
        repo_root / "androidctld/src/androidctld/_version.py",
        f'__version__ = "{version}"\n',
    )
    _write_file(
        repo_root / "contracts/src/androidctl_contracts/__init__.py",
        'from ._version import __version__\n__all__ = ["__version__"]\n',
    )
    _write_file(
        repo_root / "androidctl/src/androidctl/__init__.py",
        'from ._version import __version__\n__all__ = ["__version__"]\n',
    )
    _write_file(
        repo_root / "androidctld/src/androidctld/__init__.py",
        'from ._version import __version__\nSERVICE_NAME = "androidctld"\n',
    )
    _write_file(
        repo_root / "androidctld/src/androidctld/daemon/service.py",
        (
            "from androidctld import __version__\n\n"
            "class DaemonService:\n"
            "    def _handle_health(self, payload):\n"
            "        return HealthResult(\n"
            '            service="androidctld",\n'
            "            version=__version__,\n"
            '            workspace_root="/tmp",\n'
            '            owner_id="",\n'
            '        ).model_dump(mode="json")\n'
        ),
    )
    _write_file(
        repo_root / "androidctld/src/androidctld/daemon/server.py",
        (
            "from androidctld import SERVICE_NAME, __version__\n\n"
            "class AndroidctldHttpServer:\n"
            "    def _build_handler(self):\n"
            "        class RequestHandler:\n"
            '            server_version = f"{SERVICE_NAME}/{__version__}"\n'
            "        return RequestHandler\n"
        ),
    )
    _write_file(
        repo_root / "android/app/build.gradle.kts",
        _build_gradle_text(
            version_name_expr="canonicalReleaseVersion",
            version_code_expr="canonicalReleaseVersionCode",
        ),
    )
    _write_file(
        repo_root
        / "android/app/src/main/java/com/rainng/androidctl/agent/rpc/RpcEnvironment.kt",
        (
            "package com.rainng.androidctl.agent.rpc\n\n"
            "import com.rainng.androidctl.BuildConfig\n\n"
            "internal class RpcEnvironment(\n"
            "    val versionProvider: () -> String = { BuildConfig.VERSION_NAME },\n"
            ")\n"
        ),
    )
    _write_file(
        repo_root
        / "android/app/src/main/java/com/rainng/androidctl/agent/rpc/MetaGetMethod.kt",
        (
            "package com.rainng.androidctl.agent.rpc\n\n"
            "internal class MetaGetMethod(\n"
            "    private val versionProvider: () -> String,\n"
            ") : DeviceRpcMethod {\n"
            "    override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall =\n"
            "        PreparedRpcMethodSupport.prepareUnit(\n"
            "            execute = {\n"
            "                MetaResponse(\n"
            '                    service = "androidctl",\n'
            "                    version = versionProvider(),\n"
            "                )\n"
            "            },\n"
            "            encoder = MetaResponseCodec,\n"
            "        )\n"
            "}\n"
        ),
    )
    _write_file(repo_root / "androidctl/src/androidctl/resources/__init__.py", "")
    _write_file(
        repo_root / "androidctl/src/androidctl/setup/apk_resource.py",
        (
            'AGENT_APK_RESOURCE_PACKAGE = "androidctl.resources"\n'
            'AGENT_APK_NAME_TEMPLATE = "androidctl-agent-{version}-release.apk"\n'
        ),
    )
    _write_file(
        repo_root / "tools/release/pypi_release.py",
        _pypi_release_text(),
    )
    return repo_root


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pyproject_text(
    project_name: str = "androidctl",
    version: str = "0.1.0",
    where: str = '["contracts/src", "androidctld/src", "androidctl/src"]',
    include: str = '["androidctl_contracts*", "androidctld*", "androidctl*"]',
    namespaces: str = "false",
    apk_package_data: str = '["*.apk"]',
) -> str:
    return (
        "[project]\n"
        f'name = "{project_name}"\n'
        f'version = "{version}"\n'
        "dependencies = [\n"
        '    "typing-extensions>=4.12,<5",\n'
        "]\n"
        "\n"
        "[tool.setuptools.packages.find]\n"
        f"where = {where}\n"
        f"include = {include}\n"
        f"namespaces = {namespaces}\n"
        "\n"
        "[tool.setuptools.package-data]\n"
        f'"androidctl.resources" = {apk_package_data}\n'
    )


def _pypi_release_text(
    *,
    resource_dir: str = "androidctl/src/androidctl/resources",
    include_version_code_check: bool = True,
    version_code_operator: str = "!=",
    version_code_joiner: str = "or",
    derive_version_code_arg: str = "paths.version",
    version_name_suffix: str = "",
    include_decoy: bool = False,
    include_early_return_before_decoy: bool = False,
    include_try_body_return: bool = False,
    include_try_body_guarded_return: bool = False,
    include_output_sha_check: bool = True,
    checksum_suffix: str = "",
    output_path_source: str = (
        "paths.gradle_apk_metadata_path.parent / output_file_path"
    ),
) -> str:
    try_body_return = "        return None\n" if include_try_body_return else ""
    try_body_guarded_return = (
        "        if True:\n" "            return None\n"
        if include_try_body_guarded_return
        else ""
    )
    version_code_check = (
        '    version_code = element.get("versionCode")\n'
        "    expected_version_code = "
        f"derive_android_version_code({derive_version_code_arg})\n"
        f"    if type(version_code) is not int {version_code_joiner} "
        f"version_code {version_code_operator} expected_version_code:\n"
        '        raise SystemExit("bad version code")\n'
        if include_version_code_check
        else ""
    )
    early_return = "    return None\n" if include_early_return_before_decoy else ""
    decoy = (
        "    if False:\n"
        '        version_code = element.get("versionCode")\n'
        "        expected_version_code = derive_android_version_code(paths.version)\n"
        "        if (\n"
        "            type(version_code) is not int\n"
        "            or version_code != expected_version_code\n"
        "        ):\n"
        '            raise SystemExit("bad version code")\n'
        if include_decoy
        else ""
    )
    output_sha_check = (
        '    output_file = element.get("outputFile")\n'
        "    output_file_path = Path(output_file)\n"
        f"    output_path = {output_path_source}\n"
        "    output_sha256 = sha256_file(output_path)\n"
        f"    if output_sha256 != expected_sha256{checksum_suffix}:\n"
        '        raise SystemExit("bad checksum")\n'
        if include_output_sha_check
        else ""
    )
    return (
        "import json\n"
        "from pathlib import Path\n\n"
        "from tools.release.version_lockstep import derive_android_version_code\n\n"
        'PACKAGED_APK_TEMPLATE = "androidctl-agent-{version}-release.apk"\n'
        f'PACKAGED_APK_RESOURCE_DIR = Path("{resource_dir}")\n'
        'PACKAGED_APK_RESOURCE_GLOB = "androidctl-agent-*-release.apk"\n\n'
        "def inspect_gradle_apk_evidence(paths, expected_sha256):\n"
        "    try:\n"
        "        metadata = json.loads(\n"
        '        paths.gradle_apk_metadata_path.read_text(encoding="utf-8")\n'
        "        )\n"
        f"{try_body_return}"
        f"{try_body_guarded_return}"
        "    except ValueError:\n"
        '        raise SystemExit("bad metadata")\n'
        '    elements = metadata.get("elements")\n'
        "    element = elements[0]\n"
        '    version_name = element.get("versionName")\n'
        f"    if version_name != paths.version{version_name_suffix}:\n"
        '        raise SystemExit("bad version name")\n'
        f"{version_code_check}"
        f"{early_return}"
        f"{decoy}"
        f"{output_sha_check}"
    )


def _build_gradle_text(
    version_name_expr: str,
    version_code_expr: str,
    *,
    major_multiplier: str = "1_000_000L",
    include_minor_range_check: bool = True,
    include_patch_range_check: bool = True,
    include_upper_bound_check: bool = True,
) -> str:
    minor_range_check = (
        "    if (minor !in 0L..999L) {\n"
        '        throw GradleException("minor out of range")\n'
        "    }\n"
        if include_minor_range_check
        else ""
    )
    patch_range_check = (
        "    if (patch !in 0L..999L) {\n"
        '        throw GradleException("patch out of range")\n'
        "    }\n"
        if include_patch_range_check
        else ""
    )
    upper_bound_check = (
        "    if (versionCode < 1L || versionCode > 2_100_000_000L) {\n"
        '        throw GradleException("versionCode out of range")\n'
        "    }\n"
        if include_upper_bound_check
        else ""
    )
    return (
        "private fun readCanonicalReleaseVersion(): String {\n"
        "    val repoRoot = rootProject.projectDir.parentFile\n"
        '    val versionFile = repoRoot.resolve("VERSION")\n'
        '    return versionFile.readText(Charsets.UTF_8).removeSuffix("\\n")\n'
        "}\n\n"
        "private fun deriveAndroidVersionCode(canonicalVersion: String): Int {\n"
        '    val segments = canonicalVersion.split(".")\n'
        "    val major = segments[0].toLong()\n"
        "    val minor = segments[1].toLong()\n"
        "    val patch = segments[2].toLong()\n"
        f"{minor_range_check}"
        f"{patch_range_check}"
        "    val versionCode =\n"
        f"        (major * {major_multiplier}) + (minor * 1_000L) + patch\n"
        f"{upper_bound_check}"
        "    return versionCode.toInt()\n"
        "}\n\n"
        "val canonicalReleaseVersion = readCanonicalReleaseVersion()\n"
        "val canonicalReleaseVersionCode =\n"
        "    deriveAndroidVersionCode(canonicalReleaseVersion)\n\n"
        "android {\n"
        "    defaultConfig {\n"
        f"        versionCode = {version_code_expr}\n"
        f"        versionName = {version_name_expr}\n"
        "    }\n"
        "}\n"
    )
