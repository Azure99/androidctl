from __future__ import annotations

from pathlib import Path

from androidctl_contracts.command_results import (
    CommandResultCore,
    RetainedResultEnvelope,
)
from androidctl_contracts.daemon_api import CommandRunRequest
from typer.testing import CliRunner

from androidctl.app import app
from androidctl.exit_codes import ExitCode
from tests.support import (
    SOURCE_SCREEN_ABSENT,
    SOURCE_SCREEN_REQUIRED,
    assert_public_result_spine,
    assert_retained_result_spine,
    assert_truth_spine,
    parse_xml,
    patch_cli_context,
    retained_result,
    semantic_result,
)
from tests.support.daemon_fakes import ScriptedRecordingDaemon


def _loop_connect_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> RetainedResultEnvelope:
    del daemon, request, command
    return RetainedResultEnvelope.model_validate(
        retained_result(
            command="connect",
            envelope="bootstrap",
        )
    )


def _loop_observe_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request, command
    return CommandResultCore.model_validate(
        semantic_result(
            command="observe",
            category="observe",
            screen_id=daemon.current_screen_id,
            source_screen_id=daemon.current_screen_id,
            execution_outcome="notApplicable",
            continuity_status="stable",
            changed=False,
            screen_kwargs={
                "label": "Wi-Fi",
                "focus_ref": "n3",
                "target_ref": "n3",
                "context_nodes": [{"kind": "text", "text": "Network & internet"}],
            },
        )
    )


def _loop_open_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request, command
    previous = daemon.current_screen_id
    daemon.current_screen_id = "screen-00014"
    return CommandResultCore.model_validate(
        semantic_result(
            command="open",
            category="open",
            screen_id=daemon.current_screen_id,
            source_screen_id=previous,
            execution_outcome="dispatched",
            continuity_status="none",
            changed=True,
            screen_kwargs={
                "label": "Bluetooth",
                "focus_ref": "n4",
                "target_ref": "n4",
                "context_nodes": [{"kind": "text", "text": "Connected devices"}],
            },
        )
    )


def _loop_tap_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    assert command["sourceScreenId"] == "screen-00014"
    previous = daemon.current_screen_id
    daemon.current_screen_id = "screen-00015"
    return CommandResultCore.model_validate(
        semantic_result(
            command="tap",
            category="transition",
            screen_id=daemon.current_screen_id,
            source_screen_id=previous,
            execution_outcome="dispatched",
            continuity_status="stable",
            changed=True,
            screen_kwargs={
                "label": "Wi-Fi details",
                "focus_ref": "n7",
                "target_ref": "n7",
                "context_nodes": [{"kind": "text", "text": "Wi-Fi details"}],
                "dialog_nodes": [],
            },
        )
    )


def _loop_wait_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    assert command["predicate"] == {
        "kind": "screen-change",
        "sourceScreenId": "screen-00015",
    }
    return CommandResultCore.model_validate(
        semantic_result(
            command="wait",
            category="wait",
            screen_id=daemon.current_screen_id,
            source_screen_id=daemon.current_screen_id,
            execution_outcome="notApplicable",
            continuity_status="stable",
            changed=False,
            screen_kwargs={
                "label": "Forget network",
                "focus_ref": "n8",
                "target_ref": "n8",
                "context_nodes": [{"kind": "text", "text": "Wi-Fi details"}],
            },
        )
    )


def _loop_home_source_less_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    assert daemon.current_screen_id is None
    assert command == {"kind": "home"}
    return CommandResultCore.model_validate(
        semantic_result(
            command="home",
            category="transition",
            screen_id="screen-after-home",
            source_screen_id=None,
            execution_outcome="dispatched",
            continuity_status="none",
            changed=None,
            screen_kwargs={
                "label": "Launcher",
                "focus_ref": "n1",
                "target_ref": "n1",
                "context_nodes": [{"kind": "text", "text": "Home"}],
            },
        )
    )


def _loop_wait_device_unavailable_result(
    daemon: ScriptedRecordingDaemon,
    request: CommandRunRequest,
    command: dict[str, object],
) -> CommandResultCore:
    del request
    assert command["predicate"] == {
        "kind": "screen-change",
        "sourceScreenId": daemon.current_screen_id,
    }
    return CommandResultCore.model_validate(
        semantic_result(
            ok=False,
            command="wait",
            category="wait",
            payloadMode="none",
            sourceScreenId=daemon.current_screen_id,
            nextScreenId=None,
            code="DEVICE_UNAVAILABLE",
            message="No current device observation is available.",
            truth={
                "executionOutcome": "notApplicable",
                "continuityStatus": "none",
                "observationQuality": "none",
                "changed": None,
            },
            screen=None,
        )
    )


def _make_loop_daemon(root: Path) -> ScriptedRecordingDaemon:
    return ScriptedRecordingDaemon(
        root=root,
        command_handlers={
            "connect": _loop_connect_result,
            "observe": _loop_observe_result,
            "open": _loop_open_result,
            "tap": _loop_tap_result,
            "wait": _loop_wait_result,
            "home": _loop_home_source_less_result,
        },
    )


def test_public_contract_loop_connect_observe_open_tap_wait(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_loop_daemon(tmp_path),
    )
    runner = CliRunner()

    connect_result = runner.invoke(app, ["connect", "--adb", "--token", "abc"])
    observe_result = runner.invoke(app, ["observe"])
    open_result = runner.invoke(app, ["open", "app:com.android.settings"])
    tap_result = runner.invoke(app, ["tap", "n3"])
    wait_result = runner.invoke(app, ["wait", "--until", "screen-change"])

    assert connect_result.exit_code == 0
    assert observe_result.exit_code == 0
    assert open_result.exit_code == 0
    assert tap_result.exit_code == 0
    assert wait_result.exit_code == 0

    connect_root = parse_xml(connect_result.stdout)
    assert_retained_result_spine(
        connect_root, command="connect", envelope="bootstrap", ok=True
    )

    observe_root = parse_xml(observe_result.stdout)
    assert_public_result_spine(
        observe_root,
        command="observe",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00013",
        ok=True,
    )
    assert observe_root.attrib["nextScreenId"] == "screen-00013"
    assert observe_root.find("./screen") is not None
    assert_truth_spine(
        observe_root,
        execution_outcome="notApplicable",
        continuity_status="stable",
        changed=False,
    )

    open_root = parse_xml(open_result.stdout)
    assert_public_result_spine(
        open_root,
        command="open",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00013",
        ok=True,
    )
    assert open_root.attrib["nextScreenId"] == "screen-00014"
    assert open_root.find("./screen") is not None
    assert_truth_spine(
        open_root,
        execution_outcome="dispatched",
        continuity_status="none",
        changed=True,
    )
    assert open_root.find("./screen/surface/focus").attrib["inputRef"] == "n4"
    assert (
        open_root.find("./screen/groups/targets/button").attrib["label"] == "Bluetooth"
    )

    tap_root = parse_xml(tap_result.stdout)
    assert_public_result_spine(
        tap_root,
        command="tap",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00014",
        ok=True,
    )
    assert tap_root.attrib["nextScreenId"] == "screen-00015"
    assert tap_root.find("./screen") is not None
    assert tap_root.find("./screen/groups/targets/button").attrib["ref"] == "n7"
    context_text = tap_root.find("./screen/groups/context/literal")
    assert context_text is not None
    assert context_text.text == "Wi-Fi details"

    wait_root = parse_xml(wait_result.stdout)
    assert_public_result_spine(
        wait_root,
        command="wait",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00015",
        ok=True,
    )
    assert_truth_spine(
        wait_root,
        execution_outcome="notApplicable",
        continuity_status="stable",
        changed=False,
    )
    assert wait_root.find("./screen") is not None
    assert wait_root.find("./warnings") is not None
    assert wait_root.find("./screen/surface/focus").attrib["inputRef"] == "n8"
    assert (
        wait_root.find("./screen/groups/targets/button").attrib["label"]
        == "Forget network"
    )

    assert len(daemon.run_calls) == 5
    assert daemon.run_calls[3]["command"] == {
        "kind": "tap",
        "ref": "n3",
        "sourceScreenId": "screen-00014",
    }
    assert daemon.run_calls[4]["command"] == {
        "kind": "wait",
        "predicate": {
            "kind": "screen-change",
            "sourceScreenId": "screen-00015",
        },
        "timeoutMs": 2000,
    }


def test_public_contract_loop_global_action_without_source_screen(
    monkeypatch,
    tmp_path: Path,
) -> None:
    daemon = patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=ScriptedRecordingDaemon(
            root=tmp_path,
            current_screen_id=None,
            command_handlers={"home": _loop_home_source_less_result},
        ),
    )

    result = CliRunner().invoke(app, ["home"])

    assert result.exit_code == 0
    assert daemon.run_calls == [{"command": {"kind": "home"}}]
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="home",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_ABSENT,
        ok=True,
    )
    assert root.attrib["nextScreenId"] == "screen-after-home"
    assert_truth_spine(
        root,
        execution_outcome="dispatched",
        continuity_status="none",
        changed=None,
    )


def test_public_contract_loop_wait_fail_closed_renders_none_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=ScriptedRecordingDaemon(
            root=tmp_path,
            current_screen_id="screen-00022",
            command_handlers={"wait": _loop_wait_device_unavailable_result},
        ),
    )

    result = CliRunner().invoke(app, ["wait", "--until", "screen-change"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert result.stderr == ""
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="wait",
        result_family="none",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00022",
        ok=False,
    )
    assert root.attrib["code"] == "DEVICE_UNAVAILABLE"
    assert_truth_spine(
        root,
        execution_outcome="notApplicable",
        continuity_status="none",
        observation_quality="none",
        changed=None,
    )


def test_observe_keeps_the_same_semantic_result_fields_in_xml(
    monkeypatch,
    tmp_path: Path,
) -> None:
    patch_cli_context(
        monkeypatch,
        tmp_path=tmp_path,
        daemon=_make_loop_daemon(tmp_path),
    )

    result = CliRunner().invoke(app, ["observe"])

    assert result.exit_code == 0
    root = parse_xml(result.stdout)
    assert_public_result_spine(
        root,
        command="observe",
        result_family="full",
        source_screen_policy=SOURCE_SCREEN_REQUIRED,
        source_screen_id="screen-00013",
        ok=True,
    )
    assert root.attrib["nextScreenId"] == "screen-00013"
    assert root.find("./artifacts") is not None
