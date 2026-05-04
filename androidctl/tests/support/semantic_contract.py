from __future__ import annotations

from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from androidctl_contracts.command_results import RetainedResultEnvelope

SOURCE_SCREEN_REQUIRED = "required"
SOURCE_SCREEN_ABSENT = "absent"
SOURCE_SCREEN_OPTIONAL = "optional"

_SEMANTIC_COMMAND_CATEGORIES: dict[str, str] = {
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


def _normalize_actions(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return None


def _normalize_group_node(item: dict[str, object]) -> dict[str, object]:
    payload = dict(item)
    actions = _normalize_actions(payload.get("actions"))
    if actions is not None:
        payload["actions"] = actions
    return payload


def _normalize_group_nodes(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    return [_normalize_group_node(item) for item in nodes]


def semantic_screen(
    screen_id: str = "screen-00013",
    *,
    label: str = "Wi-Fi",
    focus_ref: str = "n3",
    target_ref: str = "n3",
    target_actions: str = "tap",
    context_nodes: list[dict[str, object]] | None = None,
    system_nodes: list[dict[str, object]] | None = None,
    dialog_nodes: list[dict[str, object]] | None = None,
    app_overrides: dict[str, object] | None = None,
    blocking_group: str | None = None,
) -> dict[str, object]:
    app = {
        "packageName": "com.android.settings",
        "activityName": "com.android.settings.Settings",
    }
    if app_overrides is not None:
        app.update(app_overrides)

    surface: dict[str, object] = {
        "keyboardVisible": False,
        "focus": {"inputRef": focus_ref},
    }
    if blocking_group is not None:
        surface["blockingGroup"] = blocking_group

    return {
        "screenId": screen_id,
        "app": app,
        "surface": surface,
        "groups": [
            {
                "name": "targets",
                "nodes": _normalize_group_nodes(
                    [
                        {
                            "ref": target_ref,
                            "role": "button",
                            "actions": target_actions,
                            "label": label,
                        }
                    ]
                ),
            },
            {"name": "keyboard", "nodes": []},
            {
                "name": "system",
                "nodes": _normalize_group_nodes(
                    list(system_nodes)
                    if system_nodes is not None
                    else [{"kind": "text", "text": "Battery 100 percent."}]
                ),
            },
            {
                "name": "context",
                "nodes": _normalize_group_nodes(
                    list(context_nodes)
                    if context_nodes is not None
                    else [{"kind": "text", "text": "Network & internet"}]
                ),
            },
            {
                "name": "dialog",
                "nodes": _normalize_group_nodes(
                    list(dialog_nodes)
                    if dialog_nodes is not None
                    else [
                        {
                            "ref": "n7",
                            "role": "button",
                            "actions": "tap",
                            "label": "Allow",
                        }
                    ]
                ),
            },
        ],
        "omitted": [],
        "visibleWindows": [],
        "transient": [],
    }


def semantic_result(
    *,
    command: str = "observe",
    category: str = "observe",
    screen_id: str = "screen-00013",
    source_screen_id: str | None = "screen-00013",
    execution_outcome: str = "notApplicable",
    continuity_status: str = "stable",
    changed: bool | None = False,
    screen_kwargs: dict[str, object] | None = None,
    artifacts: dict[str, object] | None = None,
    **overrides: object,
) -> dict[str, object]:
    truth: dict[str, object] = {
        "executionOutcome": execution_outcome,
        "continuityStatus": continuity_status,
        "observationQuality": "authoritative",
    }
    if changed is not None:
        truth["changed"] = changed

    payload: dict[str, object] = {
        "ok": True,
        "command": command,
        "category": category,
        "payloadMode": "full",
        "nextScreenId": screen_id,
        "truth": truth,
        "screen": semantic_screen(screen_id, **(screen_kwargs or {})),
        "uncertainty": [],
        "warnings": [],
        "artifacts": artifacts or {},
    }
    if source_screen_id is not None:
        payload["sourceScreenId"] = source_screen_id

    for key in ("truth", "screen", "artifacts"):
        if key not in overrides:
            continue
        override = overrides.pop(key)
        if isinstance(override, dict):
            base_value = payload.get(key)
            if isinstance(base_value, dict):
                payload[key] = {**base_value, **override}
            else:
                payload[key] = dict(override)
        else:
            payload[key] = override

    payload.update(overrides)
    return payload


def retained_result(
    *,
    command: str,
    envelope: str,
    ok: bool = True,
    code: str | None = None,
    message: str | None = None,
    artifacts: dict[str, object] | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = RetainedResultEnvelope.model_validate(
        {
            "ok": ok,
            "command": command,
            "envelope": envelope,
            "code": code,
            "message": message,
            "artifacts": artifacts or {},
            "details": details or {},
        }
    )
    return payload.model_dump(by_alias=True, mode="json", exclude_none=True)


def parse_xml(stdout: str) -> ElementTree.Element:
    return ElementTree.fromstring(stdout.strip())


def assert_truth_spine(
    root: Element,
    *,
    execution_outcome: str,
    continuity_status: str,
    observation_quality: str | None = None,
    changed: bool | None | object = ...,
) -> Element:
    truth = root.find("./truth")
    assert truth is not None
    assert truth.attrib["executionOutcome"] == execution_outcome
    assert truth.attrib["continuityStatus"] == continuity_status
    if observation_quality is not None:
        assert truth.attrib["observationQuality"] == observation_quality
    if changed is ...:
        return truth
    if changed is None:
        assert "changed" not in truth.attrib
        return truth
    assert truth.attrib["changed"] == str(changed).lower()
    return truth


def _assert_result_family(root: Element, *, family: str) -> None:
    assert root.find("./truth") is not None, "result is missing <truth>"
    assert root.find("./artifacts") is not None, "result is missing <artifacts>"
    if family == "full":
        assert root.attrib["payloadMode"] == "full"
        assert root.find("./screen") is not None, "full result is missing <screen>"
        return
    if family == "none":
        assert root.attrib["payloadMode"] == "none"
        assert root.find("./screen") is None, "none result must not include <screen>"
        return
    raise AssertionError(f"unknown result family: {family}")


def assert_public_result_spine(
    root: Element,
    *,
    command: str,
    result_family: str,
    source_screen_policy: str = SOURCE_SCREEN_OPTIONAL,
    source_screen_id: str | None = None,
    ok: bool | None = None,
    check_category: bool = True,
) -> None:
    assert root.tag == "result"
    assert root.attrib["command"] == command
    if check_category:
        assert root.attrib["category"] == _SEMANTIC_COMMAND_CATEGORIES[command]
    if ok is not None:
        assert root.attrib["ok"] == str(ok).lower()
    _assert_result_family(root, family=result_family)

    has_source_screen = "sourceScreenId" in root.attrib
    if source_screen_policy == SOURCE_SCREEN_REQUIRED:
        assert has_source_screen
    elif source_screen_policy == SOURCE_SCREEN_ABSENT:
        assert not has_source_screen
    elif source_screen_policy != SOURCE_SCREEN_OPTIONAL:
        raise AssertionError(f"unknown source screen policy: {source_screen_policy}")

    if source_screen_id is None:
        return
    assert root.attrib["sourceScreenId"] == source_screen_id


def assert_retained_result_spine(
    root: Element,
    *,
    command: str,
    envelope: str,
    ok: bool | None = None,
) -> None:
    assert root.tag == "retainedResult"
    assert root.attrib["command"] == command
    assert root.attrib["envelope"] == envelope
    if ok is not None:
        assert root.attrib["ok"] == str(ok).lower()

    for attr in ("category", "payloadMode", "sourceScreenId", "nextScreenId"):
        assert attr not in root.attrib
    for tag in ("truth", "uncertainty", "warnings", "screen"):
        assert root.find(f"./{tag}") is None


def assert_error_result_spine(
    root: Element,
    *,
    command: str,
    code: str,
    exit_code: int,
    tier: str,
    message: str | None | object = ...,
    hint: str | None | object = ...,
) -> None:
    assert root.tag == "errorResult"
    assert root.attrib == {
        "ok": "false",
        "code": code,
        "exitCode": str(exit_code),
        "tier": tier,
        "command": command,
    }
    allowed_children = {"message", "hint"}
    assert {child.tag for child in root} <= allowed_children

    message_element = root.find("./message")
    if message is not ...:
        if message is None:
            assert message_element is None
        else:
            assert message_element is not None
            assert message_element.text == message

    hint_element = root.find("./hint")
    if hint is not ...:
        if hint is None:
            assert hint_element is None
        else:
            assert hint_element is not None
            assert hint_element.text == hint


__all__ = [
    "SOURCE_SCREEN_ABSENT",
    "SOURCE_SCREEN_REQUIRED",
    "assert_error_result_spine",
    "assert_public_result_spine",
    "assert_retained_result_spine",
    "assert_truth_spine",
    "parse_xml",
    "retained_result",
    "semantic_result",
    "semantic_screen",
]
