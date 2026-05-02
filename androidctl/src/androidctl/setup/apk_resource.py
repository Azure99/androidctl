from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

AGENT_APK_RESOURCE_PACKAGE = "androidctl.resources"
AGENT_APK_NAME_TEMPLATE = "androidctl-agent-{version}-release.apk"
_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+\Z")


def packaged_agent_apk_name(version: str) -> str:
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError("version must be MAJOR.MINOR.PATCH")
    return AGENT_APK_NAME_TEMPLATE.format(version=version)


@contextmanager
def packaged_agent_apk_path(version: str) -> Iterator[Path]:
    apk_name = packaged_agent_apk_name(version)
    resource = resources.files(AGENT_APK_RESOURCE_PACKAGE).joinpath(apk_name)
    if not resource.is_file():
        raise FileNotFoundError(
            f"packaged Android Device Agent APK not found: {apk_name}"
        )
    with resources.as_file(resource) as apk_path:
        yield apk_path
