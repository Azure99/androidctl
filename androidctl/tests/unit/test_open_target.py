from __future__ import annotations

import pytest
from androidctl_contracts.daemon_api import OpenAppTargetPayload, OpenUrlTargetPayload

from androidctl.parsing.open_target import parse_open_target


@pytest.mark.parametrize(
    ("raw_target", "expected"),
    [
        (
            "app:com.android.settings",
            OpenAppTargetPayload(kind="app", value="com.android.settings"),
        ),
        (
            "url:https://example.com",
            OpenUrlTargetPayload(kind="url", value="https://example.com"),
        ),
        (
            "url:http://example.com",
            OpenUrlTargetPayload(kind="url", value="http://example.com"),
        ),
        (
            "url:smsto:10086?body=hi",
            OpenUrlTargetPayload(kind="url", value="smsto:10086?body=hi"),
        ),
        (
            "url:mailto:test@example.com",
            OpenUrlTargetPayload(kind="url", value="mailto:test@example.com"),
        ),
        (
            "url:mailto:",
            OpenUrlTargetPayload(kind="url", value="mailto:"),
        ),
        (
            "url:foo:",
            OpenUrlTargetPayload(kind="url", value="foo:"),
        ),
        (
            "url:example.com",
            OpenUrlTargetPayload(kind="url", value="example.com"),
        ),
        (
            "url:http://",
            OpenUrlTargetPayload(kind="url", value="http://"),
        ),
        (
            "url:Z:\\temp",
            OpenUrlTargetPayload(kind="url", value="Z:\\temp"),
        ),
        (
            "url:foo:\\bar",
            OpenUrlTargetPayload(kind="url", value="foo:\\bar"),
        ),
        (
            "http://example.com",
            OpenUrlTargetPayload(kind="url", value="http://example.com"),
        ),
        (
            "https://example.com",
            OpenUrlTargetPayload(kind="url", value="https://example.com"),
        ),
    ],
)
def test_parse_open_target_accepts_documented_shapes(
    raw_target: str,
    expected: OpenAppTargetPayload | OpenUrlTargetPayload,
) -> None:
    assert parse_open_target(raw_target) == expected


@pytest.mark.parametrize(
    "raw_target",
    [
        "smsto:10086",
        "url:",
        "http://",
        "https://",
        "http:///path",
        "https:///path",
        "http://:443",
        "https://:443",
        "http://exa mple.com",
        "https://exa mple.com",
        "bad",
    ],
)
def test_parse_open_target_rejects_non_contract_shapes(raw_target: str) -> None:
    with pytest.raises(ValueError):
        parse_open_target(raw_target)
