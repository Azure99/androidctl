import asyncio
import json
import math
import re
import time
from pathlib import Path

import pytest
import typer

from androidctl import job_commands, protocol, ui_client, ui_view
from androidctl.cli_options import CliOptions
from androidctl.coordinator import QUERY_METHODS
from androidctl.output import Output
from androidctl.protocol import CHUNK_BYTES, Result
from androidctl.ui_client import UiClient
from androidctl.ui_commands import LOCATE_ACTIONS, ROLES
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


CAPTURES = index_samples("captures", WIRE_CONTRACT["captures"])
COMPOUND_RESULTS = index_samples("compoundResults", WIRE_CONTRACT["compoundResults"])
ACTION_RESULTS = index_samples("actionResults", WIRE_CONTRACT["actionResults"])
ENTITY_FORMS = index_samples("entityForms", WIRE_CONTRACT["entityForms"], "input")
WAIT_RESULTS = index_samples("waitResults", WIRE_CONTRACT["waitResults"])
FILE_FAILURES = index_samples("fileFailures", WIRE_CONTRACT["fileFailures"])

COMPOUND_EXPECTED = {
    "ordinary-write": (
        "done",
        0,
        ['fill: done; focus=done; fill=done, verification="matches"'],
    ),
    "no-op-fill-submit-not-sent": (
        "not sent",
        2,
        [
            "fill: not sent; code=staleRef; dispatchStarted=false; "
            "focus=done, sent=false; "
            'fill=done, sent=false, verification="matches"; submit=not sent, '
            'reason="Original field is stale", sent=false'
        ],
    ),
    "ime-done": (
        "done",
        0,
        ['fill: done; focus=done; fill=done, verification="matches"; submit=done'],
    ),
    "enter-done": (
        "done",
        0,
        [
            'fill: done; focus=done; fill=done, verification="matches"; '
            "submit=done, action=key, key=enter",
            'Requested: {"target":{"ref":"e7"},"action":"fill","submit":true}',
            'Resolved: {"method":"original","target":{"ref":"e7","role":"textbox",'
            '"name":"","bounds":[0,0,100,100],"windowId":1}}',
            'focus Selected: {"action":"focus","target":{"ref":"e7","role":"textbox",'
            '"name":"","bounds":[0,0,100,100],"windowId":1}}',
            'fill Selected: {"action":"fill","target":{"ref":"e7","role":"textbox",'
            '"name":"","bounds":[0,0,100,100],"windowId":1}}',
            'fill Verification target: {"ref":"e7"}',
            'submit Selected: {"action":"key","key":"enter","target":{"ref":"e7",'
            '"role":"textbox","name":"","bounds":[0,0,100,100],"windowId":1}}',
        ],
    ),
}

ACTION_EXPECTED = {
    "plain-click": (1, "done", 0, ["click: done"]),
    "selector-click": (1, "done", 0, ["click: done; ref=e7"]),
    "coordinate-pending-at-expiry": (
        1,
        "UNKNOWN",
        3,
        [
            "tap: UNKNOWN; Request budget exhausted; snapshot unavailable; "
            "dispatchStarted=true; "
            'snapshotError="Request budget exhausted before snapshot delivery"; '
            "Do not repeat automatically; "
            "first check the effect with read-only observations."
        ],
    ),
    "scroll-page-not-sent": (
        1,
        "not sent",
        2,
        [
            "scroll: not sent; Request budget exhausted; code=timeout; "
            "dispatchStarted=false",
            'Requested: {"target":{"ref":"e30"},"action":"scroll-down"}',
            'Resolved: {"method":"original","target":{"ref":"e30","role":"scroll",'
            '"name":"Results","bounds":[0,0,100,100],"windowId":1}}',
            'Selected: {"action":"page-down","target":{"ref":"e30","role":"scroll",'
            '"name":"Results","bounds":[0,0,100,100],"windowId":1}}',
        ],
    ),
}


WAIT_EXPECTED = {
    "gone-absent": (
        "done",
        0,
        [
            'wait: done; match={"condition":"gone","complete":true,'
            '"matches":0,"unknown":0}'
        ],
    ),
    "idle-loading": (
        "failed",
        1,
        [
            "wait: failed; Condition was not met within the request budget; "
            'match={"sequence":2,"condition":"idle","stable":false,'
            '"complete":true,"loading":true}'
        ],
    ),
    "screen-change-cross-window": (
        "done",
        0,
        [
            'wait: done; match={"sequence":2,"condition":"screen-change",'
            '"baselineSequence":1,"selectionScope":{},'
            '"currentScope":{"package":"other","window":2},'
            '"scope":{"package":"fixture","window":1},"baselineCapture":"worker:1",'
            '"baseline":"published","complete":true}'
        ],
    ),
    "screen-change-scope-mismatch": (
        "not sent",
        2,
        [
            "wait: not sent; Screen change requires the published observation "
            "selection scope; "
            "code=scopeMismatch"
        ],
    ),
}

FILE_FAILURE_EXPECTED = {
    "before-open": (
        "not sent",
        2,
        [
            "pull: not sent; job=f1; Session released before transfer began; "
            "file open was not attempted",
            "code=sessionChanged",
        ],
    ),
    "read-failed": (
        "failed",
        1,
        ["pull: failed; job=f2; Read interrupted", "code=workerFailure"],
    ),
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
        "TREE_BYTES": protocol.TREE_BYTES,
        "IMAGE_BYTES": protocol.IMAGE_BYTES,
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


def test_offline_selector_choices_match_device_classification_and_actions():
    assert sorted(ROLES) == WIRE_CONTRACT["vocabulary"]["roles"]
    assert sorted(LOCATE_ACTIONS) == WIRE_CONTRACT["vocabulary"]["actions"]


def test_query_pools_share_the_expected_control_methods():
    assert WIRE_CONTRACT["queryMethods"] == sorted(QUERY_METHODS | {"pair.register"})


def constant(source, name):
    match = re.search(rf"\b{name}\s*(?:=\s*)?\(?([0-9][0-9uL *]*)", source)
    assert match, f"Production constant {name} was not found"
    return math.prod(int(part.strip().rstrip("uL")) for part in match[1].split("*"))


@pytest.mark.parametrize("sample", ENTITY_FORMS.values(), ids=ENTITY_FORMS)
def test_entity_candidates_match_device_forms(sample):
    assert ui_view.query_forms(sample["input"]) == set(sample["forms"])


@pytest.mark.parametrize("capture", CAPTURES.values(), ids=CAPTURES)
def test_host_consumes_device_produced_chunks_without_reencoding(capture, monkeypatch):
    chunks = capture["chunks"]
    identifier = chunks[0]["data"]["capture"]
    responses = {chunk["data"]["offset"]: chunk for chunk in chunks}
    requested = []

    async def read(command, args):
        assert command == "ui.read"
        assert args["capture"] == identifier
        assert args["kind"] == capture["kind"]
        requested.append(args["offset"])
        return Result.model_validate(responses[args["offset"]])

    client = UiClient(CliOptions(None, "default", False), time.monotonic() + 10)
    monkeypatch.setattr(client, "call", read)
    body, metadata = asyncio.run(client.blob(identifier, capture["kind"]))
    assert requested == list(responses)
    assert (len(chunks) > 1) == capture["multiple"]
    expected = capture["expect"]
    assert chunks[0]["data"]["encoding"] == expected["encoding"]
    if capture["kind"] == "image":
        assert len(body) == expected["bytes"]
        assert_json(chunks[0]["data"]["image"], metadata)
        return
    tree = json.loads(body)
    if expected["tree"] == "minimal":
        assert tree == {"sequence": 9}
        return
    if expected["tree"] == "projected-with-noise":
        assert len(tree["nodes"][0].pop("stateDescription")) > protocol.CHUNK_BYTES
    assert_json(WIRE_CONTRACT["projected"], tree)
    screen = ui_view.screen(tree, ui_view.project(tree))
    assert screen.attrib["unreadable"] == "true"
    button = screen.find(".//button[@name='Pay & finish']")
    assert button is not None
    assert button.attrib["ref"] == "e1"
    assert screen.find(".//text[@name='Pay & finish']") is None
    checkbox = screen.find(".//checkbox")
    assert checkbox is not None
    assert checkbox.attrib["checked"] == "mixed"
    assert "ref" not in checkbox.attrib
    assert checkbox.attrib["ref-unavailable"] == "Original identity unavailable"
    indexed = screen.find(".//button[@name='Delete']")
    assert indexed is not None
    assert indexed.attrib["ref"] == "e4"
    assert "unreadable" not in indexed.attrib
    prefix = screen.find(".//text[@name='Readable prefix']")
    assert prefix is not None
    assert prefix.attrib["unreadable"] == "true"


def test_device_shell_cleanup_overrides_the_public_exit_without_losing_execution():
    result = Result.model_validate(WIRE_CONTRACT["shellCleanupFailure"])
    assert result.result == "failed"
    assert result.exit == 3
    assert result.exit_code() == 3
    assert result.data["execution"]["exitCode"] == 7
    assert result.data["cleanup"] == "unknown"


@pytest.mark.parametrize("name", COMPOUND_EXPECTED)
def test_host_renders_device_compound_results_without_losing_steps(name, capsys):
    case = COMPOUND_RESULTS[name]
    expected_result, expected_exit, expected_lines = COMPOUND_EXPECTED[name]
    result = Result.model_validate(case["response"])
    assert_json(
        case["response"],
        result.model_dump(mode="json", by_alias=True, exclude_unset=True),
    )
    request = case["request"]
    result.data["requested"] = ui_client.requested_action(
        request["command"], request["args"]
    )
    result.data["attempts"] = 1
    original = result.wire()
    assert result.result == expected_result
    assert result.exit_code() == expected_exit
    ui_client.result_line(result, "fill", Output())
    assert capsys.readouterr().out.splitlines() == expected_lines
    assert_json(original, result.wire())


@pytest.mark.parametrize("name", ACTION_EXPECTED)
def test_host_consumes_device_action_facts_including_pending_responses(name, capsys):
    case = ACTION_RESULTS[name]
    attempts, expected_result, expected_exit, expected_lines = ACTION_EXPECTED[name]
    result = Result.model_validate(case["response"])
    assert_json(
        case["response"],
        result.model_dump(mode="json", by_alias=True, exclude_unset=True),
    )
    request = case["request"]
    result.data["requested"] = ui_client.requested_action(
        request["command"], request["args"]
    )
    result.data["attempts"] = attempts
    original = result.wire()
    assert result.result == expected_result
    assert result.exit_code() == expected_exit
    ui_client.result_line(result, result.command, Output())
    assert capsys.readouterr().out.splitlines() == expected_lines
    assert_json(original, result.wire())


@pytest.mark.parametrize("name", WAIT_EXPECTED)
def test_host_consumes_device_wait_predicates_without_action_facts(name, capsys):
    case = WAIT_RESULTS[name]
    expected_result, expected_exit, expected_lines = WAIT_EXPECTED[name]
    result = Result.model_validate(case["response"])
    assert_json(
        case["response"],
        result.model_dump(mode="json", by_alias=True, exclude_unset=True),
    )
    original = result.wire()
    assert result.command == "wait"
    assert result.result == expected_result
    assert result.exit_code() == expected_exit
    assert not {"requested", "selected", "sent", "dispatchStarted"} & result.data.keys()
    ui_client.result_line(result, "wait", Output())
    assert capsys.readouterr().out.splitlines() == expected_lines
    assert_json(original, result.wire())


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
        (CAPTURES, {"projected-tree", "raw-image", "large-tree", "minimal-tree"}),
        (COMPOUND_RESULTS, COMPOUND_EXPECTED.keys()),
        (ACTION_RESULTS, ACTION_EXPECTED.keys()),
        (WAIT_RESULTS, WAIT_EXPECTED.keys()),
        (FILE_FAILURES, FILE_FAILURE_EXPECTED.keys()),
    ],
    ids=["captures", "compoundResults", "actionResults", "waitResults", "fileFailures"],
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
