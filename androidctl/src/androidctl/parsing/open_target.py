from __future__ import annotations

from urllib.parse import SplitResult, urlsplit

from androidctl_contracts.daemon_api import (
    OpenAppTargetPayload,
    OpenTargetPayload,
    OpenUrlTargetPayload,
)


def parse_open_target(raw_target: str) -> OpenTargetPayload:
    target = raw_target.strip()
    if not target:
        raise ValueError("open target cannot be empty")
    if target.startswith("app:"):
        package_name = target[len("app:") :].strip()
        if not package_name:
            raise ValueError("app target must include a package name")
        return OpenAppTargetPayload(kind="app", value=package_name)
    if target.startswith("url:"):
        url = target[len("url:") :].strip()
        if not url:
            raise ValueError("url target must not be empty")
        return OpenUrlTargetPayload(kind="url", value=url)
    if _is_http_url(target):
        return OpenUrlTargetPayload(kind="url", value=target)
    raise ValueError(
        "target must be app:<package>, url:<target>, or absolute http(s)://..."
    )


def _is_http_url(value: str) -> bool:
    if any(char.isspace() for char in value):
        return False
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return False
    return _is_valid_http_url(parsed)


def _is_valid_http_url(parsed: SplitResult) -> bool:
    if not parsed.netloc:
        return False
    if not parsed.hostname or any(char.isspace() for char in parsed.hostname):
        return False
    try:
        _ = parsed.port
    except ValueError:
        return False
    return True
