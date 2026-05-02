from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Callable

SETUP_DEVICE_TOKEN_EXTRA = "androidctl.setup.deviceToken"
HOST_TOKEN_BYTES = 32
HOST_TOKEN_ENCODED_LENGTH = 43

_HOST_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class SetupPairingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.layer = "token"


def generate_host_token(
    *,
    token_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> str:
    raw_token = token_bytes(HOST_TOKEN_BYTES)
    if len(raw_token) != HOST_TOKEN_BYTES:
        raise SetupPairingError(
            "SETUP_TOKEN_GENERATION_FAILED",
            "host token generator returned the wrong number of bytes",
        )
    return base64.urlsafe_b64encode(raw_token).decode("ascii").rstrip("=")


def validate_host_token(token: str) -> str:
    if not token:
        raise SetupPairingError("SETUP_TOKEN_INVALID", "host token is required")
    if not _HOST_TOKEN_PATTERN.fullmatch(token):
        raise SetupPairingError(
            "SETUP_TOKEN_INVALID",
            "host token must be canonical base64url without padding",
        )

    decoded = _decode_token(token)
    if len(decoded) != HOST_TOKEN_BYTES:
        raise SetupPairingError(
            "SETUP_TOKEN_INVALID",
            "host token must decode to 32 bytes",
        )
    if _encode_token(decoded) != token:
        raise SetupPairingError(
            "SETUP_TOKEN_INVALID",
            "host token must be canonical base64url without padding",
        )
    return token


def _decode_token(token: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except ValueError as exc:
        raise SetupPairingError(
            "SETUP_TOKEN_INVALID",
            "host token must be valid base64url",
        ) from exc


def _encode_token(raw_token: bytes) -> str:
    return base64.urlsafe_b64encode(raw_token).decode("ascii").rstrip("=")
