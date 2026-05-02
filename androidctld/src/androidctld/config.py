"""Shared configuration helpers for androidctld."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

from androidctl_contracts.paths import daemon_state_root

DEFAULT_HOST = "127.0.0.1"
ACTIVE_FILE_NAME = "active.json"
ACTIVE_LOCK_FILE_NAME = "active.lock"
OWNER_LOCK_FILE_NAME = "owner.lock"
TOKEN_FILE_NAME = "token.json"
TOKEN_HEADER_NAME = "X-Androidctld-Token"


def normalize_loopback_host(host: str) -> str:
    normalized = host.strip().lower()
    if not normalized:
        raise ValueError("daemon host must not be empty")
    if normalized == "localhost":
        return DEFAULT_HOST
    try:
        parsed = ipaddress.ip_address(normalized)
    except ValueError as error:
        raise ValueError("daemon host must be a loopback address") from error
    if not parsed.is_loopback:
        raise ValueError("daemon host must be a loopback address")
    if parsed.version == 6:
        return "::1"
    return str(parsed)


@dataclass(frozen=True)
class DaemonConfig:
    workspace_root: Path
    owner_id: str
    host: str = DEFAULT_HOST
    port: int = 0
    state_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        raw_workspace_root = str(self.workspace_root).strip()
        if not raw_workspace_root:
            raise ValueError("daemon workspace_root must not be empty")
        resolved_root = Path(raw_workspace_root).expanduser().resolve()
        normalized_owner = self.owner_id.strip()
        if not normalized_owner:
            raise ValueError("daemon owner_id must not be empty")
        object.__setattr__(self, "owner_id", normalized_owner)
        object.__setattr__(self, "workspace_root", resolved_root)
        object.__setattr__(self, "host", normalize_loopback_host(self.host))
        object.__setattr__(
            self,
            "state_dir",
            daemon_state_root(resolved_root),
        )

    @property
    def active_file_path(self) -> Path:
        return self.state_dir / ACTIVE_FILE_NAME

    @property
    def active_lock_path(self) -> Path:
        return self.state_dir / ACTIVE_LOCK_FILE_NAME

    @property
    def owner_lock_path(self) -> Path:
        return self.state_dir / OWNER_LOCK_FILE_NAME

    @property
    def token_file_path(self) -> Path:
        return self.state_dir / TOKEN_FILE_NAME
