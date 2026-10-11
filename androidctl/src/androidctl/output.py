from __future__ import annotations

import json
import os
import sys
import unicodedata
from typing import TextIO

import typer

from .protocol import Result


def display_name(value: str, *, quoted: bool = True) -> str:
    encoded = "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char) in ("Cc", "Cf") else char
        for char in json.dumps(value, ensure_ascii=False)
    )
    return encoded if quoted else encoded[1:-1]


def terminal_text(text: str) -> str:
    return "".join(
        (
            f"\\x{ord(char):02x}"
            if (ord(char) < 32 and char not in "\n\t") or 127 <= ord(char) <= 159
            else char
        )
        for char in text
    )


class Output:
    """One command owns its delivery errors, independently of its remote result."""

    def __init__(self) -> None:
        self.errors: dict[str, str] = {}

    def heading(
        self, result: Result, *, before_reason: str = "", after_reason: str = ""
    ) -> None:
        reason = f"; {result.reason}" if result.reason else ""
        self.echo(
            f"{result.command}: {result.result}{before_reason}{reason}{after_reason}"
            + self.context(result)
        )

    def context(self, result: Result) -> str:
        facts = result.data.get("context")
        if not facts:
            return ""
        parts = []
        if facts["deviceSource"] != "args":
            if "deviceId" in facts:
                parts.append(f"device={facts['deviceId']}({facts['deviceSource']})")
            else:
                parts.append("deviceSource=" + facts["deviceSource"])
        if "session" in facts and facts["sessionSource"] != "args":
            parts.append(
                "session="
                + display_name(facts["session"])
                + f"({facts['sessionSource']})"
            )
        return "; " + " ".join(parts) if parts else ""

    def error(self, result: Result) -> None:
        if result.error is not None:
            self.echo(
                f"code={result.error.code}"
                + (f"; stage={result.error.stage}" if result.error.stage else "")
            )

    def unknown(
        self, result: Result, message: str = "Do not repeat this action automatically."
    ) -> None:
        if result.result == "UNKNOWN":
            self.echo(message)

    def echo(self, value: str = "", *, err: bool = False, nl: bool = True) -> None:
        channel = "stderr" if err else "stdout"
        if channel in self.errors:
            return
        stream = sys.stderr if err else sys.stdout
        try:
            typer.echo(value, err=err, nl=nl)
        except (OSError, UnicodeError) as error:
            self.failed(channel, stream, error)
        except ValueError as error:
            if not stream.closed:
                raise
            self.failed(channel, stream, error)

    def json(self, value: object) -> None:
        self.echo(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    def failed(self, channel: str, stream: TextIO, error: Exception) -> None:
        self.errors[channel] = str(error) or type(error).__name__
        # A failed buffered pipe must not replace the command's exit code at shutdown.
        try:
            descriptor = stream.fileno()
            with open(os.devnull, "wb") as sink:
                os.dup2(sink.fileno(), descriptor)
        except (OSError, ValueError):
            pass

    def attach(self, result: Result) -> None:
        if self.errors:
            result.data["deliveryError"] = "; ".join(
                f"{channel}: {message}" for channel, message in self.errors.items()
            )

    def finish(self, result: Result) -> None:
        self.attach(result)
        if result.data.get("deliveryError"):
            self.echo(
                f"delivery: {result.result}; {result.data['deliveryError']}", err=True
            )
            # Reporting a stdout failure can also fail on stderr.
            self.attach(result)
