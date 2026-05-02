from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+\Z")
_ANDROID_VERSION_COMPONENT_MAX = 999
_ANDROID_VERSION_CODE_MIN = 1
_ANDROID_VERSION_CODE_MAX = 2_100_000_000
_PACKAGED_APK_RESOURCE_PACKAGE = "androidctl.resources"
_PACKAGED_APK_RESOURCE_DIR = "androidctl/src/androidctl/resources"
_PACKAGED_APK_TEMPLATE = "androidctl-agent-{version}-release.apk"
_PACKAGED_APK_GLOB = "androidctl-agent-*-release.apk"


@dataclass(frozen=True)
class CheckFailure:
    check_name: str
    path: Path
    expected: str
    actual: str


@dataclass(frozen=True)
class CheckReport:
    repo_root: Path
    canonical_version: str | None
    checked_items: int
    failures: tuple[CheckFailure, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


class _VersionParseError(ValueError):
    pass


class _AndroidVersionCodeError(ValueError):
    pass


def parse_canonical_version_text(raw_text: str) -> str:
    if raw_text.endswith("\r\n"):
        candidate = raw_text.removesuffix("\r\n")
    elif raw_text.endswith("\n"):
        candidate = raw_text.removesuffix("\n")
    else:
        candidate = raw_text
    if (
        "\n" in candidate
        or "\r" in candidate
        or not _VERSION_PATTERN.fullmatch(candidate)
    ):
        raise _VersionParseError(
            "must contain exactly MAJOR.MINOR.PATCH with at most one terminating newline"
        )
    return candidate


def derive_android_version_code(canonical_version: str) -> int:
    normalized_version = parse_canonical_version_text(canonical_version)
    major_text, minor_text, patch_text = normalized_version.split(".")
    major = int(major_text)
    minor = int(minor_text)
    patch = int(patch_text)

    if not 0 <= minor <= _ANDROID_VERSION_COMPONENT_MAX:
        raise _AndroidVersionCodeError(
            f"minor must be in 0..{_ANDROID_VERSION_COMPONENT_MAX}, got {minor}"
        )
    if not 0 <= patch <= _ANDROID_VERSION_COMPONENT_MAX:
        raise _AndroidVersionCodeError(
            f"patch must be in 0..{_ANDROID_VERSION_COMPONENT_MAX}, got {patch}"
        )

    version_code = (major * 1_000_000) + (minor * 1_000) + patch
    if not _ANDROID_VERSION_CODE_MIN <= version_code <= _ANDROID_VERSION_CODE_MAX:
        raise _AndroidVersionCodeError(
            f"versionCode must be in {_ANDROID_VERSION_CODE_MIN}..{_ANDROID_VERSION_CODE_MAX}, got {version_code}"
        )
    return version_code


def run_checks(repo_root: Path) -> CheckReport:
    repo_root = repo_root.resolve()
    failures: list[CheckFailure] = []
    checked_items = 0

    canonical_version = _check_canonical_version(repo_root, failures)
    checked_items += 1

    if canonical_version is not None:
        _check_pyproject_version(
            repo_root,
            Path("contracts/pyproject.toml"),
            canonical_version,
            "contracts project.version",
            failures,
        )
        _check_pyproject_version(
            repo_root,
            Path("androidctl/pyproject.toml"),
            canonical_version,
            "androidctl project.version",
            failures,
        )
        _check_pyproject_version(
            repo_root,
            Path("androidctld/pyproject.toml"),
            canonical_version,
            "androidctld project.version",
            failures,
        )
        _check_dependency_pin(
            repo_root,
            Path("androidctl/pyproject.toml"),
            "androidctl-contracts",
            canonical_version,
            "androidctl androidctl-contracts pin",
            failures,
        )
        _check_dependency_pin(
            repo_root,
            Path("androidctl/pyproject.toml"),
            "androidctld",
            canonical_version,
            "androidctl androidctld pin",
            failures,
        )
        _check_dependency_pin(
            repo_root,
            Path("androidctld/pyproject.toml"),
            "androidctl-contracts",
            canonical_version,
            "androidctld androidctl-contracts pin",
            failures,
        )
        _check_version_module(
            repo_root,
            Path("contracts/src/androidctl_contracts/_version.py"),
            canonical_version,
            "contracts runtime __version__",
            failures,
        )
        _check_version_module(
            repo_root,
            Path("androidctl/src/androidctl/_version.py"),
            canonical_version,
            "androidctl runtime __version__",
            failures,
        )
        _check_version_module(
            repo_root,
            Path("androidctld/src/androidctld/_version.py"),
            canonical_version,
            "androidctld runtime __version__",
            failures,
        )
        _check_android_version_code_derivation(
            repo_root,
            Path("VERSION"),
            canonical_version,
            failures,
        )
        checked_items += 10

    _check_package_reexport(
        repo_root,
        Path("contracts/src/androidctl_contracts/__init__.py"),
        "contracts package __version__ re-export",
        failures,
    )
    _check_package_reexport(
        repo_root,
        Path("androidctl/src/androidctl/__init__.py"),
        "androidctl package __version__ re-export",
        failures,
    )
    _check_package_reexport(
        repo_root,
        Path("androidctld/src/androidctld/__init__.py"),
        "androidctld package __version__ re-export",
        failures,
    )
    _check_daemon_health_source(
        repo_root,
        Path("androidctld/src/androidctld/daemon/service.py"),
        failures,
    )
    _check_daemon_server_banner_source(
        repo_root,
        Path("androidctld/src/androidctld/daemon/server.py"),
        failures,
    )
    _check_android_version_name_source(
        repo_root,
        Path("android/app/build.gradle.kts"),
        failures,
    )
    _check_android_version_code_source(
        repo_root,
        Path("android/app/build.gradle.kts"),
        failures,
    )
    _check_android_rpc_environment_source(
        repo_root,
        Path(
            "android/app/src/main/java/com/rainng/androidctl/agent/rpc/RpcEnvironment.kt"
        ),
        failures,
    )
    _check_android_meta_get_source(
        repo_root,
        Path(
            "android/app/src/main/java/com/rainng/androidctl/agent/rpc/MetaGetMethod.kt"
        ),
        failures,
    )
    _check_androidctl_packaged_apk_package_data(
        repo_root,
        Path("androidctl/pyproject.toml"),
        failures,
    )
    _check_packaged_apk_resource_package(
        repo_root,
        Path("androidctl/src/androidctl/resources/__init__.py"),
        failures,
    )
    _check_packaged_apk_resource_resolver_source(
        repo_root,
        Path("androidctl/src/androidctl/setup/apk_resource.py"),
        failures,
    )
    _check_pypi_packaged_apk_staging_source(
        repo_root,
        Path("tools/release/pypi_release.py"),
        failures,
    )
    _check_pypi_packaged_apk_version_evidence_source(
        repo_root,
        Path("tools/release/pypi_release.py"),
        failures,
    )
    checked_items += 14

    return CheckReport(
        repo_root=repo_root,
        canonical_version=canonical_version,
        checked_items=checked_items,
        failures=tuple(failures),
    )


def format_report(report: CheckReport) -> str:
    if report.ok:
        return (
            f"release version lockstep OK: {report.canonical_version} "
            f"({report.checked_items} checks)"
        )
    lines = ["release version check failed:"]
    for failure in report.failures:
        location = failure.path.relative_to(report.repo_root).as_posix()
        lines.extend(
            [
                f"- {failure.check_name}",
                f"  file: {location}",
                f"  expected: {failure.expected}",
                f"  actual: {failure.actual}",
            ]
        )
    return "\n".join(lines)


def _check_canonical_version(
    repo_root: Path, failures: list[CheckFailure]
) -> str | None:
    version_path = repo_root / "VERSION"
    try:
        raw_text = version_path.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(
            CheckFailure(
                check_name="canonical release version",
                path=version_path,
                expected="exactly MAJOR.MINOR.PATCH with at most one terminating newline",
                actual=f"unable to read file: {error}",
            )
        )
        return None
    try:
        return parse_canonical_version_text(raw_text)
    except _VersionParseError:
        failures.append(
            CheckFailure(
                check_name="canonical release version",
                path=version_path,
                expected="exactly MAJOR.MINOR.PATCH with at most one terminating newline",
                actual=repr(raw_text),
            )
        )
        return None


def _check_pyproject_version(
    repo_root: Path,
    relative_path: Path,
    expected_version: str,
    check_name: str,
    failures: list[CheckFailure],
) -> None:
    pyproject_path = repo_root / relative_path
    try:
        project_data = _load_pyproject(pyproject_path)
        actual_version = project_data["project"]["version"]
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=pyproject_path,
                expected=expected_version,
                actual=f"unable to parse project.version: {error}",
            )
        )
        return
    if actual_version != expected_version:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=pyproject_path,
                expected=expected_version,
                actual=str(actual_version),
            )
        )


def _check_dependency_pin(
    repo_root: Path,
    relative_path: Path,
    dependency_name: str,
    expected_version: str,
    check_name: str,
    failures: list[CheckFailure],
) -> None:
    pyproject_path = repo_root / relative_path
    try:
        project_data = _load_pyproject(pyproject_path)
        dependencies = project_data["project"]["dependencies"]
        actual_pin = _find_dependency_specifier(dependencies, dependency_name)
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=pyproject_path,
                expected=f"{dependency_name}=={expected_version}",
                actual=f"unable to parse project.dependencies: {error}",
            )
        )
        return
    expected_pin = f"{dependency_name}=={expected_version}"
    if actual_pin != expected_pin:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=pyproject_path,
                expected=expected_pin,
                actual=actual_pin or "missing",
            )
        )


def _check_version_module(
    repo_root: Path,
    relative_path: Path,
    expected_version: str,
    check_name: str,
    failures: list[CheckFailure],
) -> None:
    module_path = repo_root / relative_path
    try:
        actual_version = _extract_string_assignment(module_path, "__version__")
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=module_path,
                expected=expected_version,
                actual=str(error),
            )
        )
        return
    if actual_version != expected_version:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=module_path,
                expected=expected_version,
                actual=actual_version,
            )
        )


def _check_package_reexport(
    repo_root: Path,
    relative_path: Path,
    check_name: str,
    failures: list[CheckFailure],
) -> None:
    module_path = repo_root / relative_path
    try:
        module = _parse_python_module(module_path)
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=module_path,
                expected="from ._version import __version__",
                actual=f"unable to parse module: {error}",
            )
        )
        return
    if not _has_version_reexport(module):
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=module_path,
                expected="from ._version import __version__",
                actual="designated __version__ re-export not found",
            )
        )
        return
    try:
        exported_names = _extract_dunder_all(module)
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=module_path,
                expected='__all__ is a static literal when present and exposes "__version__"',
                actual=str(error),
            )
        )
        return
    if exported_names is None:
        return
    if "__version__" not in exported_names:
        failures.append(
            CheckFailure(
                check_name=check_name,
                path=module_path,
                expected='__all__ contains "__version__"',
                actual=f"__all__ = {sorted(exported_names)!r}",
            )
        )


def _check_daemon_health_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    module_path = repo_root / relative_path
    try:
        module = _parse_python_module(module_path)
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name="daemon health version source",
                path=module_path,
                expected='HealthResult(..., version=__version__, ...) with "from androidctld import __version__"',
                actual=f"unable to parse module: {error}",
            )
        )
        return
    has_import = _module_imports_names(module, "androidctld", {"__version__"})
    service_class = _find_python_class(module, "DaemonService")
    handle_health = _find_python_method(service_class, "_handle_health")
    if (
        has_import
        and handle_health is not None
        and _method_returns_ctor_keyword_name(
            handle_health, "HealthResult", "version", "__version__"
        )
    ):
        return
    failures.append(
        CheckFailure(
            check_name="daemon health version source",
            path=module_path,
            expected='HealthResult(..., version=__version__, ...) with "from androidctld import __version__"',
            actual="designated health version wiring not found",
        )
    )


def _check_daemon_server_banner_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    module_path = repo_root / relative_path
    try:
        module = _parse_python_module(module_path)
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name="daemon server banner version source",
                path=module_path,
                expected='RequestHandler.server_version = f"{SERVICE_NAME}/{__version__}"',
                actual=f"unable to parse module: {error}",
            )
        )
        return
    has_import = _module_imports_names(
        module, "androidctld", {"SERVICE_NAME", "__version__"}
    )
    server_class = _find_python_class(module, "AndroidctldHttpServer")
    build_handler = _find_python_method(server_class, "_build_handler")
    returned_class = _find_returned_local_class(build_handler)
    if (
        has_import
        and returned_class is not None
        and _class_has_assignment(
            returned_class, "server_version", _is_service_name_version_fstring
        )
    ):
        return
    failures.append(
        CheckFailure(
            check_name="daemon server banner version source",
            path=module_path,
            expected='RequestHandler.server_version = f"{SERVICE_NAME}/{__version__}"',
            actual="designated server banner version wiring not found",
        )
    )


def _check_android_version_code_derivation(
    repo_root: Path,
    relative_path: Path,
    canonical_version: str,
    failures: list[CheckFailure],
) -> None:
    version_path = repo_root / relative_path
    try:
        derive_android_version_code(canonical_version)
    except ValueError as error:
        failures.append(
            CheckFailure(
                check_name="Android versionCode derivation",
                path=version_path,
                expected=(
                    "major * 1_000_000 + minor * 1_000 + patch with minor/patch in "
                    "0..999 and versionCode in 1..2_100_000_000"
                ),
                actual=str(error),
            )
        )


def _check_android_version_name_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    gradle_path = repo_root / relative_path
    try:
        source = gradle_path.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(
            CheckFailure(
                check_name="Android versionName source",
                path=gradle_path,
                expected="build.gradle.kts reads repo-root VERSION and assigns versionName = canonicalReleaseVersion",
                actual=f"unable to read file: {error}",
            )
        )
        return
    stripped_source = _strip_kotlin_comments(source)
    function_body = _extract_kotlin_block(
        stripped_source,
        r"private\s+fun\s+readCanonicalReleaseVersion\s*\(\s*\)\s*:\s*String\s*\{",
    )
    android_body = _extract_kotlin_block(stripped_source, r"\bandroid\s*\{")
    default_config_body = (
        _extract_kotlin_block(android_body, r"\bdefaultConfig\s*\{")
        if android_body is not None
        else None
    )
    function_lines = (
        "\n".join(_collect_top_level_lines(function_body))
        if function_body is not None
        else ""
    )
    if (
        function_body is not None
        and "rootProject.projectDir.parentFile" in function_lines
        and 'val versionFile = repoRoot.resolve("VERSION")' in function_lines
        and re.search(
            r"\bval\s+canonicalReleaseVersion\s*=\s*readCanonicalReleaseVersion\s*\(\s*\)",
            stripped_source,
        )
        and default_config_body is not None
        and "versionName = canonicalReleaseVersion"
        in "\n".join(_collect_top_level_lines(default_config_body))
    ):
        return
    failures.append(
        CheckFailure(
            check_name="Android versionName source",
            path=gradle_path,
            expected="build.gradle.kts reads repo-root VERSION and assigns versionName = canonicalReleaseVersion",
            actual="designated canonical VERSION wiring not found",
        )
    )


def _check_android_version_code_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    gradle_path = repo_root / relative_path
    try:
        source = gradle_path.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(
            CheckFailure(
                check_name="Android versionCode source",
                path=gradle_path,
                expected=(
                    "build.gradle.kts derives versionCode from canonicalReleaseVersion "
                    "using the Android formula and assigns "
                    "versionCode = canonicalReleaseVersionCode"
                ),
                actual=f"unable to read file: {error}",
            )
        )
        return
    stripped_source = _strip_kotlin_comments(source)
    function_body = _extract_kotlin_block(
        stripped_source,
        r"private\s+fun\s+deriveAndroidVersionCode\s*\(\s*canonicalVersion\s*:\s*String\s*\)\s*:\s*Int\s*\{",
    )
    android_body = _extract_kotlin_block(stripped_source, r"\bandroid\s*\{")
    default_config_body = (
        _extract_kotlin_block(android_body, r"\bdefaultConfig\s*\{")
        if android_body is not None
        else None
    )
    if (
        function_body is not None
        and re.search(
            r'val\s+segments\s*=\s*canonicalVersion\.split\(\s*"\."\s*\)',
            function_body,
        )
        and re.search(r"if\s*\(\s*minor\s*!in\s*0L\.\.999L\s*\)", function_body)
        and re.search(r"if\s*\(\s*patch\s*!in\s*0L\.\.999L\s*\)", function_body)
        and re.search(
            r"val\s+versionCode\s*=\s*\(major\s*\*\s*1_000_000L\)\s*\+\s*\(minor\s*\*\s*1_000L\)\s*\+\s*patch",
            function_body,
        )
        and re.search(
            r"if\s*\(\s*versionCode\s*<\s*1L\s*\|\|\s*versionCode\s*>\s*2_100_000_000L\s*\)",
            function_body,
        )
        and re.search(
            r"\bval\s+canonicalReleaseVersionCode\s*=\s*deriveAndroidVersionCode\s*\(\s*canonicalReleaseVersion\s*\)",
            stripped_source,
        )
        and default_config_body is not None
        and "versionCode = canonicalReleaseVersionCode"
        in "\n".join(_collect_top_level_lines(default_config_body))
    ):
        return
    failures.append(
        CheckFailure(
            check_name="Android versionCode source",
            path=gradle_path,
            expected=(
                "build.gradle.kts derives versionCode from canonicalReleaseVersion "
                "using the Android formula and assigns "
                "versionCode = canonicalReleaseVersionCode"
            ),
            actual="designated Android versionCode wiring not found",
        )
    )


def _check_android_rpc_environment_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    source_path = repo_root / relative_path
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(
            CheckFailure(
                check_name="Android RPC default version provider",
                path=source_path,
                expected="RpcEnvironment default versionProvider returns BuildConfig.VERSION_NAME",
                actual=f"unable to read file: {error}",
            )
        )
        return
    stripped_source = _strip_kotlin_comments(source)
    constructor_params = _extract_kotlin_group(
        stripped_source,
        r"internal\s+class\s+RpcEnvironment\s*\(",
    )
    has_build_config_import = bool(
        re.search(
            r"^\s*import\s+com\.rainng\.androidctl\.BuildConfig\s*$",
            stripped_source,
            re.MULTILINE,
        )
    )
    if (
        has_build_config_import
        and constructor_params is not None
        and re.search(
            r"(?:^|,)\s*val\s+versionProvider\s*:\s*\(\)\s*->\s*String\s*=\s*\{\s*BuildConfig\.VERSION_NAME\s*\}\s*(?:,|$)",
            constructor_params,
        )
    ):
        return
    failures.append(
        CheckFailure(
            check_name="Android RPC default version provider",
            path=source_path,
            expected="RpcEnvironment default versionProvider returns BuildConfig.VERSION_NAME",
            actual="designated BuildConfig.VERSION_NAME default provider not found",
        )
    )


def _check_android_meta_get_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    source_path = repo_root / relative_path
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(
            CheckFailure(
                check_name="Android meta.get version source",
                path=source_path,
                expected="MetaGetMethod uses versionProvider() for MetaResponse.version",
                actual=f"unable to read file: {error}",
            )
        )
        return
    stripped_source = _strip_kotlin_comments(source)
    constructor_params = _extract_kotlin_group(
        stripped_source,
        r"internal\s+class\s+MetaGetMethod\s*\(",
    )
    class_body = _extract_kotlin_class_body(
        stripped_source,
        "MetaGetMethod",
        r":\s*DeviceRpcMethod",
    )
    prepare_unit_args = (
        _extract_kotlin_group(
            class_body,
            r"override\s+fun\s+prepare\s*\([^)]*\)\s*:\s*PreparedRpcCall\s*=\s*PreparedRpcMethodSupport\.prepareUnit\s*\(",
        )
        if class_body is not None
        else None
    )
    execute_body = (
        _extract_kotlin_block(prepare_unit_args, r"\bexecute\s*=\s*\{")
        if prepare_unit_args is not None
        else None
    )
    meta_response_args = (
        _extract_top_level_kotlin_call_args(execute_body, "MetaResponse")
        if execute_body is not None
        else None
    )
    if (
        constructor_params is not None
        and re.search(
            r"(?:^|,)\s*private\s+val\s+versionProvider\s*:\s*\(\)\s*->\s*String\s*(?:,|$)",
            constructor_params,
        )
        and meta_response_args is not None
        and re.search(
            r"\bversion\s*=\s*versionProvider\s*\(\s*\)\s*(?:,|$)",
            meta_response_args,
        )
    ):
        return
    failures.append(
        CheckFailure(
            check_name="Android meta.get version source",
            path=source_path,
            expected="MetaGetMethod uses versionProvider() for MetaResponse.version",
            actual="designated versionProvider() response wiring not found",
        )
    )


def _check_androidctl_packaged_apk_package_data(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    pyproject_path = repo_root / relative_path
    try:
        project_data = _load_pyproject(pyproject_path)
        tool_data = _require_mapping(project_data.get("tool"), "tool")
        setuptools_data = _require_mapping(tool_data.get("setuptools"), "setuptools")
        package_data = _require_mapping(
            setuptools_data.get("package-data"),
            "package-data",
        )
        resource_patterns = package_data[_PACKAGED_APK_RESOURCE_PACKAGE]
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name="androidctl packaged APK package data",
                path=pyproject_path,
                expected=(
                    f"[tool.setuptools.package-data] "
                    f'"{_PACKAGED_APK_RESOURCE_PACKAGE}" includes "*.apk"'
                ),
                actual=f"unable to parse package-data: {error}",
            )
        )
        return
    if not isinstance(resource_patterns, list) or "*.apk" not in resource_patterns:
        failures.append(
            CheckFailure(
                check_name="androidctl packaged APK package data",
                path=pyproject_path,
                expected=(
                    f'{_PACKAGED_APK_RESOURCE_PACKAGE} package data includes "*.apk"'
                ),
                actual=repr(resource_patterns),
            )
        )


def _check_packaged_apk_resource_package(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    resource_init_path = repo_root / relative_path
    if resource_init_path.is_file():
        return
    failures.append(
        CheckFailure(
            check_name="packaged APK importlib.resources package",
            path=resource_init_path,
            expected=(
                f"{_PACKAGED_APK_RESOURCE_PACKAGE} is an importable package for "
                "importlib.resources"
            ),
            actual="package __init__.py not found",
        )
    )


def _check_packaged_apk_resource_resolver_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    resolver_path = repo_root / relative_path
    try:
        resource_package = _extract_string_assignment(
            resolver_path,
            "AGENT_APK_RESOURCE_PACKAGE",
        )
        apk_template = _extract_string_assignment(
            resolver_path,
            "AGENT_APK_NAME_TEMPLATE",
        )
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name="packaged APK resource resolver source",
                path=resolver_path,
                expected=(
                    f'AGENT_APK_RESOURCE_PACKAGE="{_PACKAGED_APK_RESOURCE_PACKAGE}" '
                    f'and AGENT_APK_NAME_TEMPLATE="{_PACKAGED_APK_TEMPLATE}"'
                ),
                actual=str(error),
            )
        )
        return
    actual = (
        f"AGENT_APK_RESOURCE_PACKAGE={resource_package!r}; "
        f"AGENT_APK_NAME_TEMPLATE={apk_template!r}"
    )
    if (
        resource_package == _PACKAGED_APK_RESOURCE_PACKAGE
        and apk_template == _PACKAGED_APK_TEMPLATE
    ):
        return
    failures.append(
        CheckFailure(
            check_name="packaged APK resource resolver source",
            path=resolver_path,
            expected=(
                f"AGENT_APK_RESOURCE_PACKAGE={_PACKAGED_APK_RESOURCE_PACKAGE!r}; "
                f"AGENT_APK_NAME_TEMPLATE={_PACKAGED_APK_TEMPLATE!r}"
            ),
            actual=actual,
        )
    )


def _check_pypi_packaged_apk_staging_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    release_tool_path = repo_root / relative_path
    try:
        apk_template = _extract_string_assignment(
            release_tool_path,
            "PACKAGED_APK_TEMPLATE",
        )
        resource_glob = _extract_string_assignment(
            release_tool_path,
            "PACKAGED_APK_RESOURCE_GLOB",
        )
        resource_dir = _extract_path_constructor_assignment(
            release_tool_path,
            "PACKAGED_APK_RESOURCE_DIR",
        )
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name="PyPI packaged APK staging source",
                path=release_tool_path,
                expected=(
                    f'PACKAGED_APK_TEMPLATE="{_PACKAGED_APK_TEMPLATE}", '
                    f'PACKAGED_APK_RESOURCE_DIR=Path("{_PACKAGED_APK_RESOURCE_DIR}")'
                ),
                actual=str(error),
            )
        )
        return
    actual = (
        f"PACKAGED_APK_TEMPLATE={apk_template!r}; "
        f"PACKAGED_APK_RESOURCE_DIR={resource_dir!r}; "
        f"PACKAGED_APK_RESOURCE_GLOB={resource_glob!r}"
    )
    if (
        apk_template == _PACKAGED_APK_TEMPLATE
        and resource_dir == _PACKAGED_APK_RESOURCE_DIR
        and resource_glob == _PACKAGED_APK_GLOB
    ):
        return
    failures.append(
        CheckFailure(
            check_name="PyPI packaged APK staging source",
            path=release_tool_path,
            expected=(
                f"PACKAGED_APK_TEMPLATE={_PACKAGED_APK_TEMPLATE!r}; "
                f"PACKAGED_APK_RESOURCE_DIR={_PACKAGED_APK_RESOURCE_DIR!r}; "
                f"PACKAGED_APK_RESOURCE_GLOB={_PACKAGED_APK_GLOB!r}"
            ),
            actual=actual,
        )
    )


def _check_pypi_packaged_apk_version_evidence_source(
    repo_root: Path,
    relative_path: Path,
    failures: list[CheckFailure],
) -> None:
    release_tool_path = repo_root / relative_path
    try:
        module = _parse_python_module(release_tool_path)
    except Exception as error:
        failures.append(
            CheckFailure(
                check_name="PyPI packaged APK version evidence source",
                path=release_tool_path,
                expected=(
                    "inspect_gradle_apk_evidence validates Gradle metadata "
                    "versionName and versionCode"
                ),
                actual=f"unable to parse module: {error}",
            )
        )
        return
    function = _find_python_function(module, "inspect_gradle_apk_evidence")
    if function is not None and _function_has_gradle_apk_evidence_flow(function):
        return
    failures.append(
        CheckFailure(
            check_name="PyPI packaged APK version evidence source",
            path=release_tool_path,
            expected=(
                "inspect_gradle_apk_evidence reads Gradle output metadata, "
                "compares versionName to paths.version, and compares versionCode "
                "to derive_android_version_code(paths.version), then compares "
                "Gradle output SHA-256 to staged source APK SHA-256"
            ),
            actual="designated packaged APK version evidence checks not found",
        )
    )


def _load_pyproject(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a table")
    return value


def _function_has_gradle_apk_evidence_flow(function: ast.FunctionDef) -> bool:
    if _has_non_final_return(function):
        return False
    statements = list(_runtime_statements(function))
    metadata_index = _find_statement_index(
        statements,
        0,
        lambda statement: _assignment_matches(
            statement,
            "metadata",
            _is_gradle_metadata_json_load,
        ),
    )
    elements_index = _find_statement_index(
        statements,
        _next_statement_start(metadata_index),
        lambda statement: _assignment_matches(
            statement,
            "elements",
            lambda value: _is_name_get_call(value, "metadata", "elements"),
        ),
    )
    element_index = _find_statement_index(
        statements,
        _next_statement_start(elements_index),
        lambda statement: _assignment_matches(
            statement,
            "element",
            _is_first_elements_item,
        ),
    )
    version_name_index = _find_statement_index(
        statements,
        _next_statement_start(element_index),
        lambda statement: _assignment_matches(
            statement,
            "version_name",
            lambda value: _is_name_get_call(value, "element", "versionName"),
        ),
    )
    version_name_check_index = _find_statement_index(
        statements,
        _next_statement_start(version_name_index),
        _is_version_name_fail_closed_if,
    )
    version_code_index = _find_statement_index(
        statements,
        _next_statement_start(version_name_check_index),
        lambda statement: _assignment_matches(
            statement,
            "version_code",
            lambda value: _is_name_get_call(value, "element", "versionCode"),
        ),
    )
    expected_version_code_index = _find_statement_index(
        statements,
        _next_statement_start(version_code_index),
        lambda statement: _assignment_matches(
            statement,
            "expected_version_code",
            _is_expected_version_code_call,
        ),
    )
    version_code_check_index = _find_statement_index(
        statements,
        _next_statement_start(expected_version_code_index),
        _is_version_code_fail_closed_if,
    )
    output_file_index = _find_statement_index(
        statements,
        _next_statement_start(version_code_check_index),
        lambda statement: _assignment_matches(
            statement,
            "output_file",
            lambda value: _is_name_get_call(value, "element", "outputFile"),
        ),
    )
    output_file_path_index = _find_statement_index(
        statements,
        _next_statement_start(output_file_index),
        lambda statement: _assignment_matches(
            statement,
            "output_file_path",
            _is_output_file_path_call,
        ),
    )
    output_path_index = _find_statement_index(
        statements,
        _next_statement_start(output_file_path_index),
        lambda statement: _assignment_matches(
            statement,
            "output_path",
            _is_gradle_output_path,
        ),
    )
    output_sha256_index = _find_statement_index(
        statements,
        _next_statement_start(output_path_index),
        lambda statement: _assignment_matches(
            statement,
            "output_sha256",
            _is_output_sha256_call,
        ),
    )
    checksum_check_index = _find_statement_index(
        statements,
        _next_statement_start(output_sha256_index),
        _is_output_sha256_fail_closed_if,
    )
    return checksum_check_index >= 0


def _has_non_final_return(function: ast.FunctionDef) -> bool:
    final_statement = function.body[-1] if function.body else None
    allowed_return = (
        final_statement if isinstance(final_statement, ast.Return) else None
    )
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and node is not allowed_return:
            return True
    return False


def _runtime_statements(function: ast.FunctionDef) -> list[ast.stmt]:
    statements: list[ast.stmt] = []
    for statement in function.body:
        if isinstance(statement, ast.Try):
            for try_statement in statement.body:
                statements.append(try_statement)
                if isinstance(try_statement, ast.Return):
                    return statements
        statements.append(statement)
        if isinstance(statement, ast.Return):
            break
    return statements


def _find_statement_index(
    statements: list[ast.stmt],
    start_index: int,
    predicate,
) -> int:
    if start_index < 0:
        return -1
    for index in range(start_index, len(statements)):
        if predicate(statements[index]):
            return index
    return -1


def _next_statement_start(index: int) -> int:
    if index < 0:
        return -1
    return index + 1


def _assignment_matches(statement: ast.stmt, target_name: str, predicate) -> bool:
    value_node: ast.AST | None = None
    if isinstance(statement, ast.Assign):
        if any(
            isinstance(target, ast.Name) and target.id == target_name
            for target in statement.targets
        ):
            value_node = statement.value
    elif isinstance(statement, ast.AnnAssign):
        if (
            isinstance(statement.target, ast.Name)
            and statement.target.id == target_name
        ):
            value_node = statement.value
    if value_node is None:
        return False
    return predicate(value_node)


def _is_gradle_metadata_json_load(node: ast.AST) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "loads"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "json"
        and len(node.args) == 1
    ):
        return False
    return _is_paths_attr_method_call(
        node.args[0],
        "gradle_apk_metadata_path",
        "read_text",
    )


def _is_paths_attr_method_call(node: ast.AST, attr_name: str, method_name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method_name
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == attr_name
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "paths"
    )


def _is_name_get_call(node: ast.AST, name: str, key: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == name
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == key
    )


def _is_first_elements_item(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "elements"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == 0
    )


def _is_expected_version_code_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "derive_android_version_code"
        and len(node.args) == 1
        and _is_paths_version(node.args[0])
    )


def _is_output_sha256_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "sha256_file"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "output_path"
    )


def _is_output_file_path_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "output_file"
    )


def _is_gradle_output_path(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and _is_gradle_metadata_parent(node.left)
        and isinstance(node.right, ast.Name)
        and node.right.id == "output_file_path"
    )


def _is_gradle_metadata_parent(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "parent"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "gradle_apk_metadata_path"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "paths"
    )


def _is_version_name_fail_closed_if(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.If)
        and _is_exact_not_eq_compare(
            statement.test,
            lambda node: isinstance(node, ast.Name) and node.id == "version_name",
            _is_paths_version,
        )
        and _body_raises_system_exit(statement.body)
    )


def _is_version_code_fail_closed_if(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.If):
        return False
    test = statement.test
    if not (
        isinstance(test, ast.BoolOp)
        and isinstance(test.op, ast.Or)
        and len(test.values) == 2
    ):
        return False
    has_type_check = any(
        _is_type_is_not_int_compare(value, "version_code") for value in test.values
    )
    has_value_check = any(
        _is_exact_not_eq_compare(
            value,
            lambda node: isinstance(node, ast.Name) and node.id == "version_code",
            lambda node: isinstance(node, ast.Name)
            and node.id == "expected_version_code",
        )
        for value in test.values
    )
    return (
        has_type_check and has_value_check and _body_raises_system_exit(statement.body)
    )


def _is_output_sha256_fail_closed_if(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.If)
        and _is_exact_not_eq_compare(
            statement.test,
            lambda node: isinstance(node, ast.Name) and node.id == "output_sha256",
            lambda node: isinstance(node, ast.Name) and node.id == "expected_sha256",
        )
        and _body_raises_system_exit(statement.body)
    )


def _is_paths_version(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "version"
        and isinstance(node.value, ast.Name)
        and node.value.id == "paths"
    )


def _is_exact_not_eq_compare(
    test: ast.AST,
    left_predicate,
    comparator_predicate,
) -> bool:
    return (
        isinstance(test, ast.Compare)
        and left_predicate(test.left)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.NotEq)
        and len(test.comparators) == 1
        and comparator_predicate(test.comparators[0])
    )


def _is_type_is_not_int_compare(test: ast.AST, name: str) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Call)
        and isinstance(test.left.func, ast.Name)
        and test.left.func.id == "type"
        and len(test.left.args) == 1
        and isinstance(test.left.args[0], ast.Name)
        and test.left.args[0].id == name
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.IsNot)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Name)
        and test.comparators[0].id == "int"
    )


def _body_raises_system_exit(statements: list[ast.stmt]) -> bool:
    for statement in statements:
        if not isinstance(statement, ast.Raise):
            continue
        exc = statement.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        if isinstance(exc, ast.Name) and exc.id == "SystemExit":
            return True
    return False


def _find_dependency_specifier(
    dependencies: object,
    package_name: str,
) -> str | None:
    if not isinstance(dependencies, list):
        raise ValueError("project.dependencies must be a list")
    matches = [
        dependency
        for dependency in dependencies
        if isinstance(dependency, str) and dependency.startswith(package_name)
    ]
    if not matches:
        return None
    return ", ".join(matches)


def _extract_string_assignment(path: Path, name: str) -> str:
    module = _parse_python_module(path)
    for node in module.body:
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                value_node = node.value
        if value_node is None:
            continue
        value = ast.literal_eval(value_node)
        if not isinstance(value, str):
            raise ValueError(f"{name} must be assigned to a string literal")
        return value
    raise ValueError(f"{name} assignment not found")


def _extract_path_constructor_assignment(path: Path, name: str) -> str:
    module = _parse_python_module(path)
    for node in module.body:
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                value_node = node.value
        if value_node is None:
            continue
        if not (
            isinstance(value_node, ast.Call)
            and isinstance(value_node.func, ast.Name)
            and value_node.func.id == "Path"
            and len(value_node.args) == 1
            and not value_node.keywords
            and isinstance(value_node.args[0], ast.Constant)
            and isinstance(value_node.args[0].value, str)
        ):
            raise ValueError(f'{name} must be assigned to Path("...")')
        return value_node.args[0].value
    raise ValueError(f"{name} assignment not found")


def _parse_python_module(path: Path) -> ast.Module:
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=path.as_posix())


def _find_python_function(module: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _find_python_class(module: ast.Module, name: str) -> ast.ClassDef | None:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _find_python_method(
    class_node: ast.ClassDef | None, name: str
) -> ast.FunctionDef | None:
    if class_node is None:
        return None
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _method_returns_ctor_keyword_name(
    method: ast.FunctionDef,
    ctor_name: str,
    keyword_name: str,
    expected_name: str,
) -> bool:
    for node in ast.walk(method):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        ctor_call = _unwrap_ctor_from_expression(node.value, ctor_name)
        if ctor_call is None:
            continue
        if _call_keyword_is_name(ctor_call, keyword_name, expected_name):
            return True
    return False


def _unwrap_ctor_from_expression(
    expression: ast.AST, ctor_name: str
) -> ast.Call | None:
    if isinstance(expression, ast.Call):
        if isinstance(expression.func, ast.Name) and expression.func.id == ctor_name:
            return expression
        if isinstance(expression.func, ast.Attribute):
            nested_call = expression.func.value
            if (
                isinstance(nested_call, ast.Call)
                and isinstance(nested_call.func, ast.Name)
                and nested_call.func.id == ctor_name
            ):
                return nested_call
    return None


def _call_keyword_is_name(
    call: ast.Call, keyword_name: str, expected_name: str
) -> bool:
    for keyword in call.keywords:
        if keyword.arg != keyword_name:
            continue
        return isinstance(keyword.value, ast.Name) and keyword.value.id == expected_name
    return False


def _find_returned_local_class(method: ast.FunctionDef | None) -> ast.ClassDef | None:
    if method is None:
        return None
    local_classes = {
        node.name: node for node in method.body if isinstance(node, ast.ClassDef)
    }
    for node in method.body:
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id in local_classes
        ):
            return local_classes[node.value.id]
    return None


def _class_has_assignment(class_node: ast.ClassDef, name: str, predicate) -> bool:
    for node in class_node.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != name:
            continue
        if predicate(node.value):
            return True
    return False


def _strip_kotlin_comments(source: str) -> str:
    result: list[str] = []
    index = 0
    length = len(source)
    block_comment_depth = 0
    state = "normal"
    while index < length:
        if state == "line_comment":
            if source[index] == "\n":
                result.append("\n")
                state = "normal"
            index += 1
            continue
        if state == "block_comment":
            if source.startswith("/*", index):
                block_comment_depth += 1
                index += 2
                continue
            if source.startswith("*/", index):
                block_comment_depth -= 1
                index += 2
                if block_comment_depth == 0:
                    state = "normal"
                continue
            if source[index] == "\n":
                result.append("\n")
            index += 1
            continue
        if state == "triple_string":
            if source.startswith('"""', index):
                result.append('"""')
                index += 3
                state = "normal"
                continue
            result.append(source[index])
            index += 1
            continue
        if state == "string":
            result.append(source[index])
            if source[index] == "\\" and index + 1 < length:
                result.append(source[index + 1])
                index += 2
                continue
            if source[index] == '"':
                state = "normal"
            index += 1
            continue
        if state == "char":
            result.append(source[index])
            if source[index] == "\\" and index + 1 < length:
                result.append(source[index + 1])
                index += 2
                continue
            if source[index] == "'":
                state = "normal"
            index += 1
            continue
        if source.startswith("//", index):
            state = "line_comment"
            index += 2
            continue
        if source.startswith("/*", index):
            state = "block_comment"
            block_comment_depth = 1
            index += 2
            continue
        if source.startswith('"""', index):
            result.append('"""')
            index += 3
            state = "triple_string"
            continue
        if source[index] == '"':
            result.append('"')
            index += 1
            state = "string"
            continue
        if source[index] == "'":
            result.append("'")
            index += 1
            state = "char"
            continue
        result.append(source[index])
        index += 1
    return "".join(result)


def _extract_kotlin_group(source: str | None, pattern: str) -> str | None:
    if source is None:
        return None
    match = re.search(pattern, source, re.MULTILINE | re.DOTALL)
    if match is None:
        return None
    open_index = match.end() - 1
    close_index = _find_matching_delimiter(source, open_index, "(", ")")
    if close_index is None:
        return None
    return source[open_index + 1 : close_index]


def _extract_kotlin_block(source: str | None, pattern: str) -> str | None:
    if source is None:
        return None
    match = re.search(pattern, source, re.MULTILINE | re.DOTALL)
    if match is None:
        return None
    open_index = match.end() - 1
    close_index = _find_matching_delimiter(source, open_index, "{", "}")
    if close_index is None:
        return None
    return source[open_index + 1 : close_index]


def _extract_kotlin_class_body(
    source: str | None, class_name: str, suffix_pattern: str = ""
) -> str | None:
    if source is None:
        return None
    match = re.search(
        rf"\b(?:internal\s+)?class\s+{re.escape(class_name)}\s*\(",
        source,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return None
    params_open_index = match.end() - 1
    params_close_index = _find_matching_delimiter(source, params_open_index, "(", ")")
    if params_close_index is None:
        return None
    suffix_match = re.match(
        rf"\s*{suffix_pattern}\s*\{{" if suffix_pattern else r"\s*\{",
        source[params_close_index + 1 :],
        re.MULTILINE | re.DOTALL,
    )
    if suffix_match is None:
        return None
    body_open_index = params_close_index + suffix_match.end()
    close_index = _find_matching_delimiter(source, body_open_index, "{", "}")
    if close_index is None:
        return None
    return source[body_open_index + 1 : close_index]


def _find_matching_delimiter(
    source: str, open_index: int, open_char: str, close_char: str
) -> int | None:
    depth = 1
    index = open_index + 1
    length = len(source)
    state = "normal"
    while index < length:
        if state == "triple_string":
            if source.startswith('"""', index):
                index += 3
                state = "normal"
                continue
            index += 1
            continue
        if state == "string":
            if source[index] == "\\" and index + 1 < length:
                index += 2
                continue
            if source[index] == '"':
                state = "normal"
            index += 1
            continue
        if state == "char":
            if source[index] == "\\" and index + 1 < length:
                index += 2
                continue
            if source[index] == "'":
                state = "normal"
            index += 1
            continue
        if source.startswith('"""', index):
            index += 3
            state = "triple_string"
            continue
        if source[index] == '"':
            index += 1
            state = "string"
            continue
        if source[index] == "'":
            index += 1
            state = "char"
            continue
        if source[index] == open_char:
            depth += 1
        elif source[index] == close_char:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _collect_top_level_lines(block: str) -> list[str]:
    lines: list[str] = []
    current_line: list[str] = []
    brace_depth = 0
    index = 0
    length = len(block)
    state = "normal"
    while index < length:
        if state == "triple_string":
            if block.startswith('"""', index):
                current_line.append('"""')
                index += 3
                state = "normal"
                continue
            current_line.append(block[index])
            index += 1
            continue
        if state == "string":
            current_line.append(block[index])
            if block[index] == "\\" and index + 1 < length:
                current_line.append(block[index + 1])
                index += 2
                continue
            if block[index] == '"':
                state = "normal"
            index += 1
            continue
        if state == "char":
            current_line.append(block[index])
            if block[index] == "\\" and index + 1 < length:
                current_line.append(block[index + 1])
                index += 2
                continue
            if block[index] == "'":
                state = "normal"
            index += 1
            continue
        if block.startswith('"""', index):
            current_line.append('"""')
            index += 3
            state = "triple_string"
            continue
        if block[index] == '"':
            current_line.append('"')
            index += 1
            state = "string"
            continue
        if block[index] == "'":
            current_line.append("'")
            index += 1
            state = "char"
            continue
        if block[index] == "{":
            brace_depth += 1
            index += 1
            continue
        if block[index] == "}":
            brace_depth = max(0, brace_depth - 1)
            index += 1
            continue
        if block[index] == "\n":
            if brace_depth == 0:
                line = "".join(current_line).strip()
                if line:
                    lines.append(line)
            current_line = []
            index += 1
            continue
        if brace_depth == 0:
            current_line.append(block[index])
        index += 1
    if brace_depth == 0:
        line = "".join(current_line).strip()
        if line:
            lines.append(line)
    return lines


def _extract_top_level_kotlin_call_args(block: str, call_name: str) -> str | None:
    top_level_text = "\n".join(_collect_top_level_lines(block))
    match = re.search(rf"\b{re.escape(call_name)}\s*\(", top_level_text)
    if match is None:
        return None
    open_index = match.end() - 1
    close_index = _find_matching_delimiter(top_level_text, open_index, "(", ")")
    if close_index is None:
        return None
    return top_level_text[open_index + 1 : close_index]


def _has_version_reexport(module: ast.Module) -> bool:
    for node in module.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 1 or node.module != "_version":
            continue
        for alias in node.names:
            if alias.name == "__version__" and alias.asname in (None, "__version__"):
                return True
    return False


def _extract_dunder_all(module: ast.Module) -> set[str] | None:
    for node in module.body:
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            ):
                value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                value_node = node.value
        if value_node is None:
            continue
        value = ast.literal_eval(value_node)
        if not isinstance(value, (list, tuple, set)) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError("__all__ must be a literal list, tuple, or set of strings")
        return set(value)
    return None


def _module_imports_names(
    module: ast.Module,
    module_name: str,
    expected_names: set[str],
) -> bool:
    for node in module.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 0 or node.module != module_name:
            continue
        imported_names = {
            alias.name for alias in node.names if alias.asname in (None, alias.name)
        }
        if expected_names.issubset(imported_names):
            return True
    return False


def _is_service_name_version_fstring(node: ast.AST) -> bool:
    if not isinstance(node, ast.JoinedStr) or len(node.values) != 3:
        return False
    first, second, third = node.values
    return (
        isinstance(first, ast.FormattedValue)
        and isinstance(first.value, ast.Name)
        and first.value.id == "SERVICE_NAME"
        and isinstance(second, ast.Constant)
        and second.value == "/"
        and isinstance(third, ast.FormattedValue)
        and isinstance(third.value, ast.Name)
        and third.value.id == "__version__"
    )
