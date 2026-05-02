from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from androidctl.exit_codes import ExitCode

ErrorTier = Literal["usage", "preDispatch", "outer"]


@dataclass(frozen=True)
class PublicError:
    code: str
    message: str
    hint: str | None
    exit_code: ExitCode
