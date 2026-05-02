from __future__ import annotations

import pytest

import androidctl.daemon.owner as owner


def test_owner_id_prefers_explicit_override() -> None:
    owner_id = owner.derive_owner_id(env={"ANDROIDCTL_OWNER_ID": "agent:codex:1"})

    assert owner_id == "agent:codex:1"


def test_owner_id_derives_shell_identity_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANDROIDCTL_OWNER_ID", raising=False)
    monkeypatch.setattr(owner, "_find_interactive_shell_ancestor_pid", lambda env: 3131)
    monkeypatch.setattr(owner, "_read_process_lifetime_discriminator", lambda pid: "42")

    owner_id = owner.derive_owner_id(env={})

    assert owner_id == "shell:3131:42"


def test_owner_id_fails_closed_when_no_safe_identity_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner, "_find_interactive_shell_ancestor_pid", lambda env: None)
    with pytest.raises(ValueError, match="ANDROIDCTL_OWNER_ID"):
        owner.derive_owner_id(env={})
