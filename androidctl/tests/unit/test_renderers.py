from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable

import pytest
from pydantic import BaseModel, ValidationError

from androidctl.errors.models import PublicError
from androidctl.exit_codes import ExitCode
from androidctl.renderers.xml import (
    render_error_text,
    render_success_text,
    render_xml,
)
from androidctl.renderers.xml_projection import project_xml_payload
from androidctl_contracts.daemon_api import RuntimeGetResult, RuntimePayload
from androidctl_contracts.public_screen import PUBLIC_NODE_ROLE_VALUES
from tests.support.semantic_contract import (
    assert_error_result_spine,
    assert_retained_result_spine,
    parse_xml,
    retained_result,
    semantic_result,
    semantic_screen,
)


def _public_error(
    *,
    code: str,
    message: str = "failure",
    exit_code: ExitCode = ExitCode.ERROR,
) -> PublicError:
    return PublicError(
        code=code,
        message=message,
        hint=None,
        exit_code=exit_code,
    )


def _list_apps_result(
    apps: list[dict[str, object]] | None = None,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "command": "list-apps",
        "apps": (
            apps
            if apps is not None
            else [
                {
                    "packageName": "com.android.settings",
                    "appLabel": "Settings",
                }
            ]
        ),
    }
    payload.update(overrides)
    return payload


def test_success_renderer_emits_semantic_xml_contract() -> None:
    xml = render_success_text(
        payload=semantic_result(),
    )

    root = parse_xml(xml)
    assert root.tag == "result"
    assert root.attrib["ok"] == "true"
    assert root.attrib["command"] == "observe"
    assert root.attrib["category"] == "observe"
    assert root.attrib["payloadMode"] == "full"
    assert root.find("./truth") is not None
    assert root.find("./uncertainty") is not None
    assert root.find("./warnings") is not None
    assert root.find("./screen") is not None
    assert root.find("./artifacts") is not None


def test_semantic_xml_renders_action_target_after_truth() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                command="focus",
                category="transition",
                source_screen_id="screen-00012",
                screen_id="screen-00013",
                execution_outcome="dispatched",
                actionTarget={
                    "sourceRef": "n1",
                    "sourceScreenId": "screen-00012",
                    "subjectRef": "n1",
                    "dispatchedRef": "n1",
                    "nextScreenId": "screen-00013",
                    "nextRef": "n1",
                    "identityStatus": "sameRef",
                    "evidence": [
                        "liveRef",
                        "requestTarget",
                        "focusConfirmation",
                    ],
                },
            )
        )
    )

    action_target = root.find("./actionTarget")
    assert action_target is not None
    assert action_target.attrib == {
        "sourceRef": "n1",
        "sourceScreenId": "screen-00012",
        "subjectRef": "n1",
        "dispatchedRef": "n1",
        "nextScreenId": "screen-00013",
        "nextRef": "n1",
        "identityStatus": "sameRef",
        "evidence": "liveRef requestTarget focusConfirmation",
    }
    assert [child.tag for child in root][:3] == [
        "truth",
        "actionTarget",
        "uncertainty",
    ]


def test_semantic_xml_omits_absent_action_target() -> None:
    root = parse_xml(render_xml(semantic_result()))

    assert root.find("./actionTarget") is None


def test_semantic_xml_renders_unconfirmed_action_target_without_next_ref() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                command="submit",
                category="transition",
                source_screen_id="screen-00012",
                screen_id="screen-00013",
                execution_outcome="dispatched",
                actionTarget={
                    "sourceRef": "n1",
                    "sourceScreenId": "screen-00012",
                    "subjectRef": "n2",
                    "nextScreenId": "screen-00013",
                    "identityStatus": "unconfirmed",
                    "evidence": [
                        "refRepair",
                        "submitConfirmation",
                        "ambiguousSuccessor",
                    ],
                },
            )
        )
    )

    action_target = root.find("./actionTarget")
    assert action_target is not None
    assert action_target.attrib == {
        "sourceRef": "n1",
        "sourceScreenId": "screen-00012",
        "subjectRef": "n2",
        "nextScreenId": "screen-00013",
        "identityStatus": "unconfirmed",
        "evidence": "refRepair submitConfirmation ambiguousSuccessor",
    }


def test_semantic_xml_rejects_malformed_action_target_via_contract() -> None:
    with pytest.raises(ValidationError, match="sameRef"):
        render_xml(
            semantic_result(
                command="focus",
                category="transition",
                source_screen_id="screen-00012",
                screen_id="screen-00013",
                execution_outcome="dispatched",
                actionTarget={
                    "sourceRef": "n1",
                    "sourceScreenId": "screen-00012",
                    "subjectRef": "n1",
                    "nextScreenId": "screen-00013",
                    "nextRef": "n2",
                    "identityStatus": "sameRef",
                    "evidence": ["liveRef", "focusConfirmation"],
                },
            )
        )


def test_retained_xml_never_renders_action_target() -> None:
    root = parse_xml(
        render_xml(retained_result(command="screenshot", envelope="artifact"))
    )

    assert root.tag == "retainedResult"
    assert root.find("./actionTarget") is None


def test_list_apps_xml_renders_success_family() -> None:
    root = parse_xml(render_xml(_list_apps_result()))

    assert root.tag == "listAppsResult"
    assert root.attrib == {"ok": "true", "command": "list-apps"}
    for attr in ("category", "payloadMode", "envelope", "code", "message"):
        assert attr not in root.attrib
    apps = root.find("./apps")
    assert apps is not None
    app = apps.find("./app")
    assert app is not None
    assert app.attrib == {
        "packageName": "com.android.settings",
        "appLabel": "Settings",
    }
    for attr in ("launchable", "activityName", "sourceKind"):
        assert attr not in app.attrib
    for tag in (
        "truth",
        "uncertainty",
        "warnings",
        "screen",
        "artifacts",
        "details",
    ):
        assert root.find(f"./{tag}") is None


def test_list_apps_xml_renders_empty_apps_container() -> None:
    root = parse_xml(render_xml(_list_apps_result(apps=[])))

    assert root.tag == "listAppsResult"
    apps = root.find("./apps")
    assert apps is not None
    assert list(apps) == []


def test_list_apps_xml_escapes_app_labels() -> None:
    xml = render_xml(
        _list_apps_result(
            apps=[
                {
                    "packageName": "com.example.mail",
                    "appLabel": 'Mail & Calendar <Beta> "CN" >',
                }
            ]
        )
    )

    assert "Mail &amp; Calendar &lt;Beta" in xml
    root = parse_xml(xml)
    app = root.find("./apps/app")
    assert app is not None
    assert app.attrib["appLabel"] == 'Mail & Calendar <Beta> "CN" >'


def test_list_apps_projection_uses_list_apps_kind() -> None:
    projection = project_xml_payload(_list_apps_result())

    assert projection["kind"] == "listApps"
    assert projection["attrs"] == {"ok": "true", "command": "list-apps"}
    assert projection["apps"] == [
        {
            "packageName": "com.android.settings",
            "appLabel": "Settings",
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "command": "list-apps"},
        {"ok": True, "command": "list-apps", "apps": "bad"},
        {"ok": True, "command": "list-apps", "apps": ["bad"]},
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"appLabel": "Settings"}],
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"packageName": "com.android.settings"}],
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"packageName": "   ", "appLabel": "Settings"}],
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [{"packageName": "com.android.settings", "appLabel": "   "}],
        },
        {"ok": False, "command": "list-apps", "apps": []},
        {
            "ok": True,
            "command": "list-apps",
            "apps": [],
            "category": "observe",
            "payloadMode": "full",
            "truth": {
                "executionOutcome": "notApplicable",
                "continuityStatus": "stable",
                "observationQuality": "authoritative",
            },
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [],
            "envelope": "artifact",
            "details": {},
        },
        {
            "ok": True,
            "command": "list-apps",
            "apps": [
                {
                    "packageName": "com.android.settings",
                    "appLabel": "Settings",
                    "launchable": True,
                }
            ],
        },
    ],
)
@pytest.mark.parametrize("entrypoint", [render_xml, project_xml_payload])
def test_list_apps_xml_entrypoints_reject_malformed_success_payloads(
    payload: dict[str, object],
    entrypoint: Callable[[dict[str, object]], object],
) -> None:
    with pytest.raises(ValidationError):
        entrypoint(payload)


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("observe", "observe"),
        ("open", "open"),
        ("tap", "transition"),
        ("wait", "wait"),
    ],
)
def test_active_semantic_command_output_root_remains_result(
    command: str,
    category: str,
) -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                command=command,
                category=category,
                execution_outcome=(
                    "dispatched" if category == "transition" else "notApplicable"
                ),
            )
        )
    )

    assert root.tag == "result"
    assert root.attrib["command"] == command
    assert root.attrib["category"] == category
    assert root.find("./truth") is not None
    assert root.find("./screen") is not None


def test_payload_mode_none_uses_unified_failure_shape() -> None:
    xml = render_xml(
        semantic_result(
            ok=False,
            command="observe",
            category="observe",
            payloadMode="none",
            sourceScreenId=None,
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

    root = parse_xml(xml)
    assert root.attrib["ok"] == "false"
    assert root.attrib["payloadMode"] == "none"
    assert root.attrib["code"] == "DEVICE_UNAVAILABLE"
    assert root.find("./message") is not None
    assert root.find("./screen") is None
    assert "nextScreenId" not in root.attrib
    assert root.find("./artifacts") is not None


def test_action_not_confirmed_failure_renders_full_stdout_result_shape() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                ok=False,
                command="long-tap",
                category="transition",
                payloadMode="full",
                source_screen_id="screen-00021",
                screen_id="screen-00022",
                code="ACTION_NOT_CONFIRMED",
                message="action was not confirmed on the refreshed screen",
                execution_outcome="dispatched",
                continuity_status="stable",
                changed=False,
            )
        )
    )

    assert root.tag == "result"
    assert root.attrib["ok"] == "false"
    assert root.attrib["code"] == "ACTION_NOT_CONFIRMED"
    assert root.find("./message").text == (
        "action was not confirmed on the refreshed screen"
    )
    assert root.find("./screen") is not None
    assert root.find("./truth").attrib == {
        "executionOutcome": "dispatched",
        "continuityStatus": "stable",
        "observationQuality": "authoritative",
        "changed": "false",
    }


def test_success_result_omits_code_and_message() -> None:
    root = parse_xml(render_xml(semantic_result(ok=True)))

    assert "code" not in root.attrib
    assert root.find("./message") is None


def test_xml_always_renders_uncertainty_and_warnings_containers() -> None:
    root = parse_xml(render_xml(semantic_result(ok=True)))

    assert root.find("./uncertainty") is not None
    assert root.find("./warnings") is not None


def test_xml_rejects_changed_without_source_screen_id() -> None:
    with pytest.raises(ValueError):
        render_xml(
            semantic_result(
                sourceScreenId=None,
                truth={
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "stable",
                    "observationQuality": "authoritative",
                    "changed": True,
                },
            )
        )


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (None, None),
    ],
)
def test_xml_changed_attr_is_boolean_token_or_omitted(
    changed: bool | None,
    expected: str | None,
) -> None:
    root = parse_xml(render_xml(semantic_result(changed=changed)))
    truth = root.find("./truth")
    assert truth is not None

    if expected is None:
        assert "changed" not in truth.attrib
    else:
        assert truth.attrib["changed"] == expected
    assert truth.attrib.get("changed") not in {"null", "", "unknown"}


@pytest.mark.parametrize(("changed", "expected"), [(True, "true"), (False, "false")])
def test_open_xml_renders_changed_with_none_continuity(
    changed: bool,
    expected: str,
) -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                command="open",
                category="open",
                execution_outcome="dispatched",
                continuity_status="none",
                source_screen_id="screen-00012",
                changed=changed,
            )
        )
    )
    truth = root.find("./truth")

    assert root.attrib["command"] == "open"
    assert root.attrib["category"] == "open"
    assert root.attrib["sourceScreenId"] == "screen-00012"
    assert truth is not None
    assert truth.attrib["continuityStatus"] == "none"
    assert truth.attrib["changed"] == expected


def test_xml_requires_screen_id_to_match_next_screen_id() -> None:
    with pytest.raises(ValueError):
        render_xml(
            semantic_result(
                nextScreenId="screen-00014",
                screen={"screenId": "screen-00013"},
            )
        )


def test_xml_promotes_blocking_group_to_front_of_groups() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(
                        screen_id="screen-00013",
                        blocking_group="dialog",
                    ),
                    "groups": [
                        {
                            "name": "dialog",
                            "nodes": [
                                {
                                    "ref": "n7",
                                    "role": "button",
                                    "actions": ["tap"],
                                    "label": "Allow",
                                }
                            ],
                        },
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "ref": "n3",
                                    "role": "button",
                                    "actions": ["tap"],
                                    "label": "Wi-Fi",
                                }
                            ],
                        },
                        {"name": "keyboard", "nodes": []},
                        {"name": "system", "nodes": []},
                        {"name": "context", "nodes": []},
                    ],
                },
            )
        )
    )

    groups = [child.tag for child in root.findall("./screen/groups/*")]
    assert groups == ["dialog", "targets", "keyboard", "system", "context"]
    assert root.find("./screen/groups/targets/button").attrib["ref"] == "n3"


def test_xml_requires_each_public_group_to_be_present_exactly_once() -> None:
    with pytest.raises(ValidationError):
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "groups": [
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "ref": "n3",
                                    "role": "button",
                                    "actions": ["tap"],
                                    "label": "Wi-Fi",
                                }
                            ],
                        }
                    ],
                },
            )
        )


def test_xml_projection_whitelists_public_sections_and_group_items() -> None:
    projection = project_xml_payload(
        semantic_result(
            warnings=["screen was refreshed"],
            artifacts={
                "screenshotPng": "/repo/.androidctl/screenshots/screen-00013.png",
                "screenXml": "/repo/.androidctl/artifacts/screens/screen-00013.xml",
            },
            screen={
                **semantic_screen(blocking_group="dialog"),
                "surface": {
                    "keyboardVisible": False,
                    "blockingGroup": "dialog",
                    "focus": {"inputRef": "n3"},
                },
                "groups": [
                    {
                        "name": "dialog",
                        "nodes": [
                            {
                                "ref": "n7",
                                "role": "button",
                                "actions": ["tap"],
                                "label": "Allow",
                            }
                        ],
                    },
                    {
                        "name": "targets",
                        "nodes": [
                            {
                                "ref": "n3",
                                "role": "input",
                                "actions": ["type"],
                                "label": "Wi-Fi",
                                "submitRefs": ["n7"],
                            }
                        ],
                    },
                    {"name": "keyboard", "nodes": []},
                    {"name": "system", "nodes": []},
                    {"name": "context", "nodes": []},
                ],
                "visibleWindows": [
                    {
                        "windowRef": "w1",
                        "role": "main",
                        "focused": True,
                        "blocking": False,
                    }
                ],
            },
        )
    )

    assert projection["kind"] == "semantic"
    assert projection["attrs"] == {
        "ok": "true",
        "command": "observe",
        "category": "observe",
        "payloadMode": "full",
        "sourceScreenId": "screen-00013",
        "nextScreenId": "screen-00013",
    }
    assert projection["message"] is None
    assert projection["truth"] == {
        "executionOutcome": "notApplicable",
        "continuityStatus": "stable",
        "observationQuality": "authoritative",
        "changed": "false",
    }
    assert projection["uncertainty"] == []
    assert projection["warnings"] == ["screen was refreshed"]
    assert projection["artifacts"] == {
        "screenshotPng": ".androidctl/screenshots/screen-00013.png",
        "screenXml": ".androidctl/artifacts/screens/screen-00013.xml",
    }

    screen = projection["screen"]
    assert screen is not None
    assert screen["attrs"] == {"screenId": "screen-00013"}
    assert screen["app"] == {
        "packageName": "com.android.settings",
        "activityName": "com.android.settings.Settings",
    }
    assert screen["surface"] == {
        "attrs": {"keyboardVisible": "false", "blockingGroup": "dialog"},
        "focus": {"inputRef": "n3"},
    }
    assert [group["name"] for group in screen["groups"]] == [
        "dialog",
        "targets",
        "keyboard",
        "system",
        "context",
    ]
    assert screen["groups"][0]["items"] == [
        {
            "tag": "button",
            "attrs": {
                "ref": "n7",
                "actions": "tap",
                "label": "Allow",
            },
            "children": [],
        }
    ]
    assert screen["groups"][1]["items"] == [
        {
            "tag": "input",
            "attrs": {
                "ref": "n3",
                "actions": "type",
                "submitRefs": "n7",
                "label": "Wi-Fi",
            },
            "children": [],
        }
    ]
    assert screen["visibleWindows"] == [
        {
            "windowRef": "w1",
            "role": "main",
            "focused": "true",
            "blocking": "false",
        }
    ]


def test_inline_xml_renders_submit_refs_on_input_only() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "surface": {
                        "keyboardVisible": False,
                        "focus": {"inputRef": "n1"},
                    },
                    "groups": [
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "ref": "n1",
                                    "role": "input",
                                    "actions": ["type"],
                                    "label": "Search",
                                    "submitRefs": ["n2"],
                                },
                                {
                                    "ref": "n2",
                                    "role": "button",
                                    "actions": ["tap"],
                                    "label": "Search",
                                },
                            ],
                        },
                        {"name": "keyboard", "nodes": []},
                        {"name": "system", "nodes": []},
                        {"name": "context", "nodes": []},
                        {"name": "dialog", "nodes": []},
                    ],
                }
            )
        )
    )

    input_node = root.find(".//*[@ref='n1']")
    button_node = root.find(".//*[@ref='n2']")
    assert input_node is not None
    assert input_node.attrib["submitRefs"] == "n2"
    assert button_node is not None
    assert "submitRefs" not in button_node.attrib
    assert not root.findall(".//*[@submitsInputRefs]")


def test_inline_xml_omits_empty_submit_refs() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "surface": {
                        "keyboardVisible": False,
                        "focus": {"inputRef": "n1"},
                    },
                    "groups": [
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "ref": "n1",
                                    "role": "input",
                                    "actions": ["type"],
                                    "label": "Search",
                                    "submitRefs": [],
                                },
                                {
                                    "ref": "n2",
                                    "role": "button",
                                    "actions": ["tap"],
                                    "label": "Search",
                                },
                            ],
                        },
                        {"name": "keyboard", "nodes": []},
                        {"name": "system", "nodes": []},
                        {"name": "context", "nodes": []},
                        {"name": "dialog", "nodes": []},
                    ],
                }
            )
        )
    )

    input_node = root.find(".//*[@ref='n1']")
    assert input_node is not None
    assert input_node.attrib["actions"] == "type"
    assert "state" not in input_node.attrib
    assert "submitRefs" not in input_node.attrib


def test_inline_xml_omits_empty_state_and_actions_sequence_attrs() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "groups": [
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "ref": "n1",
                                    "role": "input",
                                    "actions": [],
                                    "state": ["focused"],
                                    "label": "Search",
                                },
                                {
                                    "ref": "n2",
                                    "role": "button",
                                    "actions": ["tap"],
                                    "state": [],
                                    "label": "Search",
                                },
                                {
                                    "ref": "n3",
                                    "role": "text",
                                    "actions": [],
                                    "state": [],
                                    "label": "Status",
                                },
                            ],
                        },
                        {"name": "keyboard", "nodes": []},
                        {"name": "system", "nodes": []},
                        {"name": "context", "nodes": []},
                        {"name": "dialog", "nodes": []},
                    ],
                }
            )
        )
    )

    focused_node = root.find(".//*[@ref='n1']")
    action_node = root.find(".//*[@ref='n2']")
    empty_node = root.find(".//*[@ref='n3']")
    assert focused_node is not None
    assert focused_node.tag == "input"
    assert focused_node.attrib["state"] == "focused"
    assert "actions" not in focused_node.attrib
    assert "role" not in focused_node.attrib
    assert action_node is not None
    assert action_node.tag == "button"
    assert action_node.attrib["actions"] == "tap"
    assert "state" not in action_node.attrib
    assert "role" not in action_node.attrib
    assert empty_node is not None
    assert empty_node.tag == "text"
    assert "actions" not in empty_node.attrib
    assert "state" not in empty_node.attrib
    assert "role" not in empty_node.attrib


@pytest.mark.parametrize("role", PUBLIC_NODE_ROLE_VALUES)
def test_inline_xml_renders_public_node_roles_as_tags(role: str) -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "groups": [
                        {"name": "targets", "nodes": []},
                        {"name": "keyboard", "nodes": []},
                        {"name": "system", "nodes": []},
                        {
                            "name": "context",
                            "nodes": [
                                {
                                    "ref": "n1",
                                    "role": role,
                                    "label": f"{role} item",
                                }
                            ],
                        },
                        {"name": "dialog", "nodes": []},
                    ],
                }
            )
        )
    )

    items = list(root.findall("./screen/groups/context/*"))
    assert [item.tag for item in items] == [role]
    assert items[0].attrib["label"] == f"{role} item"
    assert "role" not in items[0].attrib


def test_inline_xml_distinguishes_role_text_from_literal_text_item() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "groups": [
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "ref": "n1",
                                    "role": "text",
                                    "label": "Saved networks",
                                },
                                {
                                    "kind": "text",
                                    "text": "Decorative literal",
                                },
                            ],
                        },
                        {"name": "keyboard", "nodes": []},
                        {"name": "system", "nodes": []},
                        {"name": "context", "nodes": []},
                        {"name": "dialog", "nodes": []},
                    ],
                }
            )
        )
    )

    role_text = root.find("./screen/groups/targets/text")
    assert role_text is not None
    assert role_text.attrib == {"ref": "n1", "label": "Saved networks"}
    assert role_text.text is None

    literal = root.find("./screen/groups/targets/literal")
    assert literal is not None
    assert literal.attrib == {}
    assert literal.text == "Decorative literal"


def test_xml_projection_uses_last_androidctl_marker_for_screenshot_root() -> None:
    projection = project_xml_payload(
        semantic_result(
            artifacts={
                "screenshotPng": (
                    "/tmp/.androidctl/repo/.androidctl/screenshots/screen-00013.png"
                ),
            }
        )
    )

    assert projection["artifacts"] == {
        "screenshotPng": ".androidctl/screenshots/screen-00013.png"
    }


def test_xml_projection_uses_last_androidctl_marker_for_screen_xml() -> None:
    projection = project_xml_payload(
        semantic_result(
            artifacts={
                "screenXml": (
                    "/tmp/.androidctl/repo/.androidctl/artifacts/screens/"
                    "screen-00013.xml"
                ),
            }
        )
    )

    assert projection["artifacts"] == {
        "screenXml": ".androidctl/artifacts/screens/screen-00013.xml"
    }


def test_renderer_rejects_non_contract_screen_fields() -> None:
    with pytest.raises(ValidationError):
        project_xml_payload(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "internalOnlyField": "legacy-value",
                }
            )
        )


def test_xml_revalidates_semantic_base_model_inputs_against_contract() -> None:
    class MalformedSemanticPayload(BaseModel):
        ok: bool = True
        command: str = "observe"
        category: str = "observe"
        payloadMode: str = "full"
        nextScreenId: str = "screen-00013"
        truth: dict[str, object] = {
            "executionOutcome": "notApplicable",
            "continuityStatus": "stable",
            "observationQuality": "authoritative",
            "changed": False,
        }
        screen: dict[str, object] = {
            **semantic_screen(),
            "app": 123,
        }
        uncertainty: list[str] = []
        warnings: list[str] = []
        artifacts: dict[str, object] = {}

    with pytest.raises(ValidationError):
        render_xml(MalformedSemanticPayload())


@pytest.mark.parametrize(
    "payload",
    [
        {
            "runtime": {
                "workspaceRoot": "/repo",
                "artifactRoot": "/repo/.androidctl",
                "status": "ready",
                "currentScreenId": "screen-00006",
            }
        },
        RuntimeGetResult(
            runtime=RuntimePayload(
                workspace_root="/repo",
                artifact_root="/repo/.androidctl",
                status="ready",
                current_screen_id="screen-00006",
            )
        ),
    ],
)
def test_xml_projection_and_renderer_reject_runtime_route_payloads(
    payload: dict[str, object] | RuntimeGetResult,
) -> None:
    with pytest.raises(ValidationError):
        project_xml_payload(payload)
    with pytest.raises(ValidationError):
        render_xml(payload)


def test_xml_projection_and_renderer_reject_malformed_nested_public_attrs() -> None:
    class MalformedProjectionPayload(BaseModel):
        ok: bool = True
        command: str = "observe"
        category: str = "observe"
        payloadMode: str = "full"
        sourceScreenId: str = "screen-00013"
        nextScreenId: str = "screen-00013"
        truth: dict[str, object] = {
            "executionOutcome": "notApplicable",
            "continuityStatus": "stable",
            "observationQuality": "authoritative",
            "changed": False,
        }
        screen: dict[str, object] = {
            "screenId": "screen-00013",
            "app": {
                "packageName": "",
                "activityName": 123,
                "requestedPackageName": "com.android.settings",
                "resolvedPackageName": ["com.android.settings"],
                "matchType": False,
            },
            "surface": {
                "keyboardVisible": 1,
                "blockingGroup": "",
                "focus": {"inputRef": 0},
            },
            "groups": [],
            "omitted": [],
            "visibleWindows": [],
            "transient": [],
        }
        uncertainty: list[str] = []
        warnings: list[str] = []
        artifacts: dict[str, object] = {}

    payload = MalformedProjectionPayload()

    with pytest.raises(ValidationError):
        project_xml_payload(payload)
    with pytest.raises(ValidationError):
        render_xml(payload)


def test_xml_projection_rejects_malformed_omitted_and_transient_items() -> None:
    with pytest.raises(ValidationError):
        project_xml_payload(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "omitted": [
                        {"group": "targets", "reason": "virtualized", "count": 27},
                        {"group": "overlay", "reason": "virtualized", "count": 5},
                    ],
                    "transient": [
                        {"kind": "toast", "text": "Saved"},
                        {"kind": "modal", "text": "Dropped"},
                    ],
                }
            )
        )


def test_xml_renders_omitted_entries_and_typed_transient_items() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "omitted": [
                        {"group": "targets", "reason": "virtualized", "count": 27},
                    ],
                    "transient": [
                        {"kind": "toast", "text": "Saved"},
                        {"text": "Sync complete"},
                    ],
                }
            )
        )
    )

    omitted_entry = root.find(
        "./screen/omitted/entry[@group='targets'][@reason='virtualized']"
    )
    assert omitted_entry is not None
    assert omitted_entry.attrib["count"] == "27"
    assert root.find("./screen/omitted/item") is None

    typed_transient = root.find("./screen/transient/item[@kind='toast']")
    assert typed_transient is not None
    assert typed_transient.text == "Saved"

    plain_transient_items = [
        item
        for item in root.findall("./screen/transient/item")
        if "kind" not in item.attrib
    ]
    assert [item.text for item in plain_transient_items] == ["Sync complete"]
    assert root.find("./screen/transient/text") is None


def test_xml_projection_rejects_invalid_surface_blocking_group_token() -> None:
    with pytest.raises(ValidationError):
        project_xml_payload(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "surface": {
                        "keyboardVisible": False,
                        "blockingGroup": "overlay",
                        "focus": {"inputRef": "n3"},
                    },
                }
            )
        )
    with pytest.raises(ValidationError):
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "surface": {
                        "keyboardVisible": False,
                        "blockingGroup": "overlay",
                        "focus": {"inputRef": "n3"},
                    },
                }
            )
        )


def test_xml_rejects_unknown_and_malformed_group_entries() -> None:
    with pytest.raises(ValidationError):
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "groups": [
                        {"name": "mystery", "nodes": [{"ref": "n99"}]},
                        {"name": "targets", "nodes": [{"ref": "n3", "role": "button"}]},
                        {"name": "", "nodes": [{"ref": "n4"}]},
                        {"nodes": [{"ref": "n5"}]},
                        {"name": "dialog", "nodes": "bad"},
                    ],
                },
            )
        )


def test_xml_renders_mixed_group_items_recursively() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "groups": [
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "kind": "container",
                                    "ref": "n3",
                                    "role": "scroll-container",
                                    "actions": ["scroll"],
                                    "label": "Search results",
                                    "scrollDirections": ["down", "backward"],
                                    "windowRef": "w1",
                                    "within": "Chrome",
                                    "value": "1 page",
                                    "children": [
                                        {
                                            "kind": "node",
                                            "ref": "n4",
                                            "role": "list-item",
                                            "actions": ["tap"],
                                            "label": "OpenAI",
                                            "within": "Search results",
                                        },
                                        {
                                            "kind": "text",
                                            "text": "About 1,230 results",
                                        },
                                    ],
                                }
                            ],
                        },
                        {"name": "keyboard", "nodes": []},
                        {
                            "name": "system",
                            "nodes": [{"kind": "text", "text": "Battery 100 percent."}],
                        },
                        {
                            "name": "context",
                            "nodes": [{"kind": "text", "text": "Chrome"}],
                        },
                        {"name": "dialog", "nodes": []},
                    ],
                    "visibleWindows": [
                        {
                            "windowRef": "w1",
                            "role": "main",
                            "focused": True,
                            "blocking": False,
                        }
                    ],
                }
            )
        )
    )

    container = root.find("./screen/groups/targets/scroll-container")
    assert container is not None
    assert container.attrib == {
        "ref": "n3",
        "actions": "scroll",
        "scrollDirections": "down backward",
        "windowRef": "w1",
        "label": "Search results",
        "within": "Chrome",
        "value": "1 page",
    }
    assert container.find("./label") is None
    assert container.find("./within") is None
    assert container.find("./value") is None

    child_node = container.find("./list-item")
    assert child_node is not None
    assert child_node.attrib["ref"] == "n4"
    assert child_node.attrib["label"] == "OpenAI"
    assert child_node.attrib["within"] == "Search results"
    assert "role" not in child_node.attrib
    assert "state" not in child_node.attrib
    assert child_node.find("./label") is None
    assert child_node.find("./within") is None

    child_text = container.find("./literal")
    assert child_text is not None
    assert child_text.text == "About 1,230 results"

    system_text = root.find("./screen/groups/system/literal")
    assert system_text is not None
    assert system_text.text == "Battery 100 percent."


def test_xml_preserves_presence_based_group_item_attributes() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "groups": [
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "ref": "n3",
                                    "role": "button",
                                    "actions": ["tap"],
                                    "label": "Wi-Fi",
                                    "within": "",
                                    "value": "",
                                    "windowRef": "w1",
                                },
                                {
                                    "kind": "text",
                                    "text": "Connected",
                                    "within": "Wi-Fi",
                                    "value": "On",
                                    "windowRef": "w1",
                                },
                            ],
                        },
                        {"name": "keyboard", "nodes": []},
                        {"name": "system", "nodes": []},
                        {"name": "context", "nodes": []},
                        {"name": "dialog", "nodes": []},
                    ],
                    "visibleWindows": [
                        {
                            "windowRef": "w1",
                            "role": "main",
                            "focused": True,
                            "blocking": False,
                        }
                    ],
                }
            )
        )
    )

    node = root.find("./screen/groups/targets/button")
    assert node is not None
    assert node.attrib["within"] == ""
    assert node.attrib["value"] == ""
    assert node.attrib["windowRef"] == "w1"
    assert "role" not in node.attrib

    text = root.find("./screen/groups/targets/literal")
    assert text is not None
    assert text.text == "Connected"
    assert text.attrib == {
        "windowRef": "w1",
        "within": "Wi-Fi",
        "value": "On",
    }


def test_xml_rejects_unknown_nested_node_or_window_fields() -> None:
    with pytest.raises(ValidationError):
        render_xml(
            semantic_result(
                screen={
                    **semantic_screen(),
                    "groups": [
                        {
                            "name": "targets",
                            "nodes": [
                                {
                                    "kind": "container",
                                    "ref": "n3",
                                    "role": "scroll-container",
                                    "actions": ["scroll"],
                                    "label": "Search results",
                                    "scrollDirections": ["downward"],
                                    "metadata": {"secret": "ignore-me"},
                                }
                            ],
                        },
                        {"name": "keyboard", "nodes": []},
                        {"name": "system", "nodes": []},
                        {"name": "context", "nodes": []},
                        {"name": "dialog", "nodes": []},
                    ],
                    "visibleWindows": [
                        {
                            "windowRef": "w1",
                            "role": "main",
                            "focused": True,
                            "blocking": False,
                            "ownerId": "ignore-me",
                        }
                    ],
                }
            )
        )


@pytest.mark.parametrize(
    "warning",
    [
        "ARTIFACT_SCREEN_XML_MISSING",
        "ARTIFACT_SCREEN_XML_GARBAGE_COLLECTED",
        "artifactMissing",
        "artifactGarbageCollected",
    ],
)
def test_xml_rejects_artifact_lifecycle_warning_tokens(warning: str) -> None:
    with pytest.raises(ValidationError):
        render_xml(semantic_result(warnings=[warning]))


def test_xml_rejects_screen_md_artifact_pointer() -> None:
    with pytest.raises(ValidationError):
        render_xml(
            semantic_result(
                artifacts={
                    "screenMd": "/repo/.androidctl/artifacts/screens/screen-00013.md"
                }
            )
        )


def test_screen_xml_artifact_renders_after_p2_7_projection_lands() -> None:
    root = parse_xml(
        render_xml(
            semantic_result(
                artifacts={
                    "screenXml": (
                        "/repo/.androidctl/artifacts/screens/screen-00013.xml"
                    )
                }
            )
        )
    )

    artifacts = root.find("./artifacts")
    assert artifacts is not None
    assert (
        artifacts.attrib["screenXml"]
        == ".androidctl/artifacts/screens/screen-00013.xml"
    )
    assert "screenMd" not in artifacts.attrib


def test_retained_xml_keeps_screen_xml_out_of_artifacts() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                artifacts={
                    "screenshotPng": "/repo/.androidctl/screenshots/screen-00013.png",
                    "screenXml": (
                        "/repo/.androidctl/artifacts/screens/screen-00013.xml"
                    ),
                },
            ),
        )
    )

    artifacts = root.find("./artifacts")
    assert artifacts is not None
    assert artifacts.attrib == {
        "screenshotPng": ".androidctl/screenshots/screen-00013.png"
    }


def test_close_success_renders_retained_result() -> None:
    root = parse_xml(
        render_xml(
            retained_result(
                command="close",
                envelope="lifecycle",
                details={"closed": True},
            )
        )
    )

    assert_retained_result_spine(root, command="close", envelope="lifecycle", ok=True)
    assert root.find("./artifacts") is not None
    assert root.find("./details") is None


@pytest.mark.parametrize(
    ("command", "envelope", "artifacts"),
    [
        ("connect", "bootstrap", {}),
        (
            "screenshot",
            "artifact",
            {"screenshotPng": "/repo/.androidctl/screenshots/screen-00013.png"},
        ),
        ("close", "lifecycle", {}),
    ],
)
def test_retained_success_renders_current_contract_xml(
    command: str,
    envelope: str,
    artifacts: dict[str, object],
) -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command=command,
                envelope=envelope,
                artifacts=artifacts,
                details={"route": command},
            ),
        )
    )

    assert_retained_result_spine(root, command=command, envelope=envelope, ok=True)
    if command == "screenshot":
        artifacts_node = root.find("./artifacts")
        assert artifacts_node is not None
        assert (
            artifacts_node.attrib["screenshotPng"]
            == ".androidctl/screenshots/screen-00013.png"
        )


def test_retained_failure_payload_renders_current_contract_xml() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                ok=False,
                code="SCREENSHOT_UNAVAILABLE",
                message="screenshot unavailable",
                artifacts={
                    "screenshotPng": "/repo/.androidctl/screenshots/screen-00013.png"
                },
                details={"stage": "capture"},
            ),
        )
    )

    assert_retained_result_spine(
        root,
        command="screenshot",
        envelope="artifact",
        ok=False,
    )
    assert root.attrib["code"] == "SCREENSHOT_UNAVAILABLE"
    assert root.find("./message").text == "screenshot unavailable"
    assert root.find("./artifacts").attrib["screenshotPng"] == (
        ".androidctl/screenshots/screen-00013.png"
    )


def test_retained_failure_xml_projects_safe_details_allowlist() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                ok=False,
                code="WORKSPACE_STATE_UNWRITABLE",
                message="artifact write failed",
                details={
                    "sourceCode": "ARTIFACT_WRITE_FAILED",
                    "sourceKind": "workspace",
                    "operation": "screenshot",
                    "reason": "permission-denied",
                    "token": "Bearer secret",
                    "path": "/repo/.androidctl/screenshots/shot-00001.png",
                    "nested": {"rawRid": "rid-1"},
                },
            ),
        )
    )

    details = root.find("./details")
    assert details is not None
    assert details.attrib == {
        "sourceCode": "ARTIFACT_WRITE_FAILED",
        "sourceKind": "workspace",
        "operation": "screenshot",
        "reason": "permission-denied",
    }
    assert root.find("./truth") is None
    assert root.find("./screen") is None


def test_retained_failure_xml_projects_release_version_details() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="connect",
                envelope="bootstrap",
                ok=False,
                code="DEVICE_AGENT_VERSION_MISMATCH",
                message="device agent release version mismatch",
                details={
                    "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
                    "sourceKind": "device",
                    "expectedReleaseVersion": "0.1.0",
                    "actualReleaseVersion": "0.1.1",
                    "token": "Bearer secret",
                },
            ),
        )
    )

    details = root.find("./details")
    assert details is not None
    assert details.attrib == {
        "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
        "sourceKind": "device",
        "expectedReleaseVersion": "0.1.0",
        "actualReleaseVersion": "0.1.1",
    }


def test_retained_failure_xml_omits_noncanonical_release_version_details() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="connect",
                envelope="bootstrap",
                ok=False,
                code="DEVICE_AGENT_VERSION_MISMATCH",
                message="device agent release version mismatch",
                details={
                    "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
                    "sourceKind": "device",
                    "expectedReleaseVersion": "v0.1.0",
                    "actualReleaseVersion": "0.1.1 ",
                },
            ),
        )
    )

    details = root.find("./details")
    assert details is not None
    assert details.attrib == {
        "sourceCode": "DEVICE_AGENT_VERSION_MISMATCH",
        "sourceKind": "device",
    }


def test_retained_failure_xml_omits_details_without_safe_allowlisted_attrs() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                ok=False,
                code="WORKSPACE_STATE_UNWRITABLE",
                message="artifact write failed",
                details={
                    "reason": "/repo/.androidctl/screenshots/shot-00001.png",
                    "sourceCode": "ARTIFACT WRITE FAILED",
                    "token": "Bearer secret",
                    "nested": {"rawRid": "rid-1"},
                },
            ),
        )
    )

    assert root.find("./details") is None


def test_retained_failure_xml_omits_serial_like_source_code_detail() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                ok=False,
                code="WORKSPACE_STATE_UNWRITABLE",
                message="artifact write failed",
                details={
                    "sourceCode": "ABCDEF123456",
                    "sourceKind": "workspace",
                },
            ),
        )
    )

    details = root.find("./details")
    assert details is not None
    assert details.attrib == {"sourceKind": "workspace"}


@pytest.mark.parametrize(
    "reason",
    [
        "emulator-5554",
        "Bearer secret",
        "/repo/.androidctl/screenshots/shot-00001.png",
        r"Z:\workspace\.androidctl\shot.png",
        "https://example.test/shot.png",
        "has whitespace",
        "rid-1",
        "snapshot-0001",
        "0123456789abcdef0123456789abcdef",
        "api-token",
    ],
)
def test_retained_failure_xml_omits_unsafe_reason_detail(reason: str) -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                ok=False,
                code="WORKSPACE_STATE_UNWRITABLE",
                message="artifact write failed",
                details={
                    "sourceCode": "ARTIFACT_WRITE_FAILED",
                    "sourceKind": "workspace",
                    "reason": reason,
                },
            ),
        )
    )

    details = root.find("./details")
    assert details is not None
    assert details.attrib == {
        "sourceCode": "ARTIFACT_WRITE_FAILED",
        "sourceKind": "workspace",
    }
    assert reason not in details.attrib.values()


def test_retained_failure_xml_redacts_unsafe_details() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                ok=False,
                code="ARTIFACT_WRITE_FAILED",
                message="artifact write failed",
                details={
                    "reason": "Bearer secret",
                    "sourceCode": "ARTIFACT_WRITE_FAILED",
                    "sourceKind": "workspace",
                    "params": {"body": "secret"},
                    "rawRid": "rid-1",
                    "snapshotId": "snapshot-0001",
                    "fingerprint": "0123456789abcdef0123456789abcdef",
                    "path": "/repo/.androidctl/raw.json",
                    "stack": "Traceback (most recent call last)",
                },
            ),
        )
    )

    assert_retained_result_spine(
        root, command="screenshot", envelope="artifact", ok=False
    )
    message = root.find("./message")
    assert message is not None
    assert message.text == "artifact write failed"
    details = root.find("./details")
    assert details is not None
    assert details.attrib == {
        "sourceCode": "ARTIFACT_WRITE_FAILED",
        "sourceKind": "workspace",
    }
    xml_text = ET.tostring(root, encoding="unicode")
    for unsafe_fragment in (
        "Bearer secret",
        "params",
        "token",
        "/repo/.androidctl",
        "rid-1",
        "snapshot-0001",
        "0123456789abcdef0123456789abcdef",
        "Traceback",
    ):
        assert unsafe_fragment not in xml_text


@pytest.mark.parametrize("reason", ["api-token"])
def test_retained_failure_xml_omits_token_like_reason_detail(reason: str) -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                ok=False,
                code="ARTIFACT_WRITE_FAILED",
                message="artifact write failed",
                details={
                    "sourceCode": "ARTIFACT_WRITE_FAILED",
                    "sourceKind": "workspace",
                    "reason": reason,
                },
            ),
        )
    )

    details = root.find("./details")
    assert details is not None
    assert details.attrib == {
        "sourceCode": "ARTIFACT_WRITE_FAILED",
        "sourceKind": "workspace",
    }
    assert reason not in ET.tostring(root, encoding="unicode")


def test_retained_busy_xml_reflects_payload_message_without_rewrite() -> None:
    root = parse_xml(
        render_success_text(
            payload=retained_result(
                command="screenshot",
                envelope="artifact",
                ok=False,
                code="RUNTIME_BUSY",
                message="runtime is busy",
                details={"reason": "overlapping_control_request"},
            ),
        )
    )

    message = root.find("./message")
    assert message is not None
    assert message.text == "runtime is busy"
    details = root.find("./details")
    assert details is not None
    assert details.attrib == {"reason": "overlapping_control_request"}


@pytest.mark.parametrize(
    (
        "command",
        "code",
        "message",
        "exit_code",
        "tier",
    ),
    [
        (
            "connect",
            "USAGE_ERROR",
            "failure",
            ExitCode.USAGE,
            "usage",
        ),
        ("setup", "USAGE_ERROR", "failure", ExitCode.USAGE, "usage"),
        (
            "screenshot",
            "USAGE_ERROR",
            "failure",
            ExitCode.USAGE,
            "usage",
        ),
        (
            "observe",
            "DAEMON_UNAVAILABLE",
            "unable to reach daemon",
            ExitCode.ENVIRONMENT,
            "outer",
        ),
    ],
)
def test_render_error_text_renders_error_result_xml_for_public_cli_errors(
    command: str,
    code: str,
    message: str,
    exit_code: ExitCode,
    tier: str,
) -> None:
    root = parse_xml(
        render_error_text(
            _public_error(code=code, message=message, exit_code=exit_code),
            command=command,
            tier=tier,
        )
    )

    assert_error_result_spine(
        root,
        command=command,
        code=code,
        exit_code=int(exit_code),
        tier=tier,
        message=message,
        hint=None,
    )


def test_failure_message_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        render_xml(
            semantic_result(
                ok=False,
                code="WAIT_TIMEOUT",
                message="",
            )
        )


def test_render_error_text_uses_public_error_xml_contract() -> None:
    public_error = PublicError(
        code="SCREEN_UNAVAILABLE",
        message="screen is not ready",
        hint="run `androidctl observe` to refresh the current screen",
        exit_code=ExitCode.ERROR,
    )

    root = parse_xml(
        render_error_text(public_error, command="wait", tier="preDispatch")
    )
    assert_error_result_spine(
        root,
        command="wait",
        code="SCREEN_UNAVAILABLE",
        exit_code=int(ExitCode.ERROR),
        tier="preDispatch",
        message="screen is not ready",
        hint="run `androidctl observe` to refresh the current screen",
    )
    for attr in ("category", "payloadMode", "envelope"):
        assert attr not in root.attrib
    for child in (
        "truth",
        "uncertainty",
        "warnings",
        "artifacts",
        "details",
        "screen",
    ):
        assert root.find(f"./{child}") is None


def test_render_error_text_omits_whitespace_only_message_for_non_close() -> None:
    root = parse_xml(
        render_error_text(
            _public_error(
                code="DAEMON_UNAVAILABLE",
                message=" \t\n ",
                exit_code=ExitCode.ENVIRONMENT,
            ),
            command="tap",
            tier="outer",
        )
    )

    assert_error_result_spine(
        root,
        command="tap",
        code="DAEMON_UNAVAILABLE",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message=None,
        hint=None,
    )


def test_render_error_text_omits_whitespace_only_hint() -> None:
    public_error = PublicError(
        code="SCREEN_UNAVAILABLE",
        message="screen is not ready",
        hint=" \t\n ",
        exit_code=ExitCode.ERROR,
    )

    root = parse_xml(
        render_error_text(public_error, command="wait", tier="preDispatch")
    )

    assert_error_result_spine(
        root,
        command="wait",
        code="SCREEN_UNAVAILABLE",
        exit_code=int(ExitCode.ERROR),
        tier="preDispatch",
        message="screen is not ready",
        hint=None,
    )


def test_render_error_text_preserves_padded_message_and_hint() -> None:
    public_error = PublicError(
        code="SCREEN_UNAVAILABLE",
        message="  screen is not ready  ",
        hint="  refresh first  ",
        exit_code=ExitCode.ERROR,
    )

    root = parse_xml(
        render_error_text(public_error, command="wait", tier="preDispatch")
    )

    assert_error_result_spine(
        root,
        command="wait",
        code="SCREEN_UNAVAILABLE",
        exit_code=int(ExitCode.ERROR),
        tier="preDispatch",
        message="  screen is not ready  ",
        hint="  refresh first  ",
    )


def test_render_error_text_renders_usage_error_result_for_mutating_command() -> None:
    root = parse_xml(
        render_error_text(
            _public_error(code="USAGE_ERROR"),
            command="tap",
            tier="usage",
        )
    )

    assert_error_result_spine(
        root,
        command="tap",
        code="USAGE_ERROR",
        exit_code=int(ExitCode.ERROR),
        tier="usage",
        message="failure",
    )


def test_render_error_text_renders_screen_unavailable_outer_error_result() -> None:
    root = parse_xml(
        render_error_text(
            _public_error(code="SCREEN_UNAVAILABLE"),
            command="open",
            tier="outer",
        )
    )

    assert_error_result_spine(
        root,
        command="open",
        code="SCREEN_UNAVAILABLE",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="failure",
    )


def test_render_error_text_renders_device_not_connected_outer_error_result() -> None:
    root = parse_xml(
        render_error_text(
            _public_error(code="DEVICE_NOT_CONNECTED"),
            command="tap",
            tier="outer",
        )
    )

    assert_error_result_spine(
        root,
        command="tap",
        code="DEVICE_NOT_CONNECTED",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="failure",
    )


def test_render_error_text_renders_transport_outer_error_result_for_tap() -> None:
    root = parse_xml(
        render_error_text(
            _public_error(code="DAEMON_UNAVAILABLE"),
            command="tap",
            tier="outer",
        )
    )

    assert_error_result_spine(
        root,
        command="tap",
        code="DAEMON_UNAVAILABLE",
        exit_code=int(ExitCode.ERROR),
        tier="outer",
        message="failure",
    )


def test_render_error_text_unknown_command_uses_error_result() -> None:
    root = parse_xml(
        render_error_text(
            _public_error(code="USAGE_ERROR"),
            command="setup",
            tier="usage",
        )
    )

    assert_error_result_spine(
        root,
        command="setup",
        code="USAGE_ERROR",
        exit_code=int(ExitCode.ERROR),
        tier="usage",
        message="failure",
    )


def test_render_error_text_close_uses_error_result_xml() -> None:
    root = parse_xml(
        render_error_text(
            _public_error(
                code="DAEMON_UNAVAILABLE",
                message="daemon down",
                exit_code=ExitCode.ENVIRONMENT,
            ),
            command="close",
            tier="outer",
        )
    )

    assert_error_result_spine(
        root,
        command="close",
        code="DAEMON_UNAVAILABLE",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="daemon down",
    )


@pytest.mark.parametrize(
    "message",
    ["", " \t\n "],
    ids=["empty", "whitespace"],
)
def test_render_error_text_uses_close_fallback_for_blank_message(
    message: str,
) -> None:
    root = parse_xml(
        render_error_text(
            _public_error(
                code="DAEMON_UNAVAILABLE",
                message=message,
                exit_code=ExitCode.ENVIRONMENT,
            ),
            command="close",
            tier="outer",
        )
    )

    assert_error_result_spine(
        root,
        command="close",
        code="DAEMON_UNAVAILABLE",
        exit_code=int(ExitCode.ENVIRONMENT),
        tier="outer",
        message="close failed",
    )
