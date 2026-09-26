from __future__ import annotations

import base64
from importlib.metadata import version
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

VERSION = version("androidctl")
MAX_MESSAGE = 256 * 1024
JSON_UTF8_BYTES = 96 * 1024
CHUNK_BYTES = 32 * 1024
OUTPUT_BYTES = 128 * 1024
MAX_RUN_MS = 600000
MAX_CONNECTIONS = 16
PAIRING_NAME_UNITS = 100
CONNECT_SECONDS = 5.0
RPC_SECONDS = 10.0
UNCERTAIN_EXIT = 3


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class Problem(WireModel):
    code: str
    message: str
    stage: str | None = None
    diagnostic_truncated: bool | None = None


class Result(WireModel):
    command: str
    result: Literal["done", "failed", "not sent", "UNKNOWN", "still running"]
    reason: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: Problem | None = None
    exit: int | None = None

    def exit_code(self) -> int:
        if self.exit is not None:
            return self.exit
        return {
            "done": 0,
            "failed": 1,
            "not sent": 2,
            "UNKNOWN": UNCERTAIN_EXIT,
            "still running": 4,
        }[self.result]


class Fault(Exception):
    def __init__(self, code: str, message: str, stage: str | None = None):
        super().__init__(message)
        self.problem = Problem(code=code, message=message, stage=stage)

    @classmethod
    def from_problem(cls, problem: Problem) -> Fault:
        failure = cls(problem.code, problem.message, problem.stage)
        failure.problem = problem
        return failure


class TransportFailure(Fault):
    """The request may have arrived; a missing reply never permits resending it."""


def decode_chunk(encoded: str, offset: int, size: int, *, code: str) -> bytes:
    try:
        chunk = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise Fault(code, "Invalid base64 chunk") from error
    if (
        len(chunk) > CHUNK_BYTES
        or offset + len(chunk) > size
        or not chunk
        and offset < size
    ):
        raise Fault(code, "Invalid chunk length")
    return chunk


def completed(
    command: str, data: dict[str, Any] | None = None, reason: str | None = None
) -> Result:
    return Result(command=command, result="done", data=data or {}, reason=reason)


def rejected(command: str, fault: Fault) -> Result:
    return Result(
        command=command, result="not sent", reason=str(fault), error=fault.problem
    )


def uncertain(command: str, fault: Fault, data: dict[str, Any] | None = None) -> Result:
    return Result(
        command=command,
        result="UNKNOWN",
        reason=str(fault),
        data=data or {},
        error=fault.problem,
    )
