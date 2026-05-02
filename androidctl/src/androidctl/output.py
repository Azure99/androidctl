from __future__ import annotations

from typing import Literal
from xml.sax.saxutils import escape

import click

CLI_RENDER_FAILED = "CLI_RENDER_FAILED"
CLI_OUTPUT_FAILED = "CLI_OUTPUT_FAILED"

CLI_RENDER_FAILED_MESSAGE = "androidctl failed while rendering command output"
CLI_OUTPUT_FAILED_MESSAGE = "androidctl failed while writing command output"

_RETAINED_FALLBACK_ENVELOPES = {
    "connect": "bootstrap",
    "screenshot": "artifact",
    "close": "lifecycle",
}

_SEMANTIC_FALLBACK_CATEGORIES = {
    "observe": "observe",
    "open": "open",
    "tap": "transition",
    "long-tap": "transition",
    "focus": "transition",
    "type": "transition",
    "submit": "transition",
    "scroll": "transition",
    "back": "transition",
    "home": "transition",
    "recents": "transition",
    "notifications": "transition",
    "wait": "wait",
}

_LIST_APPS_FALLBACK_COMMAND = "list-apps"


class CliOutputError(Exception):
    def __init__(self, stream_name: str) -> None:
        super().__init__(stream_name)
        self.stream_name = stream_name


def xml_text_to_utf8_bytes(xml_text: str) -> bytes:
    try:
        return xml_text.encode("utf-8") + b"\n"
    except Exception as error:
        raise CliOutputError("encoding") from error


def write_stdout_xml(xml_text: str) -> None:
    write_stdout_bytes(xml_text_to_utf8_bytes(xml_text))


def write_stderr_xml(xml_text: str) -> None:
    write_stderr_bytes(xml_text_to_utf8_bytes(xml_text))


def write_stdout_bytes(data: bytes) -> None:
    _write_binary_stream("stdout", data)


def write_stderr_bytes(data: bytes) -> None:
    _write_binary_stream("stderr", data)


def static_cli_failure_xml_bytes(*, command: str | None, code: str) -> bytes:
    message = (
        CLI_RENDER_FAILED_MESSAGE
        if code == CLI_RENDER_FAILED
        else CLI_OUTPUT_FAILED_MESSAGE
    )
    public_command = _fallback_public_command(command)
    xml_text = _static_cli_failure_xml(
        command=public_command,
        code=code,
        message=message,
    )
    return xml_text.encode("ascii") + b"\n"


def _write_binary_stream(
    stream_name: Literal["stdout", "stderr"],
    data: bytes,
) -> None:
    try:
        stream = click.get_binary_stream(stream_name)
        written = stream.write(data)
        if isinstance(written, int) and written < len(data):
            raise OSError("short write")
        stream.flush()
    except Exception as error:
        raise CliOutputError(stream_name) from error


def _static_cli_failure_xml(*, command: str, code: str, message: str) -> str:
    if command == _LIST_APPS_FALLBACK_COMMAND:
        return (
            f'<errorResult ok="false" code="{_xml_attr(code)}" '
            'exitCode="3" tier="outer" '
            f'command="{_xml_attr(command)}">'
            f"<message>{_xml_text(message)}</message>"
            "</errorResult>"
        )

    retained_envelope = _RETAINED_FALLBACK_ENVELOPES.get(command)
    if retained_envelope is not None:
        return (
            f'<retainedResult ok="false" command="{_xml_attr(command)}" '
            f'envelope="{_xml_attr(retained_envelope)}" code="{_xml_attr(code)}">'
            f"<message>{_xml_text(message)}</message>"
            "<artifacts />"
            "</retainedResult>"
        )

    category = _SEMANTIC_FALLBACK_CATEGORIES.get(command, "transition")
    return (
        f'<result ok="false" command="{_xml_attr(command)}" '
        f'category="{_xml_attr(category)}" payloadMode="none" '
        f'code="{_xml_attr(code)}">'
        f"<message>{_xml_text(message)}</message>"
        '<truth executionOutcome="unknown" continuityStatus="none" '
        'observationQuality="none" />'
        "<uncertainty />"
        "<warnings />"
        "<artifacts />"
        "</result>"
    )


def _fallback_public_command(command: str | None) -> str:
    if command == _LIST_APPS_FALLBACK_COMMAND:
        return command
    if command in _RETAINED_FALLBACK_ENVELOPES:
        return command
    if command in _SEMANTIC_FALLBACK_CATEGORIES:
        return command
    return "observe"


def _xml_attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _xml_text(value: str) -> str:
    return escape(value)
