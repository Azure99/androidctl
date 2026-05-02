from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "android_rpc_raw_boundary_tokens.json"
)
REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_TOKEN_LISTS = {
    "hostRawCallableMethods": REPO_ROOT
    / "docs"
    / "android"
    / "rpc"
    / "transport_and_envelope_contract.md",
    "daemonTypedMethods": REPO_ROOT
    / "docs"
    / "android"
    / "rpc"
    / "transport_and_envelope_contract.md",
    "daemonWrapperBackedMethods": REPO_ROOT
    / "docs"
    / "android"
    / "rpc"
    / "transport_and_envelope_contract.md",
    "androidRpcErrorCodes": REPO_ROOT
    / "docs"
    / "android"
    / "rpc"
    / "transport_and_envelope_contract.md",
    "actionKinds": REPO_ROOT / "docs" / "android" / "rpc" / "action_contract.md",
    "targetKinds": REPO_ROOT / "docs" / "android" / "rpc" / "action_contract.md",
    "nodeActions": REPO_ROOT / "docs" / "android" / "rpc" / "action_contract.md",
    "globalActions": REPO_ROOT / "docs" / "android" / "rpc" / "action_contract.md",
    "scrollDirections": REPO_ROOT / "docs" / "android" / "rpc" / "action_contract.md",
    "gestureDirections": REPO_ROOT / "docs" / "android" / "rpc" / "action_contract.md",
}
TOKEN_LIST_KEYS = {
    "hostRawCallableMethods",
    "daemonTypedMethods",
    "daemonWrapperBackedMethods",
    "actionKinds",
    "targetKinds",
    "nodeActions",
    "globalActions",
    "scrollDirections",
    "gestureDirections",
    "androidRpcErrorCodes",
}
DOC_TOKEN_BLOCK = re.compile(
    r"<!-- android-rpc-raw-boundary-tokens:(?P<key>[A-Za-z]+):start -->"
    r"(?P<body>.*?)"
    r"<!-- android-rpc-raw-boundary-tokens:(?P=key):end -->",
    re.DOTALL,
)
INLINE_CODE = re.compile(r"`([^`]+)`")


def _load_fixture() -> dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _documented_tokens(path: Path, key: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    matches = [
        match.group("body")
        for match in DOC_TOKEN_BLOCK.finditer(text)
        if match.group("key") == key
    ]
    assert len(matches) == 1, f"{path} must contain exactly one token block for {key}"

    tokens = INLINE_CODE.findall(matches[0])
    assert tokens, f"{path} token block for {key} is empty"
    duplicate_message = f"{path} token block for {key} has duplicates"
    assert len(tokens) == len(set(tokens)), duplicate_message
    return tokens


def test_android_rpc_raw_boundary_tokens_fixture_has_expected_shape() -> None:
    manifest = _load_fixture()

    assert set(manifest) == TOKEN_LIST_KEYS
    for key in TOKEN_LIST_KEYS:
        tokens = manifest[key]
        assert isinstance(tokens, list), key
        assert tokens, key
        assert all(isinstance(token, str) and token for token in tokens), key
        assert len(tokens) == len(set(tokens)), key


def test_android_rpc_raw_boundary_tokens_use_host_raw_callable_key() -> None:
    manifest = _load_fixture()

    assert "hostRawCallableMethods" in manifest


def test_android_rpc_raw_boundary_method_partitions_are_consistent() -> None:
    manifest = _load_fixture()

    host_raw_callable = set(manifest["hostRawCallableMethods"])
    daemon_typed = set(manifest["daemonTypedMethods"])
    daemon_wrapper_backed = set(manifest["daemonWrapperBackedMethods"])

    assert daemon_typed < host_raw_callable
    assert daemon_wrapper_backed < host_raw_callable
    assert daemon_typed.isdisjoint(daemon_wrapper_backed)
    assert daemon_typed | daemon_wrapper_backed == host_raw_callable


def test_android_rpc_raw_boundary_tokens_are_documented() -> None:
    manifest = _load_fixture()

    assert set(DOC_TOKEN_LISTS) == TOKEN_LIST_KEYS
    for key, path in DOC_TOKEN_LISTS.items():
        assert _documented_tokens(path, key) == manifest[key]
