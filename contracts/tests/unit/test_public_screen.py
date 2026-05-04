from __future__ import annotations

import pytest
from public_screen_payloads import public_screen_payload_base
from pydantic import ValidationError

from androidctl_contracts.public_screen import (
    OMITTED_REASON_VALUES,
    PUBLIC_NODE_ACTION_VALUES,
    PUBLIC_NODE_AMBIGUITY_VALUES,
    PUBLIC_NODE_ORIGIN_VALUES,
    PUBLIC_NODE_ROLE_VALUES,
    PUBLIC_NODE_STATE_VALUES,
    SCROLL_DIRECTION_VALUES,
    TRANSIENT_KIND_VALUES,
    PublicScreen,
)


def _screen_payload(*, context_nodes: list[dict[str, object]]) -> dict[str, object]:
    payload = public_screen_payload_base("screen-00007")
    groups = payload["groups"]
    assert isinstance(groups, list)
    groups[3]["nodes"] = context_nodes
    return payload


def _screen_payload_with_transient(
    *, transient_items: list[dict[str, object]]
) -> dict[str, object]:
    payload = _screen_payload(context_nodes=[])
    payload["transient"] = transient_items
    return payload


@pytest.mark.parametrize(
    ("constant_name", "tokens"),
    [
        ("PUBLIC_NODE_ROLE_VALUES", PUBLIC_NODE_ROLE_VALUES),
        ("PUBLIC_NODE_ACTION_VALUES", PUBLIC_NODE_ACTION_VALUES),
        ("PUBLIC_NODE_STATE_VALUES", PUBLIC_NODE_STATE_VALUES),
        ("PUBLIC_NODE_ORIGIN_VALUES", PUBLIC_NODE_ORIGIN_VALUES),
        ("PUBLIC_NODE_AMBIGUITY_VALUES", PUBLIC_NODE_AMBIGUITY_VALUES),
        ("OMITTED_REASON_VALUES", OMITTED_REASON_VALUES),
        ("TRANSIENT_KIND_VALUES", TRANSIENT_KIND_VALUES),
        ("SCROLL_DIRECTION_VALUES", SCROLL_DIRECTION_VALUES),
    ],
)
def test_public_screen_exported_registry_constants_are_closed_token_tuples(
    constant_name: str,
    tokens: tuple[str, ...],
) -> None:
    assert isinstance(tokens, tuple), constant_name
    assert all(isinstance(token, str) and token for token in tokens), constant_name
    assert len(tokens) == len(set(tokens)), constant_name


def test_public_screen_origin_and_ambiguity_registries_are_closed_empty() -> None:
    assert PUBLIC_NODE_ORIGIN_VALUES == ()
    assert PUBLIC_NODE_AMBIGUITY_VALUES == ()


def test_public_screen_accepts_non_empty_opaque_screen_ids() -> None:
    for screen_id in ("screen-00007", "opaque:alpha/beta#42", "not-a-sequence"):
        screen = PublicScreen.model_validate(
            _screen_payload(context_nodes=[]) | {"screenId": screen_id}
        )

        assert screen.screen_id == screen_id


def test_public_screen_rejects_empty_screen_id() -> None:
    with pytest.raises(ValidationError):
        PublicScreen.model_validate(
            _screen_payload(context_nodes=[]) | {"screenId": ""}
        )


def test_public_screen_accepts_canonical_text_items() -> None:
    screen = PublicScreen.model_validate(
        _screen_payload(
            context_nodes=[
                {"kind": "text", "text": "Network & internet"},
            ]
        )
    )

    assert screen.groups[3].nodes[0].kind == "text"
    assert screen.groups[3].nodes[0].text == "Network & internet"
    assert screen.model_dump(by_alias=True, mode="json")["groups"][3]["nodes"] == [
        {"kind": "text", "text": "Network & internet"}
    ]


def test_public_screen_rejects_bare_text_without_kind() -> None:
    with pytest.raises(ValidationError):
        PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {"text": "Network & internet"},
                ]
            )
        )


@pytest.mark.parametrize("field_name", ["role", "label"])
def test_public_screen_rejects_role_or_label_on_text_items(field_name: str) -> None:
    with pytest.raises(ValidationError, match='kind="text" items cannot include'):
        PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {
                        "kind": "text",
                        "text": "Network & internet",
                        field_name: "Network & internet",
                    },
                ]
            )
        )


def test_public_screen_still_accepts_role_text_nodes() -> None:
    screen = PublicScreen.model_validate(
        _screen_payload(
            context_nodes=[
                {"role": "text", "label": "Saved networks"},
            ]
        )
    )

    assert screen.groups[3].nodes[0].kind == "node"
    assert screen.groups[3].nodes[0].role == "text"
    assert screen.groups[3].nodes[0].label == "Saved networks"
    assert screen.model_dump(by_alias=True, mode="json")["groups"][3]["nodes"] == [
        {"role": "text", "label": "Saved networks"}
    ]


def test_public_screen_accepts_each_final_node_registry_token() -> None:
    for role in PUBLIC_NODE_ROLE_VALUES:
        screen = PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {"role": role, "label": f"{role} item"},
                ]
            )
        )
        assert screen.groups[3].nodes[0].role == role

    for action in PUBLIC_NODE_ACTION_VALUES:
        screen = PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {
                        "ref": "n1",
                        "role": "button",
                        "label": "Action",
                        "actions": [action],
                    },
                ]
            )
        )
        assert screen.groups[3].nodes[0].actions == (action,)

    for state in PUBLIC_NODE_STATE_VALUES:
        screen = PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {
                        "ref": "n1",
                        "role": "button",
                        "label": "State",
                        "state": [state],
                    },
                ]
            )
        )
        assert screen.groups[3].nodes[0].state == (state,)


@pytest.mark.parametrize("field_name", ["origin", "ambiguity"])
@pytest.mark.parametrize("include_null", [False, True])
def test_public_screen_accepts_absent_or_null_closed_empty_optional_registries(
    field_name: str,
    include_null: bool,
) -> None:
    context_node: dict[str, object] = {
        "ref": "n1",
        "role": "button",
        "label": "Optional",
    }
    if include_null:
        context_node[field_name] = None

    screen = PublicScreen.model_validate(_screen_payload(context_nodes=[context_node]))

    assert getattr(screen.groups[3].nodes[0], field_name) is None


@pytest.mark.parametrize(
    "node_update",
    [
        {"role": "mystery-role"},
        {"actions": ["poke"]},
        {"state": ["glowing"]},
        {"origin": "unknown-origin"},
        {"ambiguity": "unknown-ambiguity"},
    ],
)
def test_public_screen_rejects_unknown_public_node_registry_tokens(
    node_update: dict[str, object],
) -> None:
    context_node: dict[str, object] = {
        "ref": "n1",
        "role": "button",
        "label": "Mystery",
    }
    context_node.update(node_update)

    with pytest.raises(ValidationError):
        PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    context_node,
                ]
            )
        )


def test_public_screen_accepts_current_useful_final_tokens() -> None:
    screen = PublicScreen.model_validate(
        _screen_payload(
            context_nodes=[
                {
                    "ref": "n1",
                    "role": "button",
                    "label": "More options",
                    "actions": ["tap", "longTap"],
                    "state": ["unchecked", "disabled", "focused", "password"],
                },
            ]
        )
    )

    node = screen.groups[3].nodes[0]
    assert node.actions == ("tap", "longTap")
    assert node.state == ("unchecked", "disabled", "focused", "password")


def test_public_screen_input_submit_refs_validate_and_dump_canonically() -> None:
    screen = PublicScreen.model_validate(
        _screen_payload(
            context_nodes=[
                {
                    "ref": "n1",
                    "role": "input",
                    "label": "Search",
                    "actions": ["type"],
                    "submitRefs": ["n2"],
                },
                {
                    "ref": "n2",
                    "role": "button",
                    "label": "Search",
                    "actions": ["tap"],
                },
            ]
        )
    )

    dumped_nodes = screen.model_dump(by_alias=True, mode="json")["groups"][3]["nodes"]
    assert dumped_nodes[0]["submitRefs"] == ["n2"]
    assert "submitsInputRefs" not in dumped_nodes[1]


def test_public_screen_rejects_submit_ref_target_that_would_not_serialize_ref() -> None:
    with pytest.raises(ValidationError, match="same-screen public refs"):
        PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {
                        "ref": "n1",
                        "role": "input",
                        "label": "Search",
                        "actions": ["type"],
                        "submitRefs": ["n2"],
                    },
                    {
                        "kind": "text",
                        "ref": "n2",
                        "text": "Search",
                    },
                ]
            )
        )


def test_public_screen_canonical_dump_submit_refs_target_serialized_refs() -> None:
    screen = PublicScreen.model_validate(
        _screen_payload(
            context_nodes=[
                {
                    "ref": "n1",
                    "role": "input",
                    "label": "Search",
                    "actions": ["type"],
                    "submitRefs": ["n2"],
                },
                {
                    "ref": "n2",
                    "role": "button",
                    "label": "Search",
                    "actions": ["tap"],
                },
                {
                    "kind": "text",
                    "ref": "n3",
                    "text": "Decorative text",
                },
            ]
        )
    )

    dumped_nodes = screen.model_dump(by_alias=True, mode="json")["groups"][3]["nodes"]
    dumped_refs = {
        node["ref"]
        for node in dumped_nodes
        if isinstance(node, dict) and isinstance(node.get("ref"), str)
    }
    dumped_submit_refs = {
        submit_ref
        for node in dumped_nodes
        if isinstance(node, dict)
        for submit_ref in node.get("submitRefs", [])
    }

    assert dumped_refs == {"n1", "n2"}
    assert dumped_submit_refs <= dumped_refs


def test_public_screen_normalizes_empty_and_missing_submit_refs() -> None:
    screen = PublicScreen.model_validate(
        _screen_payload(
            context_nodes=[
                {
                    "ref": "n1",
                    "role": "input",
                    "label": "Search",
                    "actions": ["type"],
                    "submitRefs": [],
                },
                {
                    "ref": "n2",
                    "role": "button",
                    "label": "Search",
                    "actions": ["tap"],
                },
            ]
        )
    )

    dumped = screen.model_dump(by_alias=True, mode="json")
    assert "submitRefs" not in dumped["groups"][3]["nodes"][0]

    missing_submit_refs_screen = PublicScreen.model_validate(
        _screen_payload(
            context_nodes=[
                {
                    "ref": "n1",
                    "role": "input",
                    "label": "Search",
                    "actions": ["type"],
                },
            ]
        )
    )
    assert missing_submit_refs_screen.groups[3].nodes[0].submit_refs == ()


def test_public_screen_accepts_duplicate_refs_without_submit_refs() -> None:
    screen = PublicScreen.model_validate(
        _screen_payload(
            context_nodes=[
                {"ref": "n1", "role": "button", "label": "Search"},
                {"ref": "n1", "role": "button", "label": "Search again"},
            ]
        )
    )

    assert [node.ref for node in screen.groups[3].nodes] == ["n1", "n1"]


def test_public_screen_rejects_duplicate_refs_when_submit_refs_exist() -> None:
    with pytest.raises(ValidationError, match="public refs must be unique"):
        PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {
                        "ref": "n1",
                        "role": "input",
                        "label": "Search",
                        "actions": ["type"],
                        "submitRefs": ["n2"],
                    },
                    {"ref": "n2", "role": "button", "label": "Search"},
                    {"ref": "n2", "role": "button", "label": "Search again"},
                ]
            )
        )


@pytest.mark.parametrize("field_name", ["unknownField", "submitsInputRefs"])
def test_public_screen_rejects_unknown_public_node_fields(field_name: str) -> None:
    with pytest.raises(ValidationError):
        PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {
                        "ref": "n1",
                        "role": "input",
                        "label": "Search",
                        "actions": ["type"],
                        field_name: ["n2"],
                    },
                    {
                        "ref": "n2",
                        "role": "button",
                        "label": "Search",
                        "actions": ["tap"],
                    },
                ]
            )
        )


@pytest.mark.parametrize(
    "submit_refs",
    [
        [""],
        ["x1"],
        ["raw:1"],
        ["n2", "n2"],
    ],
)
def test_public_screen_rejects_invalid_submit_refs_tokens(
    submit_refs: list[str],
) -> None:
    with pytest.raises(ValidationError):
        PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {
                        "ref": "n1",
                        "role": "input",
                        "label": "Search",
                        "actions": ["type"],
                        "submitRefs": submit_refs,
                    },
                    {
                        "ref": "n2",
                        "role": "button",
                        "label": "Search",
                        "actions": ["tap"],
                    },
                ]
            )
        )


@pytest.mark.parametrize(
    "source_node",
    [
        {"ref": "n1", "role": "button", "label": "Search", "actions": ["tap"]},
        {"role": "input", "label": "Search", "actions": ["type"]},
        {"ref": "n1", "role": "text", "label": "Search"},
        {"kind": "text", "text": "Search"},
    ],
)
def test_public_screen_rejects_submit_refs_on_invalid_source_nodes(
    source_node: dict[str, object],
) -> None:
    source_node = dict(source_node)
    source_node["submitRefs"] = ["n2"]

    with pytest.raises(ValidationError):
        PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    source_node,
                    {
                        "ref": "n2",
                        "role": "button",
                        "label": "Search",
                        "actions": ["tap"],
                    },
                ]
            )
        )


@pytest.mark.parametrize(
    "context_nodes",
    [
        [
            {
                "ref": "n1",
                "role": "input",
                "label": "Search",
                "actions": ["type"],
                "submitRefs": ["n9"],
            },
            {"ref": "n2", "role": "button", "label": "Search", "actions": ["tap"]},
        ],
        [
            {
                "ref": "n1",
                "role": "input",
                "label": "Search",
                "actions": ["type"],
                "submitRefs": ["n1"],
            },
        ],
        [
            {
                "ref": "n1",
                "role": "input",
                "label": "Search",
                "actions": ["type"],
                "submitRefs": ["n2"],
            },
            {"ref": "n2", "role": "button", "label": "Search", "actions": ["tap"]},
            {"ref": "n2", "role": "button", "label": "Send", "actions": ["tap"]},
        ],
        [
            {
                "ref": "raw:1",
                "role": "input",
                "label": "Search",
                "actions": ["type"],
                "submitRefs": ["n2"],
            },
            {"ref": "n2", "role": "button", "label": "Search", "actions": ["tap"]},
        ],
    ],
)
def test_public_screen_rejects_invalid_submit_refs_screen_invariants(
    context_nodes: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        PublicScreen.model_validate(_screen_payload(context_nodes=context_nodes))


def test_public_screen_accepts_each_scroll_direction_token() -> None:
    for scroll_direction in SCROLL_DIRECTION_VALUES:
        screen = PublicScreen.model_validate(
            _screen_payload(
                context_nodes=[
                    {
                        "kind": "container",
                        "ref": "n1",
                        "role": "scroll-container",
                        "label": "Results",
                        "scrollDirections": [scroll_direction],
                        "children": [
                            {
                                "ref": "n2",
                                "role": "button",
                                "label": "Open",
                            }
                        ],
                    },
                ]
            )
        )
        assert screen.groups[3].nodes[0].scroll_directions == (scroll_direction,)


@pytest.mark.parametrize(
    "context_node",
    [
        {
            "kind": "container",
            "ref": "n1",
            "role": "scroll-container",
            "label": "Results",
            "scrollDirections": ["sideways"],
            "children": [
                {
                    "ref": "n2",
                    "role": "button",
                    "label": "Open",
                }
            ],
        },
        {
            "ref": "n1",
            "role": "button",
            "label": "Results",
            "scrollDirections": ["down"],
        },
    ],
)
def test_public_screen_rejects_unknown_or_misplaced_scroll_tokens_now(
    context_node: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PublicScreen.model_validate(_screen_payload(context_nodes=[context_node]))


@pytest.mark.parametrize(
    "omitted_item",
    [
        {"group": "targets", "reason": "hidden", "count": 1},
        {"group": "overlay", "reason": "offscreen", "count": 1},
    ],
)
def test_public_screen_rejects_unknown_omitted_tokens_now(
    omitted_item: dict[str, object],
) -> None:
    payload = _screen_payload(context_nodes=[])
    payload["omitted"] = [omitted_item]

    with pytest.raises(ValidationError):
        PublicScreen.model_validate(payload)


def test_public_screen_accepts_each_omitted_reason_token() -> None:
    for reason in OMITTED_REASON_VALUES:
        payload = _screen_payload(context_nodes=[])
        payload["omitted"] = [{"group": "targets", "reason": reason, "count": 1}]

        screen = PublicScreen.model_validate(payload)

        assert screen.omitted[0].reason == reason


def test_public_screen_rejects_unknown_transient_kind_now() -> None:
    with pytest.raises(ValidationError):
        PublicScreen.model_validate(
            _screen_payload_with_transient(
                transient_items=[
                    {"text": "Saved", "kind": "modal"},
                ]
            )
        )


def test_public_screen_accepts_each_transient_kind_token() -> None:
    for kind in TRANSIENT_KIND_VALUES:
        screen = PublicScreen.model_validate(
            _screen_payload_with_transient(
                transient_items=[
                    {"text": "Saved", "kind": kind},
                ]
            )
        )

        assert screen.transient[0].text == "Saved"
        assert screen.transient[0].kind == kind
        assert screen.model_dump(by_alias=True, mode="json")["transient"] == [
            {"text": "Saved", "kind": kind}
        ]


@pytest.mark.parametrize(
    "transient_item",
    [
        {"label": "Saved", "kind": "toast"},
        {"text": "", "kind": "toast"},
    ],
)
def test_public_screen_rejects_malformed_transient_text(
    transient_item: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PublicScreen.model_validate(
            _screen_payload_with_transient(
                transient_items=[
                    transient_item,
                ]
            )
        )
