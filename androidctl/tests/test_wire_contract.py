import asyncio
import math
import re
from pathlib import Path

import pytest
import typer

from androidctl import job_commands, protocol
from androidctl.cli_options import CliOptions
from androidctl.coordinator import QUERY_METHODS
from androidctl.output import Output
from androidctl.protocol import CHUNK_BYTES, Result
from ui_test_support import load_wire_contract

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "android/app/src/main/c"
WIRE_CONTRACT = load_wire_contract()


def index_samples(section, samples, key="name"):
    names = set()
    for sample in samples:
        name = sample[key]
        assert name not in names, f"{section} contains duplicate {key}: {name}"
        names.add(name)
    return {sample[key]: sample for sample in samples}


FILE_FAILURES = index_samples("fileFailures", WIRE_CONTRACT["fileFailures"])

FILE_FAILURE_EXPECTED = {
    "publication-unknown": (
        "UNKNOWN",
        3,
        [
            "push: UNKNOWN; job=f3; Publication interrupted",
            "code=workerFailure",
            "Do not repeat automatically; inspect the current job or device.",
        ],
    ),
    "publication-rejected": (
        "failed",
        1,
        ["push: failed; job=f4; Target exists", "code=publishFailed"],
    ),
}


def assert_json(expected, actual):
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert expected.keys() == actual.keys()
        for key in expected:
            assert_json(expected[key], actual[key])
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(expected) == len(actual)
        for left, right in zip(expected, actual, strict=False):
            assert_json(left, right)
    else:
        assert isinstance(expected, bool) == isinstance(actual, bool)
        assert expected == actual


@pytest.mark.parametrize(("boolean", "number"), [(True, 1), (False, 0)])
def test_json_comparison_distinguishes_boolean_from_number(boolean, number):
    with pytest.raises(AssertionError):
        assert_json({"nested": [boolean]}, {"nested": [number]})
    assert_json({"nested": [number]}, {"nested": [float(number)]})


def test_json_comparison_preserves_array_order():
    with pytest.raises(AssertionError):
        assert_json({"nodes": [1, 2]}, {"nodes": [2, 1]})


def test_wire_limits_agree_across_boundaries():
    limits = {
        "MESSAGE_BYTES": protocol.MAX_MESSAGE,
        "JSON_UTF8_BYTES": protocol.JSON_UTF8_BYTES,
        "CHUNK_BYTES": protocol.CHUNK_BYTES,
        "OUTPUT_BYTES": protocol.OUTPUT_BYTES,
        "MAX_RUN_MS": protocol.MAX_RUN_MS,
        "PAIRING_NAME_UNITS": protocol.PAIRING_NAME_UNITS,
    }
    assert WIRE_CONTRACT["limits"].keys() == limits.keys() | {"HELPER_PAYLOAD_BYTES"}
    for device, host in limits.items():
        assert WIRE_CONTRACT["limits"][device] == host, device
    helper = (NATIVE / "execute.c").read_text(encoding="utf-8")
    assert constant(helper, "OUTPUT_CAP") == protocol.OUTPUT_BYTES
    assert (
        constant(helper, "FRAME_CAP") - constant(helper, "FRAME_HEADER_BYTES")
        == WIRE_CONTRACT["limits"]["HELPER_PAYLOAD_BYTES"]
    )


def test_query_pools_share_the_expected_control_methods():
    assert WIRE_CONTRACT["queryMethods"] == sorted(QUERY_METHODS | {"pair.register"})


def constant(source, name):
    match = re.search(rf"\b{name}\s*(?:=\s*)?\(?([0-9][0-9uL *]*)", source)
    assert match, f"Production constant {name} was not found"
    return math.prod(int(part.strip().rstrip("uL")) for part in match[1].split("*"))


def test_device_shell_cleanup_overrides_the_public_exit_without_losing_execution():
    result = Result.model_validate(WIRE_CONTRACT["shellCleanupFailure"])
    assert result.result == "failed"
    assert result.exit == 3
    assert result.exit_code() == 3
    assert result.data["execution"]["exitCode"] == 7
    assert result.data["cleanup"] == "unknown"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_host_consumes_device_output_chunks(stream, monkeypatch):
    fixture = WIRE_CONTRACT["output"]
    chunks = fixture["chunks"][stream]
    responses = {chunk["data"]["offset"]: chunk for chunk in chunks}
    requested = []

    async def read(command, args, timeout):
        assert command == "output.read"
        assert args["job"] == fixture["job"]
        assert args["stream"] == stream
        requested.append(args["offset"])
        return Result.model_validate(responses[args["offset"]])

    client = job_commands.JobClient(CliOptions(None, "default", False))
    monkeypatch.setattr(client, "call", read)
    meta = next(
        item for item in fixture["metadata"]["streams"] if item["stream"] == stream
    )
    body = asyncio.run(client.output(fixture["job"], stream, meta))
    expected = (
        bytes(index % 251 for index in range(CHUNK_BYTES + 7))
        if stream == "stdout"
        else b"diagnostic\n"
    )
    assert body == expected
    assert requested == list(responses)
    assert len(chunks) == (2 if stream == "stdout" else 1)
    assert meta["read"] == len(expected)
    assert meta["truncated"] is False


@pytest.mark.parametrize(
    ("samples", "names"),
    [
        (FILE_FAILURES, FILE_FAILURE_EXPECTED.keys()),
    ],
    ids=["fileFailures"],
)
def test_named_sample_coverage_is_exact(samples, names):
    assert samples.keys() == names


@pytest.mark.parametrize("key", ["name", "input"])
def test_sample_index_rejects_duplicate_names_before_building_the_mapping(key):
    with pytest.raises(
        AssertionError, match=f"samples contains duplicate {key}: first"
    ):
        index_samples("samples", [{key: "first"}, {key: "first"}], key)


@pytest.mark.parametrize("name", FILE_FAILURE_EXPECTED)
def test_host_renders_device_file_failure_facts(name, capsys):
    case = FILE_FAILURES[name]
    expected_result, expected_exit, expected_lines = FILE_FAILURE_EXPECTED[name]
    result = Result.model_validate(case["response"])
    assert_json(
        case["response"],
        result.model_dump(mode="json", by_alias=True, exclude_unset=True),
    )
    original = result.wire()
    assert result.result == expected_result
    assert result.exit_code() == expected_exit
    with pytest.raises(typer.Exit) as stopped:
        job_commands.show(result, {}, False, Output(), device=None, session=None)
    assert stopped.value.exit_code == expected_exit
    assert capsys.readouterr().out.splitlines() == expected_lines
    assert_json(original, result.wire())
