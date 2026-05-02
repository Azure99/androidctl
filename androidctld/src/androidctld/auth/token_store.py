"""Daemon token management."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from androidctld.auth.secret_files import write_secret_json_file_atomically
from androidctld.config import DaemonConfig


@dataclass
class DaemonTokenStore:
    config: DaemonConfig | None = None
    _token: str = ""

    def current_token(self) -> str:
        if self._token:
            return self._token
        token_path = self._token_path()
        if token_path is not None:
            loaded = self._load_token(token_path)
            if loaded:
                self._token = loaded
                return self._token
        self._token = secrets.token_urlsafe(32)
        if token_path is not None:
            self._persist_token(token_path, self._token)
        return self._token

    @classmethod
    def load_existing_token(cls, path: Path) -> str | None:
        return cls._load_token(path)

    def _token_path(self) -> Path | None:
        if self.config is None:
            return None
        return self.config.token_file_path

    @staticmethod
    def _load_token(path: Path) -> str | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        token = payload.get("token")
        if isinstance(token, str):
            token = token.strip()
            if token:
                return token
        return None

    @staticmethod
    def _persist_token(path: Path, token: str) -> None:
        write_secret_json_file_atomically(path, {"token": token})
