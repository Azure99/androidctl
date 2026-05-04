from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "android_rpc_raw_boundary_tokens.json"
)
REPO_ROOT = Path(__file__).resolve().parents[3]
PRIVATE_DOCS_ROOT = REPO_ROOT / "docs"
DOC_TOKEN_LISTS = {
    "hostRawCallableMethods": PRIVATE_DOCS_ROOT
    / "android"
    / "rpc"
    / "transport_and_envelope_contract.md",
    "daemonTypedMethods": PRIVATE_DOCS_ROOT
    / "android"
    / "rpc"
    / "transport_and_envelope_contract.md",
    "daemonWrapperBackedMethods": PRIVATE_DOCS_ROOT
    / "android"
    / "rpc"
    / "transport_and_envelope_contract.md",
    "androidRpcErrorCodes": PRIVATE_DOCS_ROOT
    / "android"
    / "rpc"
    / "transport_and_envelope_contract.md",
    "actionKinds": PRIVATE_DOCS_ROOT / "android" / "rpc" / "action_contract.md",
    "targetKinds": PRIVATE_DOCS_ROOT / "android" / "rpc" / "action_contract.md",
    "nodeActions": PRIVATE_DOCS_ROOT / "android" / "rpc" / "action_contract.md",
    "globalActions": PRIVATE_DOCS_ROOT / "android" / "rpc" / "action_contract.md",
    "scrollDirections": PRIVATE_DOCS_ROOT / "android" / "rpc" / "action_contract.md",
    "gestureDirections": PRIVATE_DOCS_ROOT / "android" / "rpc" / "action_contract.md",
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


def _private_docs_checkout_present() -> bool:
    return PRIVATE_DOCS_ROOT.exists()


def _missing_private_doc_paths() -> list[Path]:
    return sorted(path for path in set(DOC_TOKEN_LISTS.values()) if not path.is_file())


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
    if not _private_docs_checkout_present():
        pytest.skip("private docs checkout is not present")
    assert PRIVATE_DOCS_ROOT.is_dir(), f"{PRIVATE_DOCS_ROOT} must be a directory"
    missing_paths = _missing_private_doc_paths()
    assert (
        not missing_paths
    ), "private docs checkout is missing required token docs: " + ", ".join(
        str(path) for path in missing_paths
    )

    manifest = _load_fixture()

    assert set(DOC_TOKEN_LISTS) == TOKEN_LIST_KEYS
    for key, path in DOC_TOKEN_LISTS.items():
        assert _documented_tokens(path, key) == manifest[key]
