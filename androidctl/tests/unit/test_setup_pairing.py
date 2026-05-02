from __future__ import annotations

import pytest

from androidctl.setup import pairing


def test_generate_host_token_uses_32_bytes_base64url_without_padding() -> None:
    token = pairing.generate_host_token(token_bytes=lambda size: bytes(range(size)))

    assert len(token) == pairing.HOST_TOKEN_ENCODED_LENGTH
    assert "=" not in token
    assert pairing.validate_host_token(token) == token


def test_generate_host_token_rejects_wrong_generator_length() -> None:
    with pytest.raises(pairing.SetupPairingError) as exc_info:
        pairing.generate_host_token(token_bytes=lambda size: b"short")

    assert exc_info.value.code == "SETUP_TOKEN_GENERATION_FAILED"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "short",
        pairing.generate_host_token(token_bytes=lambda size: bytes(range(size))) + "=",
        pairing.generate_host_token(token_bytes=lambda size: bytes(range(size)))[:-1]
        + "!",
    ],
)
def test_validate_host_token_rejects_invalid_token(token: str) -> None:
    with pytest.raises(pairing.SetupPairingError) as exc_info:
        pairing.validate_host_token(token)

    assert exc_info.value.code == "SETUP_TOKEN_INVALID"
