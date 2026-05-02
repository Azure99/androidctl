from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from collections.abc import Callable

import pytest

from androidctl import output


class RecordingBinaryStream:
    def __init__(self, *, write_result: int | None = None) -> None:
        self.data = bytearray()
        self.flushed = False
        self.write_result = write_result

    def write(self, data: bytes) -> int | None:
        self.data.extend(data)
        return self.write_result

    def flush(self) -> None:
        self.flushed = True


class RaisingBinaryStream:
    def __init__(self, error_factory: Callable[[], Exception]) -> None:
        self.error_factory = error_factory

    def write(self, data: bytes) -> int:
        raise self.error_factory()

    def flush(self) -> None:
        raise AssertionError("flush should not run after write failure")


class FlushFailingBinaryStream(RecordingBinaryStream):
    def flush(self) -> None:
        raise OSError("flush failed")


def test_write_xml_uses_utf8_bytes_under_legacy_text_stream(monkeypatch) -> None:
    raw = io.BytesIO()
    legacy_text_stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(
        "androidctl.output.click.get_binary_stream",
        lambda name: legacy_text_stream.buffer,
    )

    output.write_stdout_xml('<result message="设置 ✓" />')

    expected = b'<result message="\xe8\xae\xbe\xe7\xbd\xae \xe2\x9c\x93" />\n'
    assert raw.getvalue() == expected


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: OSError("pipe closed"),
        lambda: BrokenPipeError("pipe closed"),
    ],
)
def test_write_failure_is_cli_output_error(monkeypatch, error_factory) -> None:
    monkeypatch.setattr(
        "androidctl.output.click.get_binary_stream",
        lambda name: RaisingBinaryStream(error_factory),
    )

    with pytest.raises(output.CliOutputError):
        output.write_stdout_bytes(b"<result />\n")


def test_short_write_is_cli_output_error(monkeypatch) -> None:
    stream = RecordingBinaryStream(write_result=3)
    monkeypatch.setattr(
        "androidctl.output.click.get_binary_stream",
        lambda name: stream,
    )

    with pytest.raises(output.CliOutputError):
        output.write_stdout_bytes(b"<result />\n")

    assert stream.data == b"<result />\n"


def test_flush_failure_is_cli_output_error(monkeypatch) -> None:
    stream = FlushFailingBinaryStream()
    monkeypatch.setattr(
        "androidctl.output.click.get_binary_stream",
        lambda name: stream,
    )

    with pytest.raises(output.CliOutputError):
        output.write_stdout_bytes(b"<result />\n")


def test_encoding_failure_is_cli_output_error() -> None:
    class BadText(str):
        def encode(
            self,
            _encoding: str = "utf-8",
            _errors: str = "strict",
        ) -> bytes:
            raise UnicodeEncodeError("utf-8", "x", 0, 1, "forced")

    with pytest.raises(output.CliOutputError):
        output.xml_text_to_utf8_bytes(BadText("<result />"))


def test_static_fallback_uses_retained_literal_table() -> None:
    root = output.static_cli_failure_xml_bytes(
        command="close",
        code=output.CLI_OUTPUT_FAILED,
    ).decode("ascii")

    assert '<retainedResult ok="false" command="close" envelope="lifecycle"' in root
    assert output.CLI_OUTPUT_FAILED in root


def test_static_fallback_unknown_command_uses_safe_semantic_category() -> None:
    root = output.static_cli_failure_xml_bytes(
        command="future-command",
        code=output.CLI_RENDER_FAILED,
    ).decode("ascii")

    assert '<result ok="false" command="observe" category="observe"' in root
    assert "unknown" not in root.split("category=", maxsplit=1)[1].split('"', 2)[1]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (output.CLI_RENDER_FAILED, output.CLI_RENDER_FAILED_MESSAGE),
        (output.CLI_OUTPUT_FAILED, output.CLI_OUTPUT_FAILED_MESSAGE),
    ],
)
def test_static_fallback_list_apps_uses_outer_error_result(
    code: str,
    message: str,
) -> None:
    xml = output.static_cli_failure_xml_bytes(
        command="list-apps",
        code=code,
    ).decode("ascii")

    root = ET.fromstring(xml.strip())
    assert root.tag == "errorResult"
    assert root.attrib == {
        "ok": "false",
        "code": code,
        "exitCode": "3",
        "tier": "outer",
        "command": "list-apps",
    }
    message_element = root.find("./message")
    assert message_element is not None
    assert message_element.text == message
    assert "<listAppsResult" not in xml
    assert 'command="observe"' not in xml
